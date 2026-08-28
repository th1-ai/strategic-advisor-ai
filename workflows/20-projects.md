# Workflow: working a strategic project

Objective: advance a project one step at a time, from a fresh analysis to a
measured, resolved result - or an honest abandonment.

## Inputs

- A project opened by `workflows/10-scan.md` (or seeded by `make demo`).
- `python3 tools/project.py list` to find its id;
  `python3 tools/project.py show <id>` for the full step list.

## The step kinds, in the order a project usually visits them

| Kind | What it needs from you | Command |
|---|---|---|
| `analysis` | nothing - it renders on its own | - |
| `email` | approve, edit or reject the draft | `tools/review.py` or `tools/project.py approve-step` |
| `wait` | nothing, unless you want to change the timer | `tools/project.py change-wait` |
| `checkpoint` | confirm fixed, or push back | `tools/project.py checkpoint` |
| `marketing_action` | approve, edit or reject the brief | same as `email` |
| `tracker` | nothing - counted live; check progress any time | `tools/project.py tracker-status` |
| `pos_update` | approve (schedules), then apply (on the date) | `tools/project.py approve-step --effective`, then `apply-pos` |
| `measure` | run it, once the price change has actually applied | `tools/project.py measure` |
| `resolve` | mark it resolved with the measured figure | `tools/project.py resolve` |

Only `email`, `marketing_action` and `pos_update` go through the review
queue (`workflows/80-review.md`) - everything else you drive directly with
`tools/project.py`, because nothing about it leaves the building.

## Steps

1. **See what is next.**
   ```bash
   python3 tools/project.py show <project_id>
   ```
   The step whose status is not yet `done` is the one waiting on you.

2. **A gated step (`email` / `marketing_action`).**
   ```bash
   python3 tools/review.py list
   python3 tools/review.py show <item_id>
   python3 tools/review.py approve <item_id>      # or edit / reject
   python3 tools/review.py send                    # actually sends, if mode: live
   ```
   In `mode: shadow` the send is always blocked and the approval is kept -
   `docs/safety.md`. The step still counts as done for sequencing (labelled
   `"sent": "no (shadow)"`, never confused with a real send) and the next
   step arms right away, so you can keep walking the project in shadow
   instead of it getting stuck here - `workflows/80-review.md`.

3. **A `wait` step.** Nothing to do until a reply arrives or you decide to
   chase again: `python3 tools/project.py change-wait <step_id> --days N`.
   For a demo or a rehearsal only: `fast-forward-wait --reply "..."` - see
   the honesty note in `docs/how-it-works.md`.

4. **A `checkpoint` step.**
   ```bash
   python3 tools/project.py checkpoint <step_id> --confirm
   python3 tools/project.py checkpoint <step_id> --pushback --days 3
   ```
   Confirming arms the next step. Pushing back re-arms the preceding `wait`
   with a new due date and returns the checkpoint to `pending`.

5. **A `pos_update` step - the two-phase price change.**
   ```bash
   python3 tools/project.py approve-step <step_id> --effective 2026-09-15
   # ... on or after the effective date ...
   python3 tools/project.py apply-pos <step_id>
   ```
   Approving only schedules it - "nothing changes in the till until then."
   `apply-pos` is the actual write, guarded exactly like a send; in
   `mode: shadow` it always reports blocked and the schedule is kept.

6. **A `tracker` step.**
   ```bash
   python3 tools/project.py tracker-status <step_id>
   ```
   Counted live from `data/imports/reviews.csv` (or the fixture) every
   time - never a stored number. `fast-forward-tracker` is a demo-only
   convenience.

7. **A `measure` step.**
   ```bash
   python3 tools/project.py measure <step_id> --price-from 24 --price-to 26 \
     --baseline-month 2026-05 --target-month 2026-07
   ```
   Reads real rows from `data/imports/pos_sales_daily.csv` for both months -
   refuses to run if the `pos_update` step has not actually applied yet
   (there is nothing real to measure).

8. **Close it out.**
   ```bash
   python3 tools/project.py resolve <project_id> --measured 2016 \
     --impact "+EUR 2,016/mo measured"
   python3 tools/project.py abandon <project_id> --reason "price rollback - demand dropped too much"
   ```
   Only `resolve` counts toward `python3 tools/report.py`'s measured-impact
   total. `abandon` is honest closure for a project that did not work.

## Edge cases

- **A checkpoint or a `pos_update` step reached out of order** (its status
  is not `armed`/`scheduled` yet). `tools/project.py` refuses with a plain
  message naming the step's current status - work the steps in order.
- **A rejected `email`/`marketing_action` step.** Nothing arms
  automatically - decide by hand what the project needs next (a re-drafted
  email, a different plan, or abandoning it).
