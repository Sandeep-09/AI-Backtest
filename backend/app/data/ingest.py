"""
Ingest one data file into the cache, streaming in chunks.

Output per file (cache/files/<fingerprint>/):
  intrabar/<YYYY-MM-DD>.parquet  trade prints (or 1s bars) per Globex trade date
  bars.parquet                   base bars (1m when built from trades)
  manifest.json                  what was found: instruments, days, ordering stats

Trade date = the CME Globex session date: 18:00 ET belongs to the next day.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import db
from ..config import get_settings
from ..instruments import CONTRACTS, parse_symbol
from ..jobs import Job
from .catalog import BAR_SCHEMAS, TICK_SCHEMAS, file_cache_dir

ET = "America/New_York"
UNDEF_PRICE = np.iinfo(np.int64).max
NS_MIN = 60_000_000_000
NS_DAY = 86_400_000_000_000
CHUNK = 1_000_000


def trade_date_days(ts_ns: np.ndarray) -> np.ndarray:
    """Days since epoch of the Globex trade date for each UTC nanosecond timestamp."""
    if len(ts_ns) == 0:
        return np.zeros(0, dtype=np.int64)
    idx = pd.DatetimeIndex(ts_ns.astype("datetime64[ns]"), tz="UTC").tz_convert(ET).tz_localize(None)
    wall = idx.asi8 + 6 * 3_600_000_000_000
    return wall // NS_DAY


def day_str(d: int) -> str:
    return str(np.datetime64(int(d), "D"))


def definitions_registry() -> dict[int, dict]:
    p = get_settings().cache_dir / "definitions.json"
    if p.exists():
        return {int(k): v for k, v in json.loads(p.read_text()).items()}
    return {}


def _save_definitions(reg: dict[int, dict]) -> None:
    p = get_settings().cache_dir / "definitions.json"
    p.write_text(json.dumps({str(k): v for k, v in reg.items()}))


CONTINUOUS = re.compile(r"^([A-Z0-9]+)\.(c|n|v)\.(\d+)$")


class SymbolResolver:
    """
    Name each instrument_id. Order of preference:
      header  - raw symbol from this file's own mappings
      registry - raw symbol learned from another file (e.g. a raw-symbol gap fill) or definitions
      continuous - only the root is known (file requested e.g. NQ.v.0); contract month unknown
      inferred - parent request (NQ.FUT) without mappings; classified by price level
    """

    def __init__(self, metadata, registry: dict[int, str]) -> None:
        from databento.common.symbology import InstrumentMap

        self.imap = InstrumentMap()
        try:
            self.imap.insert_metadata(metadata)
        except Exception:  # noqa: BLE001
            pass
        self.registry = registry
        self.parent_roots = [s.split(".")[0] for s in (metadata.symbols or []) if s.endswith(".FUT")]
        self._cache: dict[tuple[int, int], tuple[str | None, str]] = {}

    def resolve(self, iid: int, day: int) -> tuple[str | None, str]:
        k = (iid, day)
        if k in self._cache:
            return self._cache[k]
        try:
            sym = self.imap.resolve(int(iid), pd.Timestamp(day_str(day)).date())
        except Exception:  # noqa: BLE001
            sym = None
        res: tuple[str | None, str]
        if sym and "." not in sym:
            res = (sym, "header")
        elif int(iid) in self.registry:
            res = (self.registry[int(iid)], "registry")
        elif sym and CONTINUOUS.match(sym):
            res = (f"{CONTINUOUS.match(sym).group(1)} id{iid}", "continuous")
        elif len(self.parent_roots) == 1:
            res = (f"{self.parent_roots[0]}#{iid}", "inferred")
        else:
            res = (None, "unknown")
        self._cache[k] = res
        return res


def _bars_from_ticks(df: pd.DataFrame) -> pd.DataFrame:
    df = df.assign(minute=(df["ts"] // NS_MIN) * NS_MIN)
    g = df.groupby(["iid", "minute"], sort=True)
    b = g.agg(open=("price", "first"), high=("price", "max"), low=("price", "min"), close=("price", "last"),
              volume=("size", "sum"), trades=("price", "size")).reset_index().rename(columns={"minute": "ts"})
    return b


class _Stats:
    def __init__(self) -> None:
        self.records = 0
        self.ts_event_decreases = 0
        self.ts_recv_decreases = 0
        self.duplicates = 0
        self.undefined_prices = 0
        self.examples: list[dict] = []
        self.prev_event: int | None = None
        self.prev_recv: int | None = None

    def update(self, arr: np.ndarray) -> None:
        names = arr.dtype.names
        self.records += len(arr)
        for field, attr, prev_attr in (("ts_event", "ts_event_decreases", "prev_event"), ("ts_recv", "ts_recv_decreases", "prev_recv")):
            if field not in names or len(arr) == 0:
                continue
            t = arr[field].astype(np.int64)
            d = np.diff(t)
            bad = np.flatnonzero(d < 0)
            n = len(bad)
            prev = getattr(self, prev_attr)
            if prev is not None and t[0] < prev:
                n += 1
            setattr(self, attr, getattr(self, attr) + n)
            for i in bad[:3]:
                if len(self.examples) < 10:
                    self.examples.append({"field": field, "record": int(self.records - len(arr) + i + 1),
                                          "prev": str(pd.Timestamp(int(t[i]), tz="UTC")),
                                          "this": str(pd.Timestamp(int(t[i + 1]), tz="UTC")),
                                          "backstep_ms": round(float(t[i] - t[i + 1]) / 1e6, 3)})
            setattr(self, prev_attr, int(t[-1]))
        if len(arr) > 1:
            self.duplicates += int((arr[1:] == arr[:-1]).sum())

    def to_dict(self) -> dict:
        return {"records": self.records, "ts_event_decreases": self.ts_event_decreases, "ts_recv_decreases": self.ts_recv_decreases,
                "consecutive_duplicates": self.duplicates, "undefined_prices": self.undefined_prices, "examples": self.examples}


def _instrument_summary(df: pd.DataFrame, resolver_how: dict[int, str]) -> dict:
    out = {}
    for iid, g in df.groupby("iid"):
        out[int(iid)] = {"symbol": g["symbol"].iloc[0], "how": resolver_how.get(int(iid), "unknown"),
                         "rows": int(len(g)), "median_price": float(g["close"].median()),
                         "min_price": float(g["low"].min()), "max_price": float(g["high"].max())}
    return out


def _classify(instruments: dict, symbols_by_iid: dict[int, str]) -> dict[int, dict]:
    """Root and kind (outright/spread/unknown) for each instrument; flags inferred cases."""
    # Price-level heuristic only for instruments whose symbol was inferred from a parent request.
    med = {i: v["median_price"] for i, v in instruments.items()}
    top = max(med.values()) if med else 0
    res = {}
    for iid, v in instruments.items():
        sym = symbols_by_iid.get(iid)
        if v.get("how") == "continuous" and sym:
            root = sym.split(" ")[0]
            # A continuous request (e.g. NQ.v.0) always resolves to an outright; only the month is unknown.
            res[iid] = {"root": root if root in CONTRACTS else None, "kind": "outright", "inferred": False, "month_unknown": True}
        elif sym and "#" in sym:
            root = sym.split("#")[0]
            kind = "spread" if (top and abs(med[iid]) < 0.5 * top) else "outright"
            res[iid] = {"root": root if root in CONTRACTS else None, "kind": kind, "inferred": True}
        else:
            p = parse_symbol(sym)
            res[iid] = {"root": p.root, "kind": p.kind, "inferred": False}
    return res


def ingest_dbn(fp: str, path: Path, job: Job | None = None) -> dict:
    import databento as dbt

    out = file_cache_dir(fp)
    shutil.rmtree(out, ignore_errors=True)
    (out / "parts").mkdir(parents=True, exist_ok=True)
    (out / "intrabar").mkdir(parents=True, exist_ok=True)
    store = dbt.DBNStore.from_file(path)
    schema = str(store.schema)
    md = store.metadata
    stats = _Stats()

    if schema == "definition":
        reg = definitions_registry()
        n = 0
        for arr in store.to_ndarray(count=CHUNK):
            stats.update(arr)
            for row in arr:
                raw = row["raw_symbol"].decode(errors="replace") if isinstance(row["raw_symbol"], bytes) else str(row["raw_symbol"])
                cls = row["instrument_class"].decode(errors="replace") if "instrument_class" in arr.dtype.names else None
                reg[int(row["instrument_id"])] = {"raw_symbol": raw, "instrument_class": cls,
                                                  "min_price_increment": float(row["min_price_increment"]) / 1e9 if "min_price_increment" in arr.dtype.names else None}
                n += 1
        _save_definitions(reg)
        manifest = {"kind": "definitions", "schema": schema, "definitions": n, "stats": stats.to_dict()}
        (out / "manifest.json").write_text(json.dumps(manifest, default=str))
        shutil.rmtree(out / "parts", ignore_errors=True)
        return manifest

    from .catalog import symbol_registry

    resolver = SymbolResolver(md, symbol_registry())
    start_ns, end_ns = int(md.start), int(md.end) if md.end else None
    is_ticks = schema in TICK_SCHEMAS
    bar_res = BAR_SCHEMAS.get(schema)
    part_no = 0
    bars_frames: list[pd.DataFrame] = []

    for arr in store.to_ndarray(count=CHUNK):
        if len(arr) == 0:
            continue
        stats.update(arr)
        names = arr.dtype.names
        if is_ticks:
            m = arr["action"] == b"T"
            # Trades schema has action 'T' on every row; MBO/MBP carry trades as 'T' among book events.
            sub = arr[m]
            if len(sub) == 0:
                continue
            px = sub["price"].astype(np.int64)
            ok = (px != UNDEF_PRICE)
            stats.undefined_prices += int((~ok).sum())
            sub, px = sub[ok], px[ok]
            ts = sub["ts_event"].astype(np.int64)
            days = trade_date_days(ts)
            frame = pd.DataFrame({"ts": ts, "price": px / 1e9, "size": sub["size"].astype(np.int64),
                                  "side": sub["side"].astype(str), "iid": sub["instrument_id"].astype(np.int64), "day": days})
            for d, g in frame.groupby("day"):
                pq.write_table(pa.Table.from_pandas(g.drop(columns=["day"]), preserve_index=False),
                               out / "parts" / f"{day_str(d)}__{part_no:06d}.parquet")
            part_no += 1
        else:
            ts = arr["ts_event"].astype(np.int64)
            frame = pd.DataFrame({"ts": ts, "iid": arr["instrument_id"].astype(np.int64),
                                  "open": arr["open"].astype(np.int64) / 1e9, "high": arr["high"].astype(np.int64) / 1e9,
                                  "low": arr["low"].astype(np.int64) / 1e9, "close": arr["close"].astype(np.int64) / 1e9,
                                  "volume": arr["volume"].astype(np.int64)})
            frame["trades"] = np.nan
            bars_frames.append(frame)
        if job is not None:
            frac = None
            if end_ns and end_ns > start_ns and "ts_event" in names:
                frac = min(0.95, max(0.0, (int(arr["ts_event"][-1]) - start_ns) / (end_ns - start_ns)) * 0.85)
            job.progress(frac, f"{path.name}: {stats.records:,} records read")

    resolver_how: dict[int, str] = {}

    def attach_symbols(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            df["symbol"] = []
            return df
        days = trade_date_days(df["ts"].to_numpy())
        # Databento symbology intervals are UTC dates, so resolve by the UTC date of each row.
        utc_days = df["ts"].to_numpy() // NS_DAY
        keys = pd.DataFrame({"iid": df["iid"].to_numpy(), "uday": utc_days}).drop_duplicates()
        rank = {"header": 0, "registry": 1, "continuous": 2, "inferred": 3, "unknown": 4}
        sym_map = {}
        for iid, d in keys.itertuples(index=False):
            sym, how = resolver.resolve(int(iid), int(d))
            sym_map[(int(iid), int(d))] = sym
            if rank[how] < rank.get(resolver_how.get(int(iid), "unknown"), 4) or int(iid) not in resolver_how:
                resolver_how[int(iid)] = how
        df = df.assign(day=days)
        df["symbol"] = [sym_map.get((int(i), int(d))) for i, d in zip(df["iid"].to_numpy(), utc_days)]
        return df

    unsorted_days: list[str] = []
    if is_ticks:
        parts = sorted((out / "parts").glob("*.parquet"))
        by_day: dict[str, list[Path]] = {}
        for p in parts:
            by_day.setdefault(p.name.split("__")[0], []).append(p)
        for k, (d, plist) in enumerate(sorted(by_day.items())):
            df = pd.concat([pq.read_table(p).to_pandas() for p in plist], ignore_index=True)
            if not df["ts"].is_monotonic_increasing:
                unsorted_days.append(d)
                df = df.sort_values("ts", kind="mergesort").reset_index(drop=True)
            pq.write_table(pa.Table.from_pandas(df, preserve_index=False), out / "intrabar" / f"{d}.parquet")
            bars_frames.append(_bars_from_ticks(df))
            for p in plist:
                p.unlink()
            if job is not None:
                job.progress(0.85 + 0.1 * (k + 1) / max(1, len(by_day)), f"{path.name}: built bars for {d}")
        base_res = "1m"
        intrabar = "tick"
    else:
        base_res = bar_res
        intrabar = None
    shutil.rmtree(out / "parts", ignore_errors=True)

    bars = pd.concat(bars_frames, ignore_index=True) if bars_frames else pd.DataFrame(
        columns=["ts", "iid", "open", "high", "low", "close", "volume", "trades"])
    if not bars.empty:
        if not bars["ts"].is_monotonic_increasing:
            bars = bars.sort_values(["ts", "iid"], kind="mergesort").reset_index(drop=True)
        bars = attach_symbols(bars)

    if schema == "ohlcv-1s" and not bars.empty:
        # Keep 1s bars as intrabar data, and build 1m base bars from them.
        for d, g in bars.groupby("day"):
            pq.write_table(pa.Table.from_pandas(g[["ts", "iid", "open", "high", "low", "close"]], preserve_index=False),
                           out / "intrabar" / f"{day_str(d)}.parquet")
        g = bars.assign(minute=(bars["ts"] // NS_MIN) * NS_MIN).groupby(["iid", "minute"], sort=True)
        bars = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                     volume=("volume", "sum"), symbol=("symbol", "first"), day=("day", "first")).reset_index().rename(columns={"minute": "ts"})
        bars["trades"] = np.nan
        base_res, intrabar = "1m", "1s"

    instruments = _instrument_summary(bars, resolver_how) if not bars.empty else {}
    classes = _classify(instruments, {i: v["symbol"] for i, v in instruments.items()})
    for iid, c in classes.items():
        instruments[iid].update(c)
    if not bars.empty:
        bars["root"] = bars["iid"].map(lambda i: classes.get(int(i), {}).get("root"))
        bars["kind"] = bars["iid"].map(lambda i: classes.get(int(i), {}).get("kind"))
        pq.write_table(pa.Table.from_pandas(bars, preserve_index=False), out / "bars.parquet")

    days = sorted({day_str(d) for d in bars["day"].unique()}) if not bars.empty else []
    manifest = {
        "kind": "ticks" if is_ticks else "bars", "schema": schema, "dataset": store.dataset, "base_res": base_res,
        "intrabar": intrabar, "days": days, "instruments": {str(k): v for k, v in instruments.items()},
        "stats": stats.to_dict(), "unsorted_days_resorted": unsorted_days,
        "synthetic": store.dataset == "SYNTHETIC.TEST", "timestamp_field": "ts_event",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, default=str))
    return manifest


_TS_COLS = ("ts_event", "timestamp", "ts", "time", "datetime", "date")


def ingest_table(fp: str, path: Path, job: Job | None = None) -> dict:
    out = file_cache_dir(fp)
    shutil.rmtree(out, ignore_errors=True)
    (out / "intrabar").mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path, compression="infer")
    cols = {c.lower(): c for c in df.columns}
    tcol = next((cols[c] for c in _TS_COLS if c in cols), None)
    if tcol is None:
        raise ValueError(f"no timestamp column (looked for {', '.join(_TS_COLS)})")
    raw_ts = df[tcol]
    if raw_ts.dtype.kind in "iu":
        ts = pd.to_datetime(raw_ts, unit="ns" if raw_ts.abs().max() > 10**15 else "s", utc=True)
        tz_note = "integer epoch timestamps, read as UTC"
    else:
        parsed = pd.to_datetime(raw_ts, errors="coerce")
        if parsed.dt.tz is None:
            ts = parsed.dt.tz_localize("UTC")
            tz_note = "timestamps had no timezone; read as UTC"
            db.add_issue("file", fp, "warning", "naive_timestamps", f"{path.name}: timestamps have no timezone. They were read as UTC; if they are exchange or local time, every signal will be shifted.")
        else:
            ts = parsed.dt.tz_convert("UTC")
            tz_note = "timezone-aware timestamps"
        bad = int(ts.isna().sum())
        if bad:
            db.add_issue("file", fp, "warning", "unparsed_timestamps", f"{path.name}: {bad} rows have unreadable timestamps and were dropped.")
    df = df.assign(ts=ts.astype("int64")).dropna(subset=["ts"])
    scol = next((cols[c] for c in ("symbol", "raw_symbol", "ticker", "instrument") if c in cols), None)
    if scol:
        df["symbol"] = df[scol].astype(str)
    else:
        m = re.search(r"(MNQ|NQ|MGC|GC)", path.name.upper())
        if not m:
            raise ValueError("no symbol column and no NQ/GC root in the file name")
        df["symbol"] = f"{m.group(1)}#table"
        db.add_issue("file", fp, "warning", "symbol_from_filename", f"{path.name}: no symbol column; treated as one continuous {m.group(1)} series (contract months unknown).")
    sym_ids = {s: i + 1 for i, s in enumerate(sorted(df["symbol"].unique()))}
    df["iid"] = df["symbol"].map(sym_ids)
    stats = {"records": int(len(df)), "ts_decreases": int((np.diff(df["ts"].to_numpy()) < 0).sum()),
             "duplicate_timestamps": int(df.duplicated(subset=["ts", "symbol"]).sum()), "timestamp_note": tz_note}
    df = df.sort_values(["ts", "iid"], kind="mergesort")
    df["day"] = trade_date_days(df["ts"].to_numpy())
    if "price" in cols and "open" not in cols:
        df = df.rename(columns={cols["price"]: "price"})
        df["size"] = df[cols["size"]] if "size" in cols else 1
        for d, g in df.groupby("day"):
            pq.write_table(pa.Table.from_pandas(g[["ts", "price", "size", "iid"]], preserve_index=False), out / "intrabar" / f"{day_str(d)}.parquet")
        bars = _bars_from_ticks(df[["ts", "price", "size", "iid"]])
        bars["symbol"] = bars["iid"].map({v: k for k, v in sym_ids.items()})
        bars["day"] = trade_date_days(bars["ts"].to_numpy())
        base_res, intrabar = "1m", "tick"
    else:
        ren = {cols[k]: k for k in ("open", "high", "low", "close", "volume") if k in cols}
        bars = df.rename(columns=ren)
        if "volume" not in bars:
            bars["volume"] = 0
        bars["trades"] = np.nan
        step = int(np.median(np.diff(np.unique(bars["ts"].to_numpy())))) if len(bars) > 2 else NS_MIN
        base_res = {60 * 10**9: "1m", 10**9: "1s", 3600 * 10**9: "1h"}.get(step, f"{step // 10**9}s")
        intrabar = None
        bars = bars[["ts", "iid", "open", "high", "low", "close", "volume", "trades", "symbol", "day"]]
    classes = {}
    for s, i in sym_ids.items():
        if "#" in s:
            classes[i] = {"root": s.split("#")[0], "kind": "outright", "inferred": True}
        else:
            p = parse_symbol(s)
            classes[i] = {"root": p.root, "kind": p.kind, "inferred": False}
    bars["root"] = bars["iid"].map(lambda i: classes[int(i)]["root"])
    bars["kind"] = bars["iid"].map(lambda i: classes[int(i)]["kind"])
    pq.write_table(pa.Table.from_pandas(bars.reset_index(drop=True), preserve_index=False), out / "bars.parquet")
    instruments = _instrument_summary(bars, {})
    for i, c in classes.items():
        if i in instruments:
            instruments[i].update(c)
    manifest = {"kind": "bars" if intrabar is None else "ticks", "schema": "table", "dataset": None, "base_res": base_res,
                "intrabar": intrabar, "days": sorted({day_str(d) for d in bars["day"].unique()}),
                "instruments": {str(k): v for k, v in instruments.items()}, "stats": stats,
                "synthetic": "synthetic" in path.name.lower(), "timestamp_field": tcol}
    (out / "manifest.json").write_text(json.dumps(manifest, default=str))
    return manifest


def file_issues_from_manifest(fp: str, name: str, man: dict) -> None:
    st = man.get("stats", {})
    if st.get("ts_recv_decreases"):
        db.add_issue("file", fp, "warning", "ts_recv_order",
                     f"{name}: {st['ts_recv_decreases']} record(s) are out of order by receive time. Trades were re-sorted by exchange time (ts_event) before use.",
                     st.get("examples"))
    if st.get("ts_event_decreases"):
        db.add_issue("file", fp, "info", "ts_event_order",
                     f"{name}: exchange timestamps (ts_event) step backwards {st['ts_event_decreases']} time(s) in file order. This is normal for files sorted by receive time; trades are sorted by ts_event before building bars.",
                     st.get("examples"))
    if st.get("consecutive_duplicates"):
        db.add_issue("file", fp, "warning", "duplicates", f"{name}: {st['consecutive_duplicates']} records are exact duplicates of the record before them.")
    if st.get("undefined_prices"):
        db.add_issue("file", fp, "info", "undefined_prices", f"{name}: {st['undefined_prices']} trade records had no price and were ignored.")
    for iid, ins in (man.get("instruments") or {}).items():
        if ins.get("kind") == "spread":
            db.add_issue("file", fp, "info", "spread_excluded", f"{name}: instrument {ins.get('symbol')} (id {iid}) looks like a calendar spread and is excluded.", ins)
        elif ins.get("root") is None:
            db.add_issue("file", fp, "warning", "unknown_instrument", f"{name}: instrument id {iid} ({ins.get('symbol')}) isn't a recognised NQ/MNQ/GC/MGC outright and is excluded.", ins)
        if ins.get("inferred"):
            db.add_issue("file", fp, "warning", "symbol_inferred",
                         f"{name}: instrument id {iid} has no symbol mapping; it was classed as {ins.get('kind')} of {ins.get('root')} from its price level. Add Databento 'definition' files for exact symbols.", ins)
    if man.get("synthetic"):
        db.add_issue("file", fp, "warning", "synthetic", f"{name} is SYNTHETIC test data, not real market data.")


def ingest_file(fp: str, job: Job | None = None) -> dict:
    r = db.q1("SELECT * FROM files WHERE fingerprint=?", (fp,))
    if r is None:
        raise KeyError(fp)
    path = Path(r["path"])
    db.clear_issues("file", fp)
    db.ex("UPDATE files SET status='ingesting', updated_at=? WHERE fingerprint=?", (db.now(), fp))
    try:
        man = ingest_dbn(fp, path, job) if r["format"] == "dbn" else ingest_table(fp, path, job)
    except Exception as e:
        db.ex("UPDATE files SET status='error', ingest_json=?, updated_at=? WHERE fingerprint=?", (db.j({"error": repr(e)}), db.now(), fp))
        db.add_issue("file", fp, "blocking", "ingest_failed", f"{path.name}: could not be read: {e}")
        raise
    file_issues_from_manifest(fp, path.name, man)
    db.ex("UPDATE files SET status='ingested', ingest_json=?, updated_at=? WHERE fingerprint=?",
          (db.j({k: v for k, v in man.items() if k != "days"} | {"day_count": len(man.get("days", [])),
                                                                 "first_day": (man.get("days") or [None])[0],
                                                                 "last_day": (man.get("days") or [None])[-1]}), db.now(), fp))
    return man
