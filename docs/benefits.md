# Measuring the benefit

## The promise, verbatim

**Does.** Scans every metric across the business daily - revenue pace,
occupancy, reviews, competitor moves - investigates anything that shifted,
and hunts for growth when things are calm. When it finds something, it opens
a strategic project and project-manages it end to end: analysis, emails to
the team, waiting on replies, checkpoints, live trackers, and a measured EUR
result at the close.

**Why it matters.** Most hotels find out about a revenue dip weeks later in
a P&L review. The Strategist finds it the next morning, tells you why,
brings a costed plan - and then actually sees the fix through to a measured
result.

**Roster ROI figure:** +EUR 29.6k measured impact banked in 90 days
(revenue). This is a roster-level illustrative figure, not a guarantee for
your property - see "An honest caveat" below.

## What to track

| Metric | Where it comes from | Command |
|---|---|---|
| Scans run, verdict split | `advisor_signals` | `python3 tools/report.py` |
| Projects open / resolved / abandoned, by mode | `advisor_projects` | `python3 tools/report.py` |
| **Measured impact (resolved projects only)** | `SUM(measured_impact) WHERE status='resolved'` | `python3 tools/report.py` |
| Questions answered, and how many needed a human | `items WHERE kind='question'` | `python3 tools/report.py` |
| Time from scan to a drafted project | `events` (`scan_run` -> `project_opened`) | `data/logs/*.jsonl` |
| Time from draft to your decision | `events` (`ingested` -> `approved`/`edited`/`rejected`) | `data/logs/*.jsonl` |

`python3 tools/report.py --export` writes resolved projects (title, mode,
measured impact, resolved date) to `data/exports/resolved_projects.csv` (or
a live sheet with `systems.sheets.adapter: google`).

## What "measured impact" actually means here

Only `python3 tools/project.py resolve` sets `measured_impact`, and only on
a project that was actually run through the step machine - the `measure`
step computes it from real POS rows (`data/imports/pos_sales_daily.csv`),
never fabricated ones (docs/how-it-works.md "Design decisions" 6). A project
closed with `python3 tools/project.py abandon` contributes nothing to the
total, on purpose: the KPI should not be a survivorship figure.

## The case for running this before a P&L review catches it

- A department revenue dip usually shows up in month-end reporting, four to
  six weeks after it started. The daily scan catches it the next morning.
- A GM doing this by hand has to notice the number, investigate, draft an
  email, remember to follow up, and remember to measure the result weeks
  later. The Strategist keeps all of that state in one place and nags
  nobody twice for the same thing (the suppression rule -
  docs/how-it-works.md).
- A competitor price move is easy to miss without someone checking monthly.
  The competitor watch does it every scan and only surfaces it when the
  move is big enough to act on (`thresholds.competitor_opportunity_pct`).

## An honest caveat

The roster's own spec (`specs/strategic-advisor-ai.md`, section 11) flags
that its demo ROI figure has no baseline, no counterfactual and no
attribution test - it is a claim, not a measurement, in the source material.
This template fixes the mechanics that make a REAL measurement possible (the
`measure` step reads real POS data, `resolve` records a real number, the KPI
sums only resolved projects) - but the number for YOUR property depends on
what your scan actually finds and what you approve. Do not quote the +EUR
29.6k figure as a guarantee; quote what `python3 tools/report.py` shows
after your own projects resolve.
