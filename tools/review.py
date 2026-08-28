#!/usr/bin/env python3
"""tools/review.py - work the review queue: list / show / approve / edit /
reject / send / stale.

    python3 tools/review.py list [--status pending_review]
    python3 tools/review.py show <item_id>
    python3 tools/review.py approve <item_id> [--effective 2026-09-15] [--note "..."]
    python3 tools/review.py edit <item_id> --body-file draft.txt [--subject "..."]
    python3 tools/review.py edit <item_id> --brief-file brief.txt
    python3 tools/review.py reject <item_id> --reason "..."
    python3 tools/review.py retry <item_id>
    python3 tools/review.py send

The queue holds exactly one kind of item: `advisor_step` - the `email`,
`marketing_action` and `pos_update` steps of a strategic project, the only
three step kinds that reach outside the agent's own database (see
docs/how-it-works.md). Every command here is a thin wrapper around
`tools/project.py`'s step-aware functions, so a project's `steps_json` and
its mirrored `items` row can never drift apart - this file never writes
`approved`/`edited`/`rejected` itself, `tools/project.py` does, through
`core.review`. `pos_update` items never appear in `send` (approving one only
schedules it - see `python3 tools/project.py apply-pos`). Nothing here
bypasses `mode: shadow` - see docs/safety.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.review import list_queue, retry, show, stale_backlog  # noqa: E402
from core.store import Store, StoreError  # noqa: E402

from tools import project as sa_project  # noqa: E402


def _step_id_for(item) -> str:
    payload = item.payload or {}
    return f"{payload.get('project_id')}:{payload.get('seq')}"


def _print_item_line(item) -> None:
    payload = item.payload or {}
    title = payload.get("title", "")
    # `item.is_sample` is set by core (core/store.py) for anything read
    # through a mock adapter outside `make demo` - see docs/integrations.md
    # "Sample data is labelled". A human working the real queue must never
    # mistake a shipped fixture for a real project step.
    mark = "[SAMPLE DATA] " if item.is_sample else ""
    print(f"  {mark}{item.id}  {item.review_status:<14} {payload.get('kind', '-'):<16} "
         f"{title[:45]}")


def cmd_list(store: Store, args) -> int:
    items = list_queue(store, status=args.status, kind="advisor_step", limit=args.limit)
    if not items:
        print("Nothing is waiting for you.")
        return 0
    print(f"{len(items)} item(s) waiting:\n")
    for item in items:
        _print_item_line(item)
    print("\nRun `python3 tools/review.py show <id>` for the full draft.")
    return 0


def cmd_show(store: Store, args) -> int:
    try:
        detail = show(store, args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if (detail["item"].get("payload") or {}).get("_sample"):
        print("[SAMPLE DATA] this item came from a project opened while at least one CSV "
             "source was not connected - see docs/integrations.md.\n")
    print(json.dumps(detail, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_approve(store: Store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    sa_project.approve_step(store, _step_id_for(item), note=args.note or "", effective=args.effective)
    print(f"approved {item.id} - {'scheduled' if args.effective else 'now in the send queue'}")
    return 0


def cmd_edit(store: Store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    changes: dict = {}
    if args.body_file:
        changes["body"] = Path(args.body_file).read_text(encoding="utf-8")
    if args.brief_file:
        changes["brief"] = Path(args.brief_file).read_text(encoding="utf-8")
    if args.subject:
        changes["subject"] = args.subject
    if not changes:
        print("error: edit needs --body-file or --brief-file", file=sys.stderr)
        return 1
    sa_project.edit_step(store, _step_id_for(item), changes, note=args.note or "")
    print(f"edited {item.id} - now in the send queue")
    return 0


def cmd_reject(store: Store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    sa_project.reject_step(store, _step_id_for(item), reason=args.reason or "")
    print(f"rejected {item.id}")
    return 0


def cmd_retry(store: Store, args) -> int:
    item = retry(store, args.id)
    print(f"queued {item.id} for another send attempt")
    return 0


def cmd_send(store: Store, settings, args) -> int:
    candidates = store.list_items(status=["approved", "edited"], kind="advisor_step", limit=args.limit)
    sendable = [it for it in candidates if (it.payload or {}).get("kind") in ("email", "marketing_action")]
    if not sendable:
        print("Nothing approved or edited is waiting to send. (A scheduled `pos_update` is "
             "applied separately - see `python3 tools/project.py apply-pos`.)")
        return 0
    # A shadow block is correct, by-design behaviour, not a failure - counting
    # it as "failed" (SIMULATION.md Finding 5) reads as something broke, when
    # nothing did: the approval was kept and nothing left the building on
    # purpose. Only a real `AdapterError` counts as failed and fails the exit
    # code; "blocked (approval kept)" always exits 0.
    sent, blocked, failed = 0, 0, 0
    for item in sendable:
        _, outcome = sa_project.send_step(store, settings, _step_id_for(item))
        print(f"{item.id}: {outcome}")
        if outcome == "sent":
            sent += 1
        elif outcome.startswith("blocked"):
            blocked += 1
        else:
            failed += 1
    print(f"\n{sent} sent, {blocked} blocked (approval kept), {failed} failed.")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="what is waiting for a human")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="full detail for one item")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve the draft unchanged")
    p_approve.add_argument("id")
    p_approve.add_argument("--effective", default=None, help="pos_update only: schedule date")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit", help="rewrite the draft, then queue it")
    p_edit.add_argument("id")
    p_edit.add_argument("--body-file", default=None)
    p_edit.add_argument("--brief-file", default=None)
    p_edit.add_argument("--subject", default=None)
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject", help="discard the draft")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", "--note", dest="reason", default="")

    p_retry = sub.add_parser("retry", help="re-queue a failed send")
    p_retry.add_argument("id")

    p_send = sub.add_parser("send", help="send everything approved or edited (email / marketing_action)")
    p_send.add_argument("--limit", type=int, default=20)

    sub.add_parser("stale", help="go-live step: mark everything still un-sent as stale")

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    sa_project.migrate_schema(store)
    try:
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve":
            return cmd_approve(store, args)
        if args.command == "edit":
            return cmd_edit(store, args)
        if args.command == "reject":
            return cmd_reject(store, args)
        if args.command == "retry":
            return cmd_retry(store, args)
        if args.command == "send":
            return cmd_send(store, settings, args)
        if args.command == "stale":
            moved = stale_backlog(store)
            print(f"marked {len(moved)} item(s) stale. Nothing from before go-live will be sent.")
            return 0
        parser.error(f"unknown command {args.command}")
        return 2
    except (StoreError, sa_project.ProjectError) as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
