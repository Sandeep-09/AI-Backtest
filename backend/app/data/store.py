"""
Assemble ingested files into one bar series per instrument root.

Files are one UTC day each, so every Globex session (18:00-17:00 ET) spans
two files. Bars and trade prints are merged across files per trade date.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .. import db
from .catalog import file_cache_dir, symbol_registry
from .ingest import day_str


@dataclass
class FileEntry:
    fp: str
    name: str
    path: str
    manifest: dict

    @property
    def dir(self) -> Path:
        return file_cache_dir(self.fp)


def ingested_files() -> list[FileEntry]:
    out = []
    for r in db.q("SELECT fingerprint, path FROM files WHERE status='ingested' ORDER BY path"):
        mp = file_cache_dir(r["fingerprint"]) / "manifest.json"
        if not mp.exists():
            continue
        man = json.loads(mp.read_text())
        if man.get("kind") in ("ticks", "bars"):
            out.append(FileEntry(r["fingerprint"], Path(r["path"]).name, r["path"], man))
    return out


def _files_for_root(root: str) -> list[FileEntry]:
    return [f for f in ingested_files() if any(i.get("root") == root and i.get("kind") == "outright"
                                               for i in (f.manifest.get("instruments") or {}).values())]


def _display_symbol(iid: int, sym: str | None, reg: dict[int, str]) -> str:
    if int(iid) in reg:
        return reg[int(iid)]
    return sym or f"id{iid}"


def all_bars(root: str) -> tuple[pd.DataFrame, list[dict]]:
    """Every outright 1m bar for `root` across files, deduplicated. Returns (bars, overlap notes)."""
    files = _files_for_root(root)
    frames = []
    for f in files:
        p = f.dir / "bars.parquet"
        if not p.exists():
            continue
        b = pq.read_table(p).to_pandas()
        b = b[(b["root"] == root) & (b["kind"] == "outright")]
        if b.empty:
            continue
        b = b.assign(fp=f.fp, file=f.name, base_res=f.manifest.get("base_res"), intrabar=f.manifest.get("intrabar") or "none",
                     synthetic=bool(f.manifest.get("synthetic")))
        frames.append(b)
    if not frames:
        return pd.DataFrame(), []
    bars = pd.concat(frames, ignore_index=True)
    reg = symbol_registry()
    bars["symbol"] = [_display_symbol(i, s, reg) for i, s in zip(bars["iid"], bars["symbol"])]
    overlaps = []
    dup = bars.duplicated(subset=["iid", "ts"], keep=False)
    if dup.any():
        # Same contract and minute in more than one file: keep the file with more data for that contract-day.
        size = bars.groupby(["fp", "iid", "day"])["volume"].transform("sum")
        bars = bars.assign(_size=size).sort_values(["iid", "ts", "_size"], ascending=[True, True, False])
        d = bars[bars.duplicated(subset=["iid", "ts"], keep=False)]
        for (iid, day), g in d.groupby(["iid", "day"]):
            overlaps.append({"symbol": g["symbol"].iloc[0], "trade_date": day_str(day), "files": sorted(g["file"].unique().tolist()),
                             "kept": g.iloc[0]["file"]})
        bars = bars.drop_duplicates(subset=["iid", "ts"], keep="first").drop(columns=["_size"])
    bars = bars.sort_values(["ts", "iid"], kind="mergesort").reset_index(drop=True)
    return bars, overlaps


def daily_volume(bars: pd.DataFrame) -> pd.DataFrame:
    v = bars.groupby(["day", "iid", "symbol"], as_index=False).agg(volume=("volume", "sum"), bars=("ts", "size"),
                                                                    first_ts=("ts", "min"), last_ts=("ts", "max"))
    return v.sort_values(["day", "volume"], ascending=[True, False])


@dataclass
class ContractChoice:
    by_day: dict[int, int]  # trade date (days) -> iid
    skipped_days: list[dict] = field(default_factory=list)
    rolls: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def choose_contracts(bars: pd.DataFrame, mode: str, symbol: str | None = None) -> ContractChoice:
    dv = daily_volume(bars)
    days = sorted(dv["day"].unique().tolist())
    ch = ContractChoice(by_day={})
    if mode == "specific":
        m = dv[dv["symbol"] == symbol]
        for d in m["day"].tolist():
            ch.by_day[int(d)] = int(m[m["day"] == d]["iid"].iloc[0])
        missing = [day_str(d) for d in days if d not in ch.by_day]
        if missing:
            ch.notes.append(f"{symbol} has no data on {len(missing)} trade date(s) where other contracts do.")
        return ch
    prev_choice = None
    for k, d in enumerate(days):
        today = dv[dv["day"] == d]
        if mode == "most_active_same_day":
            pick = int(today.iloc[0]["iid"])
        else:  # front_by_prior_volume
            if k == 0:
                ch.skipped_days.append({"trade_date": day_str(d), "reason": "first trade date: no prior session to choose the contract from"})
                continue
            prev = dv[dv["day"] == days[k - 1]]
            cands = today["iid"].tolist()
            prev_vol = {int(r.iid): int(r.volume) for r in prev.itertuples()}
            ranked = sorted(cands, key=lambda i: -prev_vol.get(int(i), -1))
            pick = int(ranked[0])
            if prev_vol.get(pick) is None:
                ch.notes.append(f"{day_str(d)}: no prior-session volume for any available contract; used {today[today['iid'] == pick]['symbol'].iloc[0]}.")
        if prev_choice is not None and pick != prev_choice:
            sym = lambda i: dv[dv["iid"] == i]["symbol"].iloc[0]  # noqa: E731
            ch.rolls.append({"trade_date": day_str(d), "from": sym(prev_choice), "to": sym(pick)})
        ch.by_day[int(d)] = pick
        prev_choice = pick
    return ch


def coverage() -> dict:
    """Per root: contracts, trade dates, granularity, overlaps, rolls under each contract rule."""
    out: dict = {}
    for root in ("NQ", "MNQ", "GC", "MGC"):
        bars, overlaps = all_bars(root)
        if bars.empty:
            continue
        dv = daily_volume(bars)
        days = sorted(dv["day"].unique().tolist())
        contracts = []
        for (iid, sym), g in dv.groupby(["iid", "symbol"]):
            contracts.append({"iid": int(iid), "symbol": sym, "first_day": day_str(g["day"].min()), "last_day": day_str(g["day"].max()),
                              "trade_dates": int(g["day"].nunique()), "volume": int(g["volume"].sum())})
        per_day = []
        for d in days:
            t = dv[dv["day"] == d]
            per_day.append({"trade_date": day_str(d), "contracts": [
                {"symbol": r.symbol, "volume": int(r.volume), "bars": int(r.bars)} for r in t.itertuples()]})
        gran = sorted(set(bars["intrabar"].unique()))
        out[root] = {
            "first_day": day_str(days[0]), "last_day": day_str(days[-1]), "trade_dates": len(days),
            "contracts": sorted(contracts, key=lambda c: c["first_day"]), "per_day": per_day,
            "symbols": [c["symbol"] for c in sorted(contracts, key=lambda c: -c["volume"])],
            "intrabar": gran, "base_res": sorted(set(bars["base_res"].unique())), "overlaps": overlaps,
            "synthetic": bool(bars["synthetic"].any()),
            "rolls": {m: choose_contracts(bars, m).rolls for m in ("front_by_prior_volume", "most_active_same_day")},
        }
    return out


class Intrabar:
    """Trade prints (or 1s bars) for a contract on a trade date, merged across files. Small LRU cache."""

    def __init__(self, root: str, max_days: int = 6) -> None:
        self.files = [f for f in _files_for_root(root) if f.manifest.get("intrabar")]
        self.kind = "tick" if any(f.manifest.get("intrabar") == "tick" for f in self.files) else (
            "1s" if self.files else None)
        self._cache: OrderedDict = OrderedDict()
        self.max_days = max_days

    def day(self, trade_date: int, iid: int) -> pd.DataFrame | None:
        key = (trade_date, iid)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        frames = []
        ds = day_str(trade_date)
        for f in self.files:
            p = f.dir / "intrabar" / f"{ds}.parquet"
            if p.exists():
                t = pq.read_table(p).to_pandas()
                t = t[t["iid"] == iid]
                if len(t):
                    frames.append(t.assign(_fp=f.fp))
        if not frames:
            df = None
        else:
            df = pd.concat(frames, ignore_index=True)
            if df["_fp"].nunique() > 1:
                # Overlapping files for the same contract-day: keep the one with the most prints per UTC day.
                df["_uday"] = df["ts"] // 86_400_000_000_000
                best = df.groupby(["_uday", "_fp"]).size().reset_index(name="n").sort_values("n", ascending=False).drop_duplicates("_uday")
                keep = set(zip(best["_uday"], best["_fp"]))
                df = df[[(u, f) in keep for u, f in zip(df["_uday"], df["_fp"])]]
            df = df.sort_values("ts", kind="mergesort").reset_index(drop=True)
            if "price" in df:
                df = df.assign(open=df["price"], high=df["price"], low=df["price"], close=df["price"])
            df = df[["ts", "open", "high", "low", "close"]]
        self._cache[key] = df
        if len(self._cache) > self.max_days:
            self._cache.popitem(last=False)
        return df

    def window(self, trade_date: int, iid: int, start_ns: int, end_ns: int) -> pd.DataFrame | None:
        d = self.day(trade_date, iid)
        if d is None:
            return None
        ts = d["ts"].to_numpy()
        a, b = np.searchsorted(ts, start_ns, "left"), np.searchsorted(ts, end_ns, "left")
        return d.iloc[a:b]


def utc_date(ns: int) -> dt.date:
    return dt.datetime.fromtimestamp(ns / 1e9, dt.timezone.utc).date()
