# Workflow: the daily scan

Objective: run the deterministic daily pulse, see the verdict, and hand off
any newly-opened project to `workflows/20-projects.md`.

## Inputs

- `data/imports/financial_daily.csv` (falls back to the bundled Hotel Aurora
  fixture if absent - `docs/integrations.md`).
- `data/imports/reviews.csv` and `data/imports/competitor_snapshots.csv`,
  optional, same fallback.
- `config/agent.yaml: rules:` and `thresholds:` - what counts as a shift.

## Steps

1. **Run it.**
   ```bash
   python3 tools/run.py --once --scan
   ```
   or `make run ARGS="--scan"`. This runs `tools/scan_engine.py`'s eight
   deterministic steps (RevPAR, occupancy vs last year, department pace
   with suppression, review sentiment, external causes, competitor watch),
   decides one verdict (`stable`, `anomaly`, `opportunity`), and prints it.

2. **If `llm.provider` is `interactive`,** you may be asked twice: once for
   `draft_project` (only when a new project needs to open - the analysis
   conclusion, the email, and the marketing brief), once for `narrate` (the
   morning note). Answer each `data/pending/*.prompt.md` into its matching
   `*.answer.json` and re-run the same command. Nothing is saved to
   `advisor_signals` until any project-opening step has fully resolved, so a
   pend here never risks a duplicate project - see
   `docs/how-it-works.md`'s module docstring for `tools/scan.py`.
   Exit code 3 either way; through `make run ARGS='...'` read the number
   after `Error` in `make`'s own banner, not `make`'s exit code.

3. **`verdict: anomaly`.** A department (or RevPAR, occupancy, or review
   sentiment) crossed its threshold and nothing already covers it. A `fix`
   project just opened - go to `workflows/20-projects.md` to work it.

4. **`verdict: opportunity`.** The competitor watch found a matching item
   that moved past `thresholds.competitor_opportunity_pct`. A `growth`
   project just opened - same next step.

5. **`verdict: stable`.** Nothing new. If a known dip is still being
   recovered, the headline says so plainly ("stable apart from the known
   ... dip").

6. **See it again any time.**
   ```bash
   python3 tools/run.py --once --question "What did today's scan find?"
   ```
   or read `data/agent.db`'s `advisor_signals` table directly with
   `python3 tools/report.py`.

## What runs when

Put this on a schedule - `workflows/90-go-live.md` covers turning it on for
real; `python3 tools/schedule.py --all` prints the exact snippet for this
machine, sourced from `config/agent.yaml: schedule:`.

## Edge cases

- **Two scans on the same day.** The second call finds `advisor_signals`
  already has a row for today and returns it unchanged - the scan and any
  project it would have opened do not run twice. If the narrative is still
  missing (an earlier `interactive` pend that was never answered), the
  retry fills in just that.
- **A department dip that is already covered.** The checklist line still
  shows `warn` (the number really is down) but the step message says which
  project is handling it, and it does not count toward the verdict - see
  the suppression rule in `docs/how-it-works.md`.
- **`--dry-run`.** Computes the scan and prints the verdict; opens no
  project, writes no signal. Safe to run any time to preview what today
  would do.
