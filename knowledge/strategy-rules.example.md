# Strategy rules - how the Strategist runs a project

<!--
Copy this to knowledge/strategy-rules.md and edit it for your property.
These are read by search_knowledge_base and referenced by
tools/scan_engine.py / tools/project.py comments - keep the two in sync if
you change a threshold; the numbers themselves live in
config/agent.yaml: thresholds:, not here.
-->

## Verification

- An anomaly is cross-checked against volume, sentiment and external causes
  before it is reported as a verdict (rule `verify-cross-sources`).
- Occupancy is checked against the same month last year, not just last month
  (rule `yoy-baseline`) - a slow February is not always a problem.

## Safety

- Never send an email, run a marketing action, or change a price without a
  person's approval. See docs/safety.md.
- A price change is scheduled first and applied later - "nothing changes in
  the till until then."
- Every price experiment carries a rollback threshold
  (`thresholds.rollback_threshold_pct`); if demand drops past it, the
  measured-impact step says so plainly.

## Strategy

- Every project must carry a measurable target - a euro figure or a count -
  before it opens.
- Watch competitor pricing at least monthly (rule `competitor-watch`).
- Prefer a pilot or a test before a permanent change.
- A project that did not work can be closed as abandoned
  (`python3 tools/project.py abandon`) - "measured impact" only counts
  resolved projects, and it should not be a survivorship figure.
