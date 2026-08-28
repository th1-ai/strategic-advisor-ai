"""Smoke test for `make demo` (tools/demo.py) - the whole loop on the bundled
fixtures, provider=mock, mode=shadow, no credentials. This is what
onboarding runs first; if this test is red, `make demo` is red.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tools.demo as demo_module


def test_demo_runs_clean_and_prints_demo_ok(capsys):
    code = demo_module.main()
    assert code == 0
    out = capsys.readouterr().out
    assert "DEMO OK" in out
    assert "0 sent (shadow)" in out


def test_demo_never_sends_anything(capsys):
    demo_module.main()
    out = capsys.readouterr().out
    assert "blocked" in out.lower()  # the gated email send was attempted and blocked
