"""Tests for tools/review.py's `send` command - SIMULATION.md Finding 5: a
shadow block is correct, by-design behaviour, not a failure, so it must be
reported as "blocked (approval kept)" and never make the command exit
non-zero the way a real send failure does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.store import Store

from tools import project as sa_project
from tools import review as sa_review
from tools.scan import run_daily_scan

AS_OF = "2026-06-15"


def _settings():
    return load_settings(provider="mock", mode="shadow")


def _store(tmp_path, name: str) -> Store:
    store = Store(_settings(), path=tmp_path / name)
    sa_project.migrate_schema(store)
    return store


def test_send_reports_a_shadow_block_as_blocked_not_failed_and_exits_0(tmp_path, capsys):
    store = _store(tmp_path, "send.db")
    settings = _settings()
    run_daily_scan(settings, store, provider="mock", as_of=AS_OF)
    project = sa_project.list_projects(store)[0]
    email_step = next(s for s in project["steps"] if s["kind"] == "email")
    item = sa_project.get_mirror_item(store, project, email_step)
    sa_review.cmd_approve(store, argparse.Namespace(id=item.id, effective=None, note=""))

    capsys.readouterr()  # clear the "approved ..." line above
    code = sa_review.cmd_send(store, settings, argparse.Namespace(limit=20))
    out = capsys.readouterr().out

    assert code == 0
    assert "failed" not in out.split("\n")[0]  # the per-item line
    assert "blocked (approval kept)" in out
    assert "0 sent, 1 blocked (approval kept), 0 failed." in out
    store.close()
