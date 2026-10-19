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
INTERP = json.loads((ROOT / "strategies" / "ema_vwap_strong_candle_NQ.json").read_text())
INTERP_GC = json.loads((ROOT / "strategies" / "ema_vwap_strong_candle_GC.json").read_text())


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


OPEN_DECISIONS = {"display-tz", "contract", "dates", "target-fill", "same-bar"}


@pytest.mark.parametrize("payload,root", [(INTERP, "NQ"), (INTERP_GC, "GC")])
def test_import_applies_every_quoted_proposal_and_leaves_gaps_open(client, payload, root):
    st = client.post("/api/strategies/import", json=payload).json()
    props = st["interpretation"]["proposals"]
    assert props and all(p["status"] == "applied" for p in props), [(p["path"], p["status"], p["reason"]) for p in props]
    assert st["interpretation"]["engine"] == "claude-manual"
    spec = st["spec"]
    assert [i["id"] for i in spec["indicators"]] == ["ema10", "ema20", "ema30", "ema60", "vwap", "atr14", "body"]
    assert spec["instrument"] == root and spec["directions"] == "long" and spec["timeframe"] == "5m"
    assert spec["stop"]["type"] == "furthest_of" and spec["target"] == {"type": "r_multiple", "r": 3.0, "ticks": None, "ref": None}
    assert spec["session"]["type"] == "eth" and spec["management"]["flat_at_session_end"] is False
    assert spec["costs"] == {"commission_per_side": 2.5, "slippage_ticks": 1.0}
    ids = {q["id"] for q in st["questions"] if q["severity"] == "required"}
    # Exactly the decisions the user hasn't made yet are still open.
    assert ids - {i for i in ids if i.startswith("ind-source-ema")} == OPEN_DECISIONS
    assert {f"ind-source-ema{n}" for n in (10, 20, 30, 60)} <= ids
    text = " ".join(" ".join(s["lines"]) for s in st["summary"])
    assert "1.5 × ATR(14) of the previous bar" in text and "the lower of" in text and "1:3" in text


def test_report_prints_metrics_for_finished_run(client):
    from app import jobs, report

    st = client.post("/api/strategies/import", json=INTERP).json()
    sid = st["strategy"]["id"]
    inds = [dict(i, source="close") if i["type"] == "ema" else i for i in st["spec"]["indicators"]]
    ops = [{"path": "indicators", "value": inds}, {"path": "display_timezone", "value": "America/New_York"},
           {"path": "data", "value": {"contract": "most_active_same_day", "start": "2026-09-08", "end": "2026-09-17"}},
           {"path": "fills.same_bar_policy", "value": "intrabar_then_stop_first"}, {"path": "fills.limit_fill", "value": "trade_through"}]
    st = client.post(f"/api/strategies/{sid}/patch", json={"ops": ops}).json()
    v, h = st["version"]["version"], st["version"]["spec_hash"]
    st = client.post(f"/api/strategies/{sid}/versions/{v}/preview").json()
    assert st["gate"]["can_approve"], st["gate"]["reasons"]
    client.post(f"/api/strategies/{sid}/versions/{v}/approve", json={"spec_hash": h})
    run = client.post(f"/api/strategies/{sid}/versions/{v}/runs", json={"spec_hash": h}).json()
    assert jobs.wait(run["job_id"], 120)["status"] == "done"
    text, rid = report.build(run["run_id"])
    assert rid == run["run_id"] and f"hash {h[:16]}" in text and "SYNTHETIC" in text
    m = client.get(f"/api/runs/{rid}").json()["result"]["metrics"]
    if m["trade_count"]:
        assert f"Trades: {m['trade_count']}" in text
    else:
        assert "No trades were taken." in text
    assert "exported to" in text


def test_unquoted_import_value_is_not_applied(client):
    bad = dict(INTERP, proposals=[{"path": "timeframe", "value": "15m", "quote": "fifteen minute bars"}])
    st = client.post("/api/strategies/import", json=bad).json()
    assert st["interpretation"]["proposals"][0]["status"] == "needs_confirmation"
    assert st["spec"]["timeframe"] is None
    _ = pd  # keep import used
