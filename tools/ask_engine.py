"""tools/ask_engine.py - turn one question into a logged, answered item.

`answer_question()` is the whole thing for one question, shared by
`tools/run.py` (real use) and `tools/demo.py` (the fixtures). Every question
becomes a row in `items` (kind="question") whether asked once from the
command line or replayed from `fixtures/inbound/`. A plain text answer needs
nobody: it goes straight to `skipped` (informational - already delivered to
whoever asked). An answer that produced a report (`generate_report`) goes to
`pending_review` so a person looks it over first. A question the loop could
not answer goes to `needs_human`.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from core.config import Settings
from core.llm import LLMPendingInteractive, LLMSchemaError
from core.store import Item, Store

from tools.tool_loop import LoopResult, ToolLoopExhausted, run_tool_loop
from tools.toolkit import ToolContext

RATE_LIMIT_MESSAGE = (
    "The Strategist has answered its daily quota of questions "
    "({limit}). This is a local safety cap, not a real outage - raise "
    "`rate_limit.max_questions_per_day` in config/agent.yaml, or wait until "
    "tomorrow. Nothing was asked or logged.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def question_external_id(question: str, day: str | None = None) -> str:
    """A stable ``external_id`` for an ad-hoc question: a hash of the
    normalized question text plus the day, not a random uuid, so re-running
    the exact same command resumes the same item instead of orphaning
    whatever was already answered. See tools/tool_loop.py's round cache for
    how a round-in-progress survives the restart too."""
    day = day or datetime.now(timezone.utc).date().isoformat()
    normalized = re.sub(r"\s+", " ", question.strip().lower())
    digest = hashlib.sha256(f"{day}\n{normalized}".encode()).hexdigest()[:12]
    return f"ask-{day}-{digest}"


def check_rate_limit(settings: Settings, store: Store) -> tuple[bool, int, int]:
    """``(ok, asked_today, limit)``. Fails OPEN: an error reading/writing the
    counter never blocks a real question. ``--dry-run`` still reads the real
    counter but never increments it."""
    limit = int(settings.agent_get("rate_limit.max_questions_per_day", 200))
    key = f"questions_asked:{datetime.now(timezone.utc).date().isoformat()}"
    try:
        asked = int(store.get(key, 0) or 0)
    except Exception:  # noqa: BLE001 - fail open, never block on a bookkeeping bug
        return True, 0, limit
    if asked >= limit:
        return False, asked, limit
    if not settings.dry_run:
        try:
            store.set(key, asked + 1)
        except Exception:  # noqa: BLE001
            pass
    return True, asked, limit


def _placeholder_item(source: str, external_id: str, question: str, asked_by: str) -> Item:
    now = _now()
    return Item(id=f"dryrun-{uuid.uuid4().hex[:10]}", kind="question", source=source,
               external_id=external_id, payload={"question": question, "asked_by": asked_by,
                                                  "asked_at": now}, created_at=now, updated_at=now)


def answer_question(settings: Settings, store: Store, question: str, *,
                    source: str = "cli", external_id: str | None = None,
                    asked_by: str = "owner", provider: str | None = None,
                    as_of: str | None = None) -> tuple[Item, bool]:
    """Answer one question end to end and queue the result. Idempotent on
    ``(source, external_id)``: an item that already left ``new`` is returned
    untouched (``did_work=False``)."""
    external_id = external_id or uuid.uuid4().hex
    record_store = None if settings.dry_run else store

    if not settings.dry_run:
        item = store.upsert_item(source, external_id, kind="question",
                                 payload={"question": question, "asked_by": asked_by,
                                         "asked_at": _now()})
        if item.review_status != "new":
            return item, False
    else:
        item = _placeholder_item(source, external_id, question, asked_by)

    ok, asked_today, limit = check_rate_limit(settings, store)
    if not ok:
        message = RATE_LIMIT_MESSAGE.format(limit=limit)
        draft = {"reply_markdown": message, "tool_calls": [], "reports": []}
        if settings.dry_run:
            item.draft, item.review_status = draft, "needs_human"
            return item, True
        store.set_fields(item.id, draft=draft)
        updated = store.transition(item.id, "needs_human", actor="agent",
                                   detail={"reason": "rate_limited", "asked_today": asked_today})
        return updated, True

    ctx = ToolContext.build(settings, store, today=as_of)
    try:
        result = run_tool_loop(settings, record_store, item, question, ctx,
                               provider=provider, fixture_id=external_id)
    except LLMPendingInteractive:
        raise  # let tools/run.py print the pending prompt and exit 3
    except (LLMSchemaError, ToolLoopExhausted) as exc:
        attempted = {"tool_calls": getattr(exc, "tool_calls", []),
                    "reports": getattr(exc, "reports", [])}
        if settings.dry_run:
            item.payload = {**(item.payload or {}), "_last_attempt": attempted}
            item.error, item.review_status = str(exc), "needs_human"
            return item, True
        store.set_fields(item.id, payload={**(item.payload or {}), "_last_attempt": attempted},
                         error=str(exc))
        updated = store.transition(item.id, "needs_human", actor="agent",
                                   detail={"reason": "loop_failed", "error": str(exc)[:300],
                                          "tool_calls": len(attempted["tool_calls"])})
        return updated, True

    draft = {"reply_markdown": result.reply_markdown, "tool_calls": result.tool_calls,
            "reports": result.reports}
    status = _terminal_status(result)
    if settings.dry_run:
        item.draft, item.review_status = draft, status
        return item, True

    store.set_fields(item.id, draft=draft)
    updated = store.transition(item.id, status, actor="agent",
                               detail={"rounds": result.rounds_used,
                                      "tool_calls": len(result.tool_calls),
                                      "reports": len(result.reports)})
    return updated, True


def _terminal_status(result: LoopResult) -> str:
    """Where a cleanly-finished question lands. `auto_sent` never applies
    here: nothing about answering a question is a guarded write - see
    docs/how-it-works.md."""
    if not result.reply_markdown.strip():
        return "needs_human"
    return "pending_review" if result.reports else "skipped"
