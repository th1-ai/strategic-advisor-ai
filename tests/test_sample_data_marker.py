"""`tools/review.py` `list` and `show` must print a `[SAMPLE DATA]` marker
for any `advisor_step` item whose `item.is_sample` is True, exactly like
every other repo in the family (`docs/integrations.md` "Sample data is
labelled"). `core.store.Item.is_sample` reads the `_sample` key that
`tools/project.py:mirror_gated_step` sets on every gated step's mirrored
`items` row whenever the project that opened it had any CSV source
unconnected (SIMULATION.md Finding 1) - this module only has to prove the
review CLI *shows* that, not re-derive the tagging itself.

`tests/conftest.py`'s autouse `_isolated_repo` fixture already points
`AGENT_CONFIG_DIR` / `AGENT_REPO_ROOT` at throwaway copies for any module
not named `test_core_*`, so this file needs no config plumbing of its own.
"""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402
from tools.review import cmd_list, cmd_show  # noqa: E402


def _sample_item(tmp_path):
    settings = load_settings(provider="mock", mode="shadow")
    store = Store(settings, path=tmp_path / "test.db")
    item = store.upsert_item(
        "advisor", "p-sample:2", kind="advisor_step",
        payload={"project_id": "p-sample", "project_title": "F&B revenue dip", "seq": 2,
                "kind": "email", "title": "Chase the F&B pace", "_sample": True},
        intent="email")
    store.set_fields(item.id, draft={"subject": "x", "body": "y"})
    item = store.transition(item.id, "pending_review", "agent") or item
    return store, item


def test_advisor_step_tagged_sample_reports_is_sample_true(tmp_path):
    store, item = _sample_item(tmp_path)
    store.close()
    assert item.payload.get("_sample") is True
    assert item.is_sample is True


def test_review_list_prints_sample_data_marker(tmp_path, capsys):
    store, _ = _sample_item(tmp_path)
    capsys.readouterr()
    assert cmd_list(store, Namespace(status=None, limit=50)) == 0
    store.close()
    assert "[SAMPLE DATA]" in capsys.readouterr().out


def test_review_show_prints_sample_data_marker(tmp_path, capsys):
    store, item = _sample_item(tmp_path)
    capsys.readouterr()
    assert cmd_show(store, Namespace(id=item.id)) == 0
    store.close()
    assert "[SAMPLE DATA]" in capsys.readouterr().out
