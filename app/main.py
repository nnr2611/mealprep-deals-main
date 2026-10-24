import logging
import os
from pathlib import Path

import anthropic
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import ingest, planner
from .deals import DealsUnavailable, best_by_item, get_provider, store_ranking
from .models import Recipe

PUBLIC = Path(__file__).resolve().parent.parent / "public"
MAX_UPLOAD = 4 * 1024 * 1024  # Vercel rejects request bodies over ~4.5 MB

log = logging.getLogger("mealprep")
app = FastAPI(title="MealPrep Deals")


def client(key: str | None) -> anthropic.Anthropic:
    """Visitors send their own key per request; the server env key is only a fallback for local runs."""
    key = (key or "").strip() or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise HTTPException(401, "Add your Anthropic API key (top right) to continue.")
    return anthropic.Anthropic(api_key=key)


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"detail": msg}, status_code=status)


@app.exception_handler(anthropic.AuthenticationError)
async def _auth(_: Request, e: Exception):
    return _err(401, "Anthropic rejected that API key. Check it and try again.")


@app.exception_handler(anthropic.RateLimitError)
async def _rate(_: Request, e: Exception):
    return _err(429, "Anthropic rate limit reached. Wait a moment and try again.")


@app.exception_handler(anthropic.APIConnectionError)
async def _conn(_: Request, e: Exception):
    return _err(502, "Could not reach the Anthropic API.")


@app.exception_handler(anthropic.APIStatusError)
async def _status(_: Request, e: Exception):
    detail = getattr(e, "message", "") or ""
    log.warning("Anthropic API error %s: %s", e.status_code, detail)
    return _err(400 if e.status_code == 400 else 502, f"Claude API error ({e.status_code}): {detail}"[:300])


@app.exception_handler(DealsUnavailable)
async def _deals(_: Request, e: Exception):
    return _err(502, str(e))


@app.exception_handler(Exception)
async def _unexpected(_: Request, e: Exception):
    log.exception("Unhandled error")  # the traceback stays in server logs; clients always get JSON
    return _err(500, "Something went wrong on the server. Please try again.")


@app.get("/")
def index():
    return FileResponse(PUBLIC / "index.html")


@app.post("/api/extract")
async def extract(file: UploadFile = File(...), x_anthropic_key: str | None = Header(None)):
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (4 MB max). Split the book into smaller parts.")
    text = ingest.extract_text(file.filename or "book.txt", data)
    if not text.strip():
        raise HTTPException(400, "No readable text found (scanned PDFs are not supported).")
    recipes, truncated = ingest.extract_recipes(text, client(x_anthropic_key))
    return {"recipes": recipes, "truncated": truncated}


class PlanRequest(BaseModel):
    postal_code: str
    recipes: list[Recipe] = Field(min_length=1, max_length=300)
    calories_per_day: int = Field(2000, ge=800, le=6000)
    protein_g_per_day: int = Field(100, ge=0, le=400)
    people: int = Field(1, ge=1, le=12)
    notes: str = Field("", max_length=1000)


@app.post("/api/plan")
def plan(req: PlanRequest, x_anthropic_key: str | None = Header(None)):
    c = client(x_anthropic_key)  # fail fast on a missing key before hitting the flyer service
    recipes = [r.model_dump() for r in req.recipes]
    provider = get_provider()
    try:
        deals = provider.deals_for(req.postal_code, planner.ingredient_queries(recipes))
        result = planner.make_plan(deals, recipes, req.calories_per_day, req.protein_g_per_day, req.people,
                                   req.notes, c)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**result, "stores": store_ranking(deals), "deals": [d.to_dict() for d in best_by_item(deals).values()],
            "deals_source": provider.name}
