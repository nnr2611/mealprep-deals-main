# MealPrep Deals

Add recipe books -> Claude extracts the recipes (kept in your browser) -> live Canadian flyer deals for your postal
code -> Claude (`claude-sonnet-5-5`) builds a weekly batch-cook plan. Cost, calories and the shopping list are computed
in code from the recipes and deals, never taken from the LLM.

## Run locally (Windows PowerShell)

    python -m venv .venv
    .\.venv\Scripts\pip install -r requirements-dev.txt
    .\.venv\Scripts\python -m uvicorn app.main:app --reload     # http://localhost:8000
    .\.venv\Scripts\python -m pytest

Click **Add API key** in the page and paste your Anthropic key (stored only in your browser), or put
`ANTHROPIC_API_KEY=...` in a gitignored `.env` file. Try it with `data/sample_recipes.md`.

## Deals

- `flipp` (default locally): live flyers via Flipp's **unofficial** endpoint (`app/deals.py`). Likely against its terms
  of service, so personal use only; get a data licence before running it in a public service.
- `sample` (default on Vercel): bundled `data/sample_deals.json`. The UI labels these as sample prices.
- Only per-weight, per-volume and egg deals are priced; other ingredients show as unpriced and the plan reports the
  percentage of ingredients it could price.
- Nutrition: `data/nutrition_sample.csv`; drop a Health Canada CNF / USDA export at `data/nutrition.csv` (same columns).

## Deploy to Vercel (free Hobby plan)

The backend is stateless (recipes live in the browser, each visitor supplies their own API key), so no database is needed.

    npm i -g vercel
    vercel            # first deploy / preview
    vercel --prod

Limits to know: request bodies are capped around 4.5 MB (uploads are limited to 4 MB) and functions are capped at
`maxDuration` (60 s in `vercel.json`); very large books are only partly read and the UI says so.
