"""
Turn base 1m bars into strategy bars, following the spec exactly:
contract per session, session window, which bars feed indicators,
timeframe aggregation anchored at the session start.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..data.ingest import day_str
from ..data.store import ContractChoice, all_bars, choose_contracts
from ..instruments import CONTRACTS, ETH_START
from ..spec.schema import TIMEFRAME_MINUTES, StrategySpec

NS_MIN = 60_000_000_000


@dataclass
class Prepared:
    bars: pd.DataFrame  # strategy bars, positional index 0..n-1
    root: str
    choice: ContractChoice | None
    warmup_from: str | None
    trade_from: str | None
    trade_to: str | None
    intrabar_kind: str | None
    base_res: list[str]
    synthetic: bool
    sub_bars: pd.DataFrame | None = None  # contract-filtered base 1m bars (sub-bar fallback for intrabar checks)
    notes: list[str] = field(default_factory=list)


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def session_window(spec: StrategySpec) -> tuple[int, int, str]:
    """(start_min, end_min, tz) of the trading window, minutes after midnight in the session tz."""
    s = spec.session
    c = CONTRACTS[spec.instrument]
    if s.type == "rth":
        return _hhmm(c.rth_start), _hhmm(c.rth_end), s.tz
    if s.type == "eth":
        return _hhmm(ETH_START), _hhmm("17:00"), "America/New_York"
    return _hhmm(s.start), _hhmm(s.end), s.tz


def _minute_of_day(ts_ns: np.ndarray, tz: str) -> tuple[np.ndarray, np.ndarray]:
    idx = pd.DatetimeIndex(ts_ns.astype("datetime64[ns]"), tz="UTC").tz_convert(tz)
    wall = idx.tz_localize(None).asi8
    mins = (wall // NS_MIN) % 1440
    return mins.astype(np.int64), wall


def _anchor_ns(ts_ns: np.ndarray, start_min: int, tz: str) -> np.ndarray:
    """UTC ns of the most recent session-start wall time at or before each timestamp."""
    mins, wall = _minute_of_day(ts_ns, tz)
    day_wall = wall - (wall % (1440 * NS_MIN))
    anchor_wall = day_wall + start_min * NS_MIN
    anchor_wall = np.where(mins >= start_min, anchor_wall, anchor_wall - 1440 * NS_MIN)
    # Convert the wall-clock anchor back to UTC (DST-safe, per unique anchor).
    uniq = np.unique(anchor_wall)
    conv = {}
    z = ZoneInfo(tz)
    for w in uniq:
        naive = dt.datetime(1970, 1, 1) + dt.timedelta(microseconds=int(w) // 1000)
        conv[w] = int(naive.replace(tzinfo=z).timestamp() * 1e9)
    return np.array([conv[w] for w in anchor_wall], dtype=np.int64)


def prepare(spec: StrategySpec, base: pd.DataFrame | None = None, choice: ContractChoice | None = None,
            limit_trade_dates: int | None = None) -> Prepared:
    root = spec.instrument
    notes: list[str] = []
    if base is None:
        base, overlaps = all_bars(root)
        if overlaps:
            notes.append(f"{len(overlaps)} contract-day(s) appear in more than one file; the file with more data was used.")
    if base.empty:
        raise ValueError(f"No ingested data for {root}.")
    if choice is None:
        choice = choose_contracts(base, spec.data.contract or "most_active_same_day", spec.data.symbol)
    chosen = pd.Series(choice.by_day)
    b = base[base["iid"].to_numpy() == base["day"].map(chosen).to_numpy()].copy()
    if b.empty:
        raise ValueError("No bars left after applying the contract rule.")

    start_d = np.datetime64(spec.data.start, "D").astype(np.int64) if spec.data.start else None
    end_d = np.datetime64(spec.data.end, "D").astype(np.int64) if spec.data.end else None
    if end_d is not None:
        b = b[b["day"] <= end_d]
    trade_days = sorted(b["day"].unique().tolist())
    in_range = [d for d in trade_days if start_d is None or d >= start_d]
    if not in_range:
        raise ValueError("No trade dates of data inside the selected date range.")
    if limit_trade_dates:
        last = in_range[min(len(in_range), limit_trade_dates) - 1]
        b = b[b["day"] <= last]
        in_range = [d for d in in_range if d <= last]

    smin, emin, tz = session_window(spec)
    mins, _ = _minute_of_day(b["ts"].to_numpy(), tz)
    if spec.session.type == "eth":
        in_sess = np.ones(len(b), dtype=bool)
    elif smin < emin:
        in_sess = (mins >= smin) & (mins < emin)
    else:
        in_sess = (mins >= smin) | (mins < emin)
    b["in_session"] = in_sess
    use = spec.session.indicators_use if spec.session.type != "eth" else "all_hours"
    if use == "session_bars_only":
        b = b[b["in_session"]]

    # Aggregate to the strategy timeframe, buckets anchored at the session start.
    tf = TIMEFRAME_MINUTES[spec.timeframe] * NS_MIN
    anchors = _anchor_ns(b["ts"].to_numpy(), smin, tz)
    bucket = anchors + ((b["ts"].to_numpy() - anchors) // tf) * tf
    b = b.assign(bucket=bucket)
    g = b.groupby(["iid", "day", "bucket"], sort=False)
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum"), symbol=("symbol", "first"),
                in_session=("in_session", "first"), last_1m=("ts", "max"), n_1m=("ts", "size")).reset_index()
    out = out.rename(columns={"bucket": "ts"}).sort_values("ts", kind="mergesort").reset_index(drop=True)
    out["end_ts"] = np.minimum(out["ts"] + tf, out["last_1m"] + NS_MIN)
    # A bucket counts as "in session" if it starts inside the window.
    # Segment = run of bars on the same contract (indicators restart at each roll).
    seg = (out["iid"] != out["iid"].shift()).cumsum() - 1
    out["seg"] = seg.astype(int)
    # Last in-session bar of each trade date, and whether the next bar is in the same session.
    nxt_day = out["day"].shift(-1)
    nxt_sess = out["in_session"].astype(bool).shift(-1, fill_value=False)
    out["session_last"] = out["in_session"] & ((nxt_day != out["day"]) | (~nxt_sess))
    out["tradable_day"] = out["day"].isin(in_range)
    out = out.drop(columns=["last_1m"])
    sym_kind = base.groupby("iid")["intrabar"].first().to_dict()
    kinds = {sym_kind.get(i) for i in out["iid"].unique()}
    intrabar_kind = "tick" if "tick" in kinds else ("1s" if "1s" in kinds else None)
    if intrabar_kind is None and TIMEFRAME_MINUTES[spec.timeframe] > 1:
        intrabar_kind = "1m"  # base 1m bars serve as sub-bars
    return Prepared(bars=out, root=root, choice=choice,
                    warmup_from=day_str(trade_days[0]) if trade_days else None,
                    trade_from=day_str(in_range[0]), trade_to=day_str(in_range[-1]), intrabar_kind=intrabar_kind,
                    base_res=sorted(set(base["base_res"].unique())), synthetic=bool(base["synthetic"].any()),
                    sub_bars=b[["ts", "iid", "day", "open", "high", "low", "close"]].reset_index(drop=True), notes=notes)
