"""Tests for tools/projects_engine.py - the step machine's pure functions.
No store, no adapters - see docs/how-it-works.md section "Step kinds".
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from tools import projects_engine as pe


def test_build_steps_seeds_payloads_by_kind():
    steps = pe.build_steps(["analysis", "email"], payloads={"email": {"subject": "hi"}})
    assert steps[0]["status"] == "pending"
    assert steps[1]["payload"] == {"subject": "hi"}


def test_entry_status_matches_the_spec_table():
    assert pe.entry_status("email") == "awaiting_approval"
    assert pe.entry_status("marketing_action") == "awaiting_approval"
    assert pe.entry_status("pos_update") == "awaiting_approval"
    assert pe.entry_status("tracker") == "tracking"
    assert pe.entry_status("analysis") == "armed"
    assert pe.entry_status("wait") == "armed"


def test_arm_next_promotes_the_following_step_only():
    steps = pe.build_steps(["analysis", "email", "wait"])
    nxt = pe.arm_next(steps, 1)
    assert nxt["seq"] == 2 and nxt["status"] == "awaiting_approval"
    assert steps[2]["status"] == "pending"  # step 3 untouched


def test_arm_next_returns_none_past_the_last_step():
    steps = pe.build_steps(["analysis"])
    assert pe.arm_next(steps, 1) is None


def test_checkpoint_confirm_arms_a_gated_next_step():
    steps = pe.build_steps(["checkpoint", "marketing_action"])
    steps[0]["status"] = "armed"
    nxt = pe.checkpoint_confirm(steps, 1)
    assert steps[0]["status"] == "done"
    assert nxt["kind"] == "marketing_action" and nxt["status"] == "awaiting_approval"


def test_checkpoint_pushback_rearms_the_preceding_wait():
    steps = pe.build_steps(["wait", "checkpoint"])
    steps[0]["status"], steps[1]["status"] = "done", "armed"
    wait = pe.checkpoint_pushback(steps, 2, "2026-06-15", 5)
    assert wait["status"] == "armed"
    assert wait["payload"]["due"] == "2026-06-20"
    assert steps[1]["status"] == "pending"  # checkpoint goes back to pending


def test_checkpoint_pushback_without_a_preceding_wait_raises():
    steps = pe.build_steps(["checkpoint"])
    steps[0]["status"] = "armed"
    with pytest.raises(ValueError):
        pe.checkpoint_pushback(steps, 1, "2026-06-15", 5)


def test_fast_forward_wait_only_arms_a_following_checkpoint():
    steps = pe.build_steps(["wait", "resolve"])  # next step is NOT a checkpoint
    steps[0]["status"] = "armed"
    nxt = pe.fast_forward_wait(steps, 1, "sure, go ahead")
    assert steps[0]["status"] == "done"
    assert steps[0]["payload"]["reply"] == "sure, go ahead"
    assert nxt is None
    assert steps[1]["status"] == "pending"  # resolve is untouched, per spec


def test_tracker_status_counts_live_and_respects_category():
    reviews = [
        {"review_date": "2026-05-10", "rating": 5, "category": "fnb"},
        {"review_date": "2026-05-11", "rating": 4, "category": "fnb"},
        {"review_date": "2026-05-12", "rating": 5, "category": "rooms"},   # wrong category
        {"review_date": "2026-04-01", "rating": 5, "category": "fnb"},    # before `since`
        {"review_date": "2026-05-15", "rating": 2, "category": "fnb"},
    ]
    status = pe.tracker_status(reviews, since="2026-05-01", min_rating=4, target=3, category="fnb")
    assert status["positive"] == 2
    assert status["negative"] == 1
    assert status["reached"] is False


def test_compute_impact_matches_the_spec_formula():
    baseline = [{"units": 22.0, "covers": 120.0}] * 30
    target = [{"units": 22.1, "covers": 121.0}] * 30
    impact = pe.compute_impact(baseline, target, price_from=28.0, price_to=31.0,
                               rollback_threshold_pct=10.0)
    assert impact.delta_pct == pytest.approx(0.4545, abs=0.01)
    assert impact.uplift_month == pytest.approx(round(22.1 * 3 * 30.4, 2))
    assert impact.keep_price is True


def test_compute_impact_flags_rollback_when_demand_drops_past_the_threshold():
    baseline = [{"units": 22.0, "covers": 120.0}] * 30
    target = [{"units": 15.0, "covers": 110.0}] * 30
    impact = pe.compute_impact(baseline, target, price_from=28.0, price_to=31.0,
                               rollback_threshold_pct=10.0)
    assert impact.keep_price is False
