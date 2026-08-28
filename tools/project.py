#!/usr/bin/env python3
"""tools/project.py - open, inspect and advance a strategic project.

    python3 tools/project.py list [--status active]
    python3 tools/project.py show <project_id>
    python3 tools/project.py approve-step <step_id> [--effective 2026-09-15]
    python3 tools/project.py edit-step <step_id> --body-file draft.txt [--subject "..."]
    python3 tools/project.py reject-step <step_id> --reason "..."
    python3 tools/project.py send-step <step_id>
    python3 tools/project.py apply-pos <step_id>
    python3 tools/project.py change-wait <step_id> --days 5
    python3 tools/project.py fast-forward-wait <step_id> --reply "..."
    python3 tools/project.py checkpoint <step_id> --confirm
    python3 tools/project.py checkpoint <step_id> --pushback --days 3
    python3 tools/project.py tracker-status <step_id>
    python3 tools/project.py fast-forward-tracker <step_id>
    python3 tools/project.py measure <step_id> --price-from 28 --price-to 31 \
        --baseline-month 2026-06 --target-month 2026-08
    python3 tools/project.py resolve <project_id> --measured 2016 --impact "+EUR 2,016/mo measured"
    python3 tools/project.py abandon <project_id> --reason "..."

A project is a row in `advisor_projects` with its ordered steps stored as one
JSON array (`steps_json`) - see docs/how-it-works.md "Design decisions" 11:
one column instead of a second table, because the step list is always read
and written as a whole and nothing needs a cross-project step query.

Only `email`, `marketing_action` and `pos_update` steps reach outside the
agent's own database. Those three, and only those three, are mirrored into
the shared `items` table (`kind="advisor_step"`) so `core.review`'s guard and
FSM govern them exactly like every other guarded write in this family -
`approve-step` / `edit-step` / `reject-step` here are thin wrappers around
`core.review.approve/edit/reject`. Everything else (`analysis`, `wait`,
`checkpoint`, `tracker`, `measure`, `resolve`) is a step a human still
explicitly advances with a command below, just not through that FSM,
because nothing leaves the building.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_email, get_messaging  # noqa: E402
from core.adapters.base import AdapterError, AdapterNotImplemented  # noqa: E402
from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.review import WriteBlocked, approve, edit, reject  # noqa: E402
from core.review import assert_write_allowed  # noqa: E402
from core.store import Store, StoreError, utcnow  # noqa: E402

from tools import data as sa_data  # noqa: E402
from tools import projects_engine as pe  # noqa: E402

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS advisor_projects (
  id               TEXT PRIMARY KEY,
  title            TEXT NOT NULL,
  mode             TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'active',
  metric_key       TEXT,
  target_label     TEXT,
  projected_impact TEXT,
  measured_impact  REAL,
  summary          TEXT,
  steps_json       TEXT NOT NULL DEFAULT '[]',
  created_on       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  resolved_on      TEXT,
  seeded           INTEGER NOT NULL DEFAULT 0,
  is_sample        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_advisor_projects_status ON advisor_projects (status, mode);

CREATE TABLE IF NOT EXISTS advisor_signals (
  id             TEXT PRIMARY KEY,
  scan_date      TEXT NOT NULL,
  verdict        TEXT NOT NULL,
  headline       TEXT NOT NULL,
  checklist_json TEXT,
  narrative      TEXT,
  created_at     TEXT NOT NULL,
  UNIQUE (scan_date)
);
"""


def migrate_schema(store: Store) -> None:
    store.migrate(SCHEMA_SQL)
    # Migration for a database created before is_sample existed - the CREATE
    # TABLE IF NOT EXISTS above never adds a column to an already-existing
    # table (same pattern Event & Wedding AI uses for the same reason).
    cols = {r["name"] for r in store.db.execute("PRAGMA table_info(advisor_projects)").fetchall()}
    if "is_sample" not in cols:
        store.db.execute("ALTER TABLE advisor_projects ADD COLUMN is_sample INTEGER NOT NULL DEFAULT 0")


class ProjectError(ValueError):
    """A project or step command could not do what was asked."""


# --------------------------------------------------------------------------
# project rows
# --------------------------------------------------------------------------
def _row_to_project(row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else []
    return {"id": row["id"], "title": row["title"], "mode": row["mode"], "status": row["status"],
           "metric_key": row["metric_key"], "target_label": row["target_label"],
           "projected_impact": row["projected_impact"], "measured_impact": row["measured_impact"],
           "summary": row["summary"], "steps": json.loads(row["steps_json"] or "[]"),
           "created_on": row["created_on"], "updated_at": row["updated_at"],
           "resolved_on": row["resolved_on"], "seeded": bool(row["seeded"]),
           # `is_sample` may be missing on a row read before migrate_schema()'s
           # ALTER TABLE ran this process - default to False, never crash.
           "is_sample": bool(row["is_sample"]) if "is_sample" in keys else False}


def get_project(store: Store, project_id: str) -> dict:
    row = store.db.execute("SELECT * FROM advisor_projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise ProjectError(f"no project {project_id}")
    return _row_to_project(row)


def list_projects(store: Store, *, status: str | None = None) -> list[dict]:
    if status:
        rows = store.db.execute(
            "SELECT * FROM advisor_projects WHERE status=? ORDER BY created_on", (status,)).fetchall()
    else:
        rows = store.db.execute("SELECT * FROM advisor_projects ORDER BY created_on").fetchall()
    return [_row_to_project(r) for r in rows]


def _save_project(store: Store, project: dict) -> None:
    project["updated_at"] = utcnow()
    store.db.execute(
        "UPDATE advisor_projects SET title=?, status=?, projected_impact=?, measured_impact=?, "
        "summary=?, steps_json=?, updated_at=?, resolved_on=? WHERE id=?",
        (project["title"], project["status"], project["projected_impact"],
         project["measured_impact"], project["summary"], json.dumps(project["steps"]),
         project["updated_at"], project["resolved_on"], project["id"]))


def seed_project(store: Store, *, project_id: str, title: str, mode: str, status: str,
                 metric_key: str, target_label: str, projected_impact: str, summary: str,
                 steps: list[dict], created_on: str, resolved_on: str | None = None,
                 measured_impact: float | None = None) -> dict:
    """Insert a project with a fully custom, hand-built step list - for
    `tools/demo.py` only, to show a project mid-flight or already resolved
    without waiting for the scan to discover it organically (the same
    seeded-storyline idea the source demo uses, see docs/how-it-works.md).
    Every seeded row is flagged `seeded: true`. Does NOT mirror any step
    into the review queue - call `mirror_gated_step` yourself for whichever
    single step should actually be live and awaiting a human."""
    now = utcnow()
    store.db.execute(
        "INSERT INTO advisor_projects (id, title, mode, status, metric_key, target_label, "
        "projected_impact, measured_impact, summary, steps_json, created_on, updated_at, "
        "resolved_on, seeded) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (project_id, title, mode, status, metric_key, target_label, projected_impact,
         measured_impact, summary, json.dumps(steps), created_on, now, resolved_on, 1))
    store.record_event(None, "agent", "project_seeded", {"project_id": project_id, "title": title})
    return get_project(store, project_id)

def open_project(store: Store, *, mode: str, title: str, metric_key: str, target_label: str,
                 projected_impact: str, summary: str, templates: dict[str, list[str]],
                 payloads: dict[str, dict], titles: dict[str, str] | None = None,
                 seeded: bool = False, project_id: str | None = None,
                 is_sample: bool = False) -> dict:
    """Open a new project with a deterministic step template for ``mode``
    (config/agent.yaml: project_templates) - the model never chooses the
    steps, only the prose inside them (task draft_project). ``payloads``
    (keyed by kind) must include ``"analysis"``; step 1 completes
    immediately with that content (it always renders, per spec) and step 2
    is armed right after - mirrored into the review queue if it is gated,
    using whatever payload was seeded for its kind.

    ``is_sample`` - True when the scan that opened this project had ANY
    CSV-fallback source unconnected (`tools/scan.py:run_daily_scan`), even
    if the specific signal that fired was real: the checklist and analysis
    text still drew on whatever else was fixture. Stored on the project row
    and mirrored onto every gated step's `items` payload as `_sample`, which
    is exactly the key `core.store.Item.is_sample` already reads - `[SAMPLE]`
    then shows up in `tools/project.py`/`tools/review.py` list and show for
    free (SIMULATION.md Finding 1)."""
    kinds = templates.get(mode, pe.DEFAULT_TEMPLATES.get(mode, []))
    if not kinds:
        raise ProjectError(f"no step template for mode '{mode}'")
    project_id = project_id or uuid.uuid4().hex[:12]
    now = utcnow()
    steps = pe.build_steps(kinds, titles, payloads)
    steps[0]["status"] = "done"
    next_step = pe.arm_next(steps, 1)
    store.db.execute(
        "INSERT INTO advisor_projects (id, title, mode, status, metric_key, target_label, "
        "projected_impact, measured_impact, summary, steps_json, created_on, updated_at, "
        "resolved_on, seeded, is_sample) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (project_id, title, mode, "active", metric_key, target_label, projected_impact,
         None, summary, json.dumps(steps), now, now, None, int(seeded), int(is_sample)))
    store.record_event(None, "agent", "project_opened",
                       {"project_id": project_id, "title": title, "mode": mode,
                        "is_sample": is_sample})
    project = get_project(store, project_id)
    if next_step is not None:
        mirror_gated_step(store, project, next_step)
    return project


# --------------------------------------------------------------------------
# mirroring gated steps into the shared `items` table
# --------------------------------------------------------------------------
def _step_external_id(project_id: str, seq: int) -> str:
    return f"{project_id}:{seq}"


def find_step_by_id(store: Store, step_id: str) -> tuple[dict, dict]:
    """``step_id`` is ``<project_id>:<seq>``. Returns ``(project, step)``."""
    if ":" not in step_id:
        raise ProjectError(f"'{step_id}' is not a step id - use '<project_id>:<seq>' "
                           f"(see `python3 tools/project.py show <project_id>`)")
    project_id, _, seq_text = step_id.partition(":")
    project = get_project(store, project_id)
    step = pe.find_step(project["steps"], int(seq_text))
    if step is None:
        raise ProjectError(f"no step {seq_text} on project {project_id}")
    return project, step


def mirror_gated_step(store: Store, project: dict, step: dict) -> None:
    """Create (or refresh) the `items` row a gated step (`email`,
    `marketing_action`, `pos_update`) is decided through. Called whenever
    such a step becomes `awaiting_approval`."""
    if step["kind"] not in pe.GATED_KINDS:
        return
    external_id = _step_external_id(project["id"], step["seq"])
    item = store.upsert_item("advisor", external_id, kind="advisor_step",
                             payload={"project_id": project["id"], "project_title": project["title"],
                                     "seq": step["seq"], "kind": step["kind"], "title": step["title"],
                                     # same key core.store.Item.is_sample reads (Finding 1)
                                     "_sample": bool(project.get("is_sample", False))},
                             intent=step["kind"])
    if item.review_status == "new":
        store.set_fields(item.id, draft=step.get("payload") or {})
        store.transition(item.id, "pending_review", "agent",
                         {"project_id": project["id"], "seq": step["seq"]})


def get_mirror_item(store: Store, project: dict, step: dict):
    item = store.get_by_external("advisor", _step_external_id(project["id"], step["seq"]))
    if item is None:
        raise ProjectError(f"step {project['id']}:{step['seq']} has no review item yet - "
                           f"it is not awaiting approval.")
    return item


def _persist_and_mirror(store: Store, project: dict, changed: dict | None) -> None:
    """Save the project, and if the newly-armed step is gated, mirror it."""
    _save_project(store, project)
    if changed is not None and changed["kind"] in pe.GATED_KINDS:
        mirror_gated_step(store, project, changed)


# --------------------------------------------------------------------------
# gated verbs: approve / edit / reject / send-step / apply-pos
# --------------------------------------------------------------------------
def approve_step(store: Store, step_id: str, *, note: str = "", effective: str | None = None) -> dict:
    project, step = find_step_by_id(store, step_id)
    item = get_mirror_item(store, project, step)
    approve(store, item.id, note=note)
    if step["kind"] == "pos_update":
        if not effective:
            raise ProjectError("pos_update needs --effective YYYY-MM-DD")
        step["status"] = "scheduled"
        step["payload"] = {**step.get("payload", {}), "effective": effective}
        store.record_event(item.id, "human", "pos_scheduled",
                           {"effective": effective, "item": step["payload"].get("item")})
        _save_project(store, project)
    return project


def edit_step(store: Store, step_id: str, changes: dict, *, note: str = "") -> dict:
    """``changes`` is merged onto the item's existing draft (and the step's
    payload), not a replacement - editing the body must not lose
    ``to_email``/``to_role`` or any other field the original draft carried."""
    project, step = find_step_by_id(store, step_id)
    item = get_mirror_item(store, project, step)
    merged = {**(item.draft or {}), **changes}
    edit(store, item.id, merged, note=note)
    step["payload"] = {**step.get("payload", {}), **changes}
    _save_project(store, project)
    return project


def reject_step(store: Store, step_id: str, *, reason: str = "") -> dict:
    project, step = find_step_by_id(store, step_id)
    item = get_mirror_item(store, project, step)
    reject(store, item.id, reason=reason)
    step["status"] = "done"
    step["payload"] = {**step.get("payload", {}), "rejected": True, "reason": reason}
    _save_project(store, project)
    return project


# --------------------------------------------------------------------------
# send-step: the actual write, for email / marketing_action only
# --------------------------------------------------------------------------
def _claim_one_for_send(store: Store, item_id: str):
    cur = store.db.execute(
        "UPDATE items SET review_status='sending', updated_at=? "
        "WHERE id=? AND review_status IN ('approved','edited')", (utcnow(), item_id))
    if cur.rowcount != 1:
        return None
    store.record_event(item_id, "agent", "status:sending", {"claim": True})
    return store.get_item(item_id)


def send_step(store: Store, settings: Settings, step_id: str) -> tuple[dict, str]:
    project, step = find_step_by_id(store, step_id)
    if step["kind"] not in ("email", "marketing_action"):
        raise ProjectError(f"'{step['kind']}' steps do not use send-step - "
                           f"pos_update uses approve-step then apply-pos.")
    item = get_mirror_item(store, project, step)
    claimed = _claim_one_for_send(store, item.id)
    if claimed is None:
        raise ProjectError(f"step {step_id} is '{item.review_status}', not approved or edited yet - "
                           f"run approve-step or edit-step first.")
    draft = claimed.draft or {}
    try:
        if step["kind"] == "email":
            email = get_email(settings)
            result = email.send(draft.get("to_email", ""), draft.get("subject", ""),
                                draft.get("body", ""), item=claimed)
        else:
            messaging = get_messaging(settings)
            result = messaging.notify_staff(draft.get("brief", ""), item=claimed)
    except WriteBlocked as exc:
        # Shadow blocks the write, but a hotel still needs to be able to walk
        # a WHOLE project step by step (SIMULATION.md Finding 2) - the human
        # already approved this step, so it counts as done for sequencing:
        # arm the next step, exactly like a real send would. What did NOT
        # happen is recorded on the step's own payload, never silently, so
        # `show`/`list` always say "sent: no (shadow)" for it. The mirrored
        # item itself stays `approved` (not `sent`), so `make review send`
        # still finds it and re-reports "blocked (approval kept)" every time
        # it is retried - re-arming the same next step again is a no-op.
        store.transition(claimed.id, "approved", "agent", {"blocked": str(exc)[:200]})
        already_done = step["status"] == "done"
        step["status"] = "done"
        step["payload"] = {**step.get("payload", {}), "sent": "no (shadow)"}
        next_step = None if already_done else pe.arm_next(project["steps"], step["seq"])
        _persist_and_mirror(store, project, next_step)
        return project, f"blocked (approval kept): {exc}"
    except AdapterError as exc:
        store.mark_send_failed(claimed.id, str(exc))
        return project, f"failed: {exc}"
    store.mark_sent(claimed.id, result.get("message_id") if isinstance(result, dict) else None)
    step["status"] = "done"
    pe.arm_next(project["steps"], step["seq"])
    _persist_and_mirror(store, project, pe.find_step(project["steps"], step["seq"] + 1))
    return project, "sent"


# --------------------------------------------------------------------------
# pos_update: two-phase. core/adapters/base.py's POS stub has no write
# method (docs/how-it-works.md "The two-phase price change") - the guard is
# called directly, the same function every @guarded_write decorator calls.
# --------------------------------------------------------------------------
def apply_pos(store: Store, settings: Settings, step_id: str) -> tuple[dict, str]:
    """Phase 2 of the two-phase price change. Claims the item exactly like
    an email send (``approved -> sending -> sent|failed`` - the FSM has no
    direct `approved -> sent` edge, on purpose: a crash mid-write must show
    up as `sending`, never silently as done)."""
    project, step = find_step_by_id(store, step_id)
    if step["kind"] != "pos_update":
        raise ProjectError(f"'{step['kind']}' is not a pos_update step")
    if step["status"] != "scheduled":
        raise ProjectError(f"step {step_id} is '{step['status']}', not 'scheduled' - "
                           f"run approve-step --effective <date> first.")
    item = get_mirror_item(store, project, step)
    try:
        assert_write_allowed(settings, "pos_price_change", item)
    except WriteBlocked as exc:
        return project, f"blocked (approval kept, still scheduled): {exc}"

    claimed = _claim_one_for_send(store, item.id)
    if claimed is None:
        raise ProjectError(f"step {step_id} is '{item.review_status}', not approved - "
                           f"someone else may already be applying it.")
    from core.adapters import get_stub
    pos = get_stub("pos", settings)
    setter = getattr(pos, "set_price", None)
    payload = step.get("payload", {})
    if setter is None:
        store.mark_send_failed(claimed.id, "pos stub has no set_price() yet - "
                               "see docs/integrations.md#implement-your-own")
        return project, ("not implemented: the POS stub has no set_price() yet - see "
                         "docs/integrations.md#implement-your-own. The approval is kept; "
                         f"retry with `python3 tools/review.py retry {claimed.id}` once it exists.")
    try:
        setter(payload.get("item_id"), payload.get("to"))
    except Exception as exc:  # noqa: BLE001 - record and report, never crash the CLI
        store.mark_send_failed(claimed.id, str(exc))
        return project, f"failed: {exc}"
    store.mark_sent(claimed.id)
    step["status"] = "done"
    pe.arm_next(project["steps"], step["seq"])
    _save_project(store, project)
    return project, f"applied: {payload.get('item')} is now {payload.get('to')}"


# --------------------------------------------------------------------------
# ungated verbs: wait / checkpoint / tracker / measure / resolve / abandon
# --------------------------------------------------------------------------
def change_wait(store: Store, step_id: str, today: str, days: int) -> dict:
    project, step = find_step_by_id(store, step_id)
    if step["kind"] != "wait":
        raise ProjectError(f"'{step['kind']}' is not a wait step")
    if step["status"] != "armed":
        raise ProjectError(f"step {step_id} is '{step['status']}', not 'armed'")
    pe.change_wait_days(step, today, days)
    store.record_event(None, "human", "wait_days_changed", {"project_id": project["id"],
                       "seq": step["seq"], "days": days})
    _save_project(store, project)
    return project


def fast_forward_wait(store: Store, step_id: str, reply_text: str) -> dict:
    project, step = find_step_by_id(store, step_id)
    if step["kind"] != "wait":
        raise ProjectError(f"'{step['kind']}' is not a wait step")
    if step["status"] != "armed":
        raise ProjectError(f"step {step_id} is '{step['status']}', not 'armed'")
    next_step = pe.fast_forward_wait(project["steps"], step["seq"], reply_text)
    store.record_event(None, "agent", "wait_fast_forwarded",
                       {"project_id": project["id"], "seq": step["seq"], "demo": True})
    _persist_and_mirror(store, project, next_step)
    return project


def checkpoint_confirm(store: Store, step_id: str) -> dict:
    project, step = find_step_by_id(store, step_id)
    if step["kind"] != "checkpoint":
        raise ProjectError(f"'{step['kind']}' is not a checkpoint step")
    if step["status"] != "armed":
        raise ProjectError(f"step {step_id} is '{step['status']}', not 'armed'")
    next_step = pe.checkpoint_confirm(project["steps"], step["seq"])
    store.record_event(None, "human", "checkpoint_confirmed",
                       {"project_id": project["id"], "seq": step["seq"]})
    _persist_and_mirror(store, project, next_step)
    return project


def checkpoint_pushback(store: Store, step_id: str, today: str, days: int) -> dict:
    project, step = find_step_by_id(store, step_id)
    if step["kind"] != "checkpoint":
        raise ProjectError(f"'{step['kind']}' is not a checkpoint step")
    if step["status"] != "armed":
        raise ProjectError(f"step {step_id} is '{step['status']}', not 'armed'")
    pe.checkpoint_pushback(project["steps"], step["seq"], today, days)
    store.record_event(None, "human", "checkpoint_pushed_back",
                       {"project_id": project["id"], "seq": step["seq"], "days": days})
    _save_project(store, project)
    return project


def tracker_status(store: Store, reviews: list[dict], step_id: str) -> dict:
    project, step = find_step_by_id(store, step_id)
    if step["kind"] != "tracker":
        raise ProjectError(f"'{step['kind']}' is not a tracker step")
    payload = step.get("payload", {})
    return pe.tracker_status(reviews, since=payload.get("since", ""),
                             min_rating=payload.get("min_rating", 4), target=payload.get("target", 20),
                             category=payload.get("category"))


def fast_forward_tracker(store: Store, reviews: list[dict], step_id: str) -> dict:
    project, step = find_step_by_id(store, step_id)
    if step["kind"] != "tracker":
        raise ProjectError(f"'{step['kind']}' is not a tracker step")
    if step["status"] != "tracking":
        raise ProjectError(f"step {step_id} is '{step['status']}', not 'tracking'")
    status = tracker_status(store, reviews, step_id)
    next_step = pe.fast_forward_tracker(project["steps"], step["seq"], {**status, "reached": True})
    store.record_event(None, "agent", "tracker_fast_forwarded",
                       {"project_id": project["id"], "seq": step["seq"], "demo": True})
    _persist_and_mirror(store, project, next_step)
    return project


def measure(store: Store, step_id: str, *, baseline_rows: list[dict], target_rows: list[dict],
           price_from: float, price_to: float, rollback_threshold_pct: float) -> dict:
    project, step = find_step_by_id(store, step_id)
    if step["kind"] != "measure":
        raise ProjectError(f"'{step['kind']}' is not a measure step")
    if step["status"] != "armed":
        raise ProjectError(f"step {step_id} is '{step['status']}', not 'armed'")
    impact = pe.compute_impact(baseline_rows, target_rows, price_from=price_from, price_to=price_to,
                               rollback_threshold_pct=rollback_threshold_pct)
    step["status"] = "done"
    step["payload"] = {**step.get("payload", {}), "result": impact.as_dict()}
    next_step = pe.arm_next(project["steps"], step["seq"])
    store.record_event(None, "agent", "measured", {"project_id": project["id"],
                       "seq": step["seq"], **impact.as_dict()})
    _save_project(store, project)
    return project


def resolve_project(store: Store, project_id: str, *, measured_impact: float, impact_label: str,
                    today: str) -> dict:
    project = get_project(store, project_id)
    project["status"], project["resolved_on"] = "resolved", today
    project["measured_impact"] = measured_impact
    project["summary"] = f"{project['summary']} — {impact_label}".strip(" —")
    store.record_event(None, "human", "project_resolved",
                       {"project_id": project_id, "measured_impact": measured_impact,
                        "impact_label": impact_label})
    _save_project(store, project)
    return project


def abandon_project(store: Store, project_id: str, *, reason: str, today: str) -> dict:
    """Not in the demo (spec open question 14): a project that did not
    work needs a way to close honestly instead of every seeded storyline
    succeeding, so "measured impact" is not a survivorship figure."""
    project = get_project(store, project_id)
    project["status"], project["resolved_on"] = "abandoned", today
    project["summary"] = f"{project['summary']} — abandoned: {reason}".strip(" —")
    store.record_event(None, "human", "project_abandoned",
                       {"project_id": project_id, "reason": reason})
    _save_project(store, project)
    return project


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _print_project_line(p: dict) -> None:
    next_step = next((s for s in p["steps"] if s["status"] not in ("done",)), None)
    where = f"{next_step['kind']} ({next_step['status']})" if next_step else "complete"
    mark = "[SAMPLE] " if p.get("is_sample") else ""
    print(f"  {mark}{p['id']}  {p['mode']:<7} {p['status']:<10} {p['title'][:40]:<40} next: {where}")


def cmd_list(store: Store, args) -> int:
    projects = list_projects(store, status=args.status)
    if not projects:
        print("No strategic projects yet. `python3 tools/run.py --once --scan` opens one "
             "when the daily scan finds something.")
        return 0
    print(f"{len(projects)} project(s):\n")
    for p in projects:
        _print_project_line(p)
    print("\nRun `python3 tools/project.py show <id>` for the full detail.")
    return 0


def cmd_show(store: Store, args) -> int:
    project = get_project(store, args.id)
    if project.get("is_sample"):
        print("[SAMPLE] this project was opened while at least one CSV source was not "
             "connected - its numbers may include the bundled Hotel Aurora fixture, not only "
             "this property's own data. See docs/integrations.md.\n")
    print(json.dumps(project, indent=2, ensure_ascii=False, default=str))
    return 0


def _measure_rows(settings: Settings, month: str) -> list[dict]:
    rows = sa_data.load_pos_sales_daily(settings)
    return [r for r in rows if r.get("date", "")[:7] == month]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="every strategic project")
    p_list.add_argument("--status", default=None)

    p_show = sub.add_parser("show", help="one project, in full")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve-step", help="approve a gated step's draft")
    p_approve.add_argument("id")
    p_approve.add_argument("--effective", default=None, help="pos_update only: schedule date")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit-step", help="rewrite a gated step's draft")
    p_edit.add_argument("id")
    p_edit.add_argument("--body-file", default=None, help="email: new body")
    p_edit.add_argument("--brief-file", default=None, help="marketing_action: new brief")
    p_edit.add_argument("--subject", default=None)
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject-step", help="discard a gated step's draft")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", default="")

    p_send = sub.add_parser("send-step", help="send an approved email / marketing_action")
    p_send.add_argument("id")

    p_apply = sub.add_parser("apply-pos", help="apply a scheduled price change")
    p_apply.add_argument("id")

    p_wait = sub.add_parser("change-wait", help="push a wait step's due date")
    p_wait.add_argument("id")
    p_wait.add_argument("--days", type=int, required=True)
    p_wait.add_argument("--as-of", default=None)

    p_ffw = sub.add_parser("fast-forward-wait", help="(demo) simulate a reply arriving")
    p_ffw.add_argument("id")
    p_ffw.add_argument("--reply", required=True)

    p_cp = sub.add_parser("checkpoint", help="record the checkpoint decision")
    p_cp.add_argument("id")
    group = p_cp.add_mutually_exclusive_group(required=True)
    group.add_argument("--confirm", action="store_true", help="confirmed fixed")
    group.add_argument("--pushback", action="store_true", help="not yet - push back")
    p_cp.add_argument("--days", type=int, default=3)
    p_cp.add_argument("--as-of", default=None)

    p_ts = sub.add_parser("tracker-status", help="live progress on a tracker step")
    p_ts.add_argument("id")

    p_fft = sub.add_parser("fast-forward-tracker", help="(demo) treat the tracker as reached")
    p_fft.add_argument("id")

    p_measure = sub.add_parser("measure", help="compute the measured impact")
    p_measure.add_argument("id")
    p_measure.add_argument("--price-from", type=float, required=True)
    p_measure.add_argument("--price-to", type=float, required=True)
    p_measure.add_argument("--baseline-month", required=True, help="YYYY-MM")
    p_measure.add_argument("--target-month", required=True, help="YYYY-MM")

    p_resolve = sub.add_parser("resolve", help="close a project out, impact locked into the ledger")
    p_resolve.add_argument("id")
    p_resolve.add_argument("--measured", type=float, required=True)
    p_resolve.add_argument("--impact", required=True)
    p_resolve.add_argument("--as-of", default=None)

    p_abandon = sub.add_parser("abandon", help="close a project as 'did not work'")
    p_abandon.add_argument("id")
    p_abandon.add_argument("--reason", required=True)
    p_abandon.add_argument("--as-of", default=None)

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    migrate_schema(store)
    today = sa_data.today_iso(getattr(args, "as_of", None))
    try:
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve-step":
            approve_step(store, args.id, note=args.note, effective=args.effective)
            print(f"approved {args.id}")
            return 0
        if args.command == "edit-step":
            new_draft: dict = {}
            if args.body_file:
                new_draft["body"] = Path(args.body_file).read_text(encoding="utf-8")
            if args.brief_file:
                new_draft["brief"] = Path(args.brief_file).read_text(encoding="utf-8")
            if args.subject:
                new_draft["subject"] = args.subject
            if not new_draft:
                parser.error("edit-step needs --body-file or --brief-file")
            edit_step(store, args.id, new_draft, note=args.note)
            print(f"edited {args.id}")
            return 0
        if args.command == "reject-step":
            reject_step(store, args.id, reason=args.reason)
            print(f"rejected {args.id}")
            return 0
        if args.command == "send-step":
            _, outcome = send_step(store, settings, args.id)
            print(outcome)
            return 0
        if args.command == "apply-pos":
            _, outcome = apply_pos(store, settings, args.id)
            print(outcome)
            return 0
        if args.command == "change-wait":
            change_wait(store, args.id, today, args.days)
            print(f"{args.id}: due date pushed {args.days} day(s)")
            return 0
        if args.command == "fast-forward-wait":
            fast_forward_wait(store, args.id, args.reply)
            print(f"{args.id}: (demo) reply recorded")
            return 0
        if args.command == "checkpoint":
            if args.confirm:
                checkpoint_confirm(store, args.id)
                print(f"{args.id}: confirmed fixed")
            else:
                checkpoint_pushback(store, args.id, today, args.days)
                print(f"{args.id}: pushed back {args.days} day(s)")
            return 0
        if args.command == "tracker-status":
            reviews = sa_data.load_reviews(settings)
            status = tracker_status(store, reviews, args.id)
            print(json.dumps(status, indent=2))
            return 0
        if args.command == "fast-forward-tracker":
            reviews = sa_data.load_reviews(settings)
            fast_forward_tracker(store, reviews, args.id)
            print(f"{args.id}: (demo) goal treated as reached")
            return 0
        if args.command == "measure":
            baseline = _measure_rows(settings, args.baseline_month)
            target = _measure_rows(settings, args.target_month)
            thresholds = settings.agent_get("thresholds", {}) or {}
            measure(store, args.id, baseline_rows=baseline, target_rows=target,
                   price_from=args.price_from, price_to=args.price_to,
                   rollback_threshold_pct=thresholds.get("rollback_threshold_pct", 10.0))
            print(f"{args.id}: measured")
            return 0
        if args.command == "resolve":
            resolve_project(store, args.id, measured_impact=args.measured, impact_label=args.impact,
                           today=today)
            print(f"{args.id}: resolved — {args.impact}")
            return 0
        if args.command == "abandon":
            abandon_project(store, args.id, reason=args.reason, today=today)
            print(f"{args.id}: abandoned — {args.reason}")
            return 0
        parser.error(f"unknown command {args.command}")
        return 2
    except (ProjectError, StoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except AdapterNotImplemented as exc:
        print(f"not implemented: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
