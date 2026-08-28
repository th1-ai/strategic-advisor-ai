#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

`load_settings(demo=True)` forces `llm.provider=mock`, `mode=shadow` and the
`mock` adapter for every system, whatever config/hotel.yaml says, so this
always works on a fresh clone with a blank .env. It runs against its own
database (data/demo/demo.db) and never touches data/agent.db (that is
`make run`'s file), so running it twice always shows the same result.

FIXTURE_TODAY pins "today" to the last day of fixtures/hotel/financial_daily.json
(2026-06-15), so the numbers you see are real arithmetic over invented data -
see docs/how-it-works.md "Design decisions". A real `make run --scan` always
uses the real date.

Two projects are seeded directly (not discovered by the scan) to show the
full step machine without waiting weeks for a real storyline: a growth
project mid-flight (a scheduled price change, ready to measure) and a
resolved one (proves the measured-impact KPI is real). Both are flagged
`seeded: true` and this script says so out loud - see
docs/how-it-works.md "Design decisions" for why the demo does not fabricate
POS data the way the source system's own fast-forward does.

Prints one line every check reads for the pass/fail signal:

    DEMO OK — 3 items processed, 3 drafted, 0 sent (shadow)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.store import Store  # noqa: E402

from tools import project as sa_project  # noqa: E402
from tools.ask_engine import answer_question  # noqa: E402
from tools.scan import run_daily_scan  # noqa: E402

FIXTURE_TODAY = "2026-06-15"


def _seed_growth_project(store: Store) -> dict:
    """p2: mid-flight, price already applied historically, ready to measure
    for real - see the module docstring for why the pos_update step is
    seeded `done` rather than performed live (a real apply-pos is guarded
    exactly like a send, and shadow mode blocks it every time, on purpose)."""
    steps = [
        {"seq": 1, "kind": "analysis", "status": "done", "title": "Analysis", "payload": {
            "conclusion": "Competitors raised burger prices 15-17% since May while the Aurora "
                          "Burger has not moved. Attach rate and covers are stable, so this "
                          "reads as room to reprice, not a demand problem.",
            "ruled_out": ["No drop in dinner covers that would explain caution on price",
                         "No supplier cost spike driving the competitor moves"]}},
        {"seq": 2, "kind": "email", "status": "done", "title": "Email the team", "payload": {
            "to_role": "F&B Director", "to_email": "manager@example.com",
            "subject": "Proposing EUR 24 -> EUR 26 on the Aurora Burger",
            "body": "Competitors are 15-17% up on comparable burgers. Proposing a modest "
                   "EUR 2 increase, tested for two months before we call it permanent."}},
        {"seq": 3, "kind": "wait", "status": "done", "title": "Wait for a reply", "payload": {
            "days": 3, "due": "2026-05-03", "reply": "Agreed, let's test it from May 1st."}},
        {"seq": 4, "kind": "pos_update", "status": "done", "title": "Reprice the item", "payload": {
            "item_id": "burger-aurora", "item": "Aurora Burger", "venue": "The Aurora Grill",
            "from": 24.0, "to": 26.0, "effective": "2026-05-01", "applied_at": "2026-05-01",
            "note": "Approved and applied by the GM on 2026-04-29."}},
        {"seq": 5, "kind": "measure", "status": "armed", "title": "Measure the result", "payload": {}},
        {"seq": 6, "kind": "resolve", "status": "pending", "title": "Resolve the project", "payload": {}},
    ]
    return sa_project.seed_project(
        store, project_id="p2-reprice-burger", title="Reprice the Aurora Burger vs competitors",
        mode="growth", status="active", metric_key="competitor_price:Aurora Burger",
        target_label="Close the gap on the Aurora Burger",
        projected_impact="~EUR 1,300/month, pending measurement",
        summary="Competitors raised burger prices 15-17% since May; the Aurora Burger price "
                "moved from EUR 24 to EUR 26 on 2026-05-01 - measurement is due.",
        steps=steps, created_on="2026-04-28")


def _seed_resolved_project(store: Store) -> dict:
    """p3: complete history, measured impact locked in - proves the KPI in
    tools/report.py is real, not a seeded number with nothing behind it."""
    done = lambda title: {"status": "done", "title": title, "payload": {}}  # noqa: E731
    steps = [
        {"seq": 1, "kind": "analysis", **done("Analysis"),
         "payload": {"conclusion": "Spa bookings made at check-in convert far less often than "
                                   "a mention at the time of the room booking.",
                    "ruled_out": ["No shortage of spa availability during the test window"]}},
        {"seq": 2, "kind": "email", **done("Email the team")},
        {"seq": 3, "kind": "wait", **done("Wait for a reply")},
        {"seq": 4, "kind": "checkpoint", **done("Checkpoint: is it fixed?")},
        {"seq": 5, "kind": "marketing_action", **done("Run the promo")},
        {"seq": 6, "kind": "tracker", **done("Track the recovery")},
        {"seq": 7, "kind": "resolve", **done("Resolve the project")},
    ]
    return sa_project.seed_project(
        store, project_id="p3-spa-upsell", title="Spa upsell at booking", mode="growth",
        status="resolved", metric_key="revenue_spa", target_label="+EUR 15,000 in spa revenue",
        projected_impact="+EUR 15,000", measured_impact=18400.0,
        summary="Spa upsell offered at the moment of booking, not at check-in - "
                "+EUR 18,400 measured over the quarter.",
        steps=steps, created_on="2026-02-01", resolved_on="2026-05-20")


def _load_fixture_questions() -> list[dict]:
    import json
    inbound = REPO_ROOT / "fixtures" / "inbound"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(inbound.glob("question-*.json"))]


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()  # every `make demo` is a clean, repeatable run
    store = Store(settings, path=demo_db)
    sa_project.migrate_schema(store)

    print(f"Strategic Advisor AI demo - pretending today is {FIXTURE_TODAY}\n")

    print("Seeding two projects to show the full step machine (seeded=true, not scan-discovered):")
    p2 = _seed_growth_project(store)
    p3 = _seed_resolved_project(store)
    print(f"  {p2['id']}  {p2['mode']:<7} {p2['status']:<9} {p2['title']}")
    print(f"  {p3['id']}  {p3['mode']:<7} {p3['status']:<9} {p3['title']} "
         f"(measured +EUR {p3['measured_impact']:,.0f})\n")

    print("Running the daily scan for today...")
    code, scan_stats = run_daily_scan(settings, store, provider="mock", as_of=FIXTURE_TODAY)
    print()

    stats = {"processed": scan_stats["processed"], "drafted": scan_stats["drafted"],
            "needs_human": scan_stats["needs_human"], "sent": 0}

    p1 = next((p for p in sa_project.list_projects(store, status="active")
              if p["id"] not in (p2["id"],)), None)
    if p1 is not None:
        email_step = next(s for s in p1["steps"] if s["kind"] == "email")
        step_id = f"{p1['id']}:{email_step['seq']}"
        print(f"Approving the draft email on '{p1['title']}' ({step_id})...")
        sa_project.approve_step(store, step_id, note="looks right, send it")
        _, outcome = sa_project.send_step(store, settings, step_id)
        print(f"  send-step: {outcome}\n")

    print(f"Measuring the result on '{p2['title']}' (real arithmetic on the bundled POS export):")
    from tools import data as sa_data
    baseline = [r for r in sa_data.load_pos_sales_daily(settings) if r["date"][:7] == "2026-05"]
    target = [r for r in sa_data.load_pos_sales_daily(settings) if r["date"][:7] == "2026-07"]
    thresholds = settings.agent_get("thresholds", {}) or {}
    sa_project.measure(store, "p2-reprice-burger:5", baseline_rows=baseline, target_rows=target,
                       price_from=24.0, price_to=26.0,
                       rollback_threshold_pct=thresholds.get("rollback_threshold_pct", 10.0))
    p2_after = sa_project.get_project(store, "p2-reprice-burger")
    measured = next(s for s in p2_after["steps"] if s["kind"] == "measure")["payload"]["result"]
    print(f"  volume change: {measured['delta_pct']:+.1f}% -> projected uplift "
         f"EUR {measured['uplift_month']:,.0f}/month, keep_price={measured['keep_price']}\n")

    print("Asking a few sample questions (fixtures/inbound/question-*.json):\n")
    stats.update({"processed": 0, "drafted": 0, "needs_human": 0})
    for entry in _load_fixture_questions():
        item, _ = answer_question(settings, store, entry["question"], source="demo",
                                  external_id=entry["id"], asked_by=entry.get("asked_by", "owner"),
                                  provider="mock", as_of=FIXTURE_TODAY)
        stats["processed"] += 1
        stats["drafted"] += 1
        if item.review_status == "needs_human":
            stats["needs_human"] += 1
        draft = item.draft or {}
        answer_preview = (draft.get("reply_markdown") or "").splitlines()[0][:70]
        print(f"  {entry['id']}: \"{entry['question'][:50]}\" -> status={item.review_status}")
        print(f"      {answer_preview}")

    stats["processed"] += scan_stats["processed"]
    stats["drafted"] += scan_stats["drafted"]
    stats["needs_human"] += scan_stats["needs_human"]

    print("\nNothing was sent: mode is shadow, and every gated write (email, marketing action, "
         "price change) is blocked - see the send-step and measure output above.")
    print("Next: `make review` to see the F&B project's email, or read workflows/10-scan.md.\n")

    print(f"DEMO OK — {summary_line(stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
