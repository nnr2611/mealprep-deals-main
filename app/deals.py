"""Grocery deal providers. Everything downstream uses the Deal dataclass, so the source is swappable.

IMPORTANT: Canada has no official public flyer API. The 'flipp' adapter talks to an unofficial,
undocumented endpoint and is likely against its terms of service; get a data licence (or use the
sample/manual provider) before running it in production."""
import json
import os
from dataclasses import asdict, dataclass
from typing import Protocol

import httpx2 as httpx


@dataclass
class Deal:
    store: str
    item: str          # generic item name, e.g. "chicken thighs"
    price: float       # sale price in CAD per `unit`
    unit: str          # "kg", "lb", "each", "L" ...
    regular_price: float | None = None
    valid_until: str | None = None

    def to_dict(self):
        return asdict(self)


class DealProvider(Protocol):
    def deals_for(self, postal_code: str) -> list[Deal]: ...


def normalize_postal(code: str) -> str:
    code = "".join(code.split()).upper()
    if len(code) != 6 or not (code[0].isalpha() and code[1].isdigit() and code[2].isalpha()
                              and code[3].isdigit() and code[4].isalpha() and code[5].isdigit()):
        raise ValueError("Enter a valid Canadian postal code like M5V 2T6")
    return code


class SampleProvider:
    """Bundled deals; ignores postal code beyond validating it. Good for development and demos."""

    def deals_for(self, postal_code: str) -> list[Deal]:
        normalize_postal(postal_code)
        with open("data/sample_deals.json") as f:
            return [Deal(**d) for d in json.load(f)]


class FlippProvider:
    """Unofficial Flipp search adapter. Verify the endpoint/response shape yourself before relying on it."""
    URL = "https://backflipp.wishabi.com/flipp/items/search"

    def __init__(self, stores: tuple[str, ...] = ("loblaws", "no frills", "metro", "sobeys", "walmart", "freshco", "food basics")):
        self.stores = stores

    def deals_for(self, postal_code: str, queries: tuple[str, ...] = ("chicken", "beef", "salmon", "eggs", "rice", "pasta", "vegetables", "fruit", "yogurt", "beans")) -> list[Deal]:
        pc = normalize_postal(postal_code)
        deals: list[Deal] = []
        with httpx.Client(timeout=15) as c:
            for q in queries:
                r = c.get(self.URL, params={"locale": "en-ca", "postal_code": pc, "q": q})
                r.raise_for_status()
                for it in r.json().get("items", []):
                    merchant = (it.get("merchant_name") or "").lower()
                    if it.get("current_price") is None or not any(s in merchant for s in self.stores):
                        continue
                    deals.append(Deal(store=it["merchant_name"], item=q, price=float(it["current_price"]),
                                      unit="each", valid_until=it.get("valid_to")))
        return deals


def get_provider() -> DealProvider:
    return FlippProvider() if os.environ.get("DEALS_PROVIDER") == "flipp" else SampleProvider()


def best_by_item(deals: list[Deal]) -> dict[str, Deal]:
    """Cheapest deal per item across stores (units assumed comparable within an item)."""
    best: dict[str, Deal] = {}
    for d in deals:
        if d.item not in best or d.price < best[d.item].price:
            best[d.item] = d
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
