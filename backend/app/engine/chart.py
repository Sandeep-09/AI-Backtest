"""Chart payloads. Times are shifted to the display timezone (wall-clock seconds) for the chart library."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..spec.schema import StrategySpec
from ..summary.render import indicator_label


def wall_seconds(ns: np.ndarray | list | int, tz: str) -> np.ndarray:
    arr = np.atleast_1d(np.asarray(ns, dtype=np.int64))
    idx = pd.DatetimeIndex(arr.astype("datetime64[ns]"), tz="UTC").tz_convert(tz).tz_localize(None)
    return (idx.asi8 // 1_000_000_000).astype(np.int64)


def window(spec: StrategySpec, bars: pd.DataFrame, ind: dict[str, np.ndarray], a: int, z: int) -> dict:
    tz = spec.display_timezone or "America/New_York"
    a, z = max(0, a), min(len(bars), z)
    w = bars.iloc[a:z]
    t = wall_seconds(w["ts"].to_numpy(), tz)
    candles = [{"time": int(tt), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close),
                "i": int(a + k), "in_session": bool(r.in_session)} for k, (tt, r) in enumerate(zip(t, w.itertuples(index=False)))]
    lines = []
    for spec_ind in spec.indicators:
        v = ind.get(spec_ind.id)
        if v is None:
            continue
        seg = w["seg"].to_numpy()
        pts = []
        for k, tt in enumerate(t):
            x = v[a + k]
            if not np.isnan(x):
                pts.append({"time": int(tt), "value": round(float(x), 6)})
            elif pts and k and seg[k] != seg[k - 1]:
                pts.append({"time": int(tt)})  # whitespace: break the line at a roll
        lines.append({"id": spec_ind.id, "label": indicator_label(spec_ind), "overlay": spec_ind.type not in ("rsi", "atr", "candle_body", "candle_range"),
                      "points": pts})
    rolls = []
    seg = w["seg"].to_numpy()
    for k in range(1, len(w)):
        if seg[k] != seg[k - 1]:
            rolls.append({"time": int(t[k]), "from": str(w.iloc[k - 1]["symbol"]), "to": str(w.iloc[k]["symbol"])})
    return {"timezone": tz, "candles": candles, "indicators": lines, "rolls": rolls}


def trade_overlay(spec: StrategySpec, t: dict) -> dict:
    tz = spec.display_timezone or "America/New_York"
    sec = lambda ns: int(wall_seconds(ns, tz)[0])  # noqa: E731
    return {
        "n": t["n"], "direction": t["direction"],
        "signal_time": sec(t["signal_bar_ts"]), "entry_time": sec(t["entry_ts"]), "exit_time": sec(t["exit_ts"]),
        "planned_entry": t["planned_entry"], "entry_price": t["entry_price"], "stop": t["initial_stop"], "target": t["target"],
        "exit_price": t["exit_price"], "exit_reason": t["exit_reason"],
        "stop_history": [{"time": sec(s["ts"]), "stop": s["stop"], "why": s["why"]} for s in t.get("stop_history", [])],
        "order_level": (t.get("order") or {}).get("level"),
    }


def compact(spec: StrategySpec, bars: pd.DataFrame, ind: dict[str, np.ndarray], a: int = 0, z: int | None = None) -> dict:
    """Column arrays for a whole range (smaller than per-candle objects)."""
    tz = spec.display_timezone or "America/New_York"
    z = len(bars) if z is None else min(len(bars), z)
    a = max(0, a)
    w = bars.iloc[a:z]
    t = wall_seconds(w["ts"].to_numpy(), tz).tolist()

    def col(x) -> list:
        return [None if (v != v) else round(float(v), 6) for v in x]

    seg = w["seg"].to_numpy()
    lines = []
    for spec_ind in spec.indicators:
        v = ind.get(spec_ind.id)
        if v is None:
            continue
        lines.append({"id": spec_ind.id, "label": indicator_label(spec_ind),
                      "overlay": spec_ind.type not in ("rsi", "atr", "candle_body", "candle_range"), "v": col(v[a:z])})
    rolls = [{"time": t[k], "from": str(w.iloc[k - 1]["symbol"]), "to": str(w.iloc[k]["symbol"])}
             for k in range(1, len(w)) if seg[k] != seg[k - 1]]
    return {"tz": tz, "t": t, "o": col(w["open"].to_numpy()), "h": col(w["high"].to_numpy()), "l": col(w["low"].to_numpy()),
            "c": col(w["close"].to_numpy()), "out": [k for k, s in enumerate(w["in_session"].to_numpy()) if not s],
            "ind": lines, "rolls": rolls, "offset": a}
