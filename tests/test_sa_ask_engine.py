"""Tests for tools/ask_engine.py - the whole loop for one question, with
provider=mock against fixtures/expected/ask/. No network, no credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.store import Store

from tools import project as sa_project
from tools.ask_engine import answer_question, question_external_id
from tools.scan import run_daily_scan
from tools.tool_loop import answer_language_guidance

AS_OF = "2026-06-15"


def _settings(*, dry_run: bool = False, mode: str = "shadow"):
    return load_settings(provider="mock", mode=mode, dry_run=dry_run)


def test_a_plain_clean_answer_is_skipped_with_no_human_needed(tmp_path):
    store = Store(_settings(), path=tmp_path / "ask1.db")
    item, did_work = answer_question(_settings(), store, "What did today's scan find?",
                                     source="test", external_id="q-daily-pulse",
                                     provider="mock", as_of=AS_OF)
    assert did_work is True
    assert item.review_status == "skipped"
    assert "anomaly" in item.draft["reply_markdown"].lower()
    store.close()


def test_a_report_question_lands_in_pending_review(tmp_path):
    store = Store(_settings(), path=tmp_path / "ask2.db")
    item, _ = answer_question(_settings(), store,
                              "Build me a report on F&B performance this month with a chart",
                              source="test", external_id="q-fnb-report", provider="mock", as_of=AS_OF)
    assert item.review_status == "pending_review"
    assert len(item.draft["reports"]) == 1
    store.close()


def test_a_multi_round_question_replays_correctly(tmp_path):
    store = Store(_settings(), path=tmp_path / "ask3.db")
    item, _ = answer_question(_settings(), store, "How did the spa upsell project turn out?",
                              source="test", external_id="q-spa-upsell-result", provider="mock",
                              as_of=AS_OF)
    assert item.review_status == "skipped"
    assert len(item.draft["tool_calls"]) == 2
    assert item.draft["tool_calls"][0]["name"] == "list_projects"
    assert item.draft["tool_calls"][1]["name"] == "get_project_detail"
    store.close()


def test_asking_the_same_question_twice_is_idempotent(tmp_path):
    store = Store(_settings(), path=tmp_path / "ask4.db")
    item1, did_work1 = answer_question(_settings(), store, "What did today's scan find?",
                                       source="test", external_id="q-daily-pulse", provider="mock",
                                       as_of=AS_OF)
    item2, did_work2 = answer_question(_settings(), store, "What did today's scan find?",
                                       source="test", external_id="q-daily-pulse", provider="mock",
                                       as_of=AS_OF)
    assert did_work1 is True
    assert did_work2 is False
    assert item1.id == item2.id
    store.close()


def test_question_id_is_stable_from_wording_not_random(tmp_path):
    a = question_external_id("Why is F&B revenue down?", AS_OF)
    b = question_external_id("  WHY is F&B  revenue down?  ", AS_OF)
    c = question_external_id("Why is F&B revenue down?", "2026-06-16")
    assert a == b
    assert a != c
    assert a.startswith(f"ask-{AS_OF}-")


def test_dry_run_answers_but_writes_nothing(tmp_path):
    store = Store(_settings(), path=tmp_path / "ask5.db")
    item, did_work = answer_question(_settings(dry_run=True), store, "What did today's scan find?",
                                     source="test", external_id="q-daily-pulse", provider="mock",
                                     as_of=AS_OF)
    assert did_work is True
    assert item.review_status == "skipped"
    # nothing was actually inserted - a second real call still starts fresh
    real_item, real_did_work = answer_question(_settings(), store, "What did today's scan find?",
                                               source="test", external_id="q-daily-pulse",
                                               provider="mock", as_of=AS_OF)
    assert real_did_work is True
    store.close()


# --------------------------------------------------------------------------
# SIMULATION.md Finding 4 - prompts/ask.md answers in the question's own
# language when it is one of hotel.languages, else the default with a note.
# config/hotel.example.yaml (what tests sandbox onto): languages [en, pt, es].
# --------------------------------------------------------------------------
def test_answer_language_guidance_uses_the_questions_own_supported_language():
    settings = _settings()
    assert "pt" in settings.hotel.languages
    question = "Olá, gostaria de saber o resultado do scan para o quarto, obrigado."
    guidance = answer_language_guidance(settings, question)
    assert guidance["detected"] == "pt"
    assert guidance["supported"] is True
    assert "pt" in guidance["instruction"]
    assert "not one of this hotel's languages" not in guidance["instruction"]


def test_answer_language_guidance_falls_back_to_the_default_for_an_unsupported_language():
    settings = _settings()
    assert "de" not in settings.hotel.languages
    question = "Guten Tag, bitte, wie war das Ergebnis des heutigen Scans? Vielen Dank."
    guidance = answer_language_guidance(settings, question)
    assert guidance["detected"] == "de"
    assert guidance["supported"] is False
    assert settings.hotel.default_language in guidance["instruction"]
    assert "not one of this hotel's languages" in guidance["instruction"]


def test_ask_prompt_carries_the_language_instruction_for_a_portuguese_question():
    """prompts/ask.md's System section states the language rule, and the
    built prompt's Item block carries the Portuguese question's own computed
    guidance - both required for the model to actually follow it."""
    from core.templates import build_prompt

    settings = _settings()
    question = "Olá, gostaria de saber o resultado do scan para o quarto, obrigado."
    prompt = build_prompt(
        "ask", settings=settings,
        item={"question": question, "round": 1, "of": 6, "last_round": False,
             "answer_language": answer_language_guidance(settings, question), "transcript": []},
        persona_name="the Strategist", tool_list="")
    assert "LANGUAGE" in prompt.system
    assert "answer_language" in prompt.system
    assert '"detected": "pt"' in prompt.user
    assert '"supported": true' in prompt.user


def test_ask_can_see_a_project_the_scan_actually_opened(tmp_path):
    store = Store(_settings(), path=tmp_path / "ask6.db")
    sa_project.migrate_schema(store)
    run_daily_scan(_settings(), store, provider="mock", as_of=AS_OF)
    item, _ = answer_question(_settings(), store, "What strategic projects are open right now?",
                              source="test", external_id="q-open-projects", provider="mock",
                              as_of=AS_OF)
    tool_result = item.draft["tool_calls"][0]["result"]
    assert tool_result["count"] >= 1
    store.close()
