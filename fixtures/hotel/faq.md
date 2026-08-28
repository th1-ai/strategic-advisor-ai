# Hotel Aurora - frequently asked questions

## How does the Strategist decide what to escalate?

It runs a deterministic daily scan (RevPAR, occupancy vs last year, revenue
pace by department, review sentiment, competitor prices). A metric crossing
its threshold in `config/agent.yaml: thresholds:` opens a strategic project.
Nothing is invented by the model - see docs/how-it-works.md.

## Does it ever send an email or change a price on its own?

No. Every email, marketing action and price change waits for a person to
approve it - see docs/safety.md. `mode: shadow` blocks all three even when
approved; nothing leaves the building until a human flips the switch in
`config/hotel.yaml` after working through `workflows/90-go-live.md`.

## What counts as "measured impact"?

Only the euro figure recorded when a project is resolved
(`python3 tools/project.py resolve`), and only for projects a human actually
closed out. A project can also be abandoned if the fix did not work - see
docs/how-it-works.md "Design decisions" 7.

## How often does the scan run?

Once a day, on the cadence in `config/agent.yaml: schedule:`. Run it by hand
any time with `python3 tools/run.py --once --scan`.
