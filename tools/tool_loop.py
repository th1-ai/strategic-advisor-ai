"""tools/tool_loop.py - the bounded round-trip loop that answers one question.

Every answer is built by `core.llm.complete()` returning one "step" object per
round, validated against `prompts/schemas/step.json`: either more tool calls,
or a final answer. Same schema-constrained-JSON technique ARCHITECTURE.md
section 3 documents for the `claude-code`/`anthropic` providers - there is no
native tool-calling in this family's LLM contract, so the loop drives it by
hand. Ported from Portfolio Analyst AI, which uses the identical mechanism.

Resumable by design: every round that resolves is cached on
`item.payload["_rounds"]` and replayed on restart with no LLM call and no
tool re-execution - see the module docstring parity with
`tools/ask_engine.py:answer_question` and docs/how-it-works.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.config import Settings
from core.i18n import detect_language
from core.llm import LLMResult, LLMSchemaError, complete
from core.store import Item, Store
from core.templates import build_prompt

from tools.toolkit import ToolContext, call_tool, render_tool_list

#: friendly names for core.i18n's eight supported codes - just for the prose
#: in the language instruction below, never used for detection itself.
_LANGUAGE_NAMES = {"en": "English", "fr": "French", "de": "German", "es": "Spanish",
                   "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish"}


def _language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code, code)


def answer_language_guidance(settings: Settings, question: str) -> dict:
    """Which language to answer one question in (build-repo.md's "reply only
    in the hotel's languages" rule, applied to the ask loop - SIMULATION.md
    Finding 4): the question's own language when it is one of
    ``hotel.languages``; otherwise the hotel's default language, with a note.
    Pure and cheap (no model call) - reuses ``core.i18n.detect_language``,
    the same stopword-vote detector guest-facing replies use."""
    guess = detect_language(question, settings=settings)
    supported = guess.lang in settings.hotel.languages
    default = settings.hotel.default_language
    if supported:
        instruction = (f"Answer in {_language_name(guess.lang)} ({guess.lang}) - the "
                       f"question's own language, one of this hotel's languages.")
    else:
        instruction = (
            f"This question looks like {_language_name(guess.lang)} ({guess.lang}), which is "
            f"not one of this hotel's languages ({', '.join(settings.hotel.languages)}). Answer "
            f"in {_language_name(default)} ({default}) instead, and add one short line noting "
            f"that {_language_name(guess.lang)} is not supported here yet.")
    return {"detected": guess.lang, "supported": supported, "instruction": instruction}


def _load_step_schema() -> dict:
    path = Path(__file__).resolve().parent.parent / "prompts" / "schemas" / "step.json"
    return json.loads(path.read_text(encoding="utf-8"))


STEP_SCHEMA = _load_step_schema()


class ToolLoopExhausted(RuntimeError):
    """Ran out of rounds without a usable final answer. Queue as needs_human."""

    def __init__(self, message: str, tool_calls: list[dict] | None = None,
                 reports: list[dict] | None = None) -> None:
        super().__init__(message)
        self.tool_calls = tool_calls or []
        self.reports = reports or []


@dataclass
class LoopResult:
    reply_markdown: str
    tool_calls: list[dict] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)
    rounds_used: int = 0


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _transcript_entries(tool_calls: list[dict], max_result_chars: int) -> list[dict]:
    out = []
    for call in tool_calls:
        result_text = json.dumps(call["result"], ensure_ascii=False, default=str)
        out.append({"tool": call["name"], "arguments": call["arguments"],
                   "result": _truncate(result_text, max_result_chars)})
    return out


def _replay_cached_rounds(item: Item) -> tuple[list[dict], list[dict], list[dict]]:
    cached_rounds = [dict(r) for r in ((item.payload or {}).get("_rounds") or [])]
    tool_calls: list[dict] = []
    reports: list[dict] = []
    for record in cached_rounds:
        tool_calls.extend(record.get("tool_calls") or [])
        reports.extend(record.get("reports") or [])
    return cached_rounds, tool_calls, reports


def _cache_round(store: Store | None, item: Item, cached_rounds: list[dict],
                 round_no: int, round_tool_calls: list[dict], round_reports: list[dict]) -> None:
    if store is None:
        return
    cached_rounds.append({"round": round_no, "tool_calls": round_tool_calls,
                          "reports": round_reports})
    store.set_fields(item.id, payload={**(item.payload or {}), "_rounds": cached_rounds})


def run_tool_loop(settings: Settings, store: Store | None, item: Item, question: str, ctx: ToolContext,
                  *, provider: str | None = None, fixture_id: str | None = None) -> LoopResult:
    """Run the bounded loop for one question. Raises :class:`LLMSchemaError`
    or :class:`ToolLoopExhausted` on failure - both are caught by
    `tools/ask_engine.py` and turned into a `needs_human` item, never a crash.
    """
    max_rounds = int(settings.agent_get("tool_loop.max_rounds", 6))
    max_result_chars = int(settings.agent_get("tool_loop.max_tool_result_chars", 12000))
    max_arg_chars = int(settings.agent_get("tool_loop.max_tool_arg_echo_chars", 600))

    cached_rounds, tool_calls, reports = _replay_cached_rounds(item)
    start_round = len(cached_rounds) + 1
    if start_round > max_rounds:
        raise ToolLoopExhausted(
            f"no final answer after {max_rounds} round(s) - every round was already "
            f"answered (see item.payload._rounds) but none was a 'final' step.",
            tool_calls=tool_calls, reports=reports)

    # Computed once - the question's own language never changes round to
    # round (Finding 4). Deterministic, no model call, so it is safe to
    # compute even on a resumed/replayed round.
    language = answer_language_guidance(settings, question)

    for round_no in range(start_round, max_rounds + 1):
        last_round = round_no == max_rounds
        prompt = build_prompt(
            "ask", settings=settings,
            item={"question": question, "round": round_no, "of": max_rounds,
                 "last_round": last_round, "answer_language": language,
                 "transcript": _transcript_entries(tool_calls, max_result_chars)},
            persona_name=settings.agent_get("persona.name", "the Strategist"),
            tool_list=render_tool_list(), max_tool_result_chars=max_result_chars)
        round_fixture = f"{fixture_id}-r{round_no}" if fixture_id else None
        result: LLMResult = complete("ask", prompt, STEP_SCHEMA, settings=settings,
                                     provider=provider, store=store, item_id=item.id,
                                     fixture_id=round_fixture)
        step = result.data or {}

        if step.get("step") == "final":
            reply = _parse_final(step.get("final_json"))
            return LoopResult(reply_markdown=reply, tool_calls=tool_calls,
                              reports=reports, rounds_used=round_no)

        if step.get("step") != "tools":
            continue

        round_tool_calls: list[dict] = []
        round_reports: list[dict] = []
        for call in step.get("tool_calls") or []:
            name = call.get("name", "")
            try:
                args = json.loads(call.get("arguments_json") or "{}")
            except json.JSONDecodeError as exc:
                logged = {"error": f"arguments_json was not valid JSON: {exc}"}
                entry = {"name": name, "arguments": call.get("arguments_json", "")[:max_arg_chars],
                        "result": logged}
                tool_calls.append(entry)
                round_tool_calls.append(entry)
                continue
            try:
                out = call_tool(ctx, name, args)
                if name == "generate_report":
                    reports.append(out)
                    round_reports.append(out)
            except Exception as exc:  # noqa: BLE001 - never crash the question
                out = {"error": str(exc)}
            entry = {"name": name,
                    "arguments": _truncate(json.dumps(args, ensure_ascii=False), max_arg_chars),
                    "result": out}
            tool_calls.append(entry)
            round_tool_calls.append(entry)

        _cache_round(store, item, cached_rounds, round_no, round_tool_calls, round_reports)

    raise ToolLoopExhausted(
        f"no final answer after {max_rounds} round(s) - the model never returned "
        f"a 'step': 'final' step even on the last round.",
        tool_calls=tool_calls, reports=reports)


def _parse_final(final_json: Any) -> str:
    if not final_json:
        raise LLMSchemaError("final step had no final_json")
    try:
        data = json.loads(final_json) if isinstance(final_json, str) else final_json
    except json.JSONDecodeError as exc:
        raise LLMSchemaError(f"final_json was not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not data.get("reply_markdown"):
        raise LLMSchemaError("final_json must be an object with a 'reply_markdown' key")
    return str(data["reply_markdown"])
