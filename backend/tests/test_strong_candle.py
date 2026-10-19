import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.engine.conditions import Ctx, eval_group
from app.engine.simulate import Simulator
from app.spec.schema import Group, StrategySpec

from .test_engine import make_bars, prep_of, spec_with

ROOT = Path(__file__).resolve().parents[2]
INTERP = json.loads((ROOT / "strategies" / "ema_vwap_strong_candle.json").read_text())


def long_when() -> Group:
    p = next(p for p in INTERP["proposals"] if p["path"] == "entry.long")
    return Group.model_validate(p["value"]["when"])


def ctx_with(bars_rows, lines: dict, atr, body=None):
    df = make_bars(bars_rows)
    ind = {k: np.asarray(v, float) for k, v in lines.items()}
    ind["atr14"] = np.asarray(atr, float)
    ind["body"] = (df["close"] - df["open"]).to_numpy(float) if body is None else np.asarray(body, float)
    return Ctx(df, ind, 0.25)


def test_breakout_rule_needs_open_below_one_close_above_all_and_strong_body():
    lines = {"ema10": [100] * 3, "ema20": [101] * 3, "ema30": [102] * 3, "ema60": [103] * 3, "vwap": [101.5] * 3}
    atr = [2.0, 2.0, 2.0]  # rule uses the previous bar's ATR: 1.5 x 2 = 3 points
    g = long_when()
    # bar 1: opens 100.5 (below several lines), closes 104 (above all), body 3.5 >= 3 -> True
    # bar 2: same but body only 2.5 (opens 101.5) -> False (not strong enough)
    c = ctx_with([(99, 99.5, 98.5, 99), (100.5, 104.5, 100.25, 104), (101.5, 104.5, 101.25, 104)], lines, atr)
    assert list(eval_group(c, g)) == [False, True, False]
    # Opens above every line (no break inside the candle) -> False even if strong.
    c2 = ctx_with([(99, 99.5, 98.5, 99), (103.5, 108, 103.25, 107.5)], {k: v[:2] for k, v in lines.items()}, [1.0, 1.0])
    assert list(eval_group(c2, g)) == [False, False]
    # Closes above all but one line -> False.
    c3 = ctx_with([(99, 99.5, 98.5, 99), (99, 102.75, 98.75, 102.75)], {k: v[:2] for k, v in lines.items()}, [1.0, 1.0])
    assert list(eval_group(c3, g)) == [False, False]


def test_stop_is_lower_of_ema60_and_signal_low():
    rows = [(100, 100.5, 99.5, 100)] * 25 + [(100, 102, 99.75, 101.75)] + [(101.75, 103, 101.5, 102.75)] * 4
    df = make_bars(rows)
    spec = spec_with(directions="long", indicators=[{"id": "ema10", "type": "ema", "period": 10, "source": "close"},
                                                   {"id": "ema20", "type": "ema", "period": 20, "source": "close"},
                                                   {"id": "ema60", "type": "ema", "period": 3, "source": "close"}],
                     stop={"type": "furthest_of", "levels": [{"kind": "indicator", "ref": "ema60"}, {"kind": "price", "field": "low"}],
                           "offset_ticks": 1})
    sim = Simulator(spec, prep_of(df))
    t = sim.run()["trades"][0]
    i = t["signal_i"]
    expect = min(sim.ind["ema60"][i], df.loc[i, "low"]) - 0.25
    assert t["initial_stop"] == pytest.approx(np.floor(expect / 0.25) * 0.25)
    assert t["stop_calc"].startswith("lower of:")


def test_import_applies_every_quoted_proposal_and_leaves_gaps_open(client):
    st = client.post("/api/strategies/import", json=INTERP).json()
    props = st["interpretation"]["proposals"]
    assert props and all(p["status"] == "applied" for p in props), [(p["path"], p["status"], p["reason"]) for p in props]
    assert st["interpretation"]["engine"] == "claude-manual"
    spec = st["spec"]
    assert [i["id"] for i in spec["indicators"]] == ["ema10", "ema20", "ema30", "ema60", "vwap", "atr14", "body"]
    assert spec["directions"] == "long" and spec["timeframe"] == "5m" and spec["stop"]["type"] == "furthest_of"
    ids = {q["id"] for q in st["questions"]}
    # Not decided by the user -> still questions, never defaults.
    assert {"target-r", "instrument", "session", "sizing", "commission"} <= ids
    assert any(i.startswith("ind-source-ema") for i in ids)
    text = " ".join(" ".join(s["lines"]) for s in st["summary"])
    assert "1.5 × ATR(14) of the previous bar" in text and "the lower of" in text


def test_unquoted_import_value_is_not_applied(client):
    bad = dict(INTERP, proposals=[{"path": "timeframe", "value": "15m", "quote": "fifteen minute bars"}])
    st = client.post("/api/strategies/import", json=bad).json()
    assert st["interpretation"]["proposals"][0]["status"] == "needs_confirmation"
    assert st["spec"]["timeframe"] is None
    _ = pd  # keep import used
