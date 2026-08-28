# Workflow: first-run setup

Objective: get the Strategist from a fresh clone to a working demo, then to
real config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet - it never
   overwrites your own copies). `make doctor` will show two `FAIL` lines
   right after setup - expected: "hotel identity" (the property name is
   still the shipped placeholder) and "AI disclosure" (`knowledge/signature.md`
   and `knowledge/disclosure.md` do not exist yet - step 3 creates them).
   Everything else should be `ok` or `warn` (the financial ledger, reviews
   and competitor snapshots will `WARN` until you connect real data - see
   step 5).

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect to see two seeded projects, a daily scan that opens a third (an
   F&B revenue dip), a blocked send attempt, a real measured-impact
   calculation, five sample questions answered, and the line
   `DEMO OK — N items processed, N drafted, 0 sent (shadow)`. If you do not
   see that, stop and read `workflows/99-troubleshooting.md` before going
   further.

3. **Fill in the property.** Edit `config/hotel.yaml` (name, address,
   contact, currency, languages). Then:
   ```bash
   cp knowledge/property.example.md       knowledge/property.md
   cp knowledge/faq.example.md            knowledge/faq.md
   cp knowledge/strategy-rules.example.md knowledge/strategy-rules.md
   cp knowledge/signature.example.md      knowledge/signature.md
   cp knowledge/disclosure.example.md     knowledge/disclosure.md
   ```
   Replace the Hotel Aurora content with the real property's facts and your
   own strategy rules (thresholds themselves live in
   `config/agent.yaml: thresholds:`, not here - `knowledge/strategy-rules.md`
   is prose your Claude session and `search_knowledge_base` can read).
   `signature.md` is your email sign-off plus the EU AI Act Article 50 line
   (`docs/safety.md`); `disclosure.md` is the one-line version for anything a
   `marketing_action` brief becomes guest-facing text - `make doctor`'s "AI
   disclosure" check FAILs until both exist and have actually been edited.

4. **Pick how the agent thinks.** `config/hotel.yaml`'s `llm.provider`
   starts as `interactive` - it asks you, in this Claude Code session,
   instead of calling a model. That costs nothing extra and is the best way
   to see how the Strategist reasons before you trust it further.
   `docs/how-it-works.md` and `docs/safety.md` explain the other three
   providers (`mock`, `claude-code`, `anthropic`) and when to move to one.

5. **Connect your real data (optional for now).** `docs/integrations.md`
   covers every source: `data/imports/financial_daily.csv`,
   `reviews.csv`, `competitor_snapshots.csv`, `pos_items.csv` and
   `pos_sales_daily.csv`. Until they exist, the scan and the ask tools
   answer "not connected" for that source rather than a plausible-looking
   number - see `docs/how-it-works.md`. Start with `financial_daily.csv`;
   that alone unlocks the RevPAR, occupancy and department-pace checks.

6. **Connect email and messaging (optional, needed before you approve
   anything for real).** `systems.email.adapter` and
   `systems.messaging.adapter` in `config/hotel.yaml` start as `mock`. See
   `docs/integrations.md` for `imap`/`gmail` and `unipile`/`webhook`.

7. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real and `knowledge/property.md` and
   `data/imports/financial_daily.csv` exist, the "hotel identity" and
   "financial ledger" lines turn green; once `knowledge/signature.md` and
   `knowledge/disclosure.md` exist and are actually edited, "AI disclosure"
   does too. Move on to `workflows/10-scan.md` to run the daily scan for
   real, or `workflows/15-ask.md` to ask a question first.
