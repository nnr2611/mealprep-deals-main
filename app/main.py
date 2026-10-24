import anthropic
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, ingest, planner
from .deals import get_provider, normalize_postal, store_ranking

app = FastAPI(title="MealPrep Deals")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY


@app.get("/")
def index():
    return FileResponse("app/static/index.html")


@app.get("/api/books")
def books():
    return {"books": db.list_books(), "recipes": [{"id": r["id"], "name": r["name"]} for r in db.all_recipes()]}


@app.post("/api/books")
async def upload_book(title: str = Form(...), file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "File too large (25 MB max)")
    return ingest.ingest_book(title, file.filename or "book.txt", data, client())


@app.get("/api/deals")
def deals(postal_code: str):
    try:
        ds = get_provider().deals_for(postal_code)
        normalize_postal(postal_code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"deals": [d.to_dict() for d in ds], "stores": store_ranking(ds)}


class PlanRequest(BaseModel):
    postal_code: str
    calories_per_day: int = 2000
    protein_g_per_day: int = 100
    people: int = 1
    notes: str = ""


@app.post("/api/plan")
def plan(req: PlanRequest):
    try:
        ds = get_provider().deals_for(req.postal_code)
        return planner.make_plan(req.postal_code, ds, req.calories_per_day, req.protein_g_per_day,
                                 req.people, req.notes, client())
    except ValueError as e:
        raise HTTPException(400, str(e))
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude API error: {e.status_code}")
