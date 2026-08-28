---
name: strategic-advisor-ai
description: Run Strategic Advisor AI ("The Strategist") — scans the business daily, opens a strategic project when something shifts or a growth opportunity appears, and project-manages it to a measured result. Use when the user asks to run the daily scan, ask a question about performance or projects, check what is waiting for review, approve or reject a draft, advance a strategic project, or asks how the agent is doing. Trigger phrases: "run The Strategist", "/strategic-advisor-ai", "run the scan", "check the queue", "what is waiting for me", "approve that draft", "how is the project doing".
---

# Strategic Advisor AI

Runs Strategic Advisor AI's daily scan and ask loop, and works its review
queue and its projects. Everything happens from the repo root; every command
below exists and works.

## Before anything else

Read `README.md` if you have not this session, and `workflows/10-scan.md` /
`workflows/15-ask.md` for the two main loops. If the user has never run this
agent, start at `workflows/00-setup.md` instead and walk them through it.

## The loop

**1. Check the agent is healthy.**

```bash
make doctor
```

Any `FAIL` line has a fix hint. Fix it before going further. `WARN` lines
(usually a data source not yet connected) are worth mentioning but do not
stop the run.

**2. Run the daily scan, or ask a question.**

```bash
python3 tools/run.py --once --scan
python3 tools/run.py --once --question "Why is F&B revenue down?"
```

If `llm.provider` is `interactive`, the run stops with exit code 3 and parks
a prompt in `data/pending/`. That is expected. Read each `*.prompt.md`,
write the answer as JSON to the matching `*.answer.json` exactly matching
the schema, then run the same command again - see `workflows/10-scan.md` /
`workflows/15-ask.md` for what each pending prompt is for.

**3. If the scan opened a project, work it.**

```bash
python3 tools/project.py list
python3 tools/project.py show <project_id>
```

`workflows/20-projects.md` has the full table of step kinds and the command
for each. Only `email`, `marketing_action` and `pos_update` steps go through
the review queue (step 4 below) - everything else you advance directly with
`tools/project.py`.

**4. Show what is waiting in the review queue.**

```bash
make review
python3 tools/review.py show <id>
```

Summarise it for the user in plain language: which project, what shifted,
what is proposed. Do not paste raw JSON at them.

**5. Act on their decision.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file <path>      # email
python3 tools/review.py edit <id> --brief-file <path>     # marketing_action
python3 tools/review.py approve <id> --effective <date>   # pos_update
python3 tools/review.py reject <id> --reason "<why>"
python3 tools/review.py send                              # email / marketing_action
python3 tools/project.py apply-pos <step_id>               # pos_update, on/after the date
```

Read the draft back to them before approving. If they want changes, write
the new version to a file and use `edit` - the before/after is stored.

**6. Report.**

```bash
make report
```

## Rules

- **Never send or apply a price change in shadow mode**, and never work
  around a blocked write. The error message says what to do.
- **Going live is the hotel's decision.** Only raise it after
  `workflows/90-go-live.md` has been worked through.
- **Confirm before anything irreversible** - an email, a marketing action, a
  price change - even when it is approved.
- **The verdict never depends on the model.** If a number in a scan surprises
  you, check `tools/scan_engine.py` and `config/agent.yaml: thresholds:`,
  not the narrative prose.
- **Never print or paste a credential.**
- If a run fails, read the whole error, fix the cause, re-run, and note what
  you learned in `workflows/99-troubleshooting.md`.
