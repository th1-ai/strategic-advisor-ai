# Workflow: working the review queue

Objective: turn a queued project step - an email or a marketing action draft,
or a scheduled price change - into a decision, and, once approved, actually
carry it out.

The queue holds only three step kinds: `email`, `marketing_action` and
`pos_update` - the only three that reach outside the agent's own database
(`docs/how-it-works.md`). Every other step kind is worked directly with
`tools/project.py` - see `workflows/20-projects.md`. `mode: shadow` blocks
every one of these three, approved or not; see `docs/safety.md`.

## Steps

1. **See what is waiting.**
   ```bash
   make review
   ```
   Each line shows the item id, its status, the step kind, and the
   project's title for that step.

2. **Read one in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   Prints the draft (email subject/body, or the marketing brief, or the
   proposed price change) and the full event history. Summarise it for the
   hotel in plain language - which project, what changed, what is proposed
   - do not paste the raw JSON at them.

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --body-file my-version.txt [--subject "New subject"]
   python3 tools/review.py edit <id> --brief-file my-brief.txt      # marketing_action
   python3 tools/review.py approve <id> --effective 2026-09-15      # pos_update only
   python3 tools/review.py reject <id> --reason "wrong framing"
   ```
   `edit` records the before/after pair as a `learnings` row. Approving a
   `pos_update` only schedules it - "nothing changes in the till until
   then." A `pos_update` never appears in `send` (step 4) - it is applied
   separately, see `workflows/20-projects.md` step 5.

4. **Send what was approved (email / marketing_action only).**
   ```bash
   python3 tools/review.py send
   ```
   Claims everything approved/edited of those two kinds, calls the email or
   messaging adapter, and records the result. In `mode: shadow` nothing is
   sent at all: the guard blocks it with a readable message, the item
   returns to `approved` ("approval kept"), and it only actually goes out
   after you flip `mode: live` (clear the old queue first with
   `python3 tools/review.py stale` - `workflows/90-go-live.md`). The summary
   line reports this as `N sent, M blocked (approval kept), K failed` - a
   shadow block is correct, by-design behaviour, not a failure, so it never
   makes the command exit non-zero.

   **The project still moves on.** Approving a step and hitting this block
   is enough to advance the project: the step is marked done, labelled
   `"sent": "no (shadow)"` so it is never confused with a real send, and the
   next step (a `wait`, a `checkpoint`, `pos_update`, whatever the template
   says) is armed right away - see `workflows/20-projects.md`. This is what
   lets a hotel walk a whole project, start to finish, entirely in
   `mode: shadow`.

5. **A failed send.** `send` marks the item `failed` with the error
   attached.
   ```bash
   python3 tools/review.py retry <id>
   ```
   re-queues it after you have fixed the cause (usually a mailbox
   credential, or a not-yet-implemented POS write - `make doctor` and
   `docs/integrations.md` say which).

## Rules

- Only `tools/review.py` (through `tools/project.py`'s step-aware
  functions) writes `approved` / `edited` / `rejected`. This keeps a
  project's own step list in sync with the item automatically - never edit
  `advisor_projects` directly.
- Confirm with the hotel before sending anything, even an approved item, the
  first few times. `workflows/90-go-live.md` covers when to stop doing that.
