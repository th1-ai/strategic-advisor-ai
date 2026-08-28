"""tools/data.py - loaders and date maths shared by scan_engine.py, toolkit.py
and projects_engine.py.

Strategic Advisor AI has no financial-ledger, reviews or rate-shopper adapter
of its own (see docs/integrations.md): it reads a CSV export dropped in
`data/imports/` if the hotel has one, falling back to the bundled Hotel
Aurora fixture otherwise - the same pattern Portfolio Analyst AI uses. Every
`*_connected()` flag is False until the hotel's own file exists, so a tool
can say "not connected yet" instead of quietly presenting invented numbers
as this property's own. Nothing here calls a model or writes anything.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date, timedelta
from pathlib import Path

from core.config import Settings, repo_root


def fixtures_hotel_dir() -> Path:
    """``<repo>/fixtures/hotel`` - computed fresh so tests can override it via
    ``AGENT_REPO_ROOT`` (see core/config.py:repo_root)."""
    return repo_root() / "fixtures" / "hotel"


def imports_dir() -> Path:
    """``<repo>/data/imports`` - where a hotel drops its own CSV exports."""
    return repo_root() / "data" / "imports"


# --------------------------------------------------------------------------
# date maths
# --------------------------------------------------------------------------
def today_iso(override: str | None = None) -> str:
    """Today's date as ``YYYY-MM-DD``. ``override`` wins (tests, --as-of)."""
    return override or date.today().isoformat()


def add_days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def days_between(start: str, end_inclusive: str) -> list[str]:
    s, e = date.fromisoformat(start), date.fromisoformat(end_inclusive)
    return [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]


def month_key(day: str) -> str:
    """``YYYY-MM`` for ``day`` - the key the demo's ``pace()`` groups on."""
    return day[:7]


def shift_month(month: str, months: int) -> str:
    """``month`` (``YYYY-MM``) shifted by ``months`` (may be negative)."""
    y, m = (int(p) for p in month.split("-"))
    total = y * 12 + (m - 1) + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def last_n_days(today: str, n: int) -> list[str]:
    """The ``n`` days ending the day before ``today`` (a full trailing window,
    not including today - matches the source engine's "last 7 days")."""
    return days_between(add_days(today, -n), add_days(today, -1))


# --------------------------------------------------------------------------
# financial ledger
# --------------------------------------------------------------------------
_FIN_FIELDS = ("date", "revenue_rooms", "revenue_fnb", "revenue_spa",
               "occupancy_pct", "rooms_available")


def _fin_row(raw: dict) -> dict:
    out = {"date": str(raw.get("date", ""))}
    for key in _FIN_FIELDS[1:]:
        try:
            out[key] = float(raw.get(key, 0) or 0)
        except (TypeError, ValueError):
            out[key] = 0.0
    out["revenue_total"] = out["revenue_rooms"] + out["revenue_fnb"] + out["revenue_spa"]
    out["revpar"] = (out["revenue_rooms"] / out["rooms_available"]
                     if out["rooms_available"] else 0.0)
    return out


def load_financial_daily(settings: Settings | None = None) -> list[dict]:
    """Every day of the ledger, oldest first, with ``revenue_total`` and
    ``revpar`` computed. Real use: ``data/imports/financial_daily.csv`` with
    columns ``date, revenue_rooms, revenue_fnb, revenue_spa, occupancy_pct,
    rooms_available`` (extra columns ignored). Demo/tests:
    ``fixtures/hotel/financial_daily.json``."""
    csv_path = imports_dir() / "financial_daily.csv"
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = [_fin_row(r) for r in csv.DictReader(fh)]
    else:
        path = fixtures_hotel_dir() / "financial_daily.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        rows = [_fin_row(r) for r in raw]
    rows.sort(key=lambda r: r["date"])
    return rows


def financial_connected() -> bool:
    return (imports_dir() / "financial_daily.csv").exists()


def financial_window(rows: list[dict], start: str, end_inclusive: str) -> list[dict]:
    return [r for r in rows if start <= r["date"] <= end_inclusive]


def mean(rows: list[dict], field_name: str) -> float:
    if not rows:
        return 0.0
    return round(sum(r.get(field_name, 0.0) for r in rows) / len(rows), 4)


def pct_change(current: float, previous: float) -> float | None:
    """``None`` when ``previous`` is zero - callers decide how to render that."""
    if not previous:
        return None
    return (current - previous) / previous * 100.0


# --------------------------------------------------------------------------
# reviews - rating, date, category (fnb/rooms/spa)
# --------------------------------------------------------------------------
def load_reviews(settings: Settings | None = None) -> list[dict]:
    """``id, review_date, rating, category, source, text``. Real use:
    ``data/imports/reviews.csv``. Demo/tests: ``fixtures/hotel/reviews.json``."""
    csv_path = imports_dir() / "reviews.csv"
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    else:
        path = fixtures_hotel_dir() / "reviews.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    for r in rows:
        try:
            r["rating"] = float(r.get("rating", 0) or 0)
        except (TypeError, ValueError):
            r["rating"] = 0.0
    rows.sort(key=lambda r: r.get("review_date", ""))
    return rows


def reviews_connected() -> bool:
    return (imports_dir() / "reviews.csv").exists()


def reviews_window(rows: list[dict], start: str, end_inclusive: str) -> list[dict]:
    return [r for r in rows if start <= r.get("review_date", "") <= end_inclusive]


# --------------------------------------------------------------------------
# competitor menu / rate shopper snapshots
# --------------------------------------------------------------------------
def load_competitor_snapshots(settings: Settings | None = None) -> list[dict]:
    """``competitor, scraped_on, category, item, price``. Real use:
    ``data/imports/competitor_snapshots.csv``. Demo/tests:
    ``fixtures/hotel/competitor_snapshots.json``."""
    csv_path = imports_dir() / "competitor_snapshots.csv"
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    else:
        path = fixtures_hotel_dir() / "competitor_snapshots.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    for r in rows:
        try:
            r["price"] = float(r.get("price", 0) or 0)
        except (TypeError, ValueError):
            r["price"] = 0.0
    return rows


def competitors_connected() -> bool:
    return (imports_dir() / "competitor_snapshots.csv").exists()


# --------------------------------------------------------------------------
# POS - current item prices, and nightly closes for the `measure` step
# --------------------------------------------------------------------------
def load_pos_items(settings: Settings | None = None) -> list[dict]:
    """``item_id, item, venue, price``. Real use:
    ``data/imports/pos_items.csv``. Demo/tests: ``fixtures/hotel/pos_items.json``."""
    csv_path = imports_dir() / "pos_items.csv"
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    else:
        path = fixtures_hotel_dir() / "pos_items.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    for r in rows:
        try:
            r["price"] = float(r.get("price", 0) or 0)
        except (TypeError, ValueError):
            r["price"] = 0.0
    return rows


def pos_items_connected() -> bool:
    return (imports_dir() / "pos_items.csv").exists()


def load_pos_sales_daily(settings: Settings | None = None) -> list[dict]:
    """Nightly closes for the ``measure`` step: ``date, item_id, units,
    revenue, covers``. Never fabricated at runtime - see docs/how-it-works.md
    "Design decisions" 6. Real use: ``data/imports/pos_sales_daily.csv``.
    Demo/tests: ``fixtures/hotel/pos_sales_daily.json``."""
    csv_path = imports_dir() / "pos_sales_daily.csv"
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    else:
        path = fixtures_hotel_dir() / "pos_sales_daily.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    for r in rows:
        for key in ("units", "revenue", "covers"):
            try:
                r[key] = float(r.get(key, 0) or 0)
            except (TypeError, ValueError):
                r[key] = 0.0
    rows.sort(key=lambda r: r.get("date", ""))
    return rows


def pos_sales_connected() -> bool:
    return (imports_dir() / "pos_sales_daily.csv").exists()


# --------------------------------------------------------------------------
# knowledge passages - for tools/toolkit.py:search_knowledge_base
# --------------------------------------------------------------------------
def knowledge_documents() -> list[dict]:
    """Every knowledge document available right now. ``fixtures/hotel/*.md``
    always contributes so `make demo` and the tests have real content; once a
    hotel fills in ``knowledge/*.md`` the real file wins over the fixture of
    the same name."""
    by_name: dict[str, Path] = {}
    if fixtures_hotel_dir().is_dir():
        for path in sorted(fixtures_hotel_dir().glob("*.md")):
            by_name[path.name] = path
    kdir = repo_root() / "knowledge"
    if kdir.is_dir():
        real = [p for p in sorted(kdir.glob("*.md"))
                if p.name != "README.md" and ".example." not in p.name]
        examples = list(sorted(kdir.glob("*.example.md")))
        for path in real or examples:
            name = path.name.replace(".example.md", ".md")
            by_name[name] = path
    docs = []
    for name, path in sorted(by_name.items()):
        text = path.read_text(encoding="utf-8")
        title = _first_heading(text) or name
        docs.append({"doc_key": name.removesuffix(".md"), "title": title,
                    "category": name.removesuffix(".md"), "path": str(path), "text": text})
    return docs


def _first_heading(text: str) -> str:
    m = re.match(r"^#\s+(.+)$", text.strip().splitlines()[0]) if text.strip() else None
    return m.group(1).strip() if m else ""


def knowledge_passages() -> list[dict]:
    """Every document split into ``## Section`` passages."""
    out = []
    for doc in knowledge_documents():
        sections = re.split(r"^##\s+", doc["text"], flags=re.M)
        intro = sections[0].split("\n", 1)
        if len(intro) > 1 and intro[1].strip():
            out.append({"doc_key": doc["doc_key"], "title": doc["title"],
                       "category": doc["category"], "section": "Overview",
                       "passage": intro[1].strip()})
        for chunk in sections[1:]:
            lines = chunk.split("\n", 1)
            heading = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else ""
            if body:
                out.append({"doc_key": doc["doc_key"], "title": doc["title"],
                           "category": doc["category"], "section": heading,
                           "passage": body})
    return out
