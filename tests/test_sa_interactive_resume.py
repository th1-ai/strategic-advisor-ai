"""Regression tests for the resumable-question pattern (see
docs/how-it-works.md and Portfolio Analyst AI, which this pattern is ported
from): a stable question id from the wording, and a mid-loop restart that
replays cached rounds instead of re-asking the model. No network, no
credentials: only the `interactive` provider's own file protocol is
exercised, redirected into `tmp_path` so this never touches the real repo's
`data/pending/`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

import core.llm as core_llm
from core.config import load_settings
from core.llm import LLMPendingInteractive
from core.store import Store

from tools.ask_engine import answer_question, question_external_id

AS_OF = "2026-06-15"


def _settings(*, provider: str = "interactive"):
    return load_settings(provider=provider, mode="shadow")


def _redirect_pending(monkeypatch, tmp_path) -> Path:
    """Point core.llm's sub_data_dir("pending") at tmp_path/pending instead
    of this repo's real data/pending/. Patched inside core.llm's own module
    namespace, since `_interactive()` calls the `sub_data_dir` it imported
    at load time."""
    def _sub_data_dir(name: str) -> Path:
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(core_llm, "sub_data_dir", _sub_data_dir)
    return tmp_path / "pending"


def test_rerunning_the_same_ask_command_resumes_the_same_item(tmp_path, monkeypatch):
    _redirect_pending(monkeypatch, tmp_path)
    settings = _settings()
    store = Store(settings, path=tmp_path / "resume1.db")
    question = "Why is F&B revenue down this month?"
    external_id = question_external_id(question, AS_OF)

    with pytest.raises(LLMPendingInteractive) as first:
        answer_question(settings, store, question, source="cli", external_id=external_id,
                        provider="interactive", as_of=AS_OF)
    with pytest.raises(LLMPendingInteractive) as second:
        answer_question(settings, store, question, source="cli", external_id=external_id,
                        provider="interactive", as_of=AS_OF)

    item = store.get_by_external("cli", external_id)
    assert item is not None and item.review_status == "new"
    assert str(first.value) == str(second.value)  # same pending id both times
    store.close()


def test_answering_the_pending_prompt_lets_the_next_call_finish(tmp_path, monkeypatch):
    pending_dir = _redirect_pending(monkeypatch, tmp_path)
    settings = _settings()
    store = Store(settings, path=tmp_path / "resume2.db")
    question = "How is the F&B revenue project doing?"
    external_id = question_external_id(question, AS_OF)

    with pytest.raises(LLMPendingInteractive) as exc:
        answer_question(settings, store, question, source="cli", external_id=external_id,
                        provider="interactive", as_of=AS_OF)
    pid = exc.value.args[0] if exc.value.args else None
    answer_files = list(pending_dir.glob("*.answer.json"))
    assert len(answer_files) == 0
    prompt_files = list(pending_dir.glob("*.prompt.md"))
    assert len(prompt_files) == 1

    import json
    answer_path = prompt_files[0].with_suffix("").with_suffix(".answer.json")
    answer_path.write_text(json.dumps({
        "step": "final", "final_json": json.dumps({"reply_markdown": "It is recovering."})}),
        encoding="utf-8")

    item, did_work = answer_question(settings, store, question, source="cli",
                                     external_id=external_id, provider="interactive", as_of=AS_OF)
    assert did_work is True
    assert item.review_status == "skipped"
    assert item.draft["reply_markdown"] == "It is recovering."
    store.close()


def test_a_mid_loop_restart_never_re_executes_a_cached_round(tmp_path, monkeypatch):
    """The `_rounds` cache (tools/tool_loop.py) means a round that already
    ran its tools is replayed, not repeated - repeating it would double-log
    events and could double-call something with a side effect in a
    different agent."""
    pending_dir = _redirect_pending(monkeypatch, tmp_path)
    settings = _settings()
    store = Store(settings, path=tmp_path / "resume3.db")
    question = "List the open projects, then tell me if any need my attention."
    external_id = question_external_id(question, AS_OF)

    with pytest.raises(LLMPendingInteractive):
        answer_question(settings, store, question, source="cli", external_id=external_id,
                        provider="interactive", as_of=AS_OF)

    import json
    prompt_path = next(pending_dir.glob("*.prompt.md"))
    answer_path = prompt_path.with_suffix("").with_suffix(".answer.json")
    answer_path.write_text(json.dumps({
        "step": "tools",
        "tool_calls": [{"name": "list_projects", "arguments_json": json.dumps({"status": None})}]}),
        encoding="utf-8")

    with pytest.raises(LLMPendingInteractive):
        answer_question(settings, store, question, source="cli", external_id=external_id,
                        provider="interactive", as_of=AS_OF)

    item = store.get_by_external("cli", external_id)
    cached_rounds = (item.payload or {}).get("_rounds") or []
    assert len(cached_rounds) == 1
    assert cached_rounds[0]["tool_calls"][0]["name"] == "list_projects"
    store.close()
