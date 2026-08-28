"""tools/scan.py - run the daily scan, open a project if something shifted,
narrate the result. Called by `tools/run.py --scan` and `tools/demo.py`.

Resumable stage order matters here (docs/how-it-works.md, and the
resumable-stages rule in build-repo.md): the `draft_project` LLM call - the
only stage of a scan that can pend under `llm.provider: interactive` - runs
BEFORE anything is written to `advisor_signals`. If it pends, nothing has
been saved yet, so a retry recomputes the same deterministic scan (same
verdict, same scenario) and finds the same pending prompt instead of a
brand-new one - see `core.llm._pending_id`, which hashes the prompt text
itself when no fixture id is given. Only once a needed project has actually
been opened does this module reserve the day's `advisor_signals` row (its
`UNIQUE(scan_date)` is the idempotency marker), and `narrative` starts
`NULL` and is filled by a second, separately-retryable step - so a pend
during narration never risks a duplicate project either.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.config import Settings, repo_root
from core.llm import complete
from core.log import Run, get_logger
from core.store import Store
from core.templates import build_prompt

from tools import data as sa_data
from tools import project as sa_project
from tools import scan_engine

log = get_logger("scan")

#: every CSV-fallback source the daily scan reads, and whether the hotel has
#: connected its own file yet (SIMULATION.md Finding 1: each one used to be
#: silent except financial_daily.csv - now every source discloses itself,
#: `make doctor` uses these same booleans, and `not all(...)` here is what
#: tags the resulting project `_sample` in `tools/project.py:open_project`).
_SOURCE_LABELS = {"financial_daily.csv": "financial ledger", "reviews.csv": "reviews",
                  "competitor_snapshots.csv": "competitor snapshots",
                  "pos_items.csv": "your menu / POS items"}
#: financial and reviews still feed the RevPAR/occupancy/dept-pace/sentiment
#: checks on the bundled Hotel Aurora numbers when not connected (unchanged
#: behaviour, now disclosed for every source, not just financial); competitor
#: snapshots and POS items are the two sources behind the one concrete bug
#: SIMULATION.md found (an undisclosed "Reprice Aurora Burger" project), so
#: those two are SKIPPED instead - the competitor-watch check runs on an
#: empty comparison and can never propose a real-looking action for an item
#: that does not exist at this property.
_SKIP_SOURCES = frozenset({"competitor_snapshots.csv", "pos_items.csv"})


def _connected_sources() -> dict[str, bool]:
    return {"financial_daily.csv": sa_data.financial_connected(),
           "reviews.csv": sa_data.reviews_connected(),
           "competitor_snapshots.csv": sa_data.competitors_connected(),
           "pos_items.csv": sa_data.pos_items_connected()}


def _print_not_connected_notes(connected: dict[str, bool]) -> None:
    for csv_name, ok in connected.items():
        if ok:
            continue
        label = _SOURCE_LABELS[csv_name]
        if csv_name in _SKIP_SOURCES:
            print(f"{label} not connected (data/imports/{csv_name} is missing) - skipping "
                 f"the competitor-watch opportunity check on real data rather than comparing "
                 f"against the bundled Hotel Aurora sample. See docs/integrations.md.")
        else:
            print(f"{label} not connected (data/imports/{csv_name} is missing) - scanning "
                 f"the bundled Hotel Aurora sample data instead for this check. See "
                 f"docs/integrations.md before you trust it.")


def _projects_for_scan(store: Store) -> list[dict]:
    return sa_project.list_projects(store, status=None)


def _scenario_for_anomaly(anomaly: dict, result, hotel_currency: str) -> dict:
    return {"mode": "fix", "metric_key": anomaly["metric_key"], "label": anomaly["label"],
           "fact": anomaly["fact"], "headline": result.headline,
           "checklist": [c.as_dict() for c in result.checklist], "currency": hotel_currency}


def _scenario_for_opportunity(opportunity: dict, result, hotel_currency: str) -> dict:
    return {"mode": "growth", "metric_key": opportunity["metric_key"], "item": opportunity["item"],
           "category": opportunity["category"], "moves": opportunity["moves"],
           "headline": result.headline, "checklist": [c.as_dict() for c in result.checklist],
           "currency": hotel_currency}


def _pos_update_payload(opportunity: dict, pos_items: list[dict]) -> dict:
    """A conservative, deterministic reprice proposal: close HALF the gap to
    the strongest competitor move on this specific item (``best_delta``),
    not the whole thing and not the whole menu's average - the LLM never
    chooses this number, and the human still approves the exact figure."""
    match = next((p for p in pos_items if p.get("item") == opportunity["item"]), None)
    from_price = match["price"] if match else 0.0
    proposed = round(from_price * (1 + opportunity["best_delta"] / 100.0 / 2.0), 2)
    return {"item_id": match.get("item_id") if match else None, "item": opportunity["item"],
           "venue": match.get("venue") if match else None, "from": from_price, "to": proposed,
           "note": f"The strongest competitor move on this item was "
                   f"{opportunity['best_delta']:+.1f}%; proposing half that move."}


def _open_project(store: Store, settings: Settings, result, *, provider: str | None,
                  templates: dict, pos_items: list[dict], is_sample: bool = False) -> dict | None:
    if result.verdict == "anomaly":
        worst = min(result.anomalies, key=lambda a: a["delta"])
        scenario = _scenario_for_anomaly(worst, result, settings.hotel.currency)
        prompt = build_prompt("draft_project", settings=settings, item=scenario,
                              persona_name=settings.agent_get("persona.name", "the Strategist"))
        drafted = complete("draft_project", prompt, _DRAFT_SCHEMA, settings=settings,
                           provider=provider, store=store,
                           fixture_id=f"draft-{scenario['metric_key']}").data or {}
        payloads = {"analysis": drafted.get("analysis", {}),
                   "email": {"to_role": drafted.get("email", {}).get("to_role", "General Manager"),
                             "to_email": settings.contacts.manager.get("email", ""),
                             "subject": drafted.get("email", {}).get("subject", scenario["label"]),
                             "body": drafted.get("email", {}).get("body", "")},
                   "marketing_action": {"brief": (drafted.get("marketing_action") or {}).get(
                       "brief", "")},
                   "tracker": {"since": sa_data.today_iso(), "min_rating": 4,
                              "target": int(settings.agent_get("tracker.target_positive_reviews", 20)),
                              "category": _category_for_metric(scenario["metric_key"])}}
        return sa_project.open_project(
            store, mode="fix", title=f"Recover {worst['label']}", metric_key=worst["metric_key"],
            target_label=f"Bring {worst['label'].lower()} back on pace",
            projected_impact="to be measured", summary=worst["fact"],
            templates=templates, payloads=payloads, is_sample=is_sample)

    if result.verdict == "opportunity" and result.opportunity:
        opp = result.opportunity
        scenario = _scenario_for_opportunity(opp, result, settings.hotel.currency)
        prompt = build_prompt("draft_project", settings=settings, item=scenario,
                              persona_name=settings.agent_get("persona.name", "the Strategist"))
        drafted = complete("draft_project", prompt, _DRAFT_SCHEMA, settings=settings,
                           provider=provider, store=store,
                           fixture_id=f"draft-{scenario['metric_key']}").data or {}
        pos_payload = _pos_update_payload(opp, pos_items)
        payloads = {"analysis": drafted.get("analysis", {}),
                   "email": {"to_role": drafted.get("email", {}).get("to_role", "F&B Director"),
                             "to_email": settings.contacts.manager.get("email", ""),
                             "subject": drafted.get("email", {}).get("subject", opp["item"]),
                             "body": drafted.get("email", {}).get("body", "")},
                   "pos_update": pos_payload}
        return sa_project.open_project(
            store, mode="growth", title=f"Reprice {opp['item']}", metric_key=opp["metric_key"],
            target_label=f"Close the gap on {opp['item']}",
            projected_impact="to be measured", summary=scenario["headline"],
            templates=templates, payloads=payloads, is_sample=is_sample)
    return None


def _category_for_metric(metric_key: str) -> str | None:
    return {"revenue_fnb": "fnb", "revenue_rooms": "rooms", "revenue_spa": "spa"}.get(metric_key)


def _load_draft_schema() -> dict:
    path = repo_root() / "prompts" / "schemas" / "draft_project.json"
    return json.loads(path.read_text(encoding="utf-8"))


_DRAFT_SCHEMA = _load_draft_schema()


def _get_signal(store: Store, scan_date: str) -> dict | None:
    row = store.db.execute("SELECT * FROM advisor_signals WHERE scan_date=?", (scan_date,)).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "scan_date": row["scan_date"], "verdict": row["verdict"],
           "headline": row["headline"], "checklist": json.loads(row["checklist_json"] or "[]"),
           "narrative": row["narrative"]}


def _insert_signal(store: Store, scan_date: str, result) -> None:
    import uuid
    store.db.execute(
        "INSERT INTO advisor_signals (id, scan_date, verdict, headline, checklist_json, "
        "narrative, created_at) VALUES (?,?,?,?,?,?,?)",
        (uuid.uuid4().hex[:12], scan_date, result.verdict, result.headline,
         json.dumps([c.as_dict() for c in result.checklist]), None,
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    store.record_event(None, "agent", "scan_run", {"scan_date": scan_date, "verdict": result.verdict})


def _narrate(store: Store, settings: Settings, signal: dict, *, provider: str | None) -> str | None:
    prompt = build_prompt("narrate", settings=settings,
                          item={"verdict": signal["verdict"], "headline": signal["headline"],
                                "checklist": signal["checklist"]},
                          persona_name=settings.agent_get("persona.name", "the Strategist"))
    result = complete("narrate", prompt, _NARRATE_SCHEMA, settings=settings, provider=provider,
                      store=store, fixture_id=f"narrate-{signal['scan_date']}")
    narrative = (result.data or {}).get("narrative")
    store.db.execute("UPDATE advisor_signals SET narrative=? WHERE scan_date=?",
                     (narrative, signal["scan_date"]))
    return narrative


def _load_narrate_schema() -> dict:
    path = repo_root() / "prompts" / "schemas" / "narrate.json"
    return json.loads(path.read_text(encoding="utf-8"))


_NARRATE_SCHEMA = _load_narrate_schema()


def run_daily_scan(settings: Settings, store: Store, *, provider: str | None = None,
                   as_of: str | None = None) -> tuple[int, dict]:
    """The whole daily pulse: scan -> (maybe) open a project -> persist ->
    narrate. See the module docstring for why the write order matters."""
    today = sa_data.today_iso(as_of)
    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0}
    run_store = None if settings.dry_run else store
    with Run("scan", settings, run_store) as run:
        signal = _get_signal(store, today)
        if signal is None:
            connected = _connected_sources()
            if not settings.demo:
                # `make demo` (settings.demo=True) always runs on the bundled
                # fixtures on purpose and already says so out loud
                # ("pretending today is ...") - these notes are for a real
                # run, so they stay quiet here instead of cluttering every
                # `make demo` with "connect your data" advice.
                _print_not_connected_notes(connected)
            financial_rows = sa_data.load_financial_daily(settings)
            reviews = sa_data.load_reviews(settings)
            # Competitor watch is SKIPPED, not silently fixture-fed, when
            # either of its two sources is unconnected - see the module-level
            # comment on _SKIP_SOURCES. An empty list here can never surface
            # a fixture item (e.g. "Aurora Burger") as a real opportunity.
            competitor_snapshots = (sa_data.load_competitor_snapshots(settings)
                                    if connected["competitor_snapshots.csv"] else [])
            pos_items = sa_data.load_pos_items(settings) if connected["pos_items.csv"] else []
            projects = _projects_for_scan(store)
            rules = settings.agent_get("rules", {}) or {}
            thresholds = settings.agent_get("thresholds", {}) or {}
            templates = settings.agent_get("project_templates", {}) or {}
            result = scan_engine.run_scan(
                financial_rows=financial_rows, reviews=reviews,
                competitor_snapshots=competitor_snapshots, pos_items=pos_items, projects=projects,
                rules=rules, thresholds=thresholds, currency=settings.hotel.currency, today=today)
            log.info("scan computed", verdict=result.verdict, today=today)
            # Any source still unconnected -> whatever project this scan opens
            # is tagged `_sample` (mirrored into its gated steps too) and
            # printed [SAMPLE] by `tools/project.py`/`tools/review.py` - see
            # docs/how-it-works.md "Design decisions".
            is_sample = any(not ok for ok in connected.values())
            opened = None
            if not settings.dry_run:
                opened = _open_project(store, settings, result, provider=provider,
                                       templates=templates, pos_items=pos_items,
                                       is_sample=is_sample)
                _insert_signal(store, today, result)
                signal = _get_signal(store, today)
            else:
                signal = {"scan_date": today, "verdict": result.verdict, "headline": result.headline,
                          "checklist": [c.as_dict() for c in result.checklist], "narrative": None}
        if signal.get("narrative") is None and not settings.dry_run:
            _narrate(store, settings, signal, provider=provider)
            signal = _get_signal(store, today)
        # Computed from the FINAL resolved signal, not only the branch that
        # happened to do the writing this call - an `interactive` run that
        # opened a project on one invocation and only got as far as the
        # narrate pend that same call reports nothing on that call (it exits
        # via LLMPendingInteractive before reaching here); the next call
        # resumes narrate only and must still report the project truthfully.
        stats["processed"] = 1
        stats["drafted"] = 1 if signal["verdict"] in ("anomaly", "opportunity") else 0
        stats["needs_human"] = stats["drafted"]
        run.stats = dict(stats)
        print(f"scan {today}: verdict={signal['verdict']}")
        print(signal["headline"])
        if signal.get("narrative"):
            print(f"\n{signal['narrative']}")
    return 0, stats
