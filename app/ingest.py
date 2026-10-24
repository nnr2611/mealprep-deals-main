"""Recipe book ingestion: extract text and have Claude pull out structured recipes (nothing is stored server-side)."""
import io
import os
from concurrent.futures import ThreadPoolExecutor

import anthropic
from pypdf import PdfReader

from .models import BookRecipes

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
EXTRACT_WINDOW = 24000
MAX_WINDOWS = 12  # ~290k characters; keeps one request inside a serverless time limit


def extract_text(filename: str, data: bytes) -> str:
    if filename.lower().endswith(".pdf"):
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    return data.decode("utf-8", errors="replace")


def _extract_window(window: str, client: anthropic.Anthropic) -> list[dict]:
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        output_config={"effort": "low"},
        system="Extract every complete recipe from the text. Use metric units where possible. "
               "Skip fragments that are not full recipes.",
        messages=[{"role": "user", "content": window}],
        output_format=BookRecipes,
    )
    return [r.model_dump() for r in resp.parsed_output.recipes] if resp.parsed_output else []


def extract_recipes(text: str, client: anthropic.Anthropic) -> tuple[list[dict], bool]:
    """Returns (recipes, truncated). Windows are processed in parallel."""
    windows = [text[i:i + EXTRACT_WINDOW] for i in range(0, len(text), EXTRACT_WINDOW)]
    truncated = len(windows) > MAX_WINDOWS
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = pool.map(lambda w: _extract_window(w, client), windows[:MAX_WINDOWS])
    return [r for rs in results for r in rs], truncated
