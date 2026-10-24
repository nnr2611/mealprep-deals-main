import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.chdir(os.path.dirname(os.path.dirname(__file__)))
import pytest
from app import deals, nutrition, planner, ingest

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

def test_chunk():
    assert len(ingest.chunk("a\n\n" * 2000)) > 1
