"""
Causal indicators: the value at bar t uses bars <= t only.
Computed per segment (contract run), so they restart at each roll.
Values during warm-up are NaN, and NaN never satisfies a condition.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..spec.schema import Indicator


def _src(df: pd.DataFrame, source: str | None, default: str = "close") -> np.ndarray:
    s = source or default
    if s == "hl2":
        return ((df["high"] + df["low"]) / 2).to_numpy(float)
    if s == "hlc3":
        return ((df["high"] + df["low"] + df["close"]) / 3).to_numpy(float)
    return df[s].to_numpy(float)


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2.0 / (n + 1)
    v = float(np.mean(x[:n]))
    out[n - 1] = v
    for i in range(n, len(x)):
        v = a * x[i] + (1 - a) * v
        out[i] = v
    return out


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def wilder(x: np.ndarray, n: int, start: int = 0) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) - start < n:
        return out
    v = float(np.mean(x[start:start + n]))
    out[start + n - 1] = v
    for i in range(start + n, len(x)):
        v = (v * (n - 1) + x[i]) / n
        out[i] = v
    return out


def atr(df: pd.DataFrame, n: int) -> np.ndarray:
    h, lo, c = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - lo, np.abs(h - pc), np.abs(lo - pc)]), axis=0)
    tr[0] = h[0] - lo[0]
    return wilder(tr, n)


def rsi(x: np.ndarray, n: int) -> np.ndarray:
    d = np.diff(x, prepend=np.nan)
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    au, ad = wilder(up, n, start=1), wilder(dn, n, start=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = au / ad
        out = 100 - 100 / (1 + rs)
    out = np.where((ad == 0) & ~np.isnan(au), 100.0, out)
    return out


def vwap(df: pd.DataFrame) -> np.ndarray:
    tp = ((df["high"] + df["low"] + df["close"]) / 3).to_numpy(float)
    v = df["volume"].to_numpy(float)
    day = df["day"].to_numpy()
    out = np.full(len(df), np.nan)
    cpv = cv = 0.0
    for i in range(len(df)):
        if i == 0 or day[i] != day[i - 1]:
            cpv = cv = 0.0
        cpv += tp[i] * v[i]
        cv += v[i]
        out[i] = cpv / cv if cv > 0 else np.nan
    return out


def prior_session(df: pd.DataFrame, which: str) -> np.ndarray:
    sess = df[df["in_session"]] if "in_session" in df else df
    agg = sess.groupby("day")["high" if which == "high" else "low"].agg("max" if which == "high" else "min")
    days = sorted(agg.index.tolist())
    prev = {d: agg[days[k - 1]] for k, d in enumerate(days) if k > 0}
    return df["day"].map(prev).to_numpy(float)


def compute_one(df: pd.DataFrame, ind: Indicator) -> np.ndarray:
    t = ind.type
    if t == "ema":
        return ema(_src(df, ind.source), ind.period)
    if t == "sma":
        return sma(_src(df, ind.source), ind.period)
    if t == "atr":
        return atr(df, ind.period)
    if t == "rsi":
        return rsi(_src(df, ind.source), ind.period)
    if t == "vwap":
        return vwap(df)
    if t == "highest":
        return pd.Series(_src(df, ind.source, "high")).rolling(ind.period, min_periods=ind.period).max().to_numpy()
    if t == "lowest":
        return pd.Series(_src(df, ind.source, "low")).rolling(ind.period, min_periods=ind.period).min().to_numpy()
    if t == "candle_body":
        return (df["close"] - df["open"]).to_numpy(float)
    if t == "candle_range":
        return (df["high"] - df["low"]).to_numpy(float)
    if t == "prior_session_high":
        return prior_session(df, "high")
    if t == "prior_session_low":
        return prior_session(df, "low")
    raise ValueError(f"unsupported indicator {t}")


def compute(bars: pd.DataFrame, indicators: list[Indicator]) -> dict[str, np.ndarray]:
    out = {i.id: np.full(len(bars), np.nan) for i in indicators}
    for _, idx in bars.groupby("seg").indices.items():
        seg = bars.iloc[idx]
        for ind in indicators:
            out[ind.id][idx] = compute_one(seg, ind)
    return out
