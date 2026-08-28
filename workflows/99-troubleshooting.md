# Workflow: troubleshooting

Read the whole error before doing anything - every tool here is written to
say what broke and what to do about it. If you fix something not covered
below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`llm provider`: claude-code selected but `claude` is not on PATH.**
  Install Claude Code, or switch `llm.provider` to `interactive` or
  `anthropic` in `config/hotel.yaml`.
- **`llm provider`: ANTHROPIC_API_KEY is not set.** Add it to `.env`, or
  switch `llm.provider` to `claude-code` or `interactive`.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail
  loud when misconfigured (a `warn` is reserved for stubs and for
  not-yet-connected CSV sources). Read the `detail` column.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` forces `llm.provider=mock` and reads
  `fixtures/hotel/*.json` and `fixtures/inbound/*.json` - if you deleted or
  renamed those files, restore them from git.
- Read the traceback if there is one; `tools/demo.py` does not swallow
  errors on purpose, so a fixture problem shows up immediately.

## `make run ARGS="--scan"` or `--question` exits with code 3

Not an error. `llm.provider: interactive` parked a prompt. Read
`data/pending/*.prompt.md`, write your answer to the matching
`*.answer.json` (JSON only, matching the schema shown, no prose, no code
fence), and run the same command again. Through `make`, read the number
after `Error` in `make`'s own banner (`make: *** [run] Error 3`), not
`make`'s exit code.

## A `pos_update` step will not apply

- **"blocked ... still scheduled"**: `mode` is `shadow`. Expected - see
  `docs/safety.md`. Flip to `live` when you are ready
  (`workflows/90-go-live.md`).
- **"not implemented: the POS stub has no set_price()"**: nobody has wired
  a real POS integration yet. The approval is kept - `python3 tools/review.py
  retry <item_id>` once `docs/integrations.md#implement-your-own` has been
  followed, or apply the price by hand in your till and mark the item
  `sent` yourself with your Claude session's help.

## A `measure` step refuses to run

Its `pos_update` step has not actually reached `done` yet (it is still
`awaiting_approval` or `scheduled`). There is nothing real to measure until
the price actually changed - see `workflows/20-projects.md` step 5.

## An item is stuck at `sending`

A process died between claiming an item and finishing the send/apply.
`core.store.Store.reap_stuck_sending()` runs on every pass and moves
anything stuck more than 30 minutes to `failed` so you see it in the queue
instead of it vanishing. `python3 tools/review.py retry <id>` once the
cause is fixed.

## The scan's verdict looks wrong

- **A dip you know about keeps firing as a "new" anomaly.** Check
  `python3 tools/project.py list --status active` - the covering project's
  `metric_key` must match the dip's (`revenue_rooms`, `revenue_fnb`,
  `revenue_spa`, `revpar`, `occupancy`, `review_sentiment`, or
  `competitor_price:<item>`). A renamed project does not break this (unlike
  the source system it was ported from - see docs/how-it-works.md).
- **A threshold feels too sensitive or too loose.** Edit
  `config/agent.yaml: thresholds:` - nothing is hardcoded in
  `tools/scan_engine.py`.
- **A rule you turned off is still affecting the checklist wording, not the
  verdict.** `verify-cross-sources` only changes a sentence, by design -
  see the spec's own note in `docs/how-it-works.md`.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision, in order, with a run id.
`python3 tools/review.py show <id>` has the full event trail for one
gated step; `python3 tools/project.py show <project_id>` has it for a
whole project. If neither explains it, that is a real bug - describe
exactly what you ran and what you expected, and ask.
