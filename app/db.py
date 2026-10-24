"""SQLite storage. Recipe chunks live in an FTS5 table (BM25 retrieval); swap for
pgvector later by replacing `search_chunks`."""
import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("MEALPREP_DB", "mealprep.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recipes (
  id INTEGER PRIMARY KEY, book_id INTEGER REFERENCES books(id),
  name TEXT NOT NULL, servings INTEGER NOT NULL, data TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(book_id UNINDEXED, text);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def add_book(title: str, chunks: list[str]) -> int:
    with conn() as c:
        book_id = c.execute("INSERT INTO books(title) VALUES (?)", (title,)).lastrowid
        c.executemany("INSERT INTO chunks(book_id, text) VALUES (?, ?)", [(book_id, t) for t in chunks])
        return book_id


def add_recipe(book_id: int, recipe: dict) -> int:
    with conn() as c:
        return c.execute(
            "INSERT INTO recipes(book_id, name, servings, data) VALUES (?,?,?,?)",
            (book_id, recipe["name"], recipe["servings"], json.dumps(recipe)),
        ).lastrowid


def all_recipes() -> list[dict]:
    with conn() as c:
        return [{"id": r["id"], **json.loads(r["data"])} for r in c.execute("SELECT id, data FROM recipes")]


def list_books() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT id, title FROM books")]


def search_chunks(query: str, limit: int = 8) -> list[str]:
    terms = [t for t in "".join(ch if ch.isalnum() else " " for ch in query).split() if len(t) > 2]
    if not terms:
        return []
    with conn() as c:
        rows = c.execute(
            "SELECT text FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
            (" OR ".join(terms), limit),
        )
        return [r["text"] for r in rows]
