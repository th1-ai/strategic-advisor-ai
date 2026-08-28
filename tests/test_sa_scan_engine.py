"""Tests for tools/scan_engine.py - the deterministic daily scan. Every
number here is invented; no fixtures, no store, no LLM - see
docs/how-it-works.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import scan_engine as se

THRESHOLDS = {"revpar_warn_pct": -8.0, "occupancy_warn_pts": -5.0, "dept_pace_warn_pct": -5.0,
             "review_negative_warn": 2, "competitor_opportunity_pct": 8.0, "suppression_days": 14,
             "rollback_threshold_pct": 10.0}
RULES = {"yoy-baseline": True, "rule-out-external": True, "verify-cross-sources": True,
        "competitor-watch": True}


def _fin_row(date, rooms, fnb, spa, occ, rooms_available=40):
    return {"date": date, "revenue_rooms": rooms, "revenue_fnb": fnb, "revenue_spa": spa,
           "occupancy_pct": occ, "rooms_available": rooms_available,
           "revenue_total": rooms + fnb + spa, "revpar": rooms / rooms_available}


def test_revpar_ok_when_flat():
    rows = [_fin_row(f"2026-06-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 15)]
    line, anomaly, _ = se.scan_revpar(rows, "2026-06-15", THRESHOLDS, "EUR", [])
    assert line.verdict == "ok"
    assert anomaly is None


def test_revpar_warns_on_a_real_drop():
    rows = [_fin_row(f"2026-06-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 8)]
    rows += [_fin_row(f"2026-06-{d:02d}", 3000, 3000, 1000, 70) for d in range(8, 15)]
    line, anomaly, step = se.scan_revpar(rows, "2026-06-15", THRESHOLDS, "EUR", [])
    assert line.verdict == "warn"
    assert anomaly is not None
    assert anomaly["metric_key"] == "revpar"
    assert "new anomaly" in step


def test_department_pace_anomaly_suppressed_by_an_active_fix_project():
    rows = [_fin_row(f"2026-05-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 32)]
    rows += [_fin_row(f"2026-06-{d:02d}", 5000, 2500, 1000, 70) for d in range(1, 15)]
    covering = [{"title": "Recover F&B revenue", "mode": "fix", "status": "active",
                "metric_key": "revenue_fnb", "resolved_on": None}]
    lines, anomalies, steps = se.scan_department_pace(rows, "2026-06-15", THRESHOLDS, "EUR", covering)
    fnb_line = next(l for l in lines if "F&B" in l.label)
    assert fnb_line.verdict == "warn"          # the number itself is still down
    assert anomalies == []                     # but it is not a NEW anomaly
    assert any("already covered" in s for s in steps)


def test_department_pace_anomaly_not_suppressed_without_a_covering_project():
    rows = [_fin_row(f"2026-05-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 32)]
    rows += [_fin_row(f"2026-06-{d:02d}", 5000, 2500, 1000, 70) for d in range(1, 15)]
    lines, anomalies, steps = se.scan_department_pace(rows, "2026-06-15", THRESHOLDS, "EUR", [])
    assert len(anomalies) == 1
    assert anomalies[0]["metric_key"] == "revenue_fnb"
    assert any("new anomaly" in s for s in steps)


def test_rooms_and_spa_dips_can_also_be_suppressed_not_only_fnb():
    """Fixes the spec's open question 3/4: every department carries a
    metric_key, not only F&B - a rooms fix project must cover a rooms dip."""
    rows = [_fin_row(f"2026-05-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 32)]
    rows += [_fin_row(f"2026-06-{d:02d}", 4000, 3000, 1000, 70) for d in range(1, 15)]
    covering = [{"title": "Recover rooms revenue", "mode": "fix", "status": "active",
                "metric_key": "revenue_rooms", "resolved_on": None}]
    lines, anomalies, steps = se.scan_department_pace(rows, "2026-06-15", THRESHOLDS, "EUR", covering)
    rooms_line = next(l for l in lines if "Rooms" in l.label)
    assert rooms_line.verdict == "warn"
    assert anomalies == []


def test_review_sentiment_warns_on_a_negative_cluster():
    reviews = [{"review_date": "2026-06-10", "rating": 2}, {"review_date": "2026-06-11", "rating": 2},
              {"review_date": "2026-06-12", "rating": 5}]
    line, anomaly, _ = se.scan_review_sentiment(reviews, "2026-06-15", THRESHOLDS, [])
    assert line.verdict == "warn"
    assert anomaly is not None


def test_external_causes_is_honestly_labelled_a_placeholder():
    line, step = se.scan_external_causes(RULES)
    assert "not checked" in line.value
    assert "no news/events/weather feed" in step


def test_competitor_watch_finds_the_matching_own_item_generically():
    """Fixes the spec's open question 5: not hard-wired to 'burger' - any
    own menu item whose name shares a token with a moved competitor item."""
    snaps = [
        {"competitor": "Bay View Bistro", "scraped_on": "2026-05-01", "category": "food",
         "item": "Chargrilled Burger", "price": 20.0},
        {"competitor": "Bay View Bistro", "scraped_on": "2026-06-10", "category": "food",
         "item": "Chargrilled Burger", "price": 24.0},
    ]
    pos_items = [{"item": "Aurora Burger", "price": 22.0}]
    line, opportunity, step = se.scan_competitor_watch(snaps, pos_items, "2026-06-15", THRESHOLDS,
                                                        RULES, [])
    assert opportunity is not None
    assert opportunity["item"] == "Aurora Burger"
    assert opportunity["best_delta"] > THRESHOLDS["competitor_opportunity_pct"]


def test_competitor_watch_below_threshold_stays_stable():
    snaps = [
        {"competitor": "Bay View Bistro", "scraped_on": "2026-05-01", "category": "food",
         "item": "Chargrilled Burger", "price": 20.0},
        {"competitor": "Bay View Bistro", "scraped_on": "2026-06-10", "category": "food",
         "item": "Chargrilled Burger", "price": 20.5},
    ]
    pos_items = [{"item": "Aurora Burger", "price": 22.0}]
    line, opportunity, step = se.scan_competitor_watch(snaps, pos_items, "2026-06-15", THRESHOLDS,
                                                        RULES, [])
    assert opportunity is None
    assert "too small to act on" in step


def test_run_scan_verdict_is_anomaly_when_a_department_dips():
    rows = [_fin_row(f"2025-06-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 31)]
    rows += [_fin_row(f"2026-05-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 32)]
    rows += [_fin_row(f"2026-06-{d:02d}", 5000, 2400, 1000, 70) for d in range(1, 15)]
    result = se.run_scan(financial_rows=rows, reviews=[], competitor_snapshots=[], pos_items=[],
                         projects=[], rules=RULES, thresholds=THRESHOLDS, currency="EUR",
                         today="2026-06-15")
    assert result.verdict == "anomaly"
    assert "F&B" in result.headline


def test_run_scan_verdict_is_stable_when_nothing_moves():
    rows = [_fin_row(f"2025-06-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 31)]
    rows += [_fin_row(f"2026-05-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 32)]
    rows += [_fin_row(f"2026-06-{d:02d}", 5000, 3000, 1000, 70) for d in range(1, 15)]
    result = se.run_scan(financial_rows=rows, reviews=[], competitor_snapshots=[], pos_items=[],
                         projects=[], rules=RULES, thresholds=THRESHOLDS, currency="EUR",
                         today="2026-06-15")
    assert result.verdict == "stable"
