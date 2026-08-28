"""tools/toolkit.py - the eight tools the Strategist can call, and nothing
else.

Deterministic decisioning, LLM for language (ARCHITECTURE.md section 1):
every number below is plain Python over the property's own data or the
agent's own project store. None of these tools writes anything - the
Strategist's "never acts without sign-off" promise is enforced by what
exists here, the same way Portfolio Analyst AI's read-only guarantee is: a
strategic project's actual writes (email, marketing brief, price change) go
through `tools/project.py` and `core.review`'s guard, never through a
question.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from core.config import Settings
from core.store import Store

from tools import data as sa_data
from tools import project as sa_project

WINDOWS = ("today", "week", "month", "year")


class ToolError(ValueError):
    """A tool call had bad arguments or nothing to answer with."""


@dataclass
class ToolContext:
    """Everything a tool needs, loaded once per question."""

    settings: Settings
    store: Store
    today: str
    financial_rows: list[dict] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    competitor_snapshots: list[dict] = field(default_factory=list)
    pos_items: list[dict] = field(default_factory=list)
    passages: list[dict] = field(default_factory=list)
    financial_connected: bool = True
    reviews_connected: bool = True
    competitors_connected: bool = True

    @classmethod
    def build(cls, settings: Settings, store: Store, *, today: str | None = None) -> "ToolContext":
        return cls(settings=settings, store=store, today=sa_data.today_iso(today),
                  financial_rows=sa_data.load_financial_daily(settings),
                  reviews=sa_data.load_reviews(settings),
                  competitor_snapshots=sa_data.load_competitor_snapshots(settings),
                  pos_items=sa_data.load_pos_items(settings),
                  passages=sa_data.knowledge_passages(),
                  financial_connected=sa_data.financial_connected(),
                  reviews_connected=sa_data.reviews_connected(),
                  competitors_connected=sa_data.competitors_connected())


TOOL_SPECS: list[dict[str, Any]] = [
    {"name": "get_daily_pulse",
     "description": "The most recent daily scan: verdict, headline, checklist and narrative. "
                     "Pass a date for a specific day's scan, or omit for the latest.",
     "parameters": {"type": "object", "properties": {"date": {"type": ["string", "null"]}},
                    "additionalProperties": False}},
    {"name": "list_projects",
     "description": "Every strategic project, optionally filtered by status "
                     "(active, watching, resolved, abandoned).",
     "parameters": {"type": "object", "properties": {"status": {"type": ["string", "null"]}},
                    "additionalProperties": False}},
    {"name": "get_project_detail",
     "description": "One project in full: every step, its status, and its payload.",
     "parameters": {"type": "object", "properties": {"project_id": {"type": "string"}},
                    "required": ["project_id"], "additionalProperties": False}},
    {"name": "get_financial_metrics",
     "description": "RevPAR, occupancy and department revenue pace (rooms/F&B/spa) for "
                     "a window: today, week, month or year to date, each vs last year.",
     "parameters": {"type": "object",
                    "properties": {"window": {"type": "string", "enum": list(WINDOWS)}},
                    "required": ["window"], "additionalProperties": False}},
    {"name": "get_review_sentiment",
     "description": "Positive/negative review counts since a date, optionally filtered "
                     "by category (fnb, rooms, spa).",
     "parameters": {"type": "object",
                    "properties": {"since": {"type": "string"},
                                   "category": {"type": ["string", "null"]}},
                    "required": ["since"], "additionalProperties": False}},
    {"name": "get_competitor_watch",
     "description": "Competitor price moves per item since their first scrape, sorted "
                     "by size of move.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "search_knowledge_base",
     "description": "Ranked full-text search over the property knowledge base "
                     "(policies, rules, property facts). Always use this for a property "
                     "fact instead of guessing.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                    "required": ["query"], "additionalProperties": False}},
    {"name": "generate_report",
     "description": "Emit a structured report card: title, markdown body, optional "
                     "KPI tiles and a bar/line chart. Call this whenever asked for a "
                     "report, briefing or chart.",
     "parameters": {"type": "object",
                    "properties": {
                        "title": {"type": "string"}, "subtitle": {"type": ["string", "null"]},
                        "markdown": {"type": "string"},
                        "kpis": {"type": "array", "items": {"type": "object"}},
                        "chart": {"type": ["object", "null"]},
                    },
                    "required": ["title", "markdown"], "additionalProperties": False}},
]
TOOL_NAMES = tuple(spec["name"] for spec in TOOL_SPECS)


def render_tool_list() -> str:
    lines = ["Tools you may call:"]
    for spec in TOOL_SPECS:
        lines.append(f"- `{spec['name']}` - {spec['description']}")
    return "\n".join(lines)


def _not_connected(csv_name: str, what: str) -> dict:
    return {"connected": False,
           "message": f"{what} is not connected yet ({csv_name} was not found in "
                      f"data/imports/) - say so plainly instead of guessing or using "
                      f"sample data. See docs/integrations.md."}


# --------------------------------------------------------------------------
# 1. get_daily_pulse
# --------------------------------------------------------------------------
def get_daily_pulse(ctx: ToolContext, args: dict) -> dict:
    date = args.get("date")
    if date:
        row = ctx.store.db.execute(
            "SELECT * FROM advisor_signals WHERE scan_date=?", (date,)).fetchone()
    else:
        row = ctx.store.db.execute(
            "SELECT * FROM advisor_signals ORDER BY scan_date DESC LIMIT 1").fetchone()
    if row is None:
        return {"found": False, "message": "No scan has been run yet. "
               "`python3 tools/run.py --once --scan` runs one."}
    return {"found": True, "scan_date": row["scan_date"], "verdict": row["verdict"],
           "headline": row["headline"], "checklist": json.loads(row["checklist_json"] or "[]"),
           "narrative": row["narrative"]}


# --------------------------------------------------------------------------
# 2 & 3. list_projects / get_project_detail
# --------------------------------------------------------------------------
def list_projects(ctx: ToolContext, args: dict) -> dict:
    status = args.get("status")
    projects = sa_project.list_projects(ctx.store, status=status)
    rows = [{"id": p["id"], "title": p["title"], "mode": p["mode"], "status": p["status"],
            "target_label": p["target_label"], "measured_impact": p["measured_impact"],
            "created_on": p["created_on"], "resolved_on": p["resolved_on"]} for p in projects]
    return {"count": len(rows), "rows": rows}


def get_project_detail(ctx: ToolContext, args: dict) -> dict:
    project_id = args.get("project_id")
    if not project_id:
        raise ToolError("project_id is required")
    try:
        project = sa_project.get_project(ctx.store, project_id)
    except sa_project.ProjectError as exc:
        raise ToolError(str(exc)) from exc
    return project


# --------------------------------------------------------------------------
# 4. get_financial_metrics
# --------------------------------------------------------------------------
def get_financial_metrics(ctx: ToolContext, args: dict) -> dict:
    if not ctx.financial_connected:
        return _not_connected("data/imports/financial_daily.csv", "The financial ledger")
    window = args.get("window")
    if window not in WINDOWS:
        raise ToolError(f"window must be one of {', '.join(WINDOWS)}, got {window!r}")
    today = ctx.today
    if window == "today":
        start = today
    elif window == "week":
        start = sa_data.add_days(today, -(int(today[8:10]) % 7))
    elif window == "month":
        start = sa_data.month_key(today) + "-01"
    else:
        start = today[:4] + "-01-01"
    rows = sa_data.financial_window(ctx.financial_rows, start, today)
    ly_start, ly_end = sa_data.add_days(start, -365), sa_data.add_days(today, -365)
    ly_rows = sa_data.financial_window(ctx.financial_rows, ly_start, ly_end)

    def total(field_name: str, rs: list[dict]) -> float:
        return round(sum(r.get(field_name, 0.0) for r in rs), 2)

    return {"connected": True, "window": window, "from": start, "to": today,
           "revenue_rooms": total("revenue_rooms", rows), "revenue_fnb": total("revenue_fnb", rows),
           "revenue_spa": total("revenue_spa", rows), "revenue_total": total("revenue_total", rows),
           "revpar_avg": sa_data.mean(rows, "revpar"), "occupancy_avg_pct": sa_data.mean(rows, "occupancy_pct"),
           "vs_last_year_revenue_pct": sa_data.pct_change(total("revenue_total", rows),
                                                           total("revenue_total", ly_rows))}


# --------------------------------------------------------------------------
# 5. get_review_sentiment
# --------------------------------------------------------------------------
def get_review_sentiment(ctx: ToolContext, args: dict) -> dict:
    if not ctx.reviews_connected:
        return _not_connected("data/imports/reviews.csv", "The reviews feed")
    since = args.get("since")
    if not since:
        raise ToolError("since is required (YYYY-MM-DD)")
    category = args.get("category")
    rows = sa_data.reviews_window(ctx.reviews, since, ctx.today)
    if category:
        rows = [r for r in rows if r.get("category") == category]
    pos = [r for r in rows if r.get("rating", 0) >= 4]
    neg = [r for r in rows if r.get("rating", 0) <= 3]
    return {"connected": True, "since": since, "category": category, "positive": len(pos),
           "negative": len(neg), "negative_reviews": neg[:10]}


# --------------------------------------------------------------------------
# 6. get_competitor_watch
# --------------------------------------------------------------------------
def get_competitor_watch(ctx: ToolContext, args: dict) -> dict:
    if not ctx.competitors_connected:
        return _not_connected("data/imports/competitor_snapshots.csv", "The competitor watch")
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for row in ctx.competitor_snapshots:
        by_pair.setdefault((row.get("competitor", ""), row.get("item", "")), []).append(row)
    moves = []
    for (competitor, item), pts in by_pair.items():
        pts = sorted(pts, key=lambda r: r.get("scraped_on", ""))
        first, last = pts[0], pts[-1]
        if first.get("scraped_on") == last.get("scraped_on") or not first.get("price"):
            continue
        delta = (last["price"] - first["price"]) / first["price"] * 100.0
        moves.append({"competitor": competitor, "item": item, "category": first.get("category", ""),
                      "from": first["price"], "to": last["price"], "delta_pct": round(delta, 1),
                      "since": first.get("scraped_on", "")})
    moves.sort(key=lambda m: m["delta_pct"], reverse=True)
    return {"connected": True, "count": len(moves), "moves": moves}


# --------------------------------------------------------------------------
# 7. search_knowledge_base
# --------------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"a", "an", "the", "is", "are", "was", "were", "of", "to", "in",
             "on", "for", "and", "or", "it", "we", "our", "you", "your",
             "do", "does", "not", "no", "at", "by", "with", "what", "how"}


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if len(t) >= 2 and t not in _STOPWORDS}


def _score(passage: dict, query_tokens: set[str]) -> float:
    haystack = _tokens(f"{passage['title']} {passage['section']} {passage['passage']}")
    return len(query_tokens & haystack) / len(query_tokens) if query_tokens else 0.0


def search_knowledge_base(ctx: ToolContext, args: dict) -> dict:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("query must not be empty")
    top_k = int(ctx.settings.agent_get("knowledge_search.top_k", 8))
    query_tokens = _tokens(query)
    ranked = sorted(((p, _score(p, query_tokens)) for p in ctx.passages),
                    key=lambda pair: pair[1], reverse=True)
    hits = [{"doc_key": p["doc_key"], "title": p["title"], "category": p["category"],
            "section": p["section"], "passage": p["passage"], "relevance": round(s, 3)}
           for p, s in ranked if s > 0][:top_k]
    if hits:
        return {"query": query, "fallback": False, "count": len(hits), "results": hits}
    needle = query.lower()
    loose = [{"doc_key": p["doc_key"], "title": p["title"], "category": p["category"],
             "section": p["section"], "passage": p["passage"], "relevance": 0.0}
            for p in ctx.passages if needle in p["title"].lower()][:4]
    return {"query": query, "fallback": True, "count": len(loose), "results": loose}


# --------------------------------------------------------------------------
# 8. generate_report
# --------------------------------------------------------------------------
def generate_report(ctx: ToolContext, args: dict) -> dict:
    title, markdown = args.get("title"), args.get("markdown")
    if not title or not markdown:
        raise ToolError("generate_report needs both 'title' and 'markdown'")
    report = {"title": title, "subtitle": args.get("subtitle") or "", "markdown": markdown,
             "kpis": args.get("kpis") or [], "chart": args.get("chart")}
    chart = report["chart"]
    if chart is not None:
        if chart.get("type") not in ("bar", "line"):
            raise ToolError("chart.type must be 'bar' or 'line'")
        if not chart.get("series"):
            raise ToolError("chart.series must not be empty")
    return report


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
TOOL_FUNCS: dict[str, Callable[[ToolContext, dict], dict]] = {
    "get_daily_pulse": get_daily_pulse, "list_projects": list_projects,
    "get_project_detail": get_project_detail, "get_financial_metrics": get_financial_metrics,
    "get_review_sentiment": get_review_sentiment, "get_competitor_watch": get_competitor_watch,
    "search_knowledge_base": search_knowledge_base, "generate_report": generate_report,
}


def call_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    func = TOOL_FUNCS.get(name)
    if func is None:
        raise ToolError(f"no such tool '{name}'. Known: {', '.join(TOOL_NAMES)}")
    if not isinstance(args, dict):
        raise ToolError(f"{name}: arguments must be a JSON object, got {type(args).__name__}")
    return func(ctx, args)
