# MealPrep Deals

Upload recipe books -> index them (FTS5 retrieval + Claude recipe extraction) -> pull Canadian flyer deals by postal
code -> Claude (`claude-sonnet-5-5`) builds a weekly meal-prep plan from your recipes, the best deals and nutrition data.
Cost and calorie totals are recomputed in code, never taken from the LLM.

    python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
    export ANTHROPIC_API_KEY=...
    uvicorn app.main:app --reload      # open http://localhost:8000
    pytest

- Deals: `DEALS_PROVIDER=sample` (default) uses `data/sample_deals.json`. `flipp` is an unofficial adapter; check
  terms of service / get a licence before real use (`app/deals.py`).
- Nutrition: `data/nutrition_sample.csv`; drop a Health Canada CNF / USDA export at `data/nutrition.csv` (same columns).
- Try it: upload `data/sample_recipes.md`.
