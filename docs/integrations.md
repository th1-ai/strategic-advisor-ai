# Connecting your systems

Every connector here is one of three things:

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: IMAP/SMTP, CSV, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working on your machine at any time:

```bash
python3 tools/doctor.py
```

## What the Strategist actually uses

| System | Status | Reads | Writes |
|---|---|---|---|
| Financial ledger | universal (CSV) | `data/imports/financial_daily.csv` - RevPAR, occupancy, revenue by department | nothing |
| Reviews | universal (CSV) | `data/imports/reviews.csv` - rating, date, category | nothing |
| Competitor / rate-shopper | universal (CSV) | `data/imports/competitor_snapshots.csv` - scraped price points | nothing |
| POS | universal (CSV) for reads; **stub** for the price write | `data/imports/pos_items.csv`, `pos_sales_daily.csv` | `set_price()` - not implemented, see below |
| Email - `systems.email.adapter` | mock / universal (imap) / built (gmail) | replies (not yet wired) | **drafted emails, human-approved only** |
| Messaging - `systems.messaging.adapter` | mock / built (unipile) / universal (webhook) | nothing | **the approved marketing brief, sent to staff, human-approved only** |
| Sheets - `systems.sheets.adapter` | universal (csv) / built (google) | nothing | `tools/report.py --export`: resolved projects |
| PMS | **not used** | - | - |

**PMS is deliberately not used.** RevPAR and occupancy come from the daily
financial ledger, not from live reservation data - see
docs/how-it-works.md. If your PMS cannot export a daily ledger CSV, ask your
Claude Code session to add a small script that computes one from
`core.adapters.pms_csv` reservations; the scan itself does not need to change.

**News, events and weather are not connected.** The scan's `rule-out-external`
checklist line says so plainly rather than claiming a check that does not
happen - see docs/how-it-works.md "Design decisions" 5. If you want a real
check, ask your Claude Code session to add one and update
`tools/scan_engine.py:scan_external_causes`.

## Financial ledger, reviews, competitor snapshots and POS - `data/imports/*.csv`

Drop these in `data/imports/` (a hotel's own export, or a hand-maintained
spreadsheet saved as CSV). Until they exist, the corresponding tool answers
`"connected": false` and says so in plain words - see
`tools/toolkit.py:_not_connected`. `make demo` is unaffected: it always runs
on the bundled `fixtures/hotel/*.json`, whatever is or is not in
`data/imports/`.

**The daily scan (`tools/scan.py`)** prints its own "not connected" note per
source, every time it uses one. `financial_daily.csv` and `reviews.csv`
still fall back to the bundled Hotel Aurora sample when missing, same as
`make demo`; `competitor_snapshots.csv` and `pos_items.csv` are different -
either one missing SKIPS the competitor-watch check on real data instead of
comparing against the sample, so a real (non-demo) run can never propose
action on an item ("Aurora Burger") that does not exist at your property.
Any project the scan opens while a source was unconnected is tagged
`[SAMPLE]` in `tools/project.py`/`tools/review.py` show and list - see
docs/how-it-works.md "Design decisions" 13.

- `financial_daily.csv` - `date, revenue_rooms, revenue_fnb, revenue_spa,
  occupancy_pct, rooms_available`. One row per day. This is what the scan's
  RevPAR, occupancy-vs-last-year and department-pace steps read.
- `reviews.csv` - `id, review_date, rating, category, source, guest_name,
  text`. `category` should be `fnb`, `rooms` or `spa` - it drives the review
  sentiment step and the recovery tracker.
- `competitor_snapshots.csv` - `competitor, scraped_on, category, item,
  price`. At least two scrape dates per item for the competitor watch to
  compute a move.
- `pos_items.csv` - `item_id, item, venue, price`. Your own menu, current
  prices - what a `pos_update` step proposes against.
- `pos_sales_daily.csv` - `date, item_id, units, revenue, covers`. Nightly
  closes - what the `measure` step reads for a baseline and a target month.
  **Never fabricated** - see docs/how-it-works.md "Design decisions" 6.

Headers are matched by exact name; extra columns are ignored.

## Email - `systems.email.adapter`

| Adapter | Status | Needs |
|---|---|---|
| `mock` | universal | nothing - what `make demo` uses |
| `imap` | universal | mailbox + app password. **Start here.** |
| `gmail` | built | Google OAuth desktop client |

```
EMAIL_ADDRESS=reservations@example.com
EMAIL_PASSWORD=            # an APP password, never your login password
IMAP_HOST=imap.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
```

Used for exactly one thing: sending an approved project email
(`python3 tools/review.py send`, or `python3 tools/project.py send-step`).
Nothing else in this repo reads or sends mail.

## Messaging - `systems.messaging.adapter`

| Adapter | Status | Needs |
|---|---|---|
| `mock` | universal | nothing |
| `unipile` | built | your own UniPile account |
| `webhook` | universal | any URL |

Used for exactly one thing: `notify_staff()` with an approved
`marketing_action` brief (a front-desk script change, a flyer). Set
`systems.messaging.staff_chat_id` in `config/hotel.yaml`.

## Sheets - `systems.sheets.adapter`

| Adapter | Status | Needs |
|---|---|---|
| `csv` | universal | nothing - writes `data/exports/resolved_projects.csv` |
| `google` | built | service account JSON |

`python3 tools/report.py --export` writes one row per resolved project
(title, mode, measured impact, resolved date).

## Implement your own

<a id="implement-your-own"></a>

**The POS price write is the one gap worth closing.** `core/adapters/base.py`'s
`POS` stub has no `set_price()` yet (point-of-sale integrations vary too much
for one interface) - `python3 tools/project.py apply-pos` reports this
plainly and keeps the approval, it does not crash. To add it, open `claude`
in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> Add a `set_price(item_id, price)` method to `PosStub` in
> `core/adapters/domain_stub.py` (or a new adapter) for **<your POS>**, guarded
> with `@guarded_write("pos_price_change")`. Its docs are at **<url>** and I
> have credentials in `.env` as `<VAR names>`.

For any other system (a real PMS, a rate-shopper API instead of a CSV, a
review-platform API): copy the shape of `core/adapters/pms_csv.py` or
`email_imap.py`, implement `ping()` and `capabilities()` first, register it
in `core/adapters/__init__.py`, and run `make doctor`. Full recipe and the
five steps: see any other repo in this family's `docs/integrations.md`, or
ask Claude Code to walk you through `core/adapters/base.py` directly - the
interface is small and heavily commented.

### Rules that matter

- **`ping()` never raises.**
- **Every write is decorated** with `@guarded_write("<action>")` - without it
  an adapter can write while the agent is in shadow mode, which defeats the
  entire safety model.
- **Never log a credential.** `core/log.py` masks anything whose key looks
  like a secret, but do not rely on it.
- **`core/` is shared** across all 28 agents in this family. A hotel-specific
  tweak belongs in `tools/` or your own adapter file, not in `core/`.
