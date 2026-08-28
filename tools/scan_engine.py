"""tools/scan_engine.py - the deterministic daily scan.

Ported from the demo's ``advisor-engine.ts`` (``specs/strategic-advisor-ai.md``
section 3A). Every number here is plain Python over the property's own data;
the LLM (task ``narrate``, called from tools/scan.py) only writes prose
around whatever verdict this module already reached - it never sees enough
to change the verdict. See docs/how-it-works.md for the full walk-through.

``run_scan()`` takes plain data (rows, existing projects, rule/threshold
config) and returns a :class:`ScanResult` - no I/O, no store, no LLM, so it
is trivial to unit test against invented numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools import data as sa_data

DEPARTMENTS = (
    {"key": "revenue_rooms", "label": "Rooms", "metric_key": "revenue_rooms"},
    {"key": "revenue_fnb", "label": "F&B", "metric_key": "revenue_fnb"},
    {"key": "revenue_spa", "label": "Spa", "metric_key": "revenue_spa"},
)


@dataclass
class ChecklistLine:
    label: str
    value: str
    delta: float | None
    verdict: str  # "ok" | "warn"

    def as_dict(self) -> dict:
        return {"label": self.label, "value": self.value, "delta": self.delta,
                "verdict": self.verdict}


@dataclass
class ScanResult:
    steps: list[str] = field(default_factory=list)
    checklist: list[ChecklistLine] = field(default_factory=list)
    verdict: str = "stable"          # stable | anomaly | opportunity | watch
    headline: str = ""
    anomalies: list[dict] = field(default_factory=list)   # unsuppressed only
    opportunity: dict | None = None

    def as_dict(self) -> dict:
        return {"steps": self.steps, "checklist": [c.as_dict() for c in self.checklist],
                "verdict": self.verdict, "headline": self.headline,
                "anomalies": self.anomalies, "opportunity": self.opportunity}


def _on(rules: dict[str, bool], rule_id: str) -> bool:
    """A rule missing from the config counts as enabled - matches the source
    engine's ``on(id)``."""
    return bool(rules.get(rule_id, True))


def _fmt_money(value: float, currency: str) -> str:
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency + " ")
    return f"{symbol}{value:,.0f}"


# --------------------------------------------------------------------------
# suppression - generalised to every metric_key, not just F&B (fixes the
# spec's open questions 3 and 4: no more title-substring matching).
# --------------------------------------------------------------------------
def _covering_project(metric_key: str, projects: list[dict], today: str,
                      suppression_days: int) -> dict | None:
    """A ``fix`` project on ``metric_key`` covers it while active, and for
    ``suppression_days`` after it resolved - "an operational fix takes time
    to wash through revenue" (source engine comment, verbatim)."""
    cutoff = sa_data.add_days(today, -suppression_days)
    for p in projects:
        if p.get("mode") != "fix" or p.get("metric_key") != metric_key:
            continue
        if p.get("status") != "resolved":
            return p
        resolved_on = p.get("resolved_on") or ""
        if resolved_on >= cutoff:
            return p
    return None


def _anomaly_line(metric_key: str, label: str, value_str: str, delta: float,
                  projects: list[dict], today: str, suppression_days: int,
                  headline_fact: str) -> tuple[ChecklistLine, dict | None, str]:
    """One warn-threshold metric: returns ``(checklist_line, anomaly_or_None,
    step_message)``. ``headline_fact`` is the plain-English fact used both in
    the step narration and, if unsuppressed, the verdict headline."""
    covering = _covering_project(metric_key, projects, today, suppression_days)
    line = ChecklistLine(label, value_str, round(delta, 1), "warn")
    if covering is None:
        return line, {"metric_key": metric_key, "label": label, "delta": delta,
                      "fact": headline_fact}, f"{headline_fact} — new anomaly, needs investigation."
    if covering.get("status") != "resolved":
        msg = (f"{headline_fact} — already covered by the active project "
               f"'{covering['title']}' — being handled, not re-opening.")
        return line, None, msg
    msg = (f"{headline_fact} — root cause fixed by '{covering['title']}' (just resolved) — "
           f"recovery still washing through the numbers, watching.")
    return line, None, msg


def scan_revpar(rows: list[dict], today: str, thresholds: dict, currency: str,
                projects: list[dict]) -> tuple[ChecklistLine, dict | None, str]:
    last7 = sa_data.last_n_days(today, 7)
    prior7 = sa_data.days_between(sa_data.add_days(today, -14), sa_data.add_days(today, -8))
    avg_last = sa_data.mean(sa_data.financial_window(rows, last7[0], last7[-1]), "revpar")
    avg_prior = sa_data.mean(sa_data.financial_window(rows, prior7[0], prior7[-1]), "revpar")
    delta = sa_data.pct_change(avg_last, avg_prior)
    value_str = _fmt_money(avg_last, currency)
    if delta is None or delta > thresholds.get("revpar_warn_pct", -8.0):
        return (ChecklistLine("RevPAR vs last week", value_str,
                              round(delta, 1) if delta is not None else None, "ok"),
               None, f"RevPAR is {value_str} ({'flat' if delta is None else f'{delta:+.1f}%'}) — steady.")
    fact = f"RevPAR is {value_str}, {delta:+.1f}% vs the prior 7 days"
    return _anomaly_line("revpar", "RevPAR vs last week", value_str, delta, projects,
                         today, thresholds.get("suppression_days", 14), fact)


def scan_occupancy_yoy(rows: list[dict], today: str, thresholds: dict, rules: dict,
                       projects: list[dict]) -> tuple[ChecklistLine, dict | None, str]:
    if not _on(rules, "yoy-baseline"):
        return (ChecklistLine("Occupancy vs last year", "n/a", None, "ok"), None,
                "Year-over-year baseline check skipped — disabled in the Advisor's rules.")
    this_month, ly_month = sa_data.month_key(today), sa_data.shift_month(sa_data.month_key(today), -12)
    now_rows = [r for r in rows if sa_data.month_key(r["date"]) == this_month]
    ly_rows = [r for r in rows if sa_data.month_key(r["date"]) == ly_month]
    occ_now, occ_ly = sa_data.mean(now_rows, "occupancy_pct"), sa_data.mean(ly_rows, "occupancy_pct")
    diff = round(occ_now - occ_ly, 1)
    value_str = f"{occ_now:.1f}%"
    if diff > thresholds.get("occupancy_warn_pts", -5.0):
        return (ChecklistLine("Occupancy vs last year", value_str, diff, "ok"), None,
                f"Occupancy is {value_str} ({diff:+.1f} pts vs last year) — on pace.")
    fact = f"Occupancy is {value_str}, {diff:+.1f} points vs the same month last year"
    return _anomaly_line("occupancy", "Occupancy vs last year", value_str, diff, projects,
                         today, thresholds.get("suppression_days", 14), fact)


def scan_department_pace(rows: list[dict], today: str, thresholds: dict, currency: str,
                         projects: list[dict]) -> tuple[list[ChecklistLine], list[dict], list[str]]:
    this_month, last_month = sa_data.month_key(today), sa_data.shift_month(sa_data.month_key(today), -1)
    lines, anomalies, steps = [], [], []
    for dept in DEPARTMENTS:
        now_rows = [r for r in rows if sa_data.month_key(r["date"]) == this_month]
        last_rows = [r for r in rows if sa_data.month_key(r["date"]) == last_month]
        pace_now = sa_data.mean(now_rows, dept["key"])
        pace_last = sa_data.mean(last_rows, dept["key"])
        delta = sa_data.pct_change(pace_now, pace_last)
        value_str = f"{currency_symbol(currency)}{pace_now / 1000:.1f}k/day"
        if delta is None or delta > thresholds.get("dept_pace_warn_pct", -5.0):
            lines.append(ChecklistLine(f"{dept['label']} revenue pace", value_str,
                                       round(delta, 1) if delta is not None else None, "ok"))
            steps.append(f"{dept['label']} pace: {value_str} — on pace vs last month.")
            continue
        fact = f"{dept['label']} pace is {value_str}, {delta:+.1f}% vs last month"
        line, anomaly, msg = _anomaly_line(dept["metric_key"], f"{dept['label']} revenue pace",
                                           value_str, delta, projects, today,
                                           thresholds.get("suppression_days", 14), fact)
        lines.append(line)
        steps.append(msg)
        if anomaly:
            anomaly["dept_label"] = dept["label"]
            anomalies.append(anomaly)
    return lines, anomalies, steps


def currency_symbol(currency: str) -> str:
    return {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency + " ")


def scan_review_sentiment(reviews: list[dict], today: str, thresholds: dict,
                          projects: list[dict]) -> tuple[ChecklistLine, dict | None, str]:
    last7 = sa_data.last_n_days(today, 7)
    window = sa_data.reviews_window(reviews, last7[0], last7[-1]) if last7 else []
    pos = sum(1 for r in window if r.get("rating", 0) >= 4)
    neg = sum(1 for r in window if r.get("rating", 0) <= 3)
    value_str = f"{pos} positive, {neg} negative"
    if neg < thresholds.get("review_negative_warn", 2):
        return (ChecklistLine("Review sentiment (last 7 days)", value_str, None, "ok"), None,
                f"Review sentiment: {value_str} — no negative cluster forming.")
    fact = f"Review sentiment shows {neg} negative reviews in the last 7 days"
    return _anomaly_line("review_sentiment", "Review sentiment (last 7 days)", value_str,
                         -float(neg), projects, today, thresholds.get("suppression_days", 14),
                         fact)


def scan_external_causes(rules: dict) -> tuple[ChecklistLine, str]:
    """Honestly labelled placeholder - see docs/how-it-works.md "Design
    decisions" 5. No news/events/weather feed exists in this template."""
    if not _on(rules, "rule-out-external"):
        return (ChecklistLine("News, events & weather", "skipped", None, "ok"),
                "External-causes check skipped — disabled in the Advisor's rules.")
    return (ChecklistLine("News, events & weather", "not checked (no feed configured)", None, "ok"),
           "External causes not checked — no news/events/weather feed is configured "
           "(see docs/integrations.md). Treat this line as a gap, not a clean bill of health.")


# --------------------------------------------------------------------------
# competitor watch - generalised from the demo's hard-coded "burger" match
# (open question 5): shared, non-stopword name tokens instead of one literal
# word, so any own menu item can be the opportunity, not only a burger.
# --------------------------------------------------------------------------
_STOPWORDS = {"the", "a", "an", "and", "of", "with", "special", "our", "house"}


def _name_tokens(name: str) -> set[str]:
    return {t for t in name.lower().split() if t not in _STOPWORDS and len(t) > 2}


def scan_competitor_watch(snapshots: list[dict], pos_items: list[dict], today: str,
                          thresholds: dict, rules: dict,
                          projects: list[dict]) -> tuple[ChecklistLine, dict | None, str]:
    if not _on(rules, "competitor-watch"):
        return (ChecklistLine("Competitor watch", "disabled", None, "ok"), None,
                "Competitor watch: opportunity scanning disabled by rule.")

    by_pair: dict[tuple[str, str], list[dict]] = {}
    for row in snapshots:
        key = (row.get("competitor", ""), row.get("item", ""))
        by_pair.setdefault(key, []).append(row)

    changed = []
    for (competitor, item), pts in by_pair.items():
        pts = sorted(pts, key=lambda r: r.get("scraped_on", ""))
        first, last = pts[0], pts[-1]
        if first.get("scraped_on") == last.get("scraped_on") or not first.get("price"):
            continue
        delta = (last["price"] - first["price"]) / first["price"] * 100.0
        changed.append({"competitor": competitor, "item": item, "category": first.get("category", ""),
                        "from": first["price"], "to": last["price"], "delta": delta,
                        "since": first.get("scraped_on", "")})

    if not changed:
        return (ChecklistLine("Competitor watch", "no comparable price history", None, "ok"),
               None, "Competitor watch: not enough price history yet to compare.")

    avg_delta = sum(c["delta"] for c in changed) / len(changed)
    our_tokens = {item.get("item", ""): _name_tokens(item.get("item", "")) for item in pos_items}
    matches = []
    for c in changed:
        comp_tokens = _name_tokens(c["item"])
        for our_name, tokens in our_tokens.items():
            if tokens & comp_tokens:
                matches.append({**c, "our_item": our_name})
    value_str = f"{len(changed)} item(s) compared, {avg_delta:+.1f}% avg"

    if not matches:
        return (ChecklistLine("Competitor watch", value_str, round(avg_delta, 1), "ok"), None,
               f"Competitor watch: {value_str} — nothing matching our own menu moved enough to act on.")

    matches.sort(key=lambda m: m["delta"], reverse=True)
    best = matches[0]
    threshold = thresholds.get("competitor_opportunity_pct", 8.0)
    if best["delta"] < threshold:
        return (ChecklistLine("Competitor watch", value_str, round(avg_delta, 1), "ok"), None,
               f"Competitor watch: {value_str} — the strongest move ({best['delta']:+.1f}% on "
               f"{best['item']}) is too small to act on, keeping the monthly watch.")

    line = ChecklistLine("Competitor watch", value_str, round(avg_delta, 1), "warn")
    moves = "; ".join(f"{m['competitor']} {m['item']} {m['from']:.0f}->{m['to']:.0f} "
                      f"({m['delta']:+.1f}%)" for m in matches[:3])
    metric_key = f"competitor_price:{best['our_item']}"
    covering = _covering_project(metric_key, projects, today, thresholds.get("suppression_days", 14))
    if covering is not None:
        msg = (f"Opportunity: our {best['our_item']} has not moved while competitors went up "
              f"{best['delta']:+.1f}% ({moves}) — already covered by '{covering['title']}'.")
        return line, None, msg
    msg = (f"Opportunity: our {best['our_item']} has not moved while competitors went up "
          f"{best['delta']:+.1f}% ({moves}).")
    opportunity = {"metric_key": metric_key, "item": best["our_item"], "category": best["category"],
                  "avg_delta": round(avg_delta, 1), "best_delta": round(best["delta"], 1),
                  "moves": matches[:3], "headline_fact": f"Competitors raised {best['category']} "
                  f"prices {best['delta']:+.1f}% since {best['since']} — {best['our_item']} "
                  f"pricing opportunity"}
    return line, opportunity, msg


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------
def run_scan(*, financial_rows: list[dict], reviews: list[dict], competitor_snapshots: list[dict],
            pos_items: list[dict], projects: list[dict], rules: dict, thresholds: dict,
            currency: str, today: str) -> ScanResult:
    """The whole daily scan, steps 1-8. Pure function: no I/O, no store, no
    LLM. ``projects`` is a plain list of dicts (id, title, mode, status,
    metric_key, resolved_on) - ``tools/scan.py`` loads these from
    ``advisor_projects`` before calling this."""
    result = ScanResult()
    result.steps.append(
        f"{len(financial_rows)} days of financials, {len(reviews)} tagged reviews, "
        f"{len(competitor_snapshots)} competitor price points, "
        f"{sum(1 for p in projects if p.get('status') != 'resolved')} open project(s).")

    revpar_line, revpar_anomaly, revpar_step = scan_revpar(
        financial_rows, today, thresholds, currency, projects)
    result.checklist.append(revpar_line)
    result.steps.append(revpar_step)
    if revpar_anomaly:
        result.anomalies.append(revpar_anomaly)

    occ_line, occ_anomaly, occ_step = scan_occupancy_yoy(financial_rows, today, thresholds, rules, projects)
    result.checklist.append(occ_line)
    result.steps.append(occ_step)
    if occ_anomaly:
        result.anomalies.append(occ_anomaly)

    dept_lines, dept_anomalies, dept_steps = scan_department_pace(
        financial_rows, today, thresholds, currency, projects)
    result.checklist.extend(dept_lines)
    result.steps.extend(dept_steps)
    result.anomalies.extend(dept_anomalies)

    review_line, review_anomaly, review_step = scan_review_sentiment(reviews, today, thresholds, projects)
    result.checklist.append(review_line)
    result.steps.append(review_step)
    if review_anomaly:
        result.anomalies.append(review_anomaly)

    ext_line, ext_step = scan_external_causes(rules)
    result.checklist.append(ext_line)
    result.steps.append(ext_step)

    if result.anomalies:
        worst = min(result.anomalies, key=lambda a: a["delta"])
        result.verdict = "anomaly"
        headline = f"{worst['fact']} — new anomaly, investigation needed."
        if _on(rules, "verify-cross-sources"):
            headline += " Cross-checked against volume, sentiment and external causes."
        result.headline = headline
        result.steps.append(f"Verdict: anomaly. Worst signal: {worst['label']}.")
        return result

    comp_line, opportunity, comp_step = scan_competitor_watch(
        competitor_snapshots, pos_items, today, thresholds, rules, projects)
    result.checklist.append(comp_line)
    result.steps.append(comp_step)

    # Every department line still at "warn" here was suppressed (an
    # unsuppressed one would already have sent us into the anomaly branch
    # above), so this is exactly the set of known, covered dips.
    suppressed_labels = [line.label for line in dept_lines if line.verdict == "warn"]
    if opportunity is not None:
        result.verdict = "opportunity"
        result.opportunity = opportunity
        headline = opportunity["headline_fact"]
        if suppressed_labels:
            headline += f" (stable apart from the known {suppressed_labels[0]} dip)"
        headline += " (proposal awaiting approval)"
        result.headline = headline
        return result

    result.verdict = "stable"
    if suppressed_labels:
        result.headline = f"Stable apart from the known {suppressed_labels[0]} dip (recovery underway)."
    else:
        result.headline = "Business stable across every department."
    if _on(rules, "verify-cross-sources"):
        result.headline += " Cross-checked against volume, sentiment and external causes."
    return result
