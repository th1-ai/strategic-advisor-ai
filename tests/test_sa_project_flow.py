"""Tests for tools/project.py and tools/scan.py's project-opening path:
the mirrored review queue, the shadow guard, the two-phase pos_update gate,
and idempotent re-runs. provider=mock throughout - no network, no
credentials. AGENT_REPO_ROOT/AGENT_CONFIG_DIR sandboxing comes from
tests/conftest.py.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from core.config import load_settings
from core.store import Store

from tools import data as sa_data
from tools import project as sa_project
from tools.scan import run_daily_scan

AS_OF = "2026-06-15"


def _settings(*, mode: str = "shadow", dry_run: bool = False):
    return load_settings(provider="mock", mode=mode, dry_run=dry_run)


def _store(tmp_path, name: str) -> Store:
    store = Store(_settings(), path=tmp_path / name)
    sa_project.migrate_schema(store)
    return store


def test_scan_opens_a_fix_project_with_a_gated_email_step(tmp_path):
    store = _store(tmp_path, "a.db")
    code, stats = run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    assert code == 0
    assert stats["drafted"] == 1

    projects = sa_project.list_projects(store, status="active")
    assert len(projects) == 1
    project = projects[0]
    assert project["mode"] == "fix"
    email_step = next(s for s in project["steps"] if s["kind"] == "email")
    assert email_step["status"] == "awaiting_approval"

    from core.review import list_queue
    queued = list_queue(store, kind="advisor_step")
    assert len(queued) == 1
    assert queued[0].review_status == "pending_review"
    store.close()


def test_rerunning_the_scan_same_day_does_not_open_a_second_project(tmp_path):
    store = _store(tmp_path, "b.db")
    run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    assert len(sa_project.list_projects(store)) == 1
    store.close()


def test_a_dept_covered_by_an_active_fix_project_does_not_reopen(tmp_path):
    store = _store(tmp_path, "c.db")
    run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    run_daily_scan(_settings(), store, provider="mock", as_of="2026-06-16")
    projects = sa_project.list_projects(store)
    fix_projects = [p for p in projects if p["mode"] == "fix"]
    # the F&B dip is "already covered" on day two, so still just ONE fix
    # project - suppression working means the scan does not nag about it
    # again. It is free, though, to open a genuinely different project (a
    # growth opportunity, from the same day's competitor-watch step) - that
    # is a second, unrelated signal, not a duplicate.
    assert len(fix_projects) == 1
    assert all(p["metric_key"] != "revenue_fnb" or p is fix_projects[0]
              for p in projects if p["mode"] == "fix")
    store.close()


def test_approve_then_send_is_blocked_in_shadow_and_the_approval_is_kept(tmp_path):
    store = _store(tmp_path, "d.db")
    run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    project = sa_project.list_projects(store)[0]
    email_step = next(s for s in project["steps"] if s["kind"] == "email")
    step_id = f"{project['id']}:{email_step['seq']}"

    sa_project.approve_step(store, step_id, note="ok")
    item = sa_project.get_mirror_item(store, project, email_step)
    assert item.review_status == "approved"

    settings = _settings()
    _, outcome = sa_project.send_step(store, settings, step_id)
    assert outcome.startswith("blocked")
    item_after = sa_project.get_mirror_item(store, project, email_step)
    assert item_after.review_status == "approved"  # the approval survived the block
    store.close()


def test_edit_step_merges_onto_the_existing_draft_without_losing_fields(tmp_path):
    store = _store(tmp_path, "e.db")
    run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    project = sa_project.list_projects(store)[0]
    email_step = next(s for s in project["steps"] if s["kind"] == "email")
    step_id = f"{project['id']}:{email_step['seq']}"
    before = sa_project.get_mirror_item(store, project, email_step).draft
    assert before.get("to_email")

    sa_project.edit_step(store, step_id, {"body": "A rewritten body."}, note="tone")
    after = sa_project.get_mirror_item(store, project, email_step).draft
    assert after["body"] == "A rewritten body."
    assert after["to_email"] == before["to_email"]  # not wiped by the edit
    store.close()


def test_reject_step_is_terminal_and_does_not_arm_the_next_step(tmp_path):
    store = _store(tmp_path, "f.db")
    run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    project = sa_project.list_projects(store)[0]
    email_step = next(s for s in project["steps"] if s["kind"] == "email")
    step_id = f"{project['id']}:{email_step['seq']}"

    sa_project.reject_step(store, step_id, reason="wrong framing")
    item = sa_project.get_mirror_item(store, project, email_step)
    assert item.review_status == "rejected"
    project_after = sa_project.get_project(store, project["id"])
    wait_step = next(s for s in project_after["steps"] if s["kind"] == "wait")
    assert wait_step["status"] == "pending"  # nothing armed automatically
    store.close()


def test_pos_update_two_phase_gate(tmp_path):
    store = _store(tmp_path, "g.db")
    steps = [
        {"seq": 1, "kind": "analysis", "status": "done", "title": "Analysis", "payload": {}},
        {"seq": 2, "kind": "pos_update", "status": "awaiting_approval", "title": "Reprice",
         "payload": {"item_id": "burger-aurora", "item": "Aurora Burger", "to": 26.0, "from": 24.0}},
        {"seq": 3, "kind": "measure", "status": "pending", "title": "Measure", "payload": {}},
    ]
    project = sa_project.seed_project(store, project_id="p-test", title="Reprice test",
                                      mode="growth", status="active", metric_key="competitor_price:x",
                                      target_label="x", projected_impact="x", summary="x",
                                      steps=steps, created_on=AS_OF)
    sa_project.mirror_gated_step(store, project, steps[1])

    settings = _settings()
    sa_project.approve_step(store, "p-test:2", effective="2026-07-01")
    after_approve = sa_project.get_project(store, "p-test")
    pos_step = next(s for s in after_approve["steps"] if s["kind"] == "pos_update")
    assert pos_step["status"] == "scheduled"

    _, outcome = sa_project.apply_pos(store, settings, "p-test:2")
    assert "blocked" in outcome and "scheduled" in outcome  # shadow blocks the actual write
    still_scheduled = sa_project.get_project(store, "p-test")
    pos_step2 = next(s for s in still_scheduled["steps"] if s["kind"] == "pos_update")
    assert pos_step2["status"] == "scheduled"
    store.close()


def test_measure_requires_the_pos_update_to_have_actually_applied(tmp_path):
    store = _store(tmp_path, "h.db")
    steps = [{"seq": 1, "kind": "measure", "status": "pending", "title": "Measure", "payload": {}}]
    sa_project.seed_project(store, project_id="p-measure", title="x", mode="growth", status="active",
                            metric_key="x", target_label="x", projected_impact="x", summary="x",
                            steps=steps, created_on=AS_OF)
    with pytest.raises(sa_project.ProjectError):
        sa_project.measure(store, "p-measure:1", baseline_rows=[], target_rows=[],
                           price_from=24.0, price_to=26.0, rollback_threshold_pct=10.0)
    store.close()


def test_resolve_locks_in_measured_impact(tmp_path):
    store = _store(tmp_path, "i.db")
    sa_project.seed_project(store, project_id="p-resolve", title="x", mode="growth", status="active",
                            metric_key="x", target_label="x", projected_impact="x", summary="x",
                            steps=[], created_on=AS_OF)
    project = sa_project.resolve_project(store, "p-resolve", measured_impact=1234.5,
                                         impact_label="+EUR 1,234.50/mo measured", today=AS_OF)
    assert project["status"] == "resolved"
    assert project["measured_impact"] == 1234.5
    store.close()


def test_abandon_closes_a_project_without_a_measured_impact(tmp_path):
    store = _store(tmp_path, "j.db")
    sa_project.seed_project(store, project_id="p-abandon", title="x", mode="fix", status="active",
                            metric_key="x", target_label="x", projected_impact="x", summary="x",
                            steps=[], created_on=AS_OF)
    project = sa_project.abandon_project(store, "p-abandon", reason="did not move the number",
                                         today=AS_OF)
    assert project["status"] == "abandoned"
    assert project["measured_impact"] is None
    store.close()


def test_dry_run_writes_nothing(tmp_path):
    store = _store(tmp_path, "k.db")
    code, stats = run_daily_scan(_settings(dry_run=True), store, provider="mock", as_of=AS_OF)
    assert code == 0
    assert sa_project.list_projects(store) == []
    store.close()


# --------------------------------------------------------------------------
# SIMULATION.md Finding 1 - no fixture data may drive a real scan undisclosed
# --------------------------------------------------------------------------
def test_fully_unconnected_scan_tags_the_opened_project_sample(tmp_path):
    """Every source is on the bundled fixture (nothing in data/imports/) -
    the fix project the scan opens must be tagged, and so must its mirrored
    review-queue item (the same `_sample` key core.store.Item.is_sample
    reads), so `[SAMPLE]` shows up in `tools/project.py`/`tools/review.py`."""
    store = _store(tmp_path, "sample-untouched.db")
    run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    project = sa_project.list_projects(store)[0]
    assert project["is_sample"] is True

    email_step = next(s for s in project["steps"] if s["kind"] == "email")
    item = sa_project.get_mirror_item(store, project, email_step)
    assert item.is_sample is True
    store.close()


def test_one_real_csv_and_two_missing_never_opens_an_undisclosed_fixture_project(tmp_path, capsys):
    """SIMULATION.md Finding 1's concrete repro: financial_daily.csv is
    connected (real, flat numbers so no anomaly fires), reviews/competitor/
    pos stay on the bundled fixture. Before the fix, the competitor watch
    ran on the fixture's Aurora Burger data regardless and could open a
    named, undisclosed "Reprice Aurora Burger" project. Now that check is
    SKIPPED when either of its two sources is unconnected, so no such
    project - or any growth-mode project at all - can open from it."""
    store = _store(tmp_path, "sample-real-financial.db")
    # Flat, invented numbers for the whole scan window - no anomaly anywhere
    # in financial data, so the scan reaches the competitor-watch check
    # instead of returning early on a "fix" anomaly.
    rows = [{"date": f"2026-06-{day:02d}", "revenue_rooms": 5000, "revenue_fnb": 2000,
            "revenue_spa": 800, "occupancy_pct": 70, "rooms_available": 42}
           for day in range(1, 16)]
    csv_path = sa_data.imports_dir() / "financial_daily.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["date", "revenue_rooms", "revenue_fnb",
                                                "revenue_spa", "occupancy_pct", "rooms_available"])
        writer.writeheader()
        writer.writerows(rows)

    assert sa_data.financial_connected() is True
    assert sa_data.reviews_connected() is False
    assert sa_data.competitors_connected() is False
    assert sa_data.pos_items_connected() is False

    code, stats = run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    assert code == 0

    out = capsys.readouterr().out
    assert "verdict=stable" in out
    assert "financial ledger not connected" not in out          # it IS connected
    assert "competitor snapshots not connected" in out
    assert "your menu / POS items not connected" in out
    assert "skipping the competitor-watch" in out

    projects = sa_project.list_projects(store)
    assert projects == []
    assert not any("Aurora" in p.get("title", "") for p in projects)
    store.close()


# --------------------------------------------------------------------------
# SIMULATION.md Finding 2 - the project step machine must be usable in shadow
# --------------------------------------------------------------------------
def test_a_project_can_be_walked_end_to_end_in_shadow(tmp_path):
    """Walks a whole 'fix' project - email -> wait -> checkpoint ->
    marketing_action -> tracker -> resolve - purely by approving and
    (shadow-blocked) sending each gated step. Before the fix, `send_step`'s
    WriteBlocked branch never called `arm_next()`, so the project was stuck
    forever after the first email; here it reaches `resolved` without ever
    leaving `mode: shadow`."""
    store = _store(tmp_path, "walk.db")
    settings = _settings()
    assert settings.mode == "shadow"
    run_daily_scan(settings, store, provider="mock", as_of=AS_OF)
    project = sa_project.list_projects(store)[0]
    assert project["mode"] == "fix"

    # 1. email - approve, blocked-send still advances the project.
    email_step = next(s for s in project["steps"] if s["kind"] == "email")
    step_id = f"{project['id']}:{email_step['seq']}"
    sa_project.approve_step(store, step_id, note="looks right")
    project, outcome = sa_project.send_step(store, settings, step_id)
    assert outcome.startswith("blocked (approval kept)")
    email_after = next(s for s in project["steps"] if s["kind"] == "email")
    assert email_after["status"] == "done"
    assert email_after["payload"]["sent"] == "no (shadow)"
    assert sa_project.get_mirror_item(store, project, email_after).review_status == "approved"
    wait_step = next(s for s in project["steps"] if s["kind"] == "wait")
    assert wait_step["status"] == "armed"

    # 2. wait -> checkpoint arms (demo-only fast-forward, honestly labelled).
    wait_id = f"{project['id']}:{wait_step['seq']}"
    project = sa_project.fast_forward_wait(store, wait_id, "Agreed, go ahead.")
    checkpoint_step = next(s for s in project["steps"] if s["kind"] == "checkpoint")
    assert checkpoint_step["status"] == "armed"

    # 3. checkpoint confirmed -> marketing_action arms.
    cp_id = f"{project['id']}:{checkpoint_step['seq']}"
    project = sa_project.checkpoint_confirm(store, cp_id)
    marketing_step = next(s for s in project["steps"] if s["kind"] == "marketing_action")
    assert marketing_step["status"] == "awaiting_approval"

    # 4. marketing_action - same shadow-block-but-advance behaviour as email.
    mk_id = f"{project['id']}:{marketing_step['seq']}"
    sa_project.approve_step(store, mk_id, note="looks right")
    project, outcome = sa_project.send_step(store, settings, mk_id)
    assert outcome.startswith("blocked (approval kept)")
    marketing_after = next(s for s in project["steps"] if s["kind"] == "marketing_action")
    assert marketing_after["status"] == "done"
    assert marketing_after["payload"]["sent"] == "no (shadow)"
    tracker_step = next(s for s in project["steps"] if s["kind"] == "tracker")
    assert tracker_step["status"] == "tracking"

    # 5. tracker -> resolve - all the way to the end, still in shadow.
    tk_id = f"{project['id']}:{tracker_step['seq']}"
    project = sa_project.fast_forward_tracker(store, [], tk_id)
    resolve_step = next(s for s in project["steps"] if s["kind"] == "resolve")
    assert resolve_step["status"] == "armed"
    resolved = sa_project.resolve_project(store, project["id"], measured_impact=100.0,
                                          impact_label="+EUR 100/mo measured", today=AS_OF)
    assert resolved["status"] == "resolved"
    store.close()


def test_a_re_sent_shadow_blocked_step_does_not_re_arm_a_step_that_moved_on(tmp_path):
    """Calling send-step twice on the same already-blocked-and-done step must
    not clobber whatever progress the next step made in between (the
    `already_done` guard in `tools/project.py:send_step`) - re-arming would
    reset a step that has already moved to `done` back to `armed`."""
    store = _store(tmp_path, "walk-idempotent.db")
    settings = _settings()
    run_daily_scan(settings, store, provider="mock", as_of=AS_OF)
    project = sa_project.list_projects(store)[0]
    email_step = next(s for s in project["steps"] if s["kind"] == "email")
    step_id = f"{project['id']}:{email_step['seq']}"
    sa_project.approve_step(store, step_id, note="ok")
    sa_project.send_step(store, settings, step_id)  # email -> done, wait -> armed

    wait_step = next(s for s in sa_project.get_project(store, project["id"])["steps"]
                     if s["kind"] == "wait")
    wait_id = f"{project['id']}:{wait_step['seq']}"
    project = sa_project.fast_forward_wait(store, wait_id, "Agreed, go ahead.")  # wait -> done
    checkpoint_step = next(s for s in project["steps"] if s["kind"] == "checkpoint")
    assert checkpoint_step["status"] == "armed"

    # Re-sending the SAME (already-blocked-and-done) email step again must
    # not reset the wait step it already moved past back to "armed".
    project2, outcome2 = sa_project.send_step(store, settings, step_id)
    assert outcome2.startswith("blocked (approval kept)")
    wait_after = next(s for s in project2["steps"] if s["kind"] == "wait")
    assert wait_after["status"] == "done"
    checkpoint_after = next(s for s in project2["steps"] if s["kind"] == "checkpoint")
    assert checkpoint_after["status"] == "armed"
    store.close()
