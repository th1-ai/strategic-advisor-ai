# Workflow: shadow to live

Objective: decide, together with the hotel, whether the Strategist is ready
to send approved emails and marketing actions, and actually apply approved
price changes, instead of only drafting and scheduling them - and make the
change safely if so.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly
what changes.

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` is expected
      until you flip it. `warn` on the financial ledger, reviews or
      competitor snapshots means those sources are still on the bundled
      fixture - connect them first (`docs/integrations.md`), or the daily
      scan is only ever scanning invented Hotel Aurora numbers.
- [ ] `config/hotel.yaml` has the real property name, address, currency and
      contact details, and `knowledge/property.md`, `knowledge/faq.md` and
      `knowledge/strategy-rules.md` exist and are accurate.
- [ ] `config/agent.yaml: thresholds:` reflect this property, not the
      shipped defaults - a 42-room boutique hotel and a 400-room resort do
      not warn at the same RevPAR swing.
- [ ] At least one real scan (`python3 tools/run.py --once --scan`) has
      opened a real project and gone through the review queue - not just
      the demo fixtures.
- [ ] The hotel has read and edited enough drafted emails and marketing
      briefs to trust the tone, and has decided who `to_role` should
      actually reach (`config/hotel.yaml: contacts.manager`).
- [ ] `python3 tools/project.py list` shows no project stuck mid-flight for
      a reason nobody has looked at.
- [ ] A real POS price-change path is decided: either
      `core/adapters/domain_stub.py`'s `PosStub` has a real `set_price()`
      (see `docs/integrations.md#implement-your-own`), or the hotel has
      agreed that `pos_update` steps will always be applied by a person by
      hand once scheduled, reading the step's `to`/`effective` fields with
      `python3 tools/project.py show`.
- [ ] A real mailbox is connected (`systems.email.adapter: imap` or
      `gmail`) and, if `marketing_action` is used, a real messaging channel
      - `make doctor` shows both healthy.

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `send_email`, `send_message`
   and `pos_price_change` by default - it should. Going live means
   **approved drafts get sent and scheduled changes get applied**, not that
   the Strategist starts acting without your sign-off. There is no config
   that changes that.
3. Clear the shadow-era queue so nothing old goes out by surprise:
   ```bash
   python3 tools/review.py stale
   ```
4. Run `make doctor` again to confirm.
5. Run one real scan and manually watch a send go through:
   ```bash
   python3 tools/run.py --once --scan
   make review
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
6. Tell the hotel exactly what just changed: an approved email or marketing
   action now actually goes out the next time someone (or a scheduled job)
   runs `python3 tools/review.py send`; an approved price change actually
   applies the next time someone runs `python3 tools/project.py apply-pos`
   on or after its effective date. Everything still waits for your
   approval first - going live only removes the second, redundant block
   that `mode: shadow` added on top of that.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound action and every price application on the next pass,
mid-schedule, with no other change required.
