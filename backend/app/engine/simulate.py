"""
Bar-by-bar simulator. One position at a time.

Order of events inside bar i:
  1. pending market exit fills at the open
  2. pending entry order tries to fill
  3. stop / target checked (replaying trade prints when the bar is ambiguous)
  4. at the close: session-end / roll exits, breakeven, trailing, rule exits, time exits
  5. at the close: new signals create orders for bar i+1 or later

Nothing at bar i reads bar i+1. Every fill records how it was decided.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..data.ingest import day_str
from ..instruments import CONTRACTS, ContractSpec
from ..spec.schema import StrategySpec
from .conditions import Ctx, eval_group, evidence
from .indicators import compute
from .prepare import Prepared


def _iso(ns: int, tz: str) -> str:
    return pd.Timestamp(int(ns), tz="UTC").tz_convert(tz).isoformat()


@dataclass
class Position:
    direction: int
    symbol: str
    iid: int
    day: int
    signal_i: int
    entry_i: int
    entry_ts: int
    entry_price: float
    planned_entry: float
    entry_how: str
    qty: int
    stop: float
    initial_stop: float
    target: float | None
    risk_pts: float
    stop_calc: str
    target_calc: str
    evidence: list
    order: dict
    stop_history: list = field(default_factory=list)
    bars_held: int = 0
    best: float = 0.0
    worst: float = 0.0
    be_done: bool = False
    slip_fills: int = 0
    events: list = field(default_factory=list)


class Simulator:
    def __init__(self, spec: StrategySpec, prep: Prepared, intrabar=None, spec_hash: str | None = None,
                 progress=None) -> None:
        self.spec = spec
        self.prep = prep
        self.b = prep.bars
        self.c: ContractSpec = CONTRACTS[spec.instrument]
        self.tick = self.c.tick_size
        self.intrabar = intrabar  # store.Intrabar or None
        self.spec_hash = spec_hash
        self.progress = progress
        self.tz = spec.display_timezone or "America/New_York"
        self.ind = compute(self.b, spec.indicators)
        self.ctx = Ctx(self.b, self.ind, self.tick)
        n = len(self.b)
        self.sig = {}
        for d in ("long", "short"):
            r = getattr(spec.entry, d)
            on = d in spec.enabled_directions() and r is not None and r.when is not None
            self.sig[d] = eval_group(self.ctx, r.when) if on else np.zeros(n, dtype=bool)
        self.exit_sig = {}
        for d in ("long", "short"):
            r = getattr(spec.entry, d)
            self.exit_sig[d] = eval_group(self.ctx, r.exit_when) if (r is not None and r.exit_when is not None) else np.zeros(n, dtype=bool)
        self.slip = (spec.costs.slippage_ticks or 0.0) * self.tick
        self.comm = spec.costs.commission_per_side or 0.0
        self.trades: list[dict] = []
        self.skipped: list[dict] = []
        self.counters = {"signals_long": 0, "signals_short": 0, "ambiguous_resolved_intrabar": 0, "ambiguous_by_policy": 0}
        self._tw = None
        if spec.entry.time_window:
            h1, m1 = map(int, spec.entry.time_window.start.split(":"))
            h2, m2 = map(int, spec.entry.time_window.end.split(":"))
            self._tw = (h1 * 60 + m1, h2 * 60 + m2, spec.session.tz)

    # ----- helpers -----------------------------------------------------------------
    def _rt(self, x: float, mode: str = "near") -> float:
        q = x / self.tick
        q = math.floor(q + 1e-9) if mode == "down" else math.ceil(q - 1e-9) if mode == "up" else round(q)
        return round(q * self.tick, 10)

    def _bar(self, i: int):
        return self.b.iloc[i]

    def _sub(self, i: int, from_ns: int):
        """Price path inside bar i from from_ns: (rows, kind) or (None, None)."""
        row = self.b.iloc[i]
        end = int(row["end_ts"])
        if self.intrabar is not None and self.intrabar.kind in ("tick", "1s"):
            w = self.intrabar.window(int(row["day"]), int(row["iid"]), int(from_ns), end)
            if w is not None and len(w):
                return w, self.intrabar.kind
        if self.prep.sub_bars is not None and end - int(row["ts"]) > 60_000_000_000:
            sb = self.prep.sub_bars
            ts = sb["ts"].to_numpy()
            a, z = np.searchsorted(ts, from_ns, "left"), np.searchsorted(ts, end, "left")
            w = sb.iloc[a:z]
            w = w[w["iid"] == row["iid"]]
            if len(w):
                return w, "1m"
        return None, None

    def _target_hit(self, direction: int, hi: float, lo: float, target: float) -> bool:
        through = self.spec.fills.limit_fill == "trade_through"
        if direction > 0:
            return hi >= target + (self.tick if through else 0)
        return lo <= target - (self.tick if through else 0)

    def _stop_hit(self, direction: int, hi: float, lo: float, stop: float) -> bool:
        return lo <= stop if direction > 0 else hi >= stop

    def _policy_stop_first(self) -> bool:
        return self.spec.fills.same_bar_policy in ("intrabar_then_stop_first", "stop_first")

    def _use_intrabar(self) -> bool:
        return self.spec.fills.same_bar_policy in ("intrabar_then_stop_first", "intrabar_then_target_first")

    # ----- exits -------------------------------------------------------------------
    def _check_exit(self, p: Position, i: int, from_ns: int, entry_bar: bool):
        """Return (price, ts, reason, resolution) if stop/target is reached in bar i after from_ns."""
        row = self.b.iloc[i]
        o, hi, lo = float(row["open"]), float(row["high"]), float(row["low"])
        d = p.direction
        if not entry_bar:
            # Gap through a level at the open.
            if (d > 0 and o <= p.stop) or (d < 0 and o >= p.stop):
                return o - d * self.slip, int(row["ts"]), "stop", "gap through stop at the open (filled at the open)"
            if p.target is not None and ((d > 0 and o >= p.target) or (d < 0 and o <= p.target)):
                return o, int(row["ts"]), "target", "opened beyond target (filled at the open)"
        s_hit = self._stop_hit(d, hi, lo, p.stop)
        t_hit = p.target is not None and self._target_hit(d, hi, lo, p.target)
        if not s_hit and not t_hit:
            return None
        ambiguous = (s_hit and t_hit) or entry_bar
        if ambiguous and self._use_intrabar() or (not ambiguous and self.intrabar is not None):
            sub, kind = self._sub(i, from_ns)
            if sub is not None:
                for r in sub.itertuples(index=False):
                    sh = self._stop_hit(d, r.high, r.low, p.stop)
                    th = p.target is not None and self._target_hit(d, r.high, r.low, p.target)
                    if sh and th:
                        self.counters["ambiguous_by_policy"] += 1
                        if self._policy_stop_first():
                            return p.stop - d * self.slip, int(r.ts), "stop", f"both levels inside one {kind} sub-bar; policy: stop first"
                        return p.target, int(r.ts), "target", f"both levels inside one {kind} sub-bar; policy: target first"
                    if sh:
                        px = min(p.stop, r.open) if d > 0 else max(p.stop, r.open)
                        if ambiguous:
                            self.counters["ambiguous_resolved_intrabar"] += 1
                        return px - d * self.slip, int(r.ts), "stop", f"{'tick' if kind == 'tick' else kind} replay: stop reached first"
                    if th:
                        if ambiguous:
                            self.counters["ambiguous_resolved_intrabar"] += 1
                        return p.target, int(r.ts), "target", f"{'tick' if kind == 'tick' else kind} replay: target reached first"
                if entry_bar:
                    return None  # replay shows neither level was reached after the fill
        if s_hit and t_hit or (entry_bar and (s_hit or t_hit)):
            self.counters["ambiguous_by_policy"] += 1
            if s_hit and (self._policy_stop_first() or not t_hit):
                return p.stop - d * self.slip, int(row["end_ts"]) - 1, "stop", (
                    "entry bar touched the stop; no intrabar data, assumed after the fill" if entry_bar and not t_hit
                    else "bar reached both levels; no intrabar data; policy: stop first")
            return p.target, int(row["end_ts"]) - 1, "target", (
                "entry bar touched the target; no intrabar data, assumed after the fill" if entry_bar and not s_hit
                else "bar reached both levels; no intrabar data; policy: target first")
        if s_hit:
            return p.stop - d * self.slip, int(row["end_ts"]) - 1, "stop", "bar reached the stop only"
        return p.target, int(row["end_ts"]) - 1, "target", "bar reached the target only"

    def _close(self, p: Position, i: int, price: float, ts: int, reason: str, resolution: str, slipped: bool) -> None:
        c = self.c
        if slipped:
            p.slip_fills += 1
        gross = (price - p.entry_price) * p.direction * c.point_value * p.qty
        commission = self.comm * 2 * p.qty
        slip_usd = p.slip_fills * (self.spec.costs.slippage_ticks or 0) * c.tick_value * p.qty
        risk_usd = p.risk_pts * c.point_value * p.qty
        net = gross - commission
        row = self.b.iloc[i]
        t = {
            "n": len(self.trades) + 1, "direction": "long" if p.direction > 0 else "short", "symbol": p.symbol,
            "trade_date": day_str(p.day), "qty": p.qty,
            "signal_bar_ts": int(self.b.iloc[p.signal_i]["ts"]), "signal_ts": int(self.b.iloc[p.signal_i]["end_ts"]),
            "signal_i": p.signal_i, "evidence": p.evidence, "order": p.order,
            "planned_entry": p.planned_entry, "entry_ts": p.entry_ts, "entry_price": p.entry_price, "entry_how": p.entry_how,
            "entry_i": p.entry_i, "initial_stop": p.initial_stop, "stop_calc": p.stop_calc, "target": p.target,
            "target_calc": p.target_calc, "risk_points": round(p.risk_pts, 6), "risk_usd": round(risk_usd, 2),
            "planned_rr": (round(abs(p.target - p.entry_price) / p.risk_pts, 3) if p.target is not None and p.risk_pts else None),
            "stop_history": p.stop_history, "events": p.events,
            "exit_i": i, "exit_ts": int(ts), "exit_price": round(price, 6), "exit_reason": reason, "exit_resolution": resolution,
            "bars_held": p.bars_held + (0 if i == p.entry_i else 1),
            "gross_pnl": round(gross, 2), "commission": round(commission, 2), "slippage_usd": round(slip_usd, 2),
            "net_pnl": round(net, 2), "r_multiple": round((price - p.entry_price) * p.direction / p.risk_pts, 3) if p.risk_pts else None,
            "net_r": round(net / risk_usd, 3) if risk_usd else None,
            "mfe_r": round(p.best / p.risk_pts, 3) if p.risk_pts else None, "mae_r": round(p.worst / p.risk_pts, 3) if p.risk_pts else None,
            "spec_hash": self.spec_hash,
        }
        t["entry_time"] = _iso(t["entry_ts"], self.tz)
        t["exit_time"] = _iso(t["exit_ts"], self.tz)
        t["signal_time"] = _iso(t["signal_ts"], self.tz)
        _ = row
        self.trades.append(t)

    # ----- entries -----------------------------------------------------------------
    def _levels(self, direction: int, sig_i: int, fill: float):
        s, t = self.spec.stop, self.spec.target
        row = self.b.iloc[sig_i]
        d = direction
        tk = self.tick
        if s.type == "fixed_ticks":
            stop = fill - d * s.ticks * tk
            calc = f"fill {fill:g} {'−' if d > 0 else '+'} {s.ticks} ticks"
        elif s.type == "atr_multiple":
            a = self.ind[s.atr_ref][sig_i]
            if np.isnan(a):
                return None, None, None, None, "ATR not available yet (warm-up)"
            stop = fill - d * s.multiple * a
            calc = f"fill {fill:g} {'−' if d > 0 else '+'} {s.multiple:g} × ATR {a:.4g} = {fill - d * s.multiple * a:.6g}"
        elif s.type == "signal_bar_extreme":
            base = float(row["low"]) if d > 0 else float(row["high"])
            stop = base - d * s.offset_ticks * tk
            calc = f"signal bar {'low' if d > 0 else 'high'} {base:g} {'−' if d > 0 else '+'} {s.offset_ticks} tick(s)"
        elif s.type == "swing_extreme":
            lo = max(0, sig_i - s.lookback + 1)
            seg = self.b.iloc[lo:sig_i + 1]
            seg = seg[seg["seg"] == row["seg"]]
            base = float(seg["low"].min()) if d > 0 else float(seg["high"].max())
            stop = base - d * s.offset_ticks * tk
            calc = f"{'lowest low' if d > 0 else 'highest high'} of last {s.lookback} bars {base:g} {'−' if d > 0 else '+'} {s.offset_ticks} tick(s)"
        else:
            v = self.ind[s.ref][sig_i]
            if np.isnan(v):
                return None, None, None, None, f"{s.ref} not available yet (warm-up)"
            stop = v - d * s.offset_ticks * tk
            calc = f"{s.ref} at signal {v:.6g} {'−' if d > 0 else '+'} {s.offset_ticks} tick(s)"
        stop = self._rt(stop, "down" if d > 0 else "up")
        if (d > 0 and stop >= fill) or (d < 0 and stop <= fill):
            return None, None, None, None, f"stop {stop:g} is not beyond the fill {fill:g} ({calc})"
        risk = abs(fill - stop)
        if t.type == "r_multiple":
            target = self._rt(fill + d * t.r * risk, "up" if d > 0 else "down")
            tcalc = f"fill {fill:g} {'+' if d > 0 else '−'} {t.r:g} × risk {risk:g}"
        elif t.type == "fixed_ticks":
            target = fill + d * t.ticks * tk
            tcalc = f"fill {fill:g} {'+' if d > 0 else '−'} {t.ticks} ticks"
        elif t.type == "indicator":
            v = self.ind[t.ref][sig_i]
            if np.isnan(v):
                return None, None, None, None, f"{t.ref} not available yet (warm-up)"
            target = self._rt(v)
            tcalc = f"{t.ref} at signal {v:.6g}"
            if (d > 0 and target <= fill) or (d < 0 and target >= fill):
                return None, None, None, None, f"target {target:g} is not beyond the fill {fill:g}"
        else:
            target, tcalc = None, "no fixed target"
        return stop, target, calc, tcalc, None

    def _size(self, risk_pts: float) -> tuple[int | None, str | None]:
        z = self.spec.sizing
        if z.type == "fixed_contracts":
            return z.contracts, None
        per = risk_pts / self.tick * self.c.tick_value
        q = int(z.risk_usd // per) if per > 0 else 0
        if z.max_contracts:
            q = min(q, z.max_contracts)
        if q < 1:
            if z.below_one == "one":
                return 1, None
            return None, f"risk ${per:,.2f} per contract exceeds the ${z.risk_usd:,.0f} budget"
        return q, None

    def _try_fill(self, order: dict, i: int):
        row = self.b.iloc[i]
        o, hi, lo = float(row["open"]), float(row["high"]), float(row["low"])
        d = order["direction"]
        t = order["type"]
        if t == "market_next_open":
            return o + d * self.slip, o, int(row["ts"]), "market at the bar's open + slippage", True
        if t == "stop_beyond_signal_bar":
            lvl = order["level"]
            if (d > 0 and hi >= lvl) or (d < 0 and lo <= lvl):
                if (d > 0 and o >= lvl) or (d < 0 and o <= lvl):
                    return o + d * self.slip, lvl, int(row["ts"]), "stop order: opened through the level, filled at the open + slippage", True
                ts = int(row["ts"])
                sub, kind = self._sub(i, int(row["ts"]))
                if sub is not None:
                    for r in sub.itertuples(index=False):
                        if (d > 0 and r.high >= lvl) or (d < 0 and r.low <= lvl):
                            ts = int(r.ts)
                            break
                    how = f"stop order triggered at {kind} {_iso(ts, self.tz)[11:23]} + slippage"
                else:
                    how = "stop order triggered inside the bar (no intrabar data: exact time unknown) + slippage"
                return lvl + d * self.slip, lvl, ts, how, True
            return None
        # limit
        lvl = order["level"]
        through = self.spec.fills.limit_fill == "trade_through"
        hit = (lo <= lvl - (self.tick if through else 0)) if d > 0 else (hi >= lvl + (self.tick if through else 0))
        if not hit:
            return None
        if (d > 0 and o <= lvl) or (d < 0 and o >= lvl):
            return o, lvl, int(row["ts"]), "limit order: opened beyond the level, filled at the open", False
        ts = int(row["ts"])
        sub, kind = self._sub(i, int(row["ts"]))
        if sub is not None:
            for r in sub.itertuples(index=False):
                if (d > 0 and r.low <= lvl) or (d < 0 and r.high >= lvl):
                    ts = int(r.ts)
                    break
        return lvl, lvl, ts, "limit order filled at the limit price", False

    def _skip(self, i: int, direction: str, reason: str) -> None:
        if len(self.skipped) < 5000:
            self.skipped.append({"signal_ts": int(self.b.iloc[i]["end_ts"]), "time": _iso(int(self.b.iloc[i]["end_ts"]), self.tz),
                                 "direction": direction, "reason": reason, "i": i})

    def _in_window(self, i: int) -> bool:
        if self._tw is None:
            return True
        a, z, tz = self._tw
        t = pd.Timestamp(int(self.b.iloc[i]["end_ts"]), tz="UTC").tz_convert(tz)
        m = t.hour * 60 + t.minute
        return a <= m <= z if a <= z else (m >= a or m <= z)

    # ----- main loop ---------------------------------------------------------------
    def run(self) -> dict:
        b = self.b
        n = len(b)
        spec = self.spec
        m = spec.management
        day = b["day"].to_numpy()
        seg = b["seg"].to_numpy()
        in_sess = b["in_session"].to_numpy()
        sess_last = b["session_last"].to_numpy()
        tradable = b["tradable_day"].to_numpy()
        close = b["close"].to_numpy(float)
        high = b["high"].to_numpy(float)
        low = b["low"].to_numpy(float)
        pos: Position | None = None
        pending: dict | None = None
        pending_exit: tuple | None = None
        trades_today: dict[int, int] = {}
        last_exit_i = -10**9
        for i in range(n):
            if self.progress and i % 2000 == 0:
                self.progress(i / max(1, n))
            row = b.iloc[i]
            # Orders never carry into another session or contract.
            if pending is not None and (day[i] != pending["day"] or seg[i] != pending["seg"] or not in_sess[i]):
                self._skip(pending["signal_i"], pending["dir_name"], "order expired: session ended before it filled")
                pending = None
            # 1. pending market exit at the open
            if pos is not None and pending_exit is not None:
                px = float(row["open"]) - pos.direction * self.slip
                self._close(pos, i, px, int(row["ts"]), pending_exit[0], pending_exit[1], True)
                pos, pending_exit, last_exit_i = None, None, i
            # 2. pending entry
            entered_now = False
            if pending is not None and pos is None:
                f = self._try_fill(pending, i)
                if f is None:
                    pending["bars_left"] -= 1
                    if pending["bars_left"] <= 0:
                        self._skip(pending["signal_i"], pending["dir_name"], f"{pending['type']} order not filled within {pending['valid']} bar(s)")
                        pending = None
                else:
                    fill, planned, fts, how, slipped = f
                    d = pending["direction"]
                    stop, target, scalc, tcalc, err = self._levels(d, pending["signal_i"], fill)
                    if err:
                        self._skip(pending["signal_i"], pending["dir_name"], f"not taken: {err}")
                        pending = None
                    else:
                        qty, qerr = self._size(abs(fill - stop))
                        if qerr:
                            self._skip(pending["signal_i"], pending["dir_name"], f"not taken: {qerr}")
                            pending = None
                        else:
                            pos = Position(direction=d, symbol=str(row["symbol"]), iid=int(row["iid"]), day=int(day[i]),
                                           signal_i=pending["signal_i"], entry_i=i, entry_ts=fts, entry_price=round(fill, 6),
                                           planned_entry=planned, entry_how=how, qty=qty, stop=stop, initial_stop=stop,
                                           target=target, risk_pts=abs(fill - stop), stop_calc=scalc, target_calc=tcalc,
                                           evidence=pending["evidence"], order=pending["order_view"],
                                           slip_fills=1 if slipped and self.slip else 0)
                            pos.stop_history.append({"ts": fts, "stop": stop, "why": "initial"})
                            trades_today[int(day[i])] = trades_today.get(int(day[i]), 0) + 1
                            pending = None
                            entered_now = True
                            # A fill at the bar's open sees the whole bar; a fill inside the bar only sees what came after it.
                            ex = self._check_exit(pos, i, fts, entry_bar=fts > int(row["ts"]))
                            if ex:
                                self._close(pos, i, ex[0], ex[1], ex[2], ex[3], ex[2] == "stop")
                                pos, last_exit_i = None, i
            # 3. stop / target for a position from an earlier bar
            if pos is not None and not entered_now:
                ex = self._check_exit(pos, i, int(row["ts"]), entry_bar=False)
                if ex:
                    self._close(pos, i, ex[0], ex[1], ex[2], ex[3], ex[2] == "stop")
                    pos, last_exit_i = None, i
            # 4. close-time management
            if pos is not None:
                d = pos.direction
                fav = (high[i] - pos.entry_price) if d > 0 else (pos.entry_price - low[i])
                adv = (pos.entry_price - low[i]) if d > 0 else (high[i] - pos.entry_price)
                pos.best = max(pos.best, fav)
                pos.worst = max(pos.worst, adv)
                if i != pos.entry_i:
                    pos.bars_held += 1
                next_other_seg = i + 1 >= n or seg[i + 1] != seg[i]
                if m.flat_at_session_end and sess_last[i]:
                    self._close(pos, i, close[i] - d * self.slip, int(row["end_ts"]) - 1, "session end", "market exit at the session's last bar close", True)
                    pos, last_exit_i = None, i
                elif next_other_seg:
                    why = "end of data" if i + 1 >= n else "contract roll"
                    self._close(pos, i, close[i] - d * self.slip, int(row["end_ts"]) - 1, why, f"forced exit at the bar close ({why})", True)
                    pos, last_exit_i = None, i
                else:
                    if m.breakeven_at_r and not pos.be_done and pos.best >= m.breakeven_at_r * pos.risk_pts:
                        new = self._rt(pos.entry_price + d * m.breakeven_offset_ticks * self.tick)
                        if (d > 0 and new > pos.stop) or (d < 0 and new < pos.stop):
                            pos.stop = new
                            pos.stop_history.append({"ts": int(row["end_ts"]), "stop": new, "why": f"breakeven after {m.breakeven_at_r:g}R"})
                            pos.events.append({"ts": int(row["end_ts"]), "event": f"stop moved to breakeven {new:g}"})
                        pos.be_done = True
                    if m.trail and (m.trail.activate_at_r is None or pos.best >= m.trail.activate_at_r * pos.risk_pts):
                        if m.trail.type == "indicator":
                            v = self.ind[m.trail.ref][i]
                            cand = None if np.isnan(v) else self._rt(v, "down" if d > 0 else "up")
                        else:
                            ref = max(close[pos.entry_i:i + 1]) if d > 0 else min(close[pos.entry_i:i + 1])
                            cand = self._rt(ref - d * m.trail.ticks * self.tick)
                        if cand is not None and ((d > 0 and cand > pos.stop and cand < close[i]) or (d < 0 and cand < pos.stop and cand > close[i])):
                            pos.stop = cand
                            pos.stop_history.append({"ts": int(row["end_ts"]), "stop": cand, "why": "trailing"})
                    dname = "long" if d > 0 else "short"
                    if self.exit_sig[dname][i]:
                        pending_exit = ("rule exit", "exit rule true at the bar close; market exit at the next open")
                    elif m.exit_on_opposite_signal and self.sig["short" if d > 0 else "long"][i]:
                        pending_exit = ("opposite signal", "opposite signal at the bar close; market exit at the next open")
                    elif m.time_exit_bars and pos.bars_held >= m.time_exit_bars:
                        pending_exit = ("time exit", f"{m.time_exit_bars} bars in the trade; market exit at the next open")
            # 5. new signals
            for dname, dval in (("long", 1), ("short", -1)):
                if self.sig[dname][i]:
                    self.counters[f"signals_{dname}"] += 1
            sl, ss = bool(self.sig["long"][i]), bool(self.sig["short"][i])
            if not (sl or ss):
                continue
            dname = "long" if sl else "short"
            if not tradable[i]:
                continue  # warm-up period before the start date: signals are not traded
            if sl and ss:
                self._skip(i, "both", "long and short signals on the same bar: neither taken")
                continue
            if pos is not None or pending is not None:
                continue
            if not in_sess[i]:
                self._skip(i, dname, "signal outside the trading session")
                continue
            if not self._in_window(i):
                self._skip(i, dname, "signal outside the time-of-day window")
                continue
            if sess_last[i]:
                self._skip(i, dname, "signal on the session's last bar: no bar left to enter")
                continue
            if i + 1 >= n or seg[i + 1] != seg[i]:
                self._skip(i, dname, "signal on the last bar before a roll or the end of data")
                continue
            if m.max_trades_per_day and trades_today.get(int(day[i]), 0) >= m.max_trades_per_day:
                self._skip(i, dname, f"max {m.max_trades_per_day} trade(s) per session reached")
                continue
            if m.cooldown_bars and i - last_exit_i <= m.cooldown_bars:
                self._skip(i, dname, f"within {m.cooldown_bars} bar(s) of the last exit (cooldown)")
                continue
            o = spec.entry.order
            dv = 1 if dname == "long" else -1
            order = {"type": o.type, "direction": dv, "dir_name": dname, "signal_i": i, "day": int(day[i]), "seg": int(seg[i]),
                     "bars_left": o.valid_bars, "valid": o.valid_bars}
            if o.type == "stop_beyond_signal_bar":
                order["level"] = self._rt((high[i] + o.offset_ticks * self.tick) if dv > 0 else (low[i] - o.offset_ticks * self.tick))
            elif o.type == "limit_at_level":
                v = self.ctx.operand(o.level)[i]
                if np.isnan(v):
                    self._skip(i, dname, "limit level not available (warm-up)")
                    continue
                order["level"] = self._rt(v)
            order["order_view"] = {"type": o.type, "level": order.get("level"), "valid_bars": o.valid_bars,
                                   "placed_ts": int(row["end_ts"])}
            rules = getattr(spec.entry, dname).when
            order["evidence"] = evidence(self.ctx, rules, i, spec)
            pending = order
        if pos is not None:  # data ended with an open position (only possible without roll detection)
            i = n - 1
            self._close(pos, i, close[i] - pos.direction * self.slip, int(b.iloc[i]["end_ts"]) - 1, "end of data", "forced exit at the last bar", True)
        return {"trades": self.trades, "skipped": self.skipped, "counters": self.counters}


def tz_info(name: str) -> ZoneInfo:
    return ZoneInfo(name)
