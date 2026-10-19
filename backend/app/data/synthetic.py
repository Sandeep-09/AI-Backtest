"""
Synthetic Databento-format files for tests and demos.

Everything written here is fake. Files carry the dataset id "SYNTHETIC.TEST"
and live under a SYNTHETIC folder so the app labels them everywhere.

The layout mirrors the real VPS folder:
  nq_trades_warmup/glbx-mdp3-YYYYMMDD.trades.dbn.zst   continuous NQ.v.0, one file per UTC day
  nq_mbo/glbx-mdp3-YYYYMMDD.mbo.dbn.zst                continuous NQ.v.0, MBO with a book snapshot at the start
  gap_fill/nq_mbo_gap_YYYYMMDD/...mbo.dbn.zst          raw-symbol NQZ6 for a roll day
The continuous mapping switches from the September to the December contract
one UTC day after volume has already moved, like the real data.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import databento_dbn as dbn
import numpy as np
import zstandard

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
SYNTHETIC_DATASET = "SYNTHETIC.TEST"
NS = 1_000_000_000

OLD_ID, NEW_ID = 42004177, 261401  # "NQU6", "NQZ6"
OLD_SYM, NEW_SYM = "NQU6", "NQZ6"


class _SymbolMapping:
    def __init__(self, raw_symbol: str, intervals: list) -> None:
        self.raw_symbol = raw_symbol
        self.intervals = intervals


class _Interval:
    def __init__(self, start_date: dt.date, end_date: dt.date, symbol: str) -> None:
        self.start_date = start_date
        self.end_date = end_date
        self.symbol = symbol


def trade_dates(first: dt.date, n: int) -> list[dt.date]:
    out, d = [], first
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def session_bounds(d: dt.date) -> tuple[int, int]:
    start = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(18, 0), ET)
    end = dt.datetime.combine(d, dt.time(17, 0), ET)
    return int(start.timestamp() * NS), int(end.timestamp() * NS)


@dataclass
class Trade:
    ts: int
    iid: int
    price: float
    size: int
    side: str


def simulate_market(dates: list[dt.date], roll_index: int, seed: int = 7, gap_day: int | None = 1,
                    spacing_s: float = 4.0) -> list[Trade]:
    """Trade prints for both contracts. Volume moves to the new contract on dates[roll_index]."""
    rng = np.random.default_rng(seed)
    trades: list[Trade] = []
    px = 28000.0
    for di, d in enumerate(dates):
        s, e = session_bounds(d)
        n = int((e - s) / NS / spacing_s)
        ts = np.sort(rng.integers(s, e, n))
        if gap_day is not None and di == gap_day:
            g0 = int(dt.datetime.combine(d, dt.time(10, 0), ET).timestamp() * NS)
            ts = ts[(ts < g0) | (ts > g0 + 40 * 60 * NS)]
        regime = np.repeat(rng.choice([-1.0, 1.0], size=len(ts) // 500 + 1), 500)[:len(ts)]
        steps = rng.normal(0, 0.9, len(ts)) + regime * 0.12
        path = px + np.cumsum(steps)
        px = float(path[-1]) if len(path) else px
        new_share = 0.1 if di < roll_index else 0.9
        for t, p in zip(ts, path):
            iid = NEW_ID if rng.random() < new_share else OLD_ID
            price = round((p + (250.0 if iid == NEW_ID else 0.0)) / 0.25) * 0.25
            trades.append(Trade(int(t), iid, price, int(rng.integers(1, 6)), "B" if rng.random() < 0.5 else "A"))
    trades.sort(key=lambda x: x.ts)
    return trades


def _utc_day(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts / NS, UTC).date()


def _day_ns(d: dt.date) -> tuple[int, int]:
    s = int(dt.datetime.combine(d, dt.time(0, 0), UTC).timestamp() * NS)
    return s, s + 86400 * NS


def _meta(day: dt.date, schema, stype_in, symbol: str, iid: int):
    s, e = _day_ns(day)
    return dbn.Metadata(dataset=SYNTHETIC_DATASET, start=s, end=e, stype_in=stype_in, stype_out=dbn.SType.INSTRUMENT_ID,
                        schema=schema, symbols=[symbol],
                        mappings=[_SymbolMapping(symbol, [_Interval(day, day + dt.timedelta(days=1), str(iid))])])


def _write(path: Path, md, records: list[bytes]) -> Path:
    buf = bytearray(md.encode())
    for r in records:
        buf += r
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zstandard.ZstdCompressor().compress(bytes(buf)))
    return path


SIDES = {"A": dbn.Side.ASK, "B": dbn.Side.BID, "N": dbn.Side.NONE}


def _trade_records(trs: list[Trade]) -> list[bytes]:
    out = []
    for t in trs:
        out.append(bytes(dbn.TradeMsg(publisher_id=1, instrument_id=t.iid, ts_event=t.ts, price=int(round(t.price * 1e9)),
                                      size=t.size, action=dbn.Action.TRADE, side=SIDES[t.side], depth=0,
                                      ts_recv=t.ts + 20_000, flags=0)))
    return out


def _mbo_records(trs: list[Trade], day: dt.date, iid: int, backstep_at: int | None, rng) -> list[bytes]:
    day_start, _ = _day_ns(day)
    out: list[bytes] = []
    snap_ts = int(day_start - 2.5 * 86400 * NS)  # resting orders placed days earlier, like the real snapshot
    out.append(bytes(dbn.MBOMsg(publisher_id=1, instrument_id=iid, ts_event=snap_ts, order_id=0, price=np.iinfo(np.int64).max,
                                size=0, action=dbn.Action.CLEAR, side=dbn.Side.NONE, ts_recv=day_start, flags=40)))
    ref = trs[0].price if trs else 28000.0
    for k in range(3):
        out.append(bytes(dbn.MBOMsg(publisher_id=1, instrument_id=iid, ts_event=snap_ts + k, order_id=900000 + k,
                                    price=int(round((ref + 500 + k) * 1e9)), size=2, action=dbn.Action.ADD,
                                    side=dbn.Side.ASK, ts_recv=day_start, flags=40)))
    oid = 1_000_000
    last_tr = day_start
    for i, t in enumerate(trs):
        tr = t.ts + 20_000
        if backstep_at is not None and i == backstep_at:
            tr = last_tr - 1_000_000  # received 1 ms "before" the previous record
        last_tr = tr
        rest_side = SIDES["A" if t.side == "B" else "B"]
        out.append(bytes(dbn.MBOMsg(publisher_id=1, instrument_id=iid, ts_event=t.ts - 900, order_id=oid,
                                    price=int(round(t.price * 1e9)), size=t.size, action=dbn.Action.ADD, side=rest_side,
                                    ts_recv=tr - 900, flags=0)))
        out.append(bytes(dbn.MBOMsg(publisher_id=1, instrument_id=iid, ts_event=t.ts, order_id=0, price=int(round(t.price * 1e9)),
                                    size=t.size, action=dbn.Action.TRADE, side=SIDES[t.side], ts_recv=tr, flags=0)))
        out.append(bytes(dbn.MBOMsg(publisher_id=1, instrument_id=iid, ts_event=t.ts, order_id=oid, price=int(round(t.price * 1e9)),
                                    size=t.size, action=dbn.Action.FILL, side=rest_side, ts_recv=tr, flags=0)))
        if rng.random() < 0.3:
            out.append(bytes(dbn.MBOMsg(publisher_id=1, instrument_id=iid, ts_event=t.ts + 200, order_id=oid + 500_000,
                                        price=int(round((t.price - 1.0) * 1e9)), size=1, action=dbn.Action.CANCEL,
                                        side=dbn.Side.BID, ts_recv=tr + 200, flags=0)))
        oid += 1
    return out


def write_vps_like_folder(folder: Path, *, first_trade_date: dt.date = dt.date(2026, 9, 8), n_days: int = 8,
                          roll_index: int = 5, warmup_days: int = 3, seed: int = 7,
                          inject_gap: bool = True, inject_backstep: bool = True) -> dict:
    """Write NQ files laid out like the real folder. Returns facts tests can assert on."""
    rng = np.random.default_rng(seed + 1)
    dates = trade_dates(first_trade_date, n_days)
    trades = simulate_market(dates, roll_index, seed=seed, gap_day=1 if inject_gap else None)
    first_utc = _utc_day(trades[0].ts)
    last_utc = _utc_day(trades[-1].ts)
    # The continuous mapping switches one UTC day after the volume roll (like Databento's v.0 did on 2026-09-16).
    switch_utc = dates[roll_index] + dt.timedelta(days=1)
    by_day: dict[dt.date, list[Trade]] = {}
    for t in trades:
        by_day.setdefault(_utc_day(t.ts), []).append(t)
    written = []
    warmup_until = dates[warmup_days - 1]  # UTC days up to this date are "trades" schema; later ones are MBO
    root = folder / "SYNTHETIC"
    backstep_done = not inject_backstep
    for day in sorted(by_day):
        trs = by_day[day]
        v0_id = OLD_ID if day < switch_utc else NEW_ID
        v0 = [t for t in trs if t.iid == v0_id]
        tag = day.strftime("%Y%m%d")
        if day <= warmup_until:
            p = root / "nq_trades_warmup" / f"glbx-mdp3-{tag}.trades.dbn.zst"
            written.append(_write(p, _meta(day, dbn.Schema.TRADES, dbn.SType.CONTINUOUS, "NQ.v.0", v0_id), _trade_records(v0)))
        else:
            back = None
            if not backstep_done and len(v0) > 100:
                back, backstep_done = len(v0) // 2, True
            p = root / "nq_mbo" / f"glbx-mdp3-{tag}.mbo.dbn.zst"
            written.append(_write(p, _meta(day, dbn.Schema.MBO, dbn.SType.CONTINUOUS, "NQ.v.0", v0_id),
                                  _mbo_records(v0, day, v0_id, back, rng)))
        if day == dates[roll_index]:
            # Gap fill: the new contract for the roll day, requested by raw symbol.
            new = [t for t in trs if t.iid == NEW_ID]
            p = root / "gap_fill" / f"nq_mbo_gap_{tag}" / f"glbx-mdp3-{tag}.mbo.dbn.zst"
            written.append(_write(p, _meta(day, dbn.Schema.MBO, dbn.SType.RAW_SYMBOL, NEW_SYM, NEW_ID),
                                  _mbo_records(new, day, NEW_ID, None, rng)))
    return {"files": written, "trade_dates": dates, "roll_date": dates[roll_index], "switch_utc": switch_utc,
            "first_utc": first_utc, "last_utc": last_utc, "gap_date": dates[1] if inject_gap else None,
            "trades": trades}


def write_ohlcv_1m(path: Path, *, seed: int = 11, first_trade_date: dt.date = dt.date(2026, 9, 8), n_days: int = 5) -> Path:
    """GC-like 1-minute bars for one outright (tests the OHLCV path)."""
    rng = np.random.default_rng(seed)
    dates = trade_dates(first_trade_date, n_days)
    iid, tick = 41271, 0.1
    recs = []
    price = 4400.0
    for d in dates:
        s, e = session_bounds(d)
        for t in range(s, e, 60 * NS):
            if rng.random() < 0.03:
                continue
            o = price
            path_px = o + np.cumsum(rng.normal(0, 0.35, 12))
            h, lo, c = max(o, path_px.max()), min(o, path_px.min()), path_px[-1]
            o, h, lo, c = (round(x / tick) * tick for x in (o, h, lo, c))
            price = c
            recs.append(bytes(dbn.OHLCVMsg(rtype=dbn.RType.OHLCV_1M, publisher_id=1, instrument_id=iid, ts_event=t,
                                           open=int(round(o * 1e9)), high=int(round(h * 1e9)), low=int(round(lo * 1e9)),
                                           close=int(round(c * 1e9)), volume=int(rng.integers(1, 80)))))
    s0, _ = session_bounds(dates[0])
    _, e1 = session_bounds(dates[-1])
    d0, d1 = _utc_day(s0), _utc_day(e1) + dt.timedelta(days=1)
    md = dbn.Metadata(dataset=SYNTHETIC_DATASET, start=s0, end=e1, stype_in=dbn.SType.RAW_SYMBOL, stype_out=dbn.SType.INSTRUMENT_ID,
                      schema=dbn.Schema.OHLCV_1M, symbols=["GCZ6"], mappings=[_SymbolMapping("GCZ6", [_Interval(d0, d1, str(iid))])])
    return _write(path, md, recs)


def write_demo_folder(folder: Path, n_days: int = 8) -> dict:
    facts = write_vps_like_folder(folder, n_days=n_days)
    facts["files"].append(write_ohlcv_1m(folder / "SYNTHETIC" / "gc_ohlcv" / "glbx-mdp3-gc.ohlcv-1m.dbn.zst"))
    return facts


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "demo-data")
    f = write_demo_folder(target)
    for p in f["files"]:
        print("wrote", p)
