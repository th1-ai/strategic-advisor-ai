# Guardrails and safety

This agent reads your numbers, opens projects, and drafts emails and price
changes for a person to approve - it does not talk to guests directly (the
one exception, `marketing_action`, is a brief a staff member acts on, not a
message the agent sends). Everything below is built in, not optional, and
this page explains what it does and what is left for you to decide.

## What the Strategist specifically will not do

- **Never sends an email, runs a marketing action, or changes a price without
  your approval.** Structurally enforced: `email`, `marketing_action` and
  `pos_update` steps enter the review queue `awaiting_approval` and cannot
  proceed without a human decision - see docs/how-it-works.md "Step kinds and
  statuses".
- **A price change is scheduled, then applied - never both in one step.**
  Approving only schedules it: "nothing changes in the till until then."
  A second, dated command actually applies it, and that write is guarded
  exactly like a send.
- **Never re-opens a project that already covers a dip.** A `fix` project
  covers its metric while active, and for `thresholds.suppression_days`
  (default 14) after it resolves - see the suppression rule in
  docs/how-it-works.md.
- **Every project carries a measurable target before it opens** - a euro
  figure or a count, decided by the deterministic scan, never invented by
  the model.
- **A price experiment has a rollback threshold** (`thresholds.rollback_threshold_pct`).
  The `measure` step reports plainly whether it was approached.
- **Never fabricates data to make its own numbers look better.** The
  `measure` step reads real POS exports; a missing file gets a plain
  "not connected" answer, never a synthetic one - see
  docs/how-it-works.md "Design decisions" 6.
- **The verdict never depends on the model.** `tools/scan_engine.py` decides
  stable/anomaly/opportunity/watch entirely in plain Python; the LLM only
  writes the morning note and the project's prose afterwards.
- **A project that did not work can be closed as abandoned**, not silently
  left open or force-resolved - `python3 tools/project.py abandon`.

## The two modes

| Mode | What happens |
|---|---|
| `shadow` (default) | The agent reads, thinks, drafts and queues. It **never** sends a message and **never** writes to your PMS. Approving, editing or rejecting a draft records your decision (and teaches the agent) but sends nothing. At go-live, `python3 tools/review.py stale` clears that shadow-era queue so nothing old goes out by surprise. |
| `live` | Items you approved are really sent. Everything else still waits. |

`mode` lives in `config/hotel.yaml`. It is a global kill switch: flipping it back
to `shadow` stops every outbound action immediately, mid-schedule, with no other
change. `config/agent.yaml` can be stricter than `hotel.yaml`, never looser.

Two more brakes:

- `make run ARGS="--dry-run"` computes everything and writes nothing, even in
  live mode. Use it when you change a prompt.
- `review.require_approval_for` in `config/hotel.yaml` lists the actions that
  need a human even in live mode. The defaults are `send_email`, `send_message`,
  `pms_write`, `payment`, `publish`. Shortening that list is how you hand the
  agent more rope, one action at a time.

Every outbound action in the codebase goes through one function,
`core/review.py:assert_write_allowed`. There is no second path.

## The review queue

Nothing reaches a guest without passing through the queue.

```bash
make review                       # what is waiting
python3 tools/review.py show <id>  # the full draft and how it got there
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file my-version.txt
python3 tools/review.py reject <id> --reason "wrong tone"
```

An `email`, `marketing_action` or `pos_update` step moves `new -> pending_review`
the moment it is armed, and then waits. Only `tools/review.py` /
`tools/project.py`'s step-aware functions write `approved`, `edited` or
`rejected`; only a `send-step` / `apply-pos` call writes `sent`. A crash
between "about to send" and "sent" is picked up on the next pass and shown to
you as failed rather than silently retried. Every other step kind
(`analysis`, `wait`, `checkpoint`, `tracker`, `measure`, `resolve`) never
reaches this queue at all - see docs/how-it-works.md "Step kinds and statuses"
for why only three of the nine step kinds need it.

**Your edits teach it.** When you rewrite a draft, the before and after are
stored. Over time that is what makes the drafts sound like your hotel instead of
like a machine.

## What the agent will not do

- Send anything while `mode: shadow`.
- Send an item a human has not approved, when the action needs approval.
- Take a payment, issue a refund, or move money. Payment adapters are read-only
  by design.
- Invent a fact that is not in `knowledge/` or in the data it was given. When it
  is not sure, it queues the item as `needs_human` instead of guessing.
- Argue. Complaints, refund requests, legal or medical topics, and anything that
  reads as distressed go straight to a person.

## Data handling

**What leaves your machine.** With `llm.provider: anthropic` or `claude-code`,
the prompt goes to Anthropic. That prompt contains the guest message and the
relevant property facts. With `llm.provider: mock` or `interactive`, nothing
leaves the machine at all.

**What is stored, and where.** Everything lives in `data/` inside this folder:
`agent.db` (SQLite), `logs/*.jsonl`, `exports/`. `data/` is gitignored. There is
no cloud service behind this repo and no telemetry.

**Card numbers are redacted on the way in.** Every inbound message passes through
`core/redact.py` before it is stored, logged or put into a prompt. A payment card
number is replaced with `[CARD REDACTED ****1234]`, and labelled CVC and expiry
values in the same message go with it. Detection requires a real card prefix and
a valid Luhn checksum, so booking references and door codes survive. IBANs are
masked the same way. Nothing you can do in config turns this off.

**Retention.** `privacy.retention_days` (default 365) is how long processed items
stay in the database. Deleting `data/agent.db` deletes everything the agent knows.

## GDPR, in practice

If you are in the EU or handle EU guests' data, the short version:

- **You are the controller.** This software runs on your machine, under your
  control, on your data. TH1 does not receive it.
- **Your model provider is a processor.** If you use the `anthropic` or
  `claude-code` provider, Anthropic processes guest data on your behalf. Check
  their data processing terms and record them in your processing register.
- **Purpose and minimisation.** The agent sees the message and the property facts
  it needs. Do not put staff phone numbers, card data or full guest histories in
  `knowledge/`.
- **Right to erasure.** A guest asking to be deleted means removing their rows
  from `data/agent.db` and any exported CSVs. Ask your Claude session:
  *"Delete every item in data/agent.db whose payload mentions this email address,
  and tell me how many rows you removed."*
- **Retention.** Set `privacy.retention_days` to what your own policy says, not
  to the default.

This is a practical summary, not legal advice.

## Telling guests they are talking to AI

The EU AI Act (Article 50) requires that a person is told when they are
interacting with an AI system, unless it is obvious. Whether it applies to you
depends on where you and your guests are, but it is good practice everywhere and
guests react well to it.

This agent does not message guests directly, so the disclosure applies to
the `marketing_action` step whenever its brief becomes guest-facing text (a
flyer, an offer on the booking page) - add a line to whatever you publish
from it, and to the signature of any project email a person forwards to a
guest (`knowledge/signature.md`):

> This reply was prepared with AI assistance and reviewed by our team. Reply to
> this message any time to reach a person directly.

If you run in live mode with auto-send for some intents, say so plainly:

> This reply was written by our AI assistant. If you would rather speak to a
> person, just say so and we will take over.

Keep the escape hatch in the sentence. A guest who wants a human should never
have to work out how to get one.

## Subscription or API: an honest note

Two ways to pay for the reasoning:

**Your Claude Code subscription** (`llm.provider: claude-code` or `interactive`).
Flat monthly cost, no per-message billing. This is genuinely the cheapest way to
run a small hotel's agent.

The caveat, plainly: a personal Pro or Max subscription is intended for
interactive use, and Anthropic's usage policy and rate limits apply to automated
use of it. A handful of scheduled runs a day is a normal way to work. Pointing
a busy inbox at it around the clock is not, and you will hit rate limits at the
worst moment. Read the terms and decide for yourself.

**The Anthropic API** (`llm.provider: anthropic`). Pay per token, no ambiguity
about automated use, proper rate limits, and usage you can attribute. This is
the right answer for production volume. `make report` shows what you are
spending.

Start on the subscription while you are learning what the agent does. Move to the
API when it becomes part of how the hotel runs.

## If something goes wrong

1. `mode: shadow` in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env`. Every
   outbound action stops on the next pass.
2. Remove the schedule (`crontab -e`, `launchctl unload`, or
   `systemctl disable --now <slug>.timer`).
3. `make doctor` to see what the agent thinks its state is.
4. `data/logs/*.jsonl` has every decision, with the run id, in order.
