"""Per-recipe nutrition from a per-100g food table (Health Canada CNF / USDA FDC exports).

Drop a bigger CSV at data/nutrition.csv with the same columns to replace the sample."""
import csv
import difflib
import os
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA = os.environ.get("NUTRITION_CSV") or str(
    _DATA_DIR / "nutrition.csv" if (_DATA_DIR / "nutrition.csv").exists() else _DATA_DIR / "nutrition_sample.csv"
)

# grams per unit (rough household conversions; liquids/powders treated as ~water)
UNIT_G = {"g": 1, "kg": 1000, "ml": 1, "l": 1000, "tsp": 5, "tbsp": 15, "cup": 240}
EACH_G = {"egg": 50, "onion": 110, "banana": 118, "lemon": 60, "apple": 180, "garlic": 5}


def load() -> dict[str, dict]:
    with open(DATA, newline="") as f:
        return {r["food"].lower(): {k: float(v) for k, v in r.items() if k != "food"} for r in csv.DictReader(f)}


_TABLE: dict[str, dict] | None = None


def lookup(name: str) -> dict | None:
    global _TABLE
    _TABLE = _TABLE or load()
    name = name.lower()
    if name in _TABLE:
        return _TABLE[name]
    hit = difflib.get_close_matches(name, _TABLE, n=1, cutoff=0.6)
    if hit:
        return _TABLE[hit[0]]
    for food in _TABLE:  # substring fallback: "boneless chicken thighs" -> "chicken thighs"
        if food in name or name in food:
            return _TABLE[food]
    return None


def grams(ing: dict) -> float:
    u = ing["unit"].lower()
    if u in UNIT_G:
        return ing["quantity"] * UNIT_G[u]
    for k, g in EACH_G.items():
        if k in ing["name"].lower():
            return ing["quantity"] * g
    return ing["quantity"] * 100


def recipe_nutrition(recipe: dict) -> dict:
    """Per-serving kcal/protein/carbs/fat plus the ingredients we could not match."""
    total = {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
    missing = []
    for ing in recipe["ingredients"]:
        row = lookup(ing["name"])
        if not row:
            missing.append(ing["name"])
            continue
        factor = grams(ing) / 100
        for k in total:
            total[k] += row[k] * factor
    s = max(recipe["servings"], 1)
    return {**{k: round(v / s, 1) for k, v in total.items()}, "unmatched_ingredients": missing}
