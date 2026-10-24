"""Recipe book ingestion: extract text, chunk it for RAG, and have Claude pull out structured recipes."""
import io
import os

import anthropic
from pypdf import PdfReader

from . import db
from .models import BookRecipes

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
CHUNK_CHARS = 1500
EXTRACT_WINDOW = 24000


def extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    return data.decode("utf-8", errors="replace")


def chunk(text: str) -> list[str]:
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) > CHUNK_CHARS:
            chunks.append(cur.strip())
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def extract_recipes(text: str, client: anthropic.Anthropic) -> list[dict]:
    out = []
    for i in range(0, len(text), EXTRACT_WINDOW):
        resp = client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            output_config={"effort": "low"},
            system="Extract every complete recipe from the text. Use metric units where possible. "
                   "Skip fragments that are not full recipes.",
            messages=[{"role": "user", "content": text[i:i + EXTRACT_WINDOW]}],
            output_format=BookRecipes,
        )
        if resp.parsed_output:
            out += [r.model_dump() for r in resp.parsed_output.recipes]
    return out


def ingest_book(title: str, filename: str, data: bytes, client: anthropic.Anthropic) -> dict:
    text = extract_text(filename, data)
    book_id = db.add_book(title, chunk(text))
    recipes = extract_recipes(text, client)
    for r in recipes:
        db.add_recipe(book_id, r)
    return {"book_id": book_id, "recipes": len(recipes)}
