"""Everything a backtest assumes, in plain words, shown before approval and stored with each run."""
from __future__ import annotations

from . import db
from .instruments import CONTRACTS
from .spec.schema import StrategySpec
from .summary.render import _session_text


def build(spec: StrategySpec, coverage: dict) -> dict:
    c = CONTRACTS.get(spec.instrument or "")
    cov = coverage.get(spec.instrument or "", {}) if spec.instrument else {}
    rows: list[dict] = []

    def add(k: str, v: str, missing: bool = False) -> None:
        rows.append({"label": k, "value": v, "missing": missing})

    add("Instrument", f"{spec.instrument} — {c.name}; tick {c.tick_size} = ${c.tick_value:g}; ${c.point_value:g} per point" if c else "not set", c is None)
    mode = spec.data.contract
    rolls = (cov.get("rolls") or {}).get(mode or "", []) if mode != "specific" else []
    in_range = [r for r in rolls if (not spec.data.start or r["trade_date"] >= str(spec.data.start)) and (not spec.data.end or r["trade_date"] <= str(spec.data.end))]
    add("Contract", {"most_active_same_day": "Most active contract each session (same-day volume)",
                     "front_by_prior_volume": "Front month by previous session's volume",
                     "specific": f"Only {spec.data.symbol}"}.get(mode or "", "not set") +
        (("; rolls in range: " + ", ".join(f"{r['trade_date']} {r['from']}→{r['to']}" for r in in_range)) if in_range else ""), mode is None)
    add("Date range (trading)", f"{spec.data.start} to {spec.data.end} (CME trade dates; a session starts 18:00 ET the evening before)"
        if spec.data.start and spec.data.end else "not set", not (spec.data.start and spec.data.end))
    if cov:
        add("Data available", f"{cov['first_day']} to {cov['last_day']} ({cov['trade_dates']} trade dates). Indicators warm up on data before the start date, within the same contract.")
    add("Timeframe", spec.timeframe or "not set", spec.timeframe is None)
    add("Session", _session_text(spec) if spec.session else "not set", spec.session is None)
    add("Timezone (display)", spec.display_timezone or "not set", spec.display_timezone is None)
    z = spec.sizing
    add("Sizing", ("not set" if z is None else f"{z.contracts} contract(s)" if z.type == "fixed_contracts"
                   else f"risk ${z.risk_usd:,.0f}/trade, max {z.max_contracts or '∞'} contracts, below 1 → {z.below_one}"), z is None)
    add("Commission", f"${spec.costs.commission_per_side:.2f} per contract per side (${2 * spec.costs.commission_per_side:.2f} round turn)"
        if spec.costs.commission_per_side is not None else "not set", spec.costs.commission_per_side is None)
    add("Slippage", f"{spec.costs.slippage_ticks:g} tick(s) on market and stop fills (= ${spec.costs.slippage_ticks * c.tick_value:g} per contract per fill); none on limit/target fills"
        if spec.costs.slippage_ticks is not None and c else "not set", spec.costs.slippage_ticks is None)
    add("Same-bar stop & target", {"intrabar_then_stop_first": "Replay trade prints inside the bar; if still unclear, stop first",
                                   "intrabar_then_target_first": "Replay trade prints inside the bar; if still unclear, target first",
                                   "stop_first": "Always stop first (no replay)", "target_first": "Always target first (no replay)"}.get(
        spec.fills.same_bar_policy or "", "not set"), spec.fills.same_bar_policy is None)
    add("Target / limit fills", {"touch": "Filled when price touches the level", "trade_through": "Filled only when price trades 1 tick through the level"}.get(
        spec.fills.limit_fill or "", "not set" if (spec.target and spec.target.type != "none") else "n/a"),
        spec.fills.limit_fill is None and bool(spec.target and spec.target.type != "none"))
    gran = cov.get("intrabar") or []
    if cov:
        add("Data granularity", ("Bars are built from individual trade prints (trades schema and the trade records of MBO), using exchange timestamps (ts_event). "
                                 "Trade prints are replayed inside bars to order stop/target/entry events." if "tick" in gran else
                                 "Only bar data is available: events inside a bar can't be ordered; the same-bar policy decides."))

    limits = [
        "Fills are driven by trade prints, not the bid/ask. A stop triggers when a trade prints at or through it.",
        "No order-queue modelling: the Level-3 order book is not used to estimate queue position, so limit and target fills follow the touch / trade-through rule above.",
        "Slippage is a fixed number of ticks; there is no market-impact or book-depth model.",
        "Prices are not back-adjusted at rolls. Indicators restart (with warm-up) on each new contract, and open positions are closed at the last bar before a roll.",
        "Working orders are cancelled at the end of each session; one position at a time; no pyramiding or automatic reversals.",
        "Breakeven and trailing-stop changes are decided at bar closes and take effect from the next bar.",
        "A signal is evaluated on the closed signal bar only; orders fill no earlier than the following bar.",
    ]
    if mode == "most_active_same_day":
        limits.append("Contract choice uses each session's own volume, information not fully known at the session's start. Signals and fills are unaffected within a contract.")
    if spec.session and spec.session.type != "eth" and spec.session.indicators_use == "session_bars_only" and spec.management.flat_at_session_end is False:
        limits.append("With session-only bars, positions held overnight are only checked again at the next session's first bar (gaps fill at that open).")
    if cov.get("synthetic"):
        limits.insert(0, "SOME OR ALL DATA IS SYNTHETIC TEST DATA, NOT REAL MARKET DATA.")
    issues = [i for i in db.issues("data") if i["ref"] == spec.instrument] if spec.instrument else []
    return {"rows": rows, "limitations": limits, "data_issues": issues}
