"""Builds the weekly plan: deterministic cost/nutrition/shopping math in code, meal selection and prep by Claude."""
import difflib
import json
import os
from collections import Counter

import anthropic

from . import nutrition
from .deals import Deal, best_by_item, store_ranking
from .models import MealPlan

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
PACKAGE_G = {"eggs": 600}  # "each" deals: assumed package weight in grams (default 500)
MAX_CANDIDATES = 60
MAX_QUERIES = 30
PANTRY = {"salt", "pepper", "black pepper", "water", "sugar", "flour", "baking powder", "baking soda", "oil",
          "olive oil", "vegetable oil", "avocado oil", "cooking spray", "garlic powder", "onion powder", "paprika",
          "cumin", "oregano", "italian seasoning", "cinnamon", "vanilla", "vinegar", "soy sauce", "salt and pepper"}

SYSTEM = """You are a meal-prep planner for a Canadian household. Build a one-week plan that is cheap, healthy and \
practical to batch cook.

Rules:
- Choose only from the candidate recipes provided, by recipe_id. Recipes may repeat (batch cooking) - repeats are good.
- "servings" on a planned meal is the TOTAL servings the household eats at that meal (people x portions).
- Stay near the daily calorie target and hit the protein target; keep meals varied enough to eat.
- Prefer recipes whose ingredients are on sale ("deal_cost_per_serving" is already computed for you).
- Minimise the number of stores. Do not invent prices, calories or recipes; the numbers given are authoritative.
- In summary and prep_schedule always refer to recipes by name, never by recipe_id or number.
- prep_schedule: ordered steps for a single 2-3 hour batch-cook session, with storage notes. The shopping list is \
generated separately from your meals, so do not write one."""


def ingredient_queries(recipes: list[dict]) -> list[str]:
    """The ingredients worth looking up in flyers: most frequent first, pantry staples skipped."""
    count = Counter(i["name"].strip().lower() for r in recipes for i in r["ingredients"])
    return [n for n, _ in count.most_common() if n and n not in PANTRY][:MAX_QUERIES]


def _match_deal(ingredient: str, best: dict[str, Deal]) -> Deal | None:
    names = list(best)
    ingredient = ingredient.lower()
    if ingredient in best:
        return best[ingredient]
    hit = difflib.get_close_matches(ingredient, names, n=1, cutoff=0.7)
    if hit:
        return best[hit[0]]
    for n in names:
        if n in ingredient or ingredient in n:
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
    total = 0.0
    sale_items = []
    for ing in recipe["ingredients"]:
        deal = _match_deal(ing["name"], best)
        if deal:
            total += ingredient_cost(ing, deal)
            sale_items.append(f"{ing['name']} @ {deal.store}")
    s = max(recipe["servings"], 1)
    return {"deal_cost_per_serving": round(total / s, 2), "on_sale_ingredients": sale_items,
            "priced_ingredients": len(sale_items), "total_ingredients": len(recipe["ingredients"])}


def build_candidates(deals: list[Deal], recipes: list[dict]) -> list[dict]:
    """recipe_id is the 1-based position in `recipes`."""
    best = best_by_item(deals)
    out = []
    for i, r in enumerate(recipes, 1):
        out.append({
            "recipe_id": i, "name": r["name"], "servings": r["servings"],
            "ingredients": [f"{i['quantity']:g} {i['unit']} {i['name']}" for i in r["ingredients"]],
            **nutrition.recipe_nutrition(r), **recipe_deal_cost(r, best),
        })
    out.sort(key=lambda c: -c["priced_ingredients"])
    return out[:MAX_CANDIDATES]


def shopping_list(meals: list[dict], recipes: list[dict], best: dict[str, Deal]) -> list[dict]:
    """Ingredient totals scaled to the servings actually planned, each tagged with its best flyer deal."""
    need: dict[tuple[str, str], float] = {}
    for m in meals:
        r = recipes[m["recipe_id"] - 1]
        scale = m["servings"] / max(r["servings"], 1)
        for ing in r["ingredients"]:
            key = (ing["name"].strip().lower(), ing["unit"].lower())
            need[key] = need.get(key, 0) + ing["quantity"] * scale
    out = []
    for (name, unit), qty in need.items():
        deal = _match_deal(name, best)
        cost = ingredient_cost({"name": name, "quantity": qty, "unit": unit}, deal) if deal else None
        out.append({"name": name, "quantity": round(qty, 2), "unit": unit,
                    "store": deal.store if deal else None,
                    "deal": f"${deal.price:.2f}/{deal.unit}" if deal else None,
                    "est_cost": round(cost, 2) if cost is not None else None})
    return sorted(out, key=lambda i: (i["store"] is None, i["store"] or "", i["name"]))


def make_plan(deals: list[Deal], recipes: list[dict], calories_per_day: int, protein_g_per_day: int,
              people: int, notes: str, client: anthropic.Anthropic) -> dict:
    candidates = build_candidates(deals, recipes)
    if not candidates:
        raise ValueError("Add at least one recipe first.")
    best = best_by_item(deals)
    prompt = json.dumps({
        "targets": {"calories_per_person_per_day": calories_per_day, "protein_g_per_person_per_day": protein_g_per_day,
                    "people": people},
        "user_notes": notes,
        "store_ranking": store_ranking(deals),
        "best_deals": [d.to_dict() for d in best.values()],
        "candidate_recipes": candidates,
    }, indent=1)
    resp = client.messages.parse(
        model=MODEL, max_tokens=16000, output_config={"effort": "medium"},
        system=SYSTEM, messages=[{"role": "user", "content": prompt}], output_format=MealPlan,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("The model could not produce a plan; try again.")
    plan = verify(resp.parsed_output.model_dump(), candidates, calories_per_day, people)
    plan["shopping_list"] = shopping_list(plan["meals"], recipes, best)
    return plan


def verify(plan: dict, candidates: list[dict], calories_per_day: int, people: int) -> dict:
    """Recompute totals ourselves so the numbers shown never come from the LLM."""
    by_id = {c["recipe_id"]: c for c in candidates}
    days: dict[str, float] = {}
    cost = priced = total = 0.0
    valid = []
    for m in plan["meals"]:
        c = by_id.get(m["recipe_id"])
        if not c:
            continue  # drop hallucinated ids
        m["name"] = c["name"]
        m["kcal"] = round(c["kcal"] * m["servings"])
        m["protein_g"] = round(c["protein_g"] * m["servings"], 1) if "protein_g" in c else None
        valid.append(m)
        days[m["day"]] = days.get(m["day"], 0) + c["kcal"] * m["servings"]
        cost += c["deal_cost_per_serving"] * m["servings"]
        priced += c.get("priced_ingredients", 0) * m["servings"]
        total += c.get("total_ingredients", 0) * m["servings"]
    plan["meals"] = valid
    plan["totals"] = {
        "estimated_cost_cad": round(cost, 2),
        "avg_kcal_per_person_per_day": round(sum(days.values()) / max(len(days), 1) / people),
        "target_kcal": calories_per_day,
        "priced_pct": round(100 * priced / total) if total else None,
    }
    return plan
