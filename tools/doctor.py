#!/usr/bin/env python3
"""tools/doctor.py - is the Strategist configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus
checks specific to this agent: the tool specs load, the financial ledger and
reviews and competitor snapshots are readable, and every prompt/schema file
is present. Exits 0 when everything passed, 1 when a FAIL line needs fixing.
Never a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.config import repo_root as _repo_root  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402


def check_tools(settings: Settings) -> Check:
    try:
        from tools.toolkit import TOOL_SPECS
    except Exception as exc:  # noqa: BLE001
        return Check("tools", FAIL, f"tools/toolkit.py failed to import: {exc}"[:160],
                     "Run `make test` to see the full traceback.")
    return Check("tools", PASS, f"{len(TOOL_SPECS)} tools: "
                 f"{', '.join(s['name'] for s in TOOL_SPECS)}")


def check_financial_data(settings: Settings) -> Check:
    """PASS/WARN comes from `financial_connected()` - the same boolean the
    real scan and `get_financial_metrics` use - never from whether rows came
    back, because a missing CSV still returns the bundled Hotel Aurora
    fixture rows (SIMULATION.md Finding 1b: this used to read `rows` and so
    showed PASS with a fixture row count even fully disconnected)."""
    from tools.data import financial_connected, load_financial_daily
    try:
        rows = load_financial_daily(settings)
    except Exception as exc:  # noqa: BLE001
        return Check("financial ledger", FAIL, f"could not load: {exc}"[:160],
                     "Check data/imports/financial_daily.csv, or restore "
                     "fixtures/hotel/financial_daily.json from git.")
    if not financial_connected():
        return Check("financial ledger", WARN,
                     f"not connected - a real scan skips or falls back to the bundled Hotel "
                     f"Aurora sample ({len(rows)} row(s))",
                     "Add data/imports/financial_daily.csv. See docs/integrations.md.")
    if not rows:
        return Check("financial ledger", WARN, "connected but no rows found",
                     "data/imports/financial_daily.csv exists but is empty.")
    return Check("financial ledger", PASS, f"{len(rows)} day(s), {rows[0]['date']} to {rows[-1]['date']}")


def check_reviews_and_competitors(settings: Settings) -> list[Check]:
    """Same fix as ``check_financial_data``, for the other three CSV
    sources: PASS/WARN comes from the ``*_connected()`` booleans, never from
    row counts, which the bundled fixture always supplies."""
    from tools.data import (competitors_connected, load_competitor_snapshots, load_pos_items,
                            load_reviews, pos_items_connected, reviews_connected)
    out = []
    try:
        reviews = load_reviews(settings)
        comps = load_competitor_snapshots(settings)
        pos_items = load_pos_items(settings)
    except Exception as exc:  # noqa: BLE001
        return [Check("reviews / competitors / POS items", FAIL, f"could not load: {exc}"[:160], "")]
    out.append(Check("reviews", PASS if reviews_connected() else WARN,
                     f"{len(reviews)} review(s)" if reviews_connected()
                     else f"not connected ({len(reviews)} bundled sample row(s))",
                     "" if reviews_connected() else "The scan's review-sentiment check falls "
                     "back to the Hotel Aurora sample until data/imports/reviews.csv exists."))
    out.append(Check("competitor snapshots", PASS if competitors_connected() else WARN,
                     f"{len(comps)} price point(s)" if competitors_connected()
                     else f"not connected ({len(comps)} bundled sample row(s))",
                     "" if competitors_connected() else "A real scan SKIPS the competitor "
                     "watch (rather than comparing against the sample) until "
                     "data/imports/competitor_snapshots.csv exists."))
    out.append(Check("POS items", PASS if pos_items_connected() else WARN,
                     f"{len(pos_items)} item(s)" if pos_items_connected()
                     else f"not connected ({len(pos_items)} bundled sample row(s))",
                     "" if pos_items_connected() else "A real scan SKIPS the competitor watch "
                     "(it cannot match a competitor move to your own menu) until "
                     "data/imports/pos_items.csv exists."))
    return out


def check_ai_disclosure() -> Check:
    """Go-live blocker (SIMULATION.md Finding 3): ``knowledge/signature.md``
    (email) and ``knowledge/disclosure.md`` (chat) are where the EU AI Act
    Article 50 line lives - `core.adapters.base.Email.signature()` silently
    returns "" when the file is missing, so unlike every other missing
    `knowledge/*.md` file this one needs a hard FAIL, not a WARN, or a hotel
    could reach `mode: live` with the disclosure line missing from every
    outgoing project email and no error anywhere."""
    missing, unedited = [], []
    kdir = _repo_root() / "knowledge"  # core.config.repo_root() - respects
                                       # AGENT_REPO_ROOT so this is testable
    for name in ("signature.md", "disclosure.md"):
        real = kdir / name
        example = kdir / name.replace(".md", ".example.md")
        if not real.is_file():
            missing.append(name)
        elif example.is_file() and real.read_text(encoding="utf-8") == example.read_text(encoding="utf-8"):
            unedited.append(name)
    if missing:
        return Check("AI disclosure", FAIL, f"missing: {', '.join(missing)}",
                     "cp knowledge/signature.example.md knowledge/signature.md (and the same "
                     "for disclosure.md), then edit both. See workflows/00-setup.md step 3.")
    if unedited:
        return Check("AI disclosure", FAIL,
                     f"still byte-identical to the .example.md: {', '.join(unedited)}",
                     "Edit knowledge/signature.md and knowledge/disclosure.md with your own "
                     "sign-off and disclosure wording - see docs/safety.md.")
    return Check("AI disclosure", PASS, "knowledge/signature.md and knowledge/disclosure.md "
                 "exist and have been edited")


def check_knowledge_base(settings: Settings) -> Check:
    try:
        from tools.data import knowledge_passages
        passages = knowledge_passages()
    except Exception as exc:  # noqa: BLE001
        return Check("knowledge search", FAIL, f"could not load passages: {exc}"[:160], "")
    if not passages:
        return Check("knowledge search", WARN, "no passages found",
                     "search_knowledge_base will always fall back to nothing. Fill in "
                     "knowledge/property.md and knowledge/faq.md.")
    return Check("knowledge search", PASS, f"{len(passages)} passage(s) indexed")


def check_prompt_files() -> Check:
    missing = [p for p in ("prompts/narrate.md", "prompts/schemas/narrate.json",
                           "prompts/draft_project.md", "prompts/schemas/draft_project.json",
                           "prompts/ask.md", "prompts/schemas/step.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "narrate / draft_project / ask + their schemas present")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Strategic Advisor AI - doctor")

    checks = run_checks(settings, extra=[check_tools, check_financial_data, check_knowledge_base])
    checks.extend(check_reviews_and_competitors(settings))
    checks.append(check_prompt_files())
    checks.append(check_ai_disclosure())
    return print_table(checks, title="Strategic Advisor AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
