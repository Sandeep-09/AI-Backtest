"""
Deterministic completeness and consistency checks.

Each gap becomes a Question. Options are suggestions the user must click;
nothing is ever filled in automatically.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..instruments import CONTRACTS
from .schema import NEEDS_PERIOD, Group, Operand, StrategySpec


@dataclass
class Option:
    label: str
    path: str
    value: Any
    note: str | None = None


@dataclass
class Question:
    id: str
    path: str
    question: str
    severity: str = "required"  # required | warning
    options: list[Option] = field(default_factory=list)
    free_text_hint: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _walk_operands(g: Group | None):
    if g is None:
        return
    for c in g.conditions:
        if c.type == "compare":
            yield c.left
            yield c.right
        else:
            yield from _walk_operands(c)


def _check_operand(spec: StrategySpec, op: Operand, where: str, qs: list[Question]) -> None:
    if op.kind == "indicator":
        if not op.ref or spec.indicator(op.ref) is None:
            qs.append(Question(f"bad-ref-{where}-{op.ref}", where,
                               f"{where} refers to indicator '{op.ref}', which is not defined. Define it or change the condition."))
    elif op.kind == "price" and op.field is None:
        qs.append(Question(f"bad-field-{where}", where, f"{where} uses a price operand without saying which price (open/high/low/close)."))
    elif op.kind == "const" and op.value is None:
        qs.append(Question(f"bad-const-{where}", where, f"{where} uses a constant without a value."))


def check(spec: StrategySpec, coverage: dict | None = None) -> list[Question]:
    qs: list[Question] = []
    cov = coverage or {}

    # Market
    if spec.instrument is None:
        qs.append(Question("instrument", "instrument", "Which instrument should be tested?",
                           options=[Option(f"{r} ({c.name})", "instrument", r) for r, c in CONTRACTS.items()]))
    if spec.timeframe is None:
        qs.append(Question("timeframe", "timeframe", "Which bar timeframe should signals be evaluated on?",
                           options=[Option(t, "timeframe", t) for t in ("1m", "5m", "15m", "1h")],
                           free_text_hint="Supported: 1m 2m 3m 5m 10m 15m 30m 1h 2h 4h"))
    if spec.session is None:
        c = CONTRACTS.get(spec.instrument or "NQ")
        qs.append(Question("session", "session", "Which session should be traded?", options=[
            Option(f"RTH ({c.rth_note})", "session", {"type": "rth", "tz": "America/New_York"}),
            Option("ETH / full Globex 18:00-17:00 ET", "session", {"type": "eth", "tz": "America/New_York"}),
        ], free_text_hint="Or describe a custom window, e.g. 09:30-11:30 ET"))
    else:
        if spec.session.type == "custom" and (not spec.session.start or not spec.session.end):
            qs.append(Question("session-window", "session", "The custom session needs a start and end time (HH:MM)."))
        if spec.session.type != "eth" and spec.session.indicators_use is None:
            qs.append(Question("indicators-use", "session.indicators_use",
                               "Should indicators be calculated on session bars only, or on all hours (including overnight) while trading only the session?",
                               options=[Option("Session bars only", "session.indicators_use", "session_bars_only"),
                                        Option("All hours", "session.indicators_use", "all_hours")]))
    if spec.display_timezone is None:
        qs.append(Question("display-tz", "display_timezone", "Which timezone should charts and trade times be shown in?",
                           options=[Option("New York (ET)", "display_timezone", "America/New_York"),
                                    Option("Chicago (CT)", "display_timezone", "America/Chicago"),
                                    Option("UTC", "display_timezone", "UTC"),
                                    Option("India (IST)", "display_timezone", "Asia/Kolkata")]))
    if spec.directions is None:
        qs.append(Question("directions", "directions", "Should the strategy trade long, short, or both?",
                           options=[Option("Long and short", "directions", "both"), Option("Long only", "directions", "long"),
                                    Option("Short only", "directions", "short")]))

    # Data selection
    if spec.data.contract is None:
        rolls = (inst_cov_pre.get("rolls") or {}) if (inst_cov_pre := (cov.get(spec.instrument or "", {}) or {})) else {}
        def _roll_note(mode: str) -> str | None:
            r = rolls.get(mode)
            if r is None:
                return None
            return "rolls: " + (", ".join(f"{x['trade_date']} {x['from']}→{x['to']}" for x in r) if r else "none in your data")
        qs.append(Question("contract", "data.contract", "Which contract should be traded on each session (matters on roll days)?", options=[
            Option("Most active contract that session (matches your gap-fill files; uses same-day volume to pick the contract)",
                   "data.contract", "most_active_same_day", _roll_note("most_active_same_day")),
            Option("Front month by the previous session's volume (strict; no look-ahead in contract choice)",
                   "data.contract", "front_by_prior_volume", _roll_note("front_by_prior_volume")),
            Option("One specific contract", "data.contract", "specific"),
        ]))
    elif spec.data.contract == "specific" and not spec.data.symbol:
        syms = (cov.get(spec.instrument or "", {}) or {}).get("symbols", [])
        qs.append(Question("symbol", "data.symbol", "Which contract symbol?",
                           options=[Option(s, "data.symbol", s) for s in syms[:8]]))
    inst_cov = cov.get(spec.instrument or "", {}) if spec.instrument else {}
    if spec.data.start is None or spec.data.end is None:
        opts = []
        if inst_cov.get("first_day") and inst_cov.get("last_day"):
            opts.append(Option(f"All available data ({inst_cov['first_day']} to {inst_cov['last_day']})", "data",
                               {"contract": spec.data.contract, "symbol": spec.data.symbol,
                                "start": inst_cov["first_day"], "end": inst_cov["last_day"]}))
        qs.append(Question("dates", "data.start", "What date range should be backtested?", options=opts,
                           free_text_hint="e.g. 2024-01-01 to 2024-06-30"))
    elif spec.data.start > spec.data.end:
        qs.append(Question("dates-order", "data.start", "The start date is after the end date."))

    # Indicators
    for ind in spec.indicators:
        if ind.type in NEEDS_PERIOD and ind.period is None:
            qs.append(Question(f"ind-period-{ind.id}", "indicators", f"What period should {ind.type.upper()} '{ind.id}' use?"))
        if ind.type in ("ema", "sma") and ind.source is None:
            qs.append(Question(f"ind-source-{ind.id}", "indicators", f"Which price should {ind.type.upper()}({ind.period}) be calculated on?",
                               options=[Option("Close", "indicators", [i.model_dump(mode="json") | ({"source": "close"} if i.id == ind.id else {}) for i in spec.indicators])]))
    ids = [i.id for i in spec.indicators]
    if len(ids) != len(set(ids)):
        qs.append(Question("ind-dupe", "indicators", "Two indicators share the same id; give each a unique id."))

    # Entry
    if not spec.enabled_directions():
        qs.append(Question("entry-trigger", "entry.long.when",
                           "What exactly triggers an entry? Naming indicators does not define a signal "
                           "(e.g. 'go long when the close crosses above the 10 EMA').",
                           free_text_hint="Describe the long and/or short trigger in the chat box"))
    for d in spec.enabled_directions():
        rules = getattr(spec.entry, d)
        if rules is None or rules.when is None or not rules.when.conditions:
            qs.append(Question(f"entry-{d}", f"entry.{d}.when",
                               f"What exactly triggers a {d.upper()} entry? Naming indicators does not define a signal "
                               f"(e.g. 'close crosses above the 10 EMA while the 10 EMA is above the 20 EMA').",
                               free_text_hint="Describe the condition in words in the chat box"))
        else:
            for op in _walk_operands(rules.when):
                _check_operand(spec, op, f"entry.{d}.when", qs)
        if rules is not None and rules.exit_when is not None:
            for op in _walk_operands(rules.exit_when):
                _check_operand(spec, op, f"entry.{d}.exit_when", qs)
    if spec.entry.order is None:
        qs.append(Question("entry-order", "entry.order", "How and when is the entry executed after a signal?", options=[
            Option("Market order at the open of the next bar", "entry.order", {"type": "market_next_open", "valid_bars": 1}),
            Option("Stop order 1 tick beyond the signal bar's high (long) / low (short), valid 1 bar", "entry.order",
                   {"type": "stop_beyond_signal_bar", "offset_ticks": 1, "valid_bars": 1}),
        ]))
    elif spec.entry.order.type == "limit_at_level":
        if spec.entry.order.level is None:
            qs.append(Question("entry-limit-level", "entry.order", "At what price should the limit entry be placed?"))
        else:
            _check_operand(spec, spec.entry.order.level, "entry.order.level", qs)
        if spec.fills.limit_fill is None:
            qs.append(Question("limit-fill", "fills.limit_fill", "When does a resting limit order count as filled?", options=[
                Option("Price trades through the limit by 1 tick (conservative)", "fills.limit_fill", "trade_through"),
                Option("Price touches the limit", "fills.limit_fill", "touch")]))

    # Stop
    if spec.stop is None:
        qs.append(Question("stop", "stop", "Where is the stop-loss placed?", options=[
            Option("Signal bar low (long) / high (short), 1 tick beyond", "stop", {"type": "signal_bar_extreme", "offset_ticks": 1}),
            Option("Fixed distance in ticks from the fill", "stop", {"type": "fixed_ticks", "ticks": 40}, "edit the tick count after choosing"),
        ], free_text_hint="Or describe it, e.g. '1.5x ATR(14) from entry' or 'below the 20 EMA'"))
    else:
        s = spec.stop
        if s.type == "fixed_ticks" and not s.ticks:
            qs.append(Question("stop-ticks", "stop", "How many ticks from the fill is the stop?"))
        if s.type == "atr_multiple":
            if s.multiple is None:
                qs.append(Question("stop-atr-mult", "stop", "What ATR multiple is the stop distance?"))
            if not s.atr_ref or (spec.indicator(s.atr_ref) is None or spec.indicator(s.atr_ref).type != "atr"):
                qs.append(Question("stop-atr-ref", "stop", "Which ATR indicator (period) should the stop use? Add an ATR indicator."))
        if s.type == "swing_extreme" and not s.lookback:
            qs.append(Question("stop-swing", "stop", "How many bars back should the swing high/low be measured?"))
        if s.type == "indicator" and (not s.ref or spec.indicator(s.ref) is None):
            qs.append(Question("stop-ind", "stop", "Which indicator should the stop be placed at?"))
        if s.type == "furthest_of":
            if not s.levels or len(s.levels) < 2:
                qs.append(Question("stop-levels", "stop", "The 'lower/higher of' stop needs at least two levels to compare."))
            for op in s.levels or []:
                _check_operand(spec, op, "stop", qs)

    # Target / exits
    if spec.target is None:
        qs.append(Question("target", "target", "How is the profit target set?", options=[
            Option("2R (twice the initial risk)", "target", {"type": "r_multiple", "r": 2}),
            Option("No fixed target (exit by other rules)", "target", {"type": "none"}),
        ]))
    else:
        t = spec.target
        if t.type == "r_multiple" and t.r is None:
            qs.append(Question("target-r", "target", "What reward-to-risk multiple is the target?"))
        if t.type == "fixed_ticks" and not t.ticks:
            qs.append(Question("target-ticks", "target", "How many ticks from the fill is the target?"))
        if t.type == "indicator" and (not t.ref or spec.indicator(t.ref) is None):
            qs.append(Question("target-ind", "target", "Which indicator is the target?"))
        if t.type == "none":
            has_exit = any(getattr(spec.entry, d) and getattr(spec.entry, d).exit_when for d in spec.enabled_directions())
            m = spec.management
            if not (has_exit or m.time_exit_bars or m.trail or m.exit_on_opposite_signal or m.flat_at_session_end):
                qs.append(Question("no-exit", "target", "With no target, what closes a winning trade? Add an exit rule, trailing stop, time exit or session-end exit."))
        if t.type != "none" and spec.fills.limit_fill is None and not any(q.path == "fills.limit_fill" for q in qs):
            qs.append(Question("target-fill", "fills.limit_fill", "When does the profit target (a resting limit) count as filled?", options=[
                Option("Price trades through the target by 1 tick (conservative)", "fills.limit_fill", "trade_through"),
                Option("Price touches the target", "fills.limit_fill", "touch")]))

    m = spec.management
    if m.flat_at_session_end is None:
        qs.append(Question("flat-eod", "management.flat_at_session_end", "Should open trades be closed at the end of the session?",
                           options=[Option("Yes, exit at the last bar's close", "management.flat_at_session_end", True),
                                    Option("No, hold overnight", "management.flat_at_session_end", False)]))
    if m.trail is not None:
        if m.trail.type == "indicator" and (not m.trail.ref or spec.indicator(m.trail.ref) is None):
            qs.append(Question("trail-ref", "management.trail", "Which indicator should the trailing stop follow?"))
        if m.trail.type == "ticks" and not m.trail.ticks:
            qs.append(Question("trail-ticks", "management.trail", "How many ticks behind price should the trailing stop be?"))

    # Sizing and costs
    if spec.sizing is None:
        qs.append(Question("sizing", "sizing", "How many contracts per trade?", options=[
            Option("1 contract", "sizing", {"type": "fixed_contracts", "contracts": 1}),
            Option("Risk a fixed $ amount per trade", "sizing", {"type": "risk_usd", "risk_usd": 500, "max_contracts": 5},
                   "edit the amount after choosing"),
        ]))
    else:
        if spec.sizing.type == "fixed_contracts" and not spec.sizing.contracts:
            qs.append(Question("sizing-n", "sizing", "How many contracts?"))
        if spec.sizing.type == "risk_usd" and not spec.sizing.risk_usd:
            qs.append(Question("sizing-risk", "sizing", "How many dollars of risk per trade?"))
    if spec.costs.commission_per_side is None:
        qs.append(Question("commission", "costs.commission_per_side", "Commission and fees per contract per side (USD)?", options=[
            Option("$2.50 per side (typical retail all-in for NQ/GC)", "costs.commission_per_side", 2.5),
            Option("$0.75 per side (typical for micros)", "costs.commission_per_side", 0.75),
            Option("$0 (ignore commissions)", "costs.commission_per_side", 0.0)]))
    if spec.costs.slippage_ticks is None:
        qs.append(Question("slippage", "costs.slippage_ticks", "Slippage on market and stop fills, in ticks?", options=[
            Option("1 tick", "costs.slippage_ticks", 1), Option("2 ticks", "costs.slippage_ticks", 2),
            Option("0 ticks (optimistic)", "costs.slippage_ticks", 0)]))
    if spec.fills.same_bar_policy is None:
        qs.append(Question("same-bar", "fills.same_bar_policy",
                           "If one bar reaches both the stop and the target, which happened first?", options=[
                               Option("Use tick/sub-bar data when available, otherwise assume the stop hit first (conservative)",
                                      "fills.same_bar_policy", "intrabar_then_stop_first"),
                               Option("Always assume the stop hit first", "fills.same_bar_policy", "stop_first"),
                               Option("Use tick/sub-bar data, otherwise assume target first (optimistic)",
                                      "fills.same_bar_policy", "intrabar_then_target_first")]))
    return qs


def required_open(qs: list[Question]) -> list[Question]:
    return [q for q in qs if q.severity == "required"]
