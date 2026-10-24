"""Builds the weekly plan: deterministic cost/nutrition math in code, meal selection and prep by Claude."""
import difflib
import json
import os

import anthropic

from . import db, nutrition
from .deals import Deal, best_by_item, store_ranking
from .models import MealPlan

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
PACKAGE_G = {"eggs": 600}  # "each" deals: assumed package weight in grams (default 500)

SYSTEM = """You are a meal-prep planner for a Canadian household. Build a one-week plan that is cheap, healthy and \
practical to batch cook.

Rules:
- Choose only from the candidate recipes provided, by recipe_id. Recipes may repeat (batch cooking) - repeats are good.
- Stay near the daily calorie target and hit the protein target; keep meals varied enough to eat.
- Prefer recipes whose ingredients are on sale ("deal_cost_per_serving" is already computed for you).
- In shopping_list, tag each item with the store that has the best deal, e.g. "chicken thighs 1.5 kg - No Frills".
- Minimise the number of stores. Do not invent prices, calories or recipes; the numbers given are authoritative.
- prep_schedule: ordered steps for a single 2-3 hour batch-cook session, with storage notes."""


def _match_deal(ingredient: str, best: dict[str, Deal]) -> Deal | None:
    names = list(best)
    hit = difflib.get_close_matches(ingredient.lower(), names, n=1, cutoff=0.7)
    if hit:
        return best[hit[0]]
    for n in names:
        if n in ingredient.lower() or ingredient.lower() in n:
            return best[n]
    return None


def ingredient_cost(ing: dict, deal: Deal) -> float:
    g = nutrition.grams(ing)
    if deal.unit == "kg":
        return deal.price * g / 1000
    if deal.unit == "lb":
        return deal.price * g / 453.6
    if deal.unit.lower() == "l":
        return deal.price * g / 1000
    return deal.price * g / PACKAGE_G.get(deal.item, 500)


def recipe_deal_cost(recipe: dict, best: dict[str, Deal]) -> dict:
    on_sale = total = 0.0
    sale_items = []
    for ing in recipe["ingredients"]:
        deal = _match_deal(ing["name"], best)
        if deal:
            c = ingredient_cost(ing, deal)
            on_sale += c
            total += c
            sale_items.append(f"{ing['name']} @ {deal.store}")
    s = max(recipe["servings"], 1)
    return {"deal_cost_per_serving": round(total / s, 2), "on_sale_ingredients": sale_items}


def build_candidates(deals: list[Deal]) -> list[dict]:
    best = best_by_item(deals)
    out = []
    for r in db.all_recipes():
        out.append({
            "recipe_id": r["id"], "name": r["name"], "servings": r["servings"],
            "ingredients": [f"{i['quantity']:g} {i['unit']} {i['name']}" for i in r["ingredients"]],
            **nutrition.recipe_nutrition(r), **recipe_deal_cost(r, best),
        })
    return out


def make_plan(postal_code: str, deals: list[Deal], calories_per_day: int, protein_g_per_day: int,
              people: int, notes: str, client: anthropic.Anthropic) -> dict:
    candidates = build_candidates(deals)
    if not candidates:
        raise ValueError("No recipes yet - upload a recipe book first.")
    prompt = json.dumps({
        "postal_code": postal_code,
        "targets": {"calories_per_person_per_day": calories_per_day, "protein_g_per_person_per_day": protein_g_per_day,
                    "people": people},
        "user_notes": notes,
        "store_ranking": store_ranking(deals),
        "best_deals": [d.to_dict() for d in best_by_item(deals).values()],
        "candidate_recipes": candidates,
    }, indent=1)
    resp = client.messages.parse(
        model=MODEL, max_tokens=16000, output_config={"effort": "medium"},
        system=SYSTEM, messages=[{"role": "user", "content": prompt}], output_format=MealPlan,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("The model could not produce a plan; try again.")
    plan = resp.parsed_output.model_dump()
    return verify(plan, candidates, calories_per_day, people)


def verify(plan: dict, candidates: list[dict], calories_per_day: int, people: int) -> dict:
    """Recompute totals ourselves so the numbers shown never come from the LLM."""
    by_id = {c["recipe_id"]: c for c in candidates}
    days: dict[str, float] = {}
    cost = 0.0
    valid = []
    for m in plan["meals"]:
        c = by_id.get(m["recipe_id"])
        if not c:
            continue  # drop hallucinated ids
        m["name"] = c["name"]
        valid.append(m)
        days[m["day"]] = days.get(m["day"], 0) + c["kcal"] * m["servings"]
        cost += c["deal_cost_per_serving"] * m["servings"]
    plan["meals"] = valid
    plan["totals"] = {
        "estimated_cost_cad": round(cost, 2),
        "avg_kcal_per_person_per_day": round(sum(days.values()) / max(len(days), 1) / people),
        "target_kcal": calories_per_day,
    }
    return plan
