import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.chdir(os.path.dirname(os.path.dirname(__file__)))
import pytest
from app import deals, nutrition, planner

def test_postal():
    assert deals.normalize_postal("m5v 2t6") == "M5V2T6"
    with pytest.raises(ValueError):
        deals.normalize_postal("12345")

def test_best_and_rank():
    ds = deals.SampleProvider().deals_for("M5V 2T6")
    assert deals.best_by_item(ds)["chicken thighs"].store == "No Frills"
    assert deals.store_ranking(ds)[0]["best_prices"] >= 1

def test_nutrition():
    r = {"servings": 2, "ingredients": [{"name": "rolled oats", "quantity": 100, "unit": "g"}]}
    n = nutrition.recipe_nutrition(r)
    assert n["kcal"] == pytest.approx(194.5)

def test_cost_and_verify():
    best = deals.best_by_item(deals.SampleProvider().deals_for("M5V2T6"))
    r = {"servings": 1, "ingredients": [{"name": "chicken thighs", "quantity": 500, "unit": "g"}]}
    assert planner.recipe_deal_cost(r, best)["deal_cost_per_serving"] == pytest.approx(3.30, abs=0.01)
    plan = {"meals": [{"day": "Mon", "meal": "lunch", "recipe_id": 1, "servings": 2},
                      {"day": "Mon", "meal": "dinner", "recipe_id": 99, "servings": 1}]}
    cands = [{"recipe_id": 1, "name": "X", "kcal": 500, "deal_cost_per_serving": 2.0}]
    out = planner.verify(plan, cands, 1800, 1)
    assert len(out["meals"]) == 1 and out["totals"]["estimated_cost_cad"] == 4.0

def test_normalize_price():
    assert deals.normalize_price(3.49, "LB", "CHICKEN THIGHS") == (pytest.approx(7.69, abs=0.01), "kg")
    assert deals.normalize_price(6.99, "/lb 15.41/kg", "x")[1] == "kg"
    assert deals.normalize_price(2.0, None, "Spaghetti 500 g") == (pytest.approx(4.0), "kg")
    assert deals.normalize_price(4.0, None, "Milk 2 L") == (pytest.approx(2.0), "L")
    assert deals.normalize_price(3.99, None, "Large eggs")[1] == "each"


def test_name_matches():
    assert deals.name_matches("chicken thighs", "Fresh Ontario Chicken Thighs")
    assert deals.name_matches("tomatoes", "Roma Tomato")
    assert not deals.name_matches("chicken", "Chicken Noodle Soup")
    assert not deals.name_matches("chicken thighs", "Chicken Breast")


def test_flipp_parse_fixture():
    import json
    items = json.load(open("tests/fixtures/flipp_chicken.json"))["items"]
    ds = deals.FlippProvider().parse("chicken thighs", items)
    stores = {d.store for d in ds}
    assert "Real Canadian Superstore" in stores and all(d.unit == "kg" for d in ds)
    cheapest = deals.best_by_item(ds)["chicken thighs"]
    assert cheapest.store == "Real Canadian Superstore" and cheapest.price == pytest.approx(7.69, abs=0.01)
    assert cheapest.regular_price > cheapest.price and cheapest.valid_until == "2026-10-29"


def test_best_by_item_compares_same_unit_only():
    ds = [deals.Deal("A", "rice", 1.0, "each"), deals.Deal("B", "rice", 5.0, "kg"), deals.Deal("C", "rice", 4.0, "kg")]
    assert deals.best_by_item(ds)["rice"].store == "C"


def test_shopping_list_scales_by_servings():
    recipes = [{"name": "R", "servings": 2, "steps": [],
                "ingredients": [{"name": "chicken thighs", "quantity": 400, "unit": "g"}]}]
    best = deals.best_by_item(deals.SampleProvider().deals_for("M5V2T6"))
    out = planner.shopping_list([{"recipe_id": 1, "servings": 6}], recipes, best)
    assert out[0]["quantity"] == 1200 and out[0]["store"] == "No Frills" and out[0]["est_cost"] > 0


def test_ingredient_queries_skip_pantry():
    recipes = [{"ingredients": [{"name": "Salt"}, {"name": "Chicken thighs"}]},
               {"ingredients": [{"name": "chicken thighs"}, {"name": "rice"}]}]
    assert planner.ingredient_queries(recipes) == ["chicken thighs", "rice"]


def test_api_requires_key(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = TestClient(app).post("/api/extract", files={"file": ("a.txt", b"x")})
    assert r.status_code == 401 and "API key" in r.json()["detail"]
