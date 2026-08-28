# Workflow: asking a question

Objective: get a real answer about the scan, a project, or the property's
own connected data, and see how the Strategist worked it out.

## Inputs

- `data/imports/*.csv` you have connected (optional - falls back to the
  bundled fixtures with a plain "not connected" answer for anything real
  that is missing).
- `knowledge/property.md`, `knowledge/faq.md`, `knowledge/strategy-rules.md`,
  filled in (optional for now).

## Steps

1. **Ask one question.**
   ```bash
   make run ARGS='--question "Why is F&B revenue down?"'
   ```
   or, more readably for a longer question:
   ```bash
   python3 tools/run.py --once --question "What strategic projects are open right now?"
   ```
   Each question runs the bounded tool loop (`tools/tool_loop.py`): the
   model decides which of the eight tools in `tools/toolkit.py` to call
   (`get_daily_pulse`, `list_projects`, `get_project_detail`,
   `get_financial_metrics`, `get_review_sentiment`, `get_competitor_watch`,
   `search_knowledge_base`, `generate_report`), sees the results, and either
   calls more tools or gives its final answer. None of these tools writes
   anything - see `docs/how-it-works.md`. The first line printed is always
   `question id: ask-<day>-<hash>`, derived from the question's own wording
   plus today's date, not a random number - **re-running the exact same
   command later resumes this same question**.

2. **If `llm.provider` is `interactive`,** the run parks a prompt in
   `data/pending/` and exits 3. Read `*.prompt.md`, decide the step, write
   the answer as JSON to the matching `*.answer.json`, and run the exact
   same command again. A round you already answered is replayed from the
   item's own record, never re-asked - `tools/tool_loop.py`'s round cache.
   Through `make run ARGS='...'`, read the number after `Error` in `make`'s
   own banner, not `make`'s exit code. Pass `--resume <id>` to continue a
   specific pending question by the id printed in step 1 instead of by
   re-typing its exact wording.

3. **Read the answer.** A clean answer prints straight to the terminal and
   the question goes to `skipped` - nothing to review, it was already
   delivered. A question that called `generate_report` goes to
   `pending_review` instead, so someone looks the report over -
   `workflows/80-review.md`.

4. **If it could not answer,** the exit code is still 0 - this is not an
   error, it is the agent refusing to guess (`docs/safety.md`). The
   question is `needs_human`; `workflows/80-review.md` covers what to do.

5. **See everything asked so far.**
   ```bash
   make report
   ```

## Edge cases

- **A question outside the connected data** (marketing spend, GOP, anything
  `tools/toolkit.py` has no tool for). The Strategist tries what it can,
  usually `search_knowledge_base`, finds nothing, and escalates rather than
  guessing.
- **A data source with no CSV yet.** The tool answers
  `{"connected": false, "message": "..."}` and the model repeats that
  message plainly instead of falling back to the bundled Hotel Aurora
  numbers - see `docs/integrations.md`.
- **A model answer that does not match its schema.** `core.llm` raises
  `LLMSchemaError`; the question is queued `needs_human` with the error
  recorded, instead of accepting a bad answer.
- **The daily question cap is reached**
  (`rate_limit.max_questions_per_day`, default 200). Queued `needs_human`
  with a plain quota message. A local safety rail, not a real outage.
- **The exact same wording, same day.** Same id, same behaviour as a
  scheduled job would have: if it already has a clean answer you get
  "Already answered"; if it is still pending an `interactive` answer, you
  resume it, per step 2.
