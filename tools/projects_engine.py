"""tools/projects_engine.py - the strategic-project step machine.

Pure functions over plain dicts - no store, no adapters, no LLM - so every
rule here (spec section 3B) is trivial to unit test. ``tools/project.py``
is the only caller that touches the database or the review guard; this
module just decides what a status transition means.

Step kinds (``STEP_KINDS``) and statuses (``STATUSES``) match
``specs/strategic-advisor-ai.md`` section 3B, with two deliberate folds
documented in docs/how-it-works.md: the demo's ``collect_data`` +
``analysis_run`` become one ``measure`` step here.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools import data as sa_data

STEP_KINDS = ("analysis", "email", "wait", "checkpoint", "marketing_action",
              "tracker", "pos_update", "measure", "resolve")
#: kinds that reach outside the agent's own database and therefore go
#: through core.review's guard (mirrored into the shared `items` table).
GATED_KINDS = frozenset({"email", "marketing_action", "pos_update"})
STATUSES = ("pending", "armed", "awaiting_approval", "awaiting_reply",
           "tracking", "scheduled", "done")

#: the deterministic step template for each project mode, used only when
#: config/agent.yaml doesn't override `project_templates`.
DEFAULT_TEMPLATES = {
    "fix": ["analysis", "email", "wait", "checkpoint", "marketing_action", "tracker", "resolve"],
    "growth": ["analysis", "email", "wait", "pos_update", "measure", "resolve"],
}

DEFAULT_TITLES = {
    "analysis": "Analysis", "email": "Email the team", "wait": "Wait for a reply",
    "checkpoint": "Checkpoint: is it fixed?", "marketing_action": "Run the promo",
    "tracker": "Track the recovery", "pos_update": "Reprice the item",
    "measure": "Measure the result", "resolve": "Resolve the project",
}


def entry_status(kind: str) -> str:
    """The status a step enters at when it is armed (spec section 3B's
    ``armNext``): the three gated kinds start `awaiting_approval`, a
    tracker starts `tracking`, everything else starts `armed`."""
    if kind in ("email", "marketing_action", "pos_update"):
        return "awaiting_approval"
    if kind == "tracker":
        return "tracking"
    return "armed"


def build_steps(kinds: list[str], titles: dict[str, str] | None = None,
                payloads: dict[str, dict] | None = None) -> list[dict]:
    """A fresh ordered step list for a new project, every step `pending`
    until something arms it. ``payloads`` (keyed by kind) seeds each step's
    content - the LLM-drafted analysis/email/marketing_action text (task
    `draft_project`) or, for `pos_update`, the deterministic price-change
    facts the scan already computed. ``tools/project.py:open_project``
    immediately completes step 1 (`analysis` always renders on its own, per
    spec) and arms step 2 right after, so a newly-opened project already
    shows its analysis and has its next step ready to approve."""
    titles, payloads = titles or {}, payloads or {}
    return [{"seq": i, "kind": kind, "status": "pending",
            "title": titles.get(kind, DEFAULT_TITLES.get(kind, kind)),
            "payload": dict(payloads.get(kind, {}))}
           for i, kind in enumerate(kinds, start=1)]


def arm_next(steps: list[dict], completed_seq: int) -> dict | None:
    """Mark the step after ``completed_seq`` as armed for its kind. Returns
    the updated step dict, or ``None`` if there is no next step."""
    nxt = next((s for s in steps if s["seq"] == completed_seq + 1), None)
    if nxt is None:
        return None
    nxt["status"] = entry_status(nxt["kind"])
    return nxt


def find_step(steps: list[dict], seq: int) -> dict | None:
    return next((s for s in steps if s["seq"] == seq), None)


def preceding_wait(steps: list[dict], before_seq: int) -> dict | None:
    """The nearest `wait` step with `seq < before_seq` - what a checkpoint
    push-back re-arms."""
    candidates = [s for s in steps if s["kind"] == "wait" and s["seq"] < before_seq]
    return max(candidates, key=lambda s: s["seq"]) if candidates else None


# --------------------------------------------------------------------------
# checkpoint
# --------------------------------------------------------------------------
def checkpoint_confirm(steps: list[dict], checkpoint_seq: int) -> dict | None:
    """"Confirmed fixed" - the checkpoint is done, the next step is armed."""
    cp = find_step(steps, checkpoint_seq)
    if cp is None or cp["kind"] != "checkpoint":
        raise ValueError(f"no checkpoint step at seq {checkpoint_seq}")
    cp["status"] = "done"
    return arm_next(steps, checkpoint_seq)


def checkpoint_pushback(steps: list[dict], checkpoint_seq: int, today: str,
                        extra_days: int) -> dict:
    """"Not yet - push back": the preceding wait re-arms with a new due
    date and a cleared reply; the checkpoint returns to `pending`."""
    cp = find_step(steps, checkpoint_seq)
    if cp is None or cp["kind"] != "checkpoint":
        raise ValueError(f"no checkpoint step at seq {checkpoint_seq}")
    wait = preceding_wait(steps, checkpoint_seq)
    if wait is None:
        raise ValueError("checkpoint has no preceding wait step to push back")
    wait["status"] = "armed"
    wait["payload"] = {**wait.get("payload", {}), "due": sa_data.add_days(today, extra_days),
                       "reply": None}
    cp["status"] = "pending"
    return wait


# --------------------------------------------------------------------------
# wait
# --------------------------------------------------------------------------
def wait_is_due(step: dict, today: str) -> bool:
    due = (step.get("payload") or {}).get("due")
    return bool(due) and due <= today


def change_wait_days(step: dict, today: str, days: int) -> dict:
    step["payload"] = {**step.get("payload", {}), "days": days, "due": sa_data.add_days(today, days)}
    return step


def fast_forward_wait(steps: list[dict], wait_seq: int, reply_text: str) -> dict | None:
    """Demo-only convenience, always labelled as such by the caller
    (tools/project.py): simulates a reply arriving right now. Arms the next
    step only if it is a `checkpoint` - matches the spec exactly (a wait
    ahead of anything else stays `pending` until a person looks at it)."""
    step = find_step(steps, wait_seq)
    if step is None or step["kind"] != "wait":
        raise ValueError(f"no wait step at seq {wait_seq}")
    step["status"] = "done"
    step["payload"] = {**step.get("payload", {}), "reply": reply_text}
    nxt = find_step(steps, wait_seq + 1)
    if nxt is not None and nxt["kind"] == "checkpoint":
        return arm_next(steps, wait_seq)
    return None


# --------------------------------------------------------------------------
# tracker - counted live from the reviews table, never a stored number
# --------------------------------------------------------------------------
def tracker_status(reviews: list[dict], *, since: str, min_rating: float, target: int,
                   category: str | None = None) -> dict:
    window = [r for r in reviews if r.get("review_date", "") >= since]
    if category:
        window = [r for r in window if r.get("category") == category]
    pos = [r for r in window if r.get("rating", 0) >= min_rating]
    neg = [r for r in window if r.get("rating", 0) <= 3]
    return {"positive": len(pos), "negative": len(neg), "target": target,
           "reached": len(pos) >= target, "since": since, "category": category}


def fast_forward_tracker(steps: list[dict], tracker_seq: int, status: dict) -> dict | None:
    """Demo-only: the goal is already reached (or treated as reached),
    marks the tracker done and arms `resolve`."""
    step = find_step(steps, tracker_seq)
    if step is None or step["kind"] != "tracker":
        raise ValueError(f"no tracker step at seq {tracker_seq}")
    step["status"] = "done"
    step["payload"] = {**step.get("payload", {}), "final_status": status}
    return arm_next(steps, tracker_seq)


# --------------------------------------------------------------------------
# measure - folds the demo's collect_data + analysis_run (docs/how-it-works.md
# "Design decisions" 6): reads real rows, never fabricates them.
# --------------------------------------------------------------------------
@dataclass
class ImpactResult:
    baseline_units: float
    target_units: float
    delta_pct: float
    attach_baseline: float
    attach_target: float
    uplift_month: float
    keep_price: bool
    rollback_threshold_pct: float

    def as_dict(self) -> dict:
        return {"baseline_units": round(self.baseline_units, 2),
               "target_units": round(self.target_units, 2),
               "delta_pct": round(self.delta_pct, 1),
               "attach_baseline": round(self.attach_baseline, 3),
               "attach_target": round(self.attach_target, 3),
               "uplift_month": round(self.uplift_month, 2),
               "keep_price": self.keep_price,
               "rollback_threshold_pct": self.rollback_threshold_pct}


def compute_impact(baseline_rows: list[dict], target_rows: list[dict], *, price_from: float,
                   price_to: float, rollback_threshold_pct: float) -> ImpactResult:
    """Ports the demo's ``computeImpact`` (spec section 3B) exactly, over
    real POS rows instead of fabricated ones."""
    def mean_units(rows: list[dict]) -> float:
        return sum(r.get("units", 0.0) for r in rows) / len(rows) if rows else 0.0

    def attach(rows: list[dict]) -> float:
        covers = sum(r.get("covers", 0.0) for r in rows)
        return sum(r.get("units", 0.0) for r in rows) / covers if covers else 0.0

    baseline_units, target_units = mean_units(baseline_rows), mean_units(target_rows)
    delta_pct = ((target_units - baseline_units) / baseline_units * 100.0) if baseline_units else 0.0
    uplift_month = round(target_units * (price_to - price_from) * 30.4, 2)
    keep_price = delta_pct > -rollback_threshold_pct
    return ImpactResult(baseline_units, target_units, delta_pct, attach(baseline_rows),
                        attach(target_rows), uplift_month, keep_price, rollback_threshold_pct)


# --------------------------------------------------------------------------
# resolve / abandon
# --------------------------------------------------------------------------
def resolve_payload(measured_impact: float, impact_label: str) -> dict:
    return {"status": "resolved", "measured_impact": measured_impact, "impact_label": impact_label}
