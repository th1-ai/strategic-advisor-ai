"""Tests for tools/doctor.py - SIMULATION.md Finding 1 (the financial/
reviews/competitor/POS checks must WARN from the `*_connected()` booleans,
never PASS on a fixture row count) and Finding 3 (the AI-disclosure go-live
check). AGENT_REPO_ROOT/AGENT_CONFIG_DIR sandboxing comes from
tests/conftest.py, so every test here starts with no CSVs in data/imports/
and no knowledge/*.md beyond the shipped .example.md files - `check_ai_
disclosure()` reads `core.config.repo_root()`, which honours that override,
never the real repo, so these tests cannot touch the working copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings, repo_root

from tools import doctor


def _settings():
    return load_settings(provider="mock", mode="shadow")


def test_financial_ledger_warns_not_passes_when_unconnected():
    check = doctor.check_financial_data(_settings())
    assert check.status == doctor.WARN
    assert "not connected" in check.detail


def test_reviews_competitors_and_pos_items_warn_not_pass_when_unconnected():
    checks = {c.name: c for c in doctor.check_reviews_and_competitors(_settings())}
    assert set(checks) == {"reviews", "competitor snapshots", "POS items"}
    for check in checks.values():
        assert check.status == doctor.WARN, f"{check.name} should WARN, not {check.status}"
        assert "not connected" in check.detail


def test_ai_disclosure_fails_when_signature_and_disclosure_are_missing():
    check = doctor.check_ai_disclosure()
    assert check.status == doctor.FAIL
    assert "signature.md" in check.detail
    assert "disclosure.md" in check.detail


def test_ai_disclosure_fails_when_files_exist_but_are_still_the_unedited_example():
    kdir = repo_root() / "knowledge"
    (kdir / "signature.md").write_text(
        (kdir / "signature.example.md").read_text(encoding="utf-8"), encoding="utf-8")
    (kdir / "disclosure.md").write_text(
        (kdir / "disclosure.example.md").read_text(encoding="utf-8"), encoding="utf-8")
    check = doctor.check_ai_disclosure()
    assert check.status == doctor.FAIL
    assert "byte-identical" in check.detail


def test_ai_disclosure_passes_once_both_files_are_actually_edited():
    kdir = repo_root() / "knowledge"
    (kdir / "signature.md").write_text(
        "Best regards,\nThe team at Quinta do Vale.\nPrepared with AI assistance, "
        "reviewed by our team.\n", encoding="utf-8")
    (kdir / "disclosure.md").write_text(
        "This message was prepared with AI assistance and checked by our team.\n",
        encoding="utf-8")
    check = doctor.check_ai_disclosure()
    assert check.status == doctor.PASS
