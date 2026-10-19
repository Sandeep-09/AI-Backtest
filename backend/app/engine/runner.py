"""
Preview and backtest share this module, so they always run the same code on the
same spec. A preview runs the simulation on the first sessions of the approved
range; because nothing looks ahead, its example trades are exactly the trades
the full backtest produces over those sessions.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ..data.store import Intrabar, all_bars, choose_contracts
from ..instruments import CONTRACTS
from ..spec.schema import Sizing, StrategySpec
from . import chart, metrics
from .prepare import Prepared, prepare
from .simulate import Simulator

PREVIEW_STEPS = (5, 15, 40, None)


def preview_requirements(spec: StrategySpec) -> list[str]:
    miss = []
    for path, ok in (("instrument", spec.instrument), ("timeframe", spec.timeframe), ("session", spec.session),
                     ("directions", spec.directions), ("entry.order", spec.entry.order), ("stop", spec.stop), ("target", spec.target)):
        if not ok:
            miss.append(path)
    if spec.session and spec.session.type != "eth" and not spec.session.indicators_use:
        miss.append("session.indicators_use")
    for d in spec.enabled_directions():
        r = getattr(spec.entry, d)
        if not r or not r.when or not r.when.conditions:
            miss.append(f"entry.{d}.when")
    return miss


def _preview_spec(spec: StrategySpec) -> tuple[StrategySpec, list[str]]:
    """Fill presentation-only gaps for drawing examples; each is reported, none is saved."""
    s = spec.model_copy(deep=True)
    notes = []
    if s.sizing is None:
        s.sizing = Sizing(type="fixed_contracts", contracts=1)
        notes.append("Sizing not set yet: examples drawn with 1 contract.")
    if s.costs.slippage_ticks is None:
        s.costs.slippage_ticks = 0
        notes.append("Slippage not set yet: examples drawn with 0 ticks.")
    if s.costs.commission_per_side is None:
        s.costs.commission_per_side = 0
        notes.append("Commission not set yet: examples ignore commission.")
    if s.fills.same_bar_policy is None:
        s.fills.same_bar_policy = "intrabar_then_stop_first"
        notes.append("Same-bar policy not set yet: examples use tick replay, then stop-first.")
    if s.fills.limit_fill is None:
        s.fills.limit_fill = "trade_through"
        notes.append("Target fill rule not set yet: examples require a 1-tick trade-through.")
    if s.management.flat_at_session_end is None:
        s.management.flat_at_session_end = False
        notes.append("Session-end exit not decided yet: examples hold across sessions.")
    if s.data.contract is None:
        s.data.contract = "most_active_same_day"
        notes.append("Contract rule not set yet: examples use the most active contract each session.")
    if s.display_timezone is None:
        s.display_timezone = "America/New_York"
        notes.append("Display timezone not set yet: examples shown in New York time.")
    return s, notes


def synthetic_base(spec: StrategySpec, sessions: int = 4, seed: int = 3) -> pd.DataFrame:
    """Illustrative 1m bars when no real data exists. Clearly labelled; never used for a backtest."""
    from ..data.ingest import trade_date_days
    from ..data.synthetic import session_bounds, trade_dates

    c = CONTRACTS[spec.instrument]
    rng = np.random.default_rng(seed)
    rows = []
    px = 20000.0 if spec.instrument in ("NQ", "MNQ") else 2400.0
    vol = 6 * c.tick_size * (4 if spec.instrument in ("NQ", "MNQ") else 1)
    for d in trade_dates(dt.date(2025, 3, 3), sessions):
        s, e = session_bounds(d)
        for k, t in enumerate(range(s, e, 60_000_000_000)):
            drift = (1 if (k // 90) % 2 == 0 else -1) * vol * 0.12
            o = px
            path = o + np.cumsum(rng.normal(drift / 6, vol / 2.5, 6))
            h, lo, cl = max(o, path.max()), min(o, path.min()), path[-1]
            r = lambda x: round(x / c.tick_size) * c.tick_size  # noqa: E731
            rows.append((t, r(o), r(h), r(lo), r(cl)))
            px = r(cl)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
    df["iid"] = 1
    df["volume"] = 10
    df["trades"] = np.nan
    df["day"] = trade_date_days(df["ts"].to_numpy())
    df["symbol"] = f"{spec.instrument} (illustrative)"
    df["root"] = spec.instrument
    df["kind"] = "outright"
    df["base_res"] = "1m"
    df["intrabar"] = "none"
    df["synthetic"] = True
    df["fp"] = "illustrative"
    df["file"] = "illustrative"
    return df


def build(spec: StrategySpec, limit_trade_dates: int | None = None, illustrative: bool = False) -> tuple[Prepared, Intrabar | None]:
    if illustrative:
        s = spec.model_copy(deep=True)
        s.data.start = s.data.end = None
        s.data.contract = "most_active_same_day"
        base = synthetic_base(s)
        return prepare(s, base=base, choice=choose_contracts(base, "most_active_same_day"), limit_trade_dates=limit_trade_dates), None
    base, _ = all_bars(spec.instrument)
    if base.empty:
        raise LookupError(f"no ingested data for {spec.instrument}")
    prep = prepare(spec, base=base, choice=choose_contracts(base, spec.data.contract, spec.data.symbol),
                   limit_trade_dates=limit_trade_dates)
    ib = Intrabar(spec.instrument)
    return prep, (ib if ib.kind else None)


def _example(sim: Simulator, t: dict, label: str) -> dict:
    a = max(0, t["signal_i"] - 60)
    z = t["exit_i"] + 25
    return {"label": label, "trade": t, "chart": chart.window(sim.spec, sim.b, sim.ind, a, z),
            "overlay": chart.trade_overlay(sim.spec, t)}


def preview(spec: StrategySpec, spec_hash: str, has_data: bool) -> dict:
    missing = preview_requirements(spec)
    if missing:
        return {"ok": False, "missing": missing, "message": "The preview needs these rules first: " + ", ".join(missing)}
    s, notes = _preview_spec(spec)
    source = "data" if has_data and s.data.start and s.data.end else "illustrative"
    if has_data and not (s.data.start and s.data.end):
        notes.append("Date range not set yet: examples come from illustrative bars, not your data.")
    examples: dict = {}
    scanned = None
    sim = None
    for steps in (PREVIEW_STEPS if source == "data" else (None,)):
        prep, ib = build(s, limit_trade_dates=steps, illustrative=(source == "illustrative"))
        sim = Simulator(s, prep, ib, spec_hash)
        res = sim.run()
        scanned = {"trade_dates": int(prep.bars.loc[prep.bars["tradable_day"], "day"].nunique()), "from": prep.trade_from,
                   "to": prep.trade_to, "signals_long": res["counters"]["signals_long"], "signals_short": res["counters"]["signals_short"],
                   "skipped_signals": len(res["skipped"])}
        for d in s.enabled_directions():
            if d not in examples:
                first = next((t for t in res["trades"] if t["direction"] == d), None)
                if first:
                    examples[d] = first
        if all(d in examples for d in s.enabled_directions()):
            break
    out_examples = {}
    for d in s.enabled_directions():
        if d in examples:
            t = examples[d]
            lbl = (f"First {d} trade in your data: {t['symbol']} {t['signal_time'][:16].replace('T', ' ')}"
                   if source == "data" else f"ILLUSTRATIVE {d} example on synthetic bars (not your data)")
            out_examples[d] = _example(sim, t, lbl)
        else:
            out_examples[d] = None
    skipped_sample = (res["skipped"][:12] if sim else [])
    # Every trade in the scanned sessions, for drawing on the main chart. These are exactly the
    # backtest's trades over these sessions (nothing looks ahead).
    window_trades = [dict(chart.trade_overlay(sim.spec, t), net_pnl=t["net_pnl"], symbol=t["symbol"]) for t in res["trades"]]
    return {"ok": True, "source": source, "spec_hash": spec_hash, "notes": notes, "examples": out_examples,
            "scanned": scanned, "skipped_sample": skipped_sample,
            "window_chart": chart.compact(sim.spec, sim.b, sim.ind), "trades": window_trades,
            "trade_details": {str(t["n"]): t for t in res["trades"]},
            "message": None if any(out_examples.values()) else "No trade was triggered in the scanned period. The rules may be too strict, or need correcting."}


def backtest(spec: StrategySpec, spec_hash: str, progress=None) -> dict:
    prep, ib = build(spec)
    sim = Simulator(spec, prep, ib, spec_hash, progress=progress)
    res = sim.run()
    return {"prep": prep, "sim": sim, "trades": res["trades"], "skipped": res["skipped"], "counters": res["counters"],
            "metrics": metrics.compute(res["trades"]), "equity": metrics.equity_series(res["trades"])}
