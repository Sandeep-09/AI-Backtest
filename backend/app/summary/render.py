"""
Plain-English summary rendered *from the spec*. The LLM never writes this
text, so the summary can't drift from what is tested.
"""
from __future__ import annotations

from ..instruments import CONTRACTS, ETH_END, ETH_START
from ..spec.schema import Compare, Group, Indicator, Operand, StrategySpec

MISSING = "⚠ Not specified yet"

OP_TEXT = {
    "gt": "is above",
    "lt": "is below",
    "gte": "is at or above",
    "lte": "is at or below",
    "crosses_above": "crosses above",
    "crosses_below": "crosses below",
    "touches": "touches",
}


def indicator_label(ind: Indicator | None, ref: str | None = None) -> str:
    if ind is None:
        return f"[undefined indicator '{ref}']"
    t = ind.type
    src = f" of {ind.source}" if ind.source and ind.source != "close" else ""
    if t in ("ema", "sma", "rsi"):
        return f"{t.upper()}({ind.period if ind.period else '?'}){src}"
    if t == "atr":
        return f"ATR({ind.period if ind.period else '?'})"
    if t == "vwap":
        return "session VWAP"
    if t == "highest":
        return f"highest {ind.source or 'high'} of the last {ind.period or '?'} bars"
    if t == "lowest":
        return f"lowest {ind.source or 'low'} of the last {ind.period or '?'} bars"
    if t == "candle_body":
        return "candle body (close − open)"
    if t == "candle_range":
        return "candle range (high − low)"
    if t == "prior_session_high":
        return "prior session high"
    if t == "prior_session_low":
        return "prior session low"
    return t


def _ago(n: int) -> str:
    if n == 0:
        return ""
    if n == 1:
        return " of the previous bar"
    return f" {n} bars ago"


def operand_text(spec: StrategySpec, op: Operand) -> str:
    if op.kind == "indicator":
        base = indicator_label(spec.indicator(op.ref), op.ref) + _ago(op.bars_ago)
    elif op.kind == "price":
        base = f"the bar's {op.field or '?'}" if op.bars_ago == 0 else f"the {op.field or '?'}{_ago(op.bars_ago)}"
    else:
        base = f"{op.value}"
    if op.multiplier != 1.0:
        base = f"{op.multiplier:g} × {base}"
    if op.offset_ticks:
        base += f" {'+' if op.offset_ticks > 0 else '−'} {abs(op.offset_ticks)} tick{'s' if abs(op.offset_ticks) != 1 else ''}"
    return base


def condition_lines(spec: StrategySpec, g: Group | None, depth: int = 0) -> list[str]:
    if g is None or not g.conditions:
        return [MISSING]
    lines: list[str] = []
    joiner = "ALL of" if g.type == "all" else "ANY of"
    if len(g.conditions) > 1 or depth > 0:
        lines.append(("  " * depth) + f"{joiner}:")
    for c in g.conditions:
        pad = "  " * (depth + (1 if len(g.conditions) > 1 or depth > 0 else 0))
        if isinstance(c, Compare):
            txt = f"{operand_text(spec, c.left)} {OP_TEXT[c.op]} {operand_text(spec, c.right)}"
            if c.op == "crosses_above":
                txt += " (at or below it on the previous bar, above it on this bar)"
            elif c.op == "crosses_below":
                txt += " (at or above it on the previous bar, below it on this bar)"
            elif c.op == "touches":
                txt += " (the bar's low-to-high range includes it)"
            lines.append(pad + "• " + txt[0].upper() + txt[1:])
        else:
            lines.extend(condition_lines(spec, c, depth + 1))
    return lines


def _ticks(n: float | int | None) -> str:
    if n is None:
        return "?"
    return f"{n:g} tick{'s' if n != 1 else ''}"


def _session_text(spec: StrategySpec) -> str:
    s = spec.session
    if s is None:
        return MISSING
    c = CONTRACTS.get(spec.instrument or "")
    if s.type == "rth":
        txt = f"Regular trading hours: {c.rth_start}-{c.rth_end} {s.tz}" if c else "Regular trading hours (instrument not chosen yet)"
    elif s.type == "eth":
        txt = f"Full electronic session: {ETH_START} (prior day) to {ETH_END} {s.tz}, daily halt 17:00-18:00"
    else:
        txt = f"Custom window: {s.start or '?'}-{s.end or '?'} {s.tz}"
    if s.type != "eth":
        if s.indicators_use == "session_bars_only":
            txt += ". Indicators are calculated on session bars only."
        elif s.indicators_use == "all_hours":
            txt += ". Indicators are calculated on all hours; trades are only taken inside the session."
        else:
            txt += ". ⚠ Not specified whether indicators use session bars only or all hours."
    return txt


def render(spec: StrategySpec) -> list[dict]:
    """Ordered sections: {key, title, lines, paths, missing}."""
    S: list[dict] = []
    c = CONTRACTS.get(spec.instrument or "")

    shorts = short(spec)

    def add(key: str, title: str, lines: list[str], paths: list[str]) -> None:
        S.append({"key": key, "title": title, "lines": lines, "paths": paths, "short": shorts.get(key, ""),
                  "missing": any(MISSING in ln or "⚠" in ln for ln in lines)})

    # Market and data
    mk = []
    mk.append(f"Instrument: {spec.instrument} — {c.name}, tick {c.tick_size} = ${c.tick_value:g}" if c else f"Instrument: {MISSING}")
    if spec.data.contract == "front_by_prior_volume":
        mk.append("Contract: front month, picked each session by the previous session's volume. Prices are not back-adjusted; indicators restart at each roll.")
    elif spec.data.contract == "most_active_same_day":
        mk.append("Contract: the most active contract each session, by that session's volume (a mild look-ahead in contract choice only). Prices are not back-adjusted; indicators restart at each roll.")
    elif spec.data.contract == "specific":
        mk.append(f"Contract: {spec.data.symbol or MISSING} only")
    else:
        mk.append(f"Contract: {MISSING}")
    mk.append(f"Bars: {spec.timeframe or MISSING}")
    mk.append(f"Session: {_session_text(spec)}")
    mk.append(f"Dates: {spec.data.start or '?'} to {spec.data.end or '?'}" if spec.data.start and spec.data.end else f"Dates: {MISSING}")
    mk.append(f"Times shown in: {spec.display_timezone or MISSING}")
    dirs = {"both": "Long and short", "long": "Long only", "short": "Short only"}
    mk.append(f"Directions: {dirs.get(spec.directions or '', MISSING)}")
    add("market", "Market & data", mk, ["instrument", "timeframe", "session", "display_timezone", "directions", "data"])

    # Indicators
    il = []
    if not spec.indicators:
        il.append("No indicators defined.")
    for ind in spec.indicators:
        il.append(f"• {indicator_label(ind)}  [id: {ind.id}]")
    if len(spec.indicators) > 1:
        il.append("These are separate lines. No relationship between them is assumed unless a rule below states one.")
    add("indicators", "Indicators", il, ["indicators"])

    for d in ("long", "short"):
        if d not in spec.enabled_directions():
            continue
        rules = getattr(spec.entry, d)
        lines = ["Evaluated when the signal bar closes, using only that bar and earlier bars."]
        lines += condition_lines(spec, rules.when if rules else None)
        if spec.entry.time_window:
            lines.append(f"Only between {spec.entry.time_window.start} and {spec.entry.time_window.end} ({spec.session.tz if spec.session else 'session tz'}).")
        add(f"signal_{d}", f"{d.capitalize()} signal", lines, [f"entry.{d}", "entry.time_window"])

    # Entry
    o = spec.entry.order
    el = []
    if o is None:
        el.append(MISSING)
    elif o.type == "market_next_open":
        el.append("Market order, filled at the open of the bar after the signal bar, plus slippage.")
    elif o.type == "stop_beyond_signal_bar":
        el.append(f"Buy stop {_ticks(o.offset_ticks)} above the signal bar's high (long) / sell stop {_ticks(o.offset_ticks)} below its low (short).")
        el.append(f"Placed after the signal bar closes; cancelled if not filled within {o.valid_bars} bar{'s' if o.valid_bars != 1 else ''}. Fill = order price or worse on a gap, plus slippage.")
    else:
        lvl = operand_text(spec, o.level) if o.level else MISSING
        el.append(f"Limit order at {lvl} (read when the signal bar closes), working for {o.valid_bars} bar{'s' if o.valid_bars != 1 else ''}.")
    el.append("One position at a time. Orders never carry over into the next session.")
    if spec.management.max_trades_per_day:
        el.append(f"At most {spec.management.max_trades_per_day} trade(s) per session.")
    if spec.management.cooldown_bars:
        el.append(f"Wait {spec.management.cooldown_bars} bar(s) after an exit before a new signal counts.")
    add("entry", "Entry price & timing", el, ["entry.order", "management.max_trades_per_day", "management.cooldown_bars"])

    # Stop
    st = spec.stop
    sl = []
    if st is None:
        sl.append(MISSING)
    elif st.type == "fixed_ticks":
        sl.append(f"{_ticks(st.ticks)} from the actual fill price" + (f" (= {st.ticks * c.tick_size:g} points = ${st.ticks * c.tick_value:,.2f} per contract)" if c and st.ticks else "") + ".")
    elif st.type == "atr_multiple":
        sl.append(f"{st.multiple or '?'} × {indicator_label(spec.indicator(st.atr_ref), st.atr_ref)} from the actual fill price, using the ATR value at the signal bar.")
    elif st.type == "signal_bar_extreme":
        sl.append(f"Long: signal bar low − {_ticks(st.offset_ticks)}. Short: signal bar high + {_ticks(st.offset_ticks)}.")
    elif st.type == "swing_extreme":
        sl.append(f"Long: lowest low of the last {st.lookback or '?'} bars up to the signal bar − {_ticks(st.offset_ticks)}. Short: highest high + {_ticks(st.offset_ticks)}.")
    elif st.type == "indicator":
        sl.append(f"At {indicator_label(spec.indicator(st.ref), st.ref)} (value at the signal bar), {_ticks(st.offset_ticks)} beyond it.")
    elif st.type == "furthest_of":
        names = [operand_text(spec, o) for o in (st.levels or [])] or [MISSING]
        sl.append(f"Long: the lower of {', '.join(names)}, read at the signal bar, − {_ticks(st.offset_ticks)}. "
                  f"Short: the higher of them + {_ticks(st.offset_ticks)}.")
    if st is not None:
        sl.append("If the stop would be on the wrong side of the fill (e.g. after a gap), the trade is skipped and logged.")
        sl.append("Stops fill at the stop price or worse if price gaps through it, plus slippage.")
    add("stop", "Stop loss", sl, ["stop"])

    # Target
    t = spec.target
    tl = []
    if t is None:
        tl.append(MISSING)
    elif t.type == "r_multiple":
        r_txt = f"{t.r:g}" if t.r else "?"
        tl.append(f"{r_txt} × the initial risk from the fill price (risk-to-reward 1:{r_txt}). Risk = |fill − stop|.")
    elif t.type == "fixed_ticks":
        tl.append(f"{_ticks(t.ticks)} from the fill price.")
    elif t.type == "indicator":
        tl.append(f"At {indicator_label(spec.indicator(t.ref), t.ref)} (value at the signal bar).")
    else:
        tl.append("No fixed profit target.")
    if t is not None and t.type != "none":
        lf = spec.fills.limit_fill
        tl.append({"touch": "Counts as filled when price touches the target.",
                   "trade_through": "Counts as filled only when price trades 1 tick through the target.",
                   None: "⚠ Not specified whether the target needs a touch or a trade-through."}[lf])
    add("target", "Profit target", tl, ["target", "fills.limit_fill"])

    # Management
    m = spec.management
    ml = []
    if m.breakeven_at_r:
        ml.append(f"Move the stop to entry {'+ ' + _ticks(m.breakeven_offset_ticks) + ' ' if m.breakeven_offset_ticks else ''}once price has moved {m.breakeven_at_r:g}R in favour (checked at bar close, active from the next bar).")
    if m.trail:
        if m.trail.type == "indicator":
            ml.append(f"Trail the stop at {indicator_label(spec.indicator(m.trail.ref), m.trail.ref)}, updated at each bar close, only ever tightening" + (f", once {m.trail.activate_at_r:g}R is reached" if m.trail.activate_at_r else "") + ".")
        else:
            ml.append(f"Trail the stop {_ticks(m.trail.ticks)} behind the best close, updated at bar close, only tightening" + (f", once {m.trail.activate_at_r:g}R is reached" if m.trail.activate_at_r else "") + ".")
    for d in spec.enabled_directions():
        r = getattr(spec.entry, d)
        if r and r.exit_when:
            ml.append(f"{d.capitalize()} rule-based exit at the next bar's open when:")
            ml += ["  " + x for x in condition_lines(spec, r.exit_when)]
    if m.time_exit_bars:
        ml.append(f"Exit at the open of the bar after {m.time_exit_bars} bars in the trade.")
    if m.exit_on_opposite_signal:
        ml.append("Exit at the next bar's open on an opposite signal (no automatic reversal).")
    if m.flat_at_session_end is True:
        ml.append("Close any open trade at the close of the session's last bar, plus slippage.")
    elif m.flat_at_session_end is False:
        ml.append("Trades may be held across sessions.")
    else:
        ml.append(f"Flat at session end: {MISSING}")
    if not (m.breakeven_at_r or m.trail or m.time_exit_bars or m.exit_on_opposite_signal):
        ml.append("No breakeven, trailing or time-based exits.")
    add("management", "Trade management & other exits", ml,
        ["management", "entry.long.exit_when", "entry.short.exit_when"])

    # Sizing
    z = spec.sizing
    if z is None:
        zl = [MISSING]
    elif z.type == "fixed_contracts":
        zl = [f"{z.contracts or '?'} contract(s) per trade."]
    else:
        zl = [f"Risk ${z.risk_usd or 0:,.0f} per trade: contracts = floor(risk $ ÷ (stop distance in ticks × tick value))" +
              (f", capped at {z.max_contracts}" if z.max_contracts else "") + ".",
              "If that rounds to 0 contracts: " + ("skip the trade." if z.below_one == "skip" else "trade 1 contract.")]
    add("sizing", "Position sizing", zl, ["sizing"])

    # Costs and fills
    cl = []
    cl.append(f"Commission: ${spec.costs.commission_per_side:.2f} per contract per side." if spec.costs.commission_per_side is not None else f"Commission: {MISSING}")
    cl.append(f"Slippage: {_ticks(spec.costs.slippage_ticks)} on market and stop fills (none on limit fills)." if spec.costs.slippage_ticks is not None else f"Slippage: {MISSING}")
    pol = {
        "intrabar_then_stop_first": "If a bar reaches both stop and target: replay tick or sub-bar data when available; if still unclear, assume the stop hit first.",
        "intrabar_then_target_first": "If a bar reaches both stop and target: replay tick or sub-bar data when available; if still unclear, assume the target hit first.",
        "stop_first": "If a bar reaches both stop and target, always assume the stop hit first.",
        "target_first": "If a bar reaches both stop and target, always assume the target hit first.",
    }
    cl.append(pol.get(spec.fills.same_bar_policy or "", f"Same-bar stop/target: {MISSING}"))
    add("costs", "Costs & fill assumptions", cl, ["costs", "fills"])
    return S


def render_text(spec: StrategySpec) -> str:
    out = []
    for s in render(spec):
        out.append(f"## {s['title']}")
        out.extend(s["lines"])
        out.append("")
    return "\n".join(out)


# ----- short, one-line versions for the side panel ------------------------------------
_OP_SHORT = {"gt": ">", "lt": "<", "gte": "≥", "lte": "≤", "crosses_above": "crosses above", "crosses_below": "crosses below", "touches": "touches"}


def _ind_short(ind: Indicator | None, ref: str | None) -> str:
    if ind is None:
        return f"?{ref}"
    return {"ema": f"EMA{ind.period}", "sma": f"SMA{ind.period}", "atr": f"ATR{ind.period}", "rsi": f"RSI{ind.period}",
            "vwap": "VWAP", "highest": f"High{ind.period}", "lowest": f"Low{ind.period}", "prior_session_high": "PrevHigh",
            "prior_session_low": "PrevLow", "candle_body": "body", "candle_range": "range"}.get(ind.type, ind.id)


def operand_short(spec: StrategySpec, op: Operand) -> str:
    if op.kind == "indicator":
        s = _ind_short(spec.indicator(op.ref), op.ref)
    elif op.kind == "price":
        s = op.field or "?"
    else:
        s = f"{op.value:g}" if op.value is not None else "?"
    if op.bars_ago:
        s += " (prev bar)" if op.bars_ago == 1 else f" ({op.bars_ago} bars ago)"
    if op.multiplier != 1.0:
        s = f"{op.multiplier:g}×{s}"
    if op.offset_ticks:
        s += f" {'+' if op.offset_ticks > 0 else '−'}{abs(op.offset_ticks)}t"
    return s


def group_short(spec: StrategySpec, g: Group | None) -> str:
    if g is None or not g.conditions:
        return "Not set"
    cmps = [c for c in g.conditions if isinstance(c, Compare)]
    if len(cmps) == len(g.conditions) and len(cmps) > 1 and len({(c.op, c.left.model_dump_json()) for c in cmps}) == 1:
        c0 = cmps[0]
        word = "any of" if g.type == "any" else "all of"
        verb = {"lt": "below", "gt": "above", "lte": "at/below", "gte": "at/above"}.get(c0.op, _OP_SHORT[c0.op])
        return f"{operand_short(spec, c0.left)} {verb} {word} {', '.join(operand_short(spec, c.right) for c in cmps)}"
    parts = []
    for c in g.conditions:
        if isinstance(c, Compare):
            parts.append(f"{operand_short(spec, c.left)} {_OP_SHORT[c.op]} {operand_short(spec, c.right)}")
        else:
            parts.append(group_short(spec, c))
    return (" and " if g.type == "all" else " or ").join(parts) if len(parts) > 1 else parts[0]


def short(spec: StrategySpec) -> dict[str, str]:
    from ..labels import CONTRACT, SAME_BAR

    c = CONTRACTS.get(spec.instrument or "")
    out: dict[str, str] = {}
    sess = {"rth": "RTH", "eth": "Globex 18:00–17:00", "custom": f"{spec.session.start}–{spec.session.end}" if spec.session else ""}.get(
        spec.session.type if spec.session else "", None)
    bits = [spec.instrument or "instrument?", spec.timeframe or "timeframe?", sess or "session?",
            f"{spec.data.start} → {spec.data.end}" if spec.data.start and spec.data.end else "dates?",
            CONTRACT.get(spec.data.contract or "", "contract rule?")]
    out["market"] = " · ".join(bits)
    out["indicators"] = " · ".join(_ind_short(i, i.id) for i in spec.indicators) or "None"
    for d in ("long", "short"):
        r = getattr(spec.entry, d)
        out[f"signal_{d}"] = group_short(spec, r.when if r else None)
    o = spec.entry.order
    out["entry"] = ("Not set" if o is None else {"market_next_open": "Market at the next bar's open",
                    "stop_beyond_signal_bar": f"Stop order {o.offset_ticks}t beyond the signal bar",
                    "limit_at_level": "Limit at " + (operand_short(spec, o.level) if o.level else "?")}[o.type])
    st = spec.stop
    if st is None:
        out["stop"] = "Not set"
    elif st.type == "furthest_of":
        out["stop"] = f"Lower of {', '.join(operand_short(spec, x) for x in st.levels or [])} −{st.offset_ticks}t"
    elif st.type == "fixed_ticks":
        out["stop"] = f"{st.ticks} ticks from fill"
    elif st.type == "atr_multiple":
        out["stop"] = f"{st.multiple:g}× {_ind_short(spec.indicator(st.atr_ref), st.atr_ref)} from fill"
    elif st.type == "signal_bar_extreme":
        out["stop"] = f"Signal bar low/high ∓{st.offset_ticks}t"
    elif st.type == "swing_extreme":
        out["stop"] = f"Swing low/high of {st.lookback} bars ∓{st.offset_ticks}t"
    else:
        out["stop"] = f"At {_ind_short(spec.indicator(st.ref), st.ref)} ∓{st.offset_ticks}t"
    t = spec.target
    out["target"] = ("Not set" if t is None else "No target" if t.type == "none" else
                     (f"{t.r:g}R" if t.type == "r_multiple" and t.r else "R?" if t.type == "r_multiple" else
                      f"{t.ticks} ticks" if t.type == "fixed_ticks" else f"At {_ind_short(spec.indicator(t.ref), t.ref)}") +
                     {"touch": " · fills on touch", "trade_through": " · needs 1-tick trade-through", None: ""}[spec.fills.limit_fill])
    m = spec.management
    mm = []
    mm.append({True: "Flat at session end", False: "Can hold overnight", None: "Session end?"}[m.flat_at_session_end])
    if m.breakeven_at_r:
        mm.append(f"breakeven at {m.breakeven_at_r:g}R")
    if m.trail:
        mm.append("trailing stop")
    if m.time_exit_bars:
        mm.append(f"exit after {m.time_exit_bars} bars")
    if m.max_trades_per_day:
        mm.append(f"max {m.max_trades_per_day}/session")
    out["management"] = " · ".join(mm)
    z = spec.sizing
    out["sizing"] = "Not set" if z is None else (f"{z.contracts} contract(s)" if z.type == "fixed_contracts" else f"Risk ${z.risk_usd:,.0f}/trade")
    cs = spec.costs
    out["costs"] = " · ".join([
        f"${cs.commission_per_side:.2f}/side" if cs.commission_per_side is not None else "commission?",
        f"{cs.slippage_ticks:g} tick slip" if cs.slippage_ticks is not None else "slippage?",
        SAME_BAR.get(spec.fills.same_bar_policy or "", "same-bar rule?"),
    ])
    _ = c
    return out
