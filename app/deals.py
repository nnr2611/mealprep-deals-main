"""Grocery deal providers. Everything downstream uses the Deal dataclass, so the source is swappable.

IMPORTANT: Canada has no official public flyer API. The 'flipp' adapter talks to an unofficial,
undocumented endpoint and is likely against its terms of service; use it for personal/local runs only and get a
data licence before running it in a public service. The hosted (Vercel) build defaults to bundled sample data."""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import httpx

ROOT = Path(__file__).resolve().parent.parent
LB_PER_KG = 2.20462


@dataclass
class Deal:
    store: str
    item: str          # generic item name, e.g. "chicken thighs"
    price: float       # sale price in CAD per `unit`
    unit: str          # "kg", "L" or "each" (weights/volumes are normalised to kg/L)
    regular_price: float | None = None
    valid_until: str | None = None
    product: str | None = None   # product name as printed in the flyer

    def to_dict(self):
        return asdict(self)


class DealsUnavailable(RuntimeError):
    """The deal source could not be reached."""


class DealProvider(Protocol):
    name: str

    def deals_for(self, postal_code: str, queries: list[str] | None = None) -> list[Deal]: ...


def normalize_postal(code: str) -> str:
    code = "".join(code.split()).upper()
    if len(code) != 6 or not (code[0].isalpha() and code[1].isdigit() and code[2].isalpha()
                              and code[3].isdigit() and code[4].isalpha() and code[5].isdigit()):
        raise ValueError("Enter a valid Canadian postal code like M5V 2T6")
    return code


class SampleProvider:
    """Bundled deals; ignores postal code beyond validating it. Good for development and demos."""
    name = "sample"

    def deals_for(self, postal_code: str, queries: list[str] | None = None) -> list[Deal]:
        normalize_postal(postal_code)
        with open(ROOT / "data" / "sample_deals.json") as f:
            return [Deal(**d) for d in json.load(f)]


# ---- Flipp flyer parsing -------------------------------------------------------------------------------------------

_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", re.I)
_PER_100G = re.compile(r"(?:per|/)\s*100\s*g", re.I)
_PCT = re.compile(r"(\d{1,2})\s*%")
# words that mean the flyer item is a processed/other product, unless the recipe ingredient itself says so
_NOISE = {"sauce", "soup", "seasoning", "flavour", "flavoured", "flavored", "chips", "cat", "dog", "pet", "candle",
          "dressing", "cookie", "cookies", "bar", "bars", "juice", "snack", "cereal", "candy", "marinade", "spread",
          "cake", "cakes", "free", "cracker", "crackers", "puffs", "noodle", "noodles", "pudding", "pie", "pizza",
          "dip", "kit", "mix", "bun", "buns", "bread", "ice", "cream", "drink", "milkshake"}


def normalize_price(price: float, post_text: str | None, name: str) -> tuple[float, str]:
    """Flyer prices come as '3.49 LB', '/lb 15.41/kg', '2 L' etc. Normalise to CAD per kg / L, else per 'each'."""
    post = post_text or ""
    if _PER_100G.search(post):
        return price * 10, "kg"
    if re.search(r"\blb\b", post, re.I):
        return price * LB_PER_KG, "kg"
    if re.search(r"/\s*kg|\bper\s*kg|\bkg\b", post, re.I):
        return price, "kg"
    m = _SIZE.search(f"{name} {post}")
    if m:
        qty, unit = float(m.group(1)), m.group(2).lower()
        if qty > 0:
            if unit in ("g", "kg"):
                return price / (qty / 1000 if unit == "g" else qty), "kg"
            return price / (qty / 1000 if unit == "ml" else qty), "L"
    return price, "each"


def _stem(w: str) -> str:
    if w.endswith("oes") and len(w) > 4:
        return w[:-2]
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


def _words(s: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z]+", s.lower())]


def name_matches(query: str, product: str) -> bool:
    """Every word of the ingredient must appear in the product name; processed products are skipped."""
    q, p = set(_words(query)), set(_words(product))
    return bool(q) and q <= p and not ((p - q) & {_stem(w) for w in _NOISE})


def _regular_price(price: float, original: float | None, story: str | None) -> float | None:
    if original and original > price:
        return original
    m = _PCT.search(story or "")
    if m and 0 < int(m.group(1)) < 80:
        return price / (1 - int(m.group(1)) / 100)
    return None


DEFAULT_STORES = ("loblaws", "no frills", "metro", "sobeys", "walmart", "freshco", "food basics", "superstore",
                  "zehrs", "farm boy", "giant tiger", "independent", "longo", "foodland", "fortinos", "valu-mart",
                  "safeway", "costco", "t&t", "adonis", "highland farms")
_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
CACHE_TTL = 3600


class FlippProvider:
    """Unofficial Flipp search adapter (flyer items only, not Walmart's online catalogue)."""
    name = "flipp"
    URL = "https://backflipp.wishabi.com/flipp/items/search"

    def __init__(self, stores: tuple[str, ...] | None = None):
        env = os.environ.get("DEALS_STORES")
        self.stores = stores or (tuple(s.strip().lower() for s in env.split(",")) if env else DEFAULT_STORES)

    def _search(self, client: httpx.Client, pc: str, q: str) -> list[dict]:
        hit = _CACHE.get((pc, q))
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        r = client.get(self.URL, params={"locale": "en-ca", "postal_code": pc, "q": q})
        r.raise_for_status()
        items = r.json().get("items", [])
        _CACHE[(pc, q)] = (time.time(), items)
        return items

    def parse(self, query: str, items: list[dict]) -> list[Deal]:
        out = []
        for it in items:
            store = it.get("merchant_name") or ""
            price, product = it.get("current_price"), it.get("name") or ""
            if price is None or not any(s in store.lower() for s in self.stores) or not name_matches(query, product):
                continue
            norm, unit = normalize_price(float(price), it.get("post_price_text"), product)
            reg = _regular_price(float(price), it.get("original_price"), it.get("sale_story"))
            out.append(Deal(store=store, item=query, price=round(norm, 2), unit=unit,
                            regular_price=round(reg * norm / float(price), 2) if reg else None,
                            valid_until=(it.get("valid_to") or "")[:10] or None, product=product))
        return out

    def deals_for(self, postal_code: str, queries: list[str] | None = None) -> list[Deal]:
        pc = normalize_postal(postal_code)
        queries = queries or ["chicken", "beef", "salmon", "eggs", "rice", "pasta", "broccoli", "beans"]
        errors: list[Exception] = []

        def run(q: str) -> list[Deal]:
            try:
                return self.parse(q, self._search(client, pc, q))
            except (httpx.HTTPError, ValueError) as e:
                errors.append(e)
                return []

        with httpx.Client(timeout=15, headers={"User-Agent": "Mozilla/5.0 mealprep-deals"}) as client, \
                ThreadPoolExecutor(max_workers=4) as pool:
            deals = [d for ds in pool.map(run, queries) for d in ds]
        if errors and len(errors) == len(queries):
            raise DealsUnavailable("Could not reach the flyer service. Try again in a moment.")
        return deals


def get_provider() -> DealProvider:
    choice = os.environ.get("DEALS_PROVIDER") or ("sample" if os.environ.get("VERCEL") else "flipp")
    return FlippProvider() if choice == "flipp" else SampleProvider()


_UNIT_RANK = {"kg": 0, "l": 1, "each": 2}


def best_by_item(deals: list[Deal]) -> dict[str, Deal]:
    """Cheapest deal per item. Only deals in the most comparable unit (kg, then L, then each) are compared."""
    groups: dict[str, list[Deal]] = {}
    for d in deals:
        groups.setdefault(d.item, []).append(d)
    best = {}
    for item, ds in groups.items():
        unit = min((d.unit.lower() for d in ds), key=lambda u: _UNIT_RANK.get(u, 3))
        best[item] = min((d for d in ds if d.unit.lower() == unit), key=lambda d: d.price)
    return best


def store_ranking(deals: list[Deal]) -> list[dict]:
    """Rank stores by how many items they win and average discount vs regular price."""
    best = best_by_item(deals)
    stats: dict[str, dict] = {}
    for d in best.values():
        s = stats.setdefault(d.store, {"store": d.store, "best_prices": 0, "avg_discount_pct": 0.0, "_n": 0})
        s["best_prices"] += 1
        if d.regular_price:
            s["avg_discount_pct"] += 100 * (d.regular_price - d.price) / d.regular_price
            s["_n"] += 1
    out = []
    for s in stats.values():
        n = s.pop("_n")
        s["avg_discount_pct"] = round(s["avg_discount_pct"] / n, 1) if n else 0.0
        out.append(s)
    return sorted(out, key=lambda s: (-s["best_prices"], -s["avg_discount_pct"]))
