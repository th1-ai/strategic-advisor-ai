# Sub-agents in this repo

None. Strategic Advisor AI is top-level with no children
(`specs/briefs/strategic-advisor-ai.md`): "children folded into this repo:
none."

## Where it overlaps with other agents in the family

If you run more than one TH1 template at your property, worth knowing where
the lines are drawn:

- **vs Revenue Management AI ("The Quant").** Both watch RevPAR and
  competitor prices. The Quant is tactical and fast - it reprices daily. The
  Strategist is strategic and slow - it opens a project, waits for people,
  and only proposes a price change through the two-phase gate
  (`pos_update`). If you run both, decide which one owns competitor-price
  reactions so they do not both act on the same signal.
- **vs Reporting & Audit AI ("The Auditor").** Both read a financial ledger
  and both tell the owner what moved. The Auditor reports the past week.
  The Strategist investigates a shift and runs a project to fix or exploit
  it.
- **A restaurant's own F&B sales audit tooling.** If you run something that
  tracks `pos_sales_daily` as its own baseline, be aware the Strategist's
  `measure` step reads that same table (never writes to it) - see
  docs/how-it-works.md "Design decisions" 6, which explicitly avoids the
  fabricated-data anti-pattern that would otherwise corrupt a shared
  baseline.

## If you want to add one

This repo's shape (one main scan/project loop, one ask loop, a review
queue) does not assume it will stay alone. A sub-agent would get its own
`tools/<child>.py`, a `workflows/2x-<child>.md`, a config block under
`config/agent.yaml`, its own fixtures, and a README block - same pattern as
every other repo in this family. None is planned today.
