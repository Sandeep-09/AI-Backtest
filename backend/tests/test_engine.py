import copy

import numpy as np
import pandas as pd
import pytest

from app.engine import indicators, metrics
from app.engine.prepare import Prepared
from app.engine.simulate import Simulator
from app.spec.schema import StrategySpec

from .conftest import FULL_SPEC

NS_MIN = 60_000_000_000
T0 = 1_789_000_000_000_000_000 // NS_MIN * NS_MIN


def make_bars(rows, day=20000, iid=1, tf_min=5):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    n = len(df)
    df["ts"] = T0 + np.arange(n) * tf_min * NS_MIN
    df["end_ts"] = df["ts"] + tf_min * NS_MIN
    df["volume"] = 10
    df["iid"] = iid
    df["symbol"] = "NQZ6"
    df["day"] = day
    df["seg"] = 0
    df["in_session"] = True
    df["session_last"] = False
    df["tradable_day"] = True
    df["n_1m"] = tf_min
    return df


def prep_of(df):
    return Prepared(bars=df.reset_index(drop=True), root="NQ", choice=None, warmup_from=None, trade_from=None, trade_to=None,
                    intrabar_kind=None, base_res=["1m"], synthetic=True, sub_bars=None)


def spec_with(**over):
    d = copy.deepcopy(FULL_SPEC)
    for k, v in over.items():
        cur = d
        parts = k.split("__")
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = v
    return StrategySpec.model_validate(d)


# ----- indicators --------------------------------------------------------------------
def test_ema_matches_reference():
    x = np.arange(1, 31, dtype=float) + np.sin(np.arange(30))
    out = indicators.ema(x, 10)
    assert np.isnan(out[:9]).all()
    ref = x[:10].mean()
    for i in range(10, 30):
        ref = 2 / 11 * x[i] + (1 - 2 / 11) * ref
    assert out[29] == pytest.approx(ref)


def test_atr_rsi_basic():
    df = make_bars([(10, 12, 9, 11)] * 20)
    a = indicators.atr(df, 14)
    assert a[13] == pytest.approx(3.0) and np.isnan(a[12])
    r = indicators.rsi(np.arange(30, dtype=float), 14)
    assert r[-1] == pytest.approx(100.0)


def test_indicators_restart_at_roll():
    df = make_bars([(100 + i, 101 + i, 99 + i, 100 + i) for i in range(30)])
    df.loc[15:, "seg"] = 1
    from app.spec.schema import Indicator

    out = indicators.compute(df, [Indicator(id="e", type="ema", period=5, source="close")])["e"]
    assert np.isnan(out[15:19]).all() and not np.isnan(out[19])


# ----- no look-ahead -----------------------------------------------------------------
def test_future_bars_cannot_change_past_signals_or_trades():
    rng = np.random.default_rng(1)
    px = 20000 + np.cumsum(rng.normal(0, 3, 600))
    rows = []
    for p in px:
        o = round(p * 4) / 4
        h, lo = o + abs(rng.normal(0, 2)), o - abs(rng.normal(0, 2))
        c = round((lo + (h - lo) * rng.random()) * 4) / 4
        rows.append((o, round(h * 4) / 4, round(lo * 4) / 4, c))
    df = make_bars(rows)
    spec = spec_with(management__flat_at_session_end=False)
    base = Simulator(spec, prep_of(df)).run()
    cut = 400
    poisoned = df.copy()
    poisoned.loc[cut:, ["open", "high", "low", "close"]] = poisoned.loc[cut:, ["open", "high", "low", "close"]][::-1].to_numpy() + 500
    sim2 = Simulator(spec, prep_of(poisoned))
    res2 = sim2.run()
    sim1 = Simulator(spec, prep_of(df))
    for d in ("long", "short"):
        assert (sim1.sig[d][:cut] == sim2.sig[d][:cut]).all()
    done_before = [t for t in base["trades"] if t["exit_i"] < cut]
    assert done_before, "test needs some trades"
    assert done_before == [t for t in res2["trades"] if t["exit_i"] < cut]


# ----- fills, same bar, costs --------------------------------------------------------
def _one_trade_bars(last_bar):
    # 25 flat bars, then a cross up so EMA10 > EMA20, signal on bar 30, entry on bar 31 open.
    rows = [(100, 100.5, 99.5, 100)] * 25 + [(100, 101, 99.75, 100.75)] * 5
    rows.append((101, 101.5, 100.5, 101))  # bar 30: signal bar candidate
    rows.append(last_bar)
    rows += [(101, 101.25, 100.75, 101)] * 3
    return make_bars(rows)


def test_stop_first_policy_when_bar_hits_both():
    df = make_bars([(100, 100.5, 99.5, 100)] * 25 + [(100, 102, 99.75, 101.75)] + [(101.75, 102, 101.5, 101.75)] * 3)
    spec = spec_with(directions="long", fills__same_bar_policy="stop_first", stop={"type": "fixed_ticks", "ticks": 4},
                     target={"type": "r_multiple", "r": 1}, costs={"commission_per_side": 0, "slippage_ticks": 0})
    sim = Simulator(spec, prep_of(df))
    i = int(np.flatnonzero(sim.sig["long"])[0])
    # Make the bar after the entry bar reach both stop and target.
    e = i + 1
    o = float(df.loc[e, "open"])
    df.loc[e + 1, ["open", "high", "low", "close"]] = [o, o + 5, o - 5, o]
    res = Simulator(spec, prep_of(df)).run()
    t = res["trades"][0]
    assert t["exit_reason"] == "stop" and "policy: stop first" in t["exit_resolution"]
    spec_t = spec_with(directions="long", fills__same_bar_policy="target_first", stop={"type": "fixed_ticks", "ticks": 4},
                       target={"type": "r_multiple", "r": 1}, costs={"commission_per_side": 0, "slippage_ticks": 0})
    t2 = Simulator(spec_t, prep_of(df)).run()["trades"][0]
    assert t2["exit_reason"] == "target"


class FakeIntrabar:
    kind = "tick"

    def __init__(self, ticks):
        self.ticks = ticks

    def window(self, day, iid, a, z):
        t = self.ticks[(self.ticks["ts"] >= a) & (self.ticks["ts"] < z)]
        return t if len(t) else None


def test_tick_replay_decides_same_bar_order():
    df = make_bars([(100, 100.5, 99.5, 100)] * 25 + [(100, 102, 99.75, 101.75)] + [(101.75, 102, 101.5, 101.75)] * 3)
    spec = spec_with(directions="long", fills__same_bar_policy="intrabar_then_stop_first", stop={"type": "fixed_ticks", "ticks": 4},
                     target={"type": "r_multiple", "r": 1}, costs={"commission_per_side": 0, "slippage_ticks": 0},
                     fills__limit_fill="touch")
    i = int(np.flatnonzero(Simulator(spec, prep_of(df)).sig["long"])[0])
    e = i + 1
    o = float(df.loc[e, "open"])
    df.loc[e + 1, ["open", "high", "low", "close"]] = [o, o + 5, o - 5, o]
    ts0 = int(df.loc[e + 1, "ts"])
    # Ticks inside that bar: price rises to the target first, then falls through the stop.
    ticks = pd.DataFrame({"ts": [ts0 + k * 1_000_000_000 for k in range(4)], "open": [o, o + 1, o + 5, o - 5]})
    for c in ("high", "low", "close"):
        ticks[c] = ticks["open"]
    t = Simulator(spec, prep_of(df), intrabar=FakeIntrabar(ticks)).run()["trades"][0]
    assert t["exit_reason"] == "target" and "tick replay" in t["exit_resolution"]


def test_costs_and_slippage_math():
    df = make_bars([(100, 100.5, 99.5, 100)] * 25 + [(100, 102, 99.75, 101.75)] + [(101.75, 102, 101.5, 101.75)]
                   + [(101.75, 103, 101.5, 102.75)] * 2)
    spec = spec_with(directions="long", stop={"type": "fixed_ticks", "ticks": 8}, target={"type": "fixed_ticks", "ticks": 2},
                     costs={"commission_per_side": 2.5, "slippage_ticks": 1}, sizing={"type": "fixed_contracts", "contracts": 2},
                     fills__limit_fill="touch")
    t = Simulator(spec, prep_of(df)).run()["trades"][0]
    # NQ: tick 0.25 = $5. Entry = open + 1 tick slippage; target 2 ticks above the fill (limit: no slippage).
    assert t["entry_price"] == pytest.approx(t["planned_entry"] + 0.25)
    assert t["exit_price"] == pytest.approx(t["entry_price"] + 0.5)
    assert t["gross_pnl"] == pytest.approx(2 * 2 * 5.0)
    assert t["commission"] == pytest.approx(2.5 * 2 * 2)
    assert t["slippage_usd"] == pytest.approx(1 * 5.0 * 2)
    assert t["net_pnl"] == pytest.approx(20 - 10)


def test_stop_entry_and_wrong_side_stop_is_skipped():
    df = make_bars([(100, 100.5, 99.5, 100)] * 25 + [(100, 102, 99.75, 101.75)] + [(103, 104, 102.5, 103.5)] * 3)
    spec = spec_with(directions="long", entry__order={"type": "stop_beyond_signal_bar", "offset_ticks": 1, "valid_bars": 1},
                     stop={"type": "signal_bar_extreme", "offset_ticks": 1})
    t = Simulator(spec, prep_of(df)).run()["trades"][0]
    assert "opened through the level" in t["entry_how"] and t["entry_price"] == pytest.approx(103 + 0.25)
    spec2 = spec_with(directions="long", stop={"type": "indicator", "ref": "ema10", "offset_ticks": 0})
    df2 = df.copy()
    df2.loc[26, ["open", "high", "low", "close"]] = [90, 90.5, 89, 90]  # gaps below the EMA-based stop
    res = Simulator(spec2, prep_of(df2)).run()
    assert any("not beyond the fill" in s["reason"] for s in res["skipped"])


def test_metrics_on_hand_ledger():
    tr = [{"n": i + 1, "net_pnl": p, "direction": "long", "commission": 5, "slippage_usd": 5, "net_r": p / 100,
           "exit_reason": "x", "exit_resolution": "y", "exit_ts": i} for i, p in enumerate([100, -50, 200, -100, -100, 50])]
    m = metrics.compute(tr)
    assert m["trade_count"] == 6 and m["net_profit"] == 100
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["profit_factor"] == pytest.approx(350 / 250)
    assert m["expectancy"] == pytest.approx(100 / 6, abs=0.01)
    assert m["max_drawdown"] == -200  # peak 250 after trade 3, trough 50 after trade 5
    eq = metrics.equity_series(tr)
    assert [e["equity"] for e in eq] == [100, 50, 250, 150, 50, 100]
