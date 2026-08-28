#!/usr/bin/env python3
"""tools/report.py - what the Strategist found, did, and what it cost.

    make report
    python3 tools/report.py
    python3 tools/report.py --json

Reads data/agent.db - nothing here calls a model or an adapter. Numbers tied
to the roster's own claim (README.md section 2, docs/benefits.md
"Measured impact banked in 90 days"):

``scans``            how many daily scans have run, and their verdict split.
``projects``          strategic projects by mode and status.
``measured impact``   sum of `measured_impact` across RESOLVED projects only
                     - the KPI the roster's ROI figure is built from.
``questions``         ad-hoc questions asked, and the share answered cleanly
                     with no human touch (`skipped`).
``spend``             LLM calls, tokens and cost, from `core.store.usage_totals`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_sheets  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Store, TERMINAL  # noqa: E402

from tools import project as sa_project  # noqa: E402


def scan_stats(store: Store) -> dict:
    rows = store.db.execute("SELECT verdict, COUNT(*) AS n FROM advisor_signals "
                            "GROUP BY verdict").fetchall()
    by_verdict = {r["verdict"]: r["n"] for r in rows}
    total = store.db.execute("SELECT COUNT(*) AS n FROM advisor_signals").fetchone()["n"]
    return {"total": total, "by_verdict": by_verdict}


def project_stats(store: Store) -> dict:
    projects = sa_project.list_projects(store)
    by_status: dict[str, int] = {}
    by_mode: dict[str, int] = {}
    measured_total = 0.0
    for p in projects:
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
        by_mode[p["mode"]] = by_mode.get(p["mode"], 0) + 1
        if p["status"] == "resolved" and p["measured_impact"]:
            measured_total += float(p["measured_impact"])
    return {"total": len(projects), "by_status": by_status, "by_mode": by_mode,
           "measured_impact_total": round(measured_total, 2)}


def question_stats(store: Store) -> dict:
    counts = store.db.execute(
        "SELECT review_status, COUNT(*) AS n FROM items WHERE kind='question' "
        "GROUP BY review_status").fetchall()
    by_status = {r["review_status"]: r["n"] for r in counts}
    terminal = sum(by_status.get(s, 0) for s in TERMINAL)
    skipped = by_status.get("skipped", 0)
    rate = (skipped / terminal) if terminal else 0.0
    return {"by_status": by_status, "answered_cleanly_rate": round(rate, 3)}


def spend(store: Store, currency: str) -> dict:
    totals = store.usage_totals()
    return {"currency": currency, **totals}


def build_report(store: Store, currency: str) -> dict:
    return {"scans": scan_stats(store), "projects": project_stats(store),
           "questions": question_stats(store), "spend": spend(store, currency)}


def print_human(report: dict, mode: str) -> None:
    print("Strategic Advisor AI - report\n")
    print(f"Mode: {mode}\n")
    s = report["scans"]
    print(f"Scans run: {s['total']}  ({', '.join(f'{k}: {v}' for k, v in s['by_verdict'].items()) or 'none yet'})")
    p = report["projects"]
    print(f"Projects: {p['total']}  by status: {p['by_status']}  by mode: {p['by_mode']}")
    print(f"Measured impact (resolved projects only): {report['spend']['currency']} "
         f"{p['measured_impact_total']:,.2f}")
    q = report["questions"]
    print(f"Questions asked: {sum(q['by_status'].values())}  "
         f"answered cleanly (no human touch): {q['answered_cleanly_rate']:.0%}")
    sp = report["spend"]
    print(f"LLM calls: {sp.get('calls', 0)}  tokens: in {sp.get('input_tokens', 0)} / "
         f"out {sp.get('output_tokens', 0)}  cost: ${sp.get('cost_usd', 0.0):.4f}")


def export_resolved_projects(store: Store, settings) -> tuple[bool, str]:
    """`--export`: one row per resolved project, via the sheets adapter -
    `data/exports/resolved_projects.csv` with the `csv` adapter (default),
    or a live sheet with `google`. Blocked in shadow like every other write
    - see docs/safety.md."""
    projects = [p for p in sa_project.list_projects(store, status="resolved")]
    rows = [["id", "title", "mode", "measured_impact", "resolved_on"]]
    rows += [[p["id"], p["title"], p["mode"], p["measured_impact"], p["resolved_on"]]
            for p in projects]
    sheets = get_sheets(settings)
    try:
        sheets.write("resolved_projects", rows)
    except WriteBlocked as exc:
        return False, str(exc)
    return True, "exported to data/exports/resolved_projects.csv (or your configured sheet)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print JSON instead of the summary")
    parser.add_argument("--export", action="store_true",
                        help="also export resolved projects via the sheets adapter")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    sa_project.migrate_schema(store)
    try:
        report = build_report(store, settings.hotel.currency)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print_human(report, settings.mode)
        if args.export:
            ok, message = export_resolved_projects(store, settings)
            print(("" if ok else "not ") + f"exported: {message}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
