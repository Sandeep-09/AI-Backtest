#!/usr/bin/env python3
"""
Inspect market-data files before the backtester uses them.

Reports what each file actually contains (format, schema, symbols, timestamps,
ordering, coverage) instead of assuming. Read-only: it never modifies files.

Usage:
    pip install databento pandas pyarrow
    python inspect_data.py /path/to/databento/folder              # quick: metadata + first 2M records per file
    python inspect_data.py /path/to/folder --max-records 0        # full scan of every record (slow on GBs)
    python inspect_data.py /path/to/folder --json report.json     # also write a machine-readable report

Paste the printed report back into the chat so the data adapter can be fitted to it.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

DBN_SUFFIXES = (".dbn", ".dbn.zst")
TABLE_SUFFIXES = (".csv", ".csv.zst", ".csv.gz", ".parquet")
SKIP_DIRS = {".git", "__pycache__", "node_modules"}


def human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{n} B"


def ns_to_iso(ns: int | None) -> str | None:
    if ns is None:
        return None
    return dt.datetime.fromtimestamp(ns / 1e9, tz=dt.timezone.utc).isoformat()


def find_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            low = name.lower()
            if low.endswith(DBN_SUFFIXES) or low.endswith(TABLE_SUFFIXES):
                out.append(Path(dirpath) / name)
            elif low in ("metadata.json", "symbology.json", "condition.json", "manifest.json"):
                out.append(Path(dirpath) / name)
    return sorted(out)


def inspect_dbn(path: Path, max_records: int) -> dict:
    import databento as db
    import numpy as np

    rep: dict = {"path": str(path), "format": "dbn", "size": path.stat().st_size}
    store = db.DBNStore.from_file(path)
    md = store.metadata
    rep["metadata"] = {
        "dataset": store.dataset,
        "schema": str(store.schema) if store.schema is not None else None,
        "stype_in": str(store.stype_in) if store.stype_in is not None else None,
        "stype_out": str(store.stype_out),
        "symbols": list(store.symbols or [])[:50],
        "start": str(store.start),
        "end": str(store.end) if store.end is not None else None,
        "limit": md.limit,
        "compression": str(store.compression),
        "dbn_version": md.version,
        "ts_out": md.ts_out,
        "partial": list(md.partial or [])[:20],
        "not_found": list(md.not_found or [])[:20],
    }
    # Symbology mappings stored in the file header (first few, raw form).
    mappings = md.mappings or {}
    rep["mappings_count"] = sum(len(v) for v in mappings.values())
    rep["mappings_sample"] = {k: v[:5] for k, v in list(mappings.items())[:15]}

    # Stream records in chunks so multi-GB files never load at once.
    stats = {
        "records_scanned": 0,
        "scan_complete": False,
        "first_ts_event": None,
        "last_ts_event": None,
        "first_ts_recv": None,
        "last_ts_recv": None,
        "ts_event_decreases": 0,
        "ts_recv_decreases": 0,
        "max_ts_event_backstep_ms": 0.0,
    }
    actions: collections.Counter = collections.Counter()
    sides: collections.Counter = collections.Counter()
    rtypes: collections.Counter = collections.Counter()
    per_inst: dict[int, dict] = {}
    days: collections.Counter = collections.Counter()
    definitions: list[dict] = []
    sample_rows: list[dict] = []

    prev_event = None
    prev_recv = None
    t0 = time.time()
    chunk = 500_000
    try:
        it = store.to_ndarray(count=chunk)
    except Exception as e:  # noqa: BLE001
        rep["error"] = f"cannot iterate records: {e!r}"
        return rep

    names: tuple = ()
    for arr in it:
        if not names:
            names = arr.dtype.names or ()
            rep["record_fields"] = list(names)
        n = len(arr)
        if n == 0:
            continue
        if max_records and stats["records_scanned"] + n > max_records:
            arr = arr[: max_records - stats["records_scanned"]]
            n = len(arr)

        if not sample_rows:
            for row in arr[:5]:
                sample_rows.append({k: (row[k].decode(errors="replace") if isinstance(row[k], bytes) else row[k].item() if hasattr(row[k], "item") else row[k]) for k in names})

        if "ts_event" in names:
            te = arr["ts_event"].astype(np.int64)
            if stats["first_ts_event"] is None:
                stats["first_ts_event"] = int(te[0])
            stats["last_ts_event"] = int(te[-1])
            d = np.diff(te)
            stats["ts_event_decreases"] += int((d < 0).sum())
            if prev_event is not None and te[0] < prev_event:
                stats["ts_event_decreases"] += 1
            if len(d) and d.min() < 0:
                stats["max_ts_event_backstep_ms"] = max(stats["max_ts_event_backstep_ms"], float(-d.min()) / 1e6)
            prev_event = int(te[-1])
            day_keys = (te // 86_400_000_000_000).astype(np.int64)
            u, c = np.unique(day_keys, return_counts=True)
            for k, v in zip(u.tolist(), c.tolist()):
                days[k] += v
        if "ts_recv" in names:
            tr = arr["ts_recv"].astype(np.int64)
            if stats["first_ts_recv"] is None:
                stats["first_ts_recv"] = int(tr[0])
            stats["last_ts_recv"] = int(tr[-1])
            stats["ts_recv_decreases"] += int((np.diff(tr) < 0).sum())
            if prev_recv is not None and tr[0] < prev_recv:
                stats["ts_recv_decreases"] += 1
            prev_recv = int(tr[-1])
        if "rtype" in names:
            u, c = np.unique(arr["rtype"], return_counts=True)
            for k, v in zip(u.tolist(), c.tolist()):
                rtypes[int(k)] += v
        if "action" in names:
            u, c = np.unique(arr["action"], return_counts=True)
            for k, v in zip(u.tolist(), c.tolist()):
                actions[k.decode(errors="replace") if isinstance(k, bytes) else str(k)] += v
        if "side" in names:
            u, c = np.unique(arr["side"], return_counts=True)
            for k, v in zip(u.tolist(), c.tolist()):
                sides[k.decode(errors="replace") if isinstance(k, bytes) else str(k)] += v
        if "instrument_id" in names:
            ids = arr["instrument_id"]
            price_field = "price" if "price" in names else ("close" if "close" in names else None)
            for iid in np.unique(ids).tolist():
                m = ids == iid
                s = per_inst.setdefault(int(iid), {"records": 0, "min_price": None, "max_price": None, "trades": 0})
                s["records"] += int(m.sum())
                if price_field:
                    p = arr[price_field][m].astype(np.int64)
                    p = p[(p != np.iinfo(np.int64).max) & (p != 0)]
                    if len(p):
                        lo, hi = float(p.min()) / 1e9, float(p.max()) / 1e9
                        s["min_price"] = lo if s["min_price"] is None else min(s["min_price"], lo)
                        s["max_price"] = hi if s["max_price"] is None else max(s["max_price"], hi)
                if "action" in names:
                    s["trades"] += int((arr["action"][m] == b"T").sum())
        if "raw_symbol" in names and len(definitions) < 400:
            for row in arr[: 400 - len(definitions)]:
                definitions.append({
                    "instrument_id": int(row["instrument_id"]),
                    "raw_symbol": row["raw_symbol"].decode(errors="replace") if isinstance(row["raw_symbol"], bytes) else str(row["raw_symbol"]),
                    "instrument_class": (row["instrument_class"].decode(errors="replace") if "instrument_class" in names and isinstance(row["instrument_class"], bytes) else None),
                    "expiration": ns_to_iso(int(row["expiration"])) if "expiration" in names else None,
                    "min_price_increment": float(row["min_price_increment"]) / 1e9 if "min_price_increment" in names else None,
                })

        stats["records_scanned"] += n
        if max_records and stats["records_scanned"] >= max_records:
            break
    else:
        stats["scan_complete"] = True

    stats["scan_seconds"] = round(time.time() - t0, 1)
    for k in ("first_ts_event", "last_ts_event", "first_ts_recv", "last_ts_recv"):
        stats[k] = ns_to_iso(stats[k])
    rep["stats"] = stats
    rep["rtype_counts"] = dict(rtypes)
    rep["action_counts"] = dict(actions)
    rep["side_counts"] = dict(sides)

    # Resolve instrument ids to symbols using the file's own mappings.
    try:
        from databento.common.symbology import InstrumentMap

        imap = InstrumentMap()
        imap.insert_metadata(md)
        any_day = None
        if days:
            any_day = dt.date(1970, 1, 1) + dt.timedelta(days=max(days, key=days.get))
        for iid, s in per_inst.items():
            s["symbol"] = imap.resolve(iid, any_day) if any_day else None
    except Exception as e:  # noqa: BLE001
        rep["symbol_resolution_error"] = repr(e)
    rep["instruments"] = dict(sorted(per_inst.items(), key=lambda kv: -kv[1]["records"])[:40])
    rep["instrument_count"] = len(per_inst)
    rep["days_with_records"] = len(days)
    if days:
        ks = sorted(days)
        rep["first_day"] = str(dt.date(1970, 1, 1) + dt.timedelta(days=ks[0]))
        rep["last_day"] = str(dt.date(1970, 1, 1) + dt.timedelta(days=ks[-1]))
        # Calendar days (UTC) inside the span that have no records at all.
        missing = [str(dt.date(1970, 1, 1) + dt.timedelta(days=k)) for k in range(ks[0], ks[-1] + 1) if k not in days]
        rep["utc_days_without_records"] = {"count": len(missing), "sample": missing[:40]}
    if definitions:
        rep["definitions_sample"] = definitions[:60]
        rep["definitions_count_sampled"] = len(definitions)
    rep["sample_rows"] = sample_rows
    return rep


def inspect_table(path: Path, max_records: int) -> dict:
    import pandas as pd

    rep: dict = {"path": str(path), "format": "parquet" if path.suffix == ".parquet" else "csv", "size": path.stat().st_size}
    try:
        if rep["format"] == "parquet":
            import pyarrow.parquet as pq

            pf = pq.ParquetFile(path)
            rep["rows"] = pf.metadata.num_rows
            rep["columns"] = [f"{f.name}: {f.type}" for f in pf.schema_arrow]
            df = pf.read_row_group(0).to_pandas().head(max_records or 200_000) if pf.num_row_groups else pd.DataFrame()
        else:
            df = pd.read_csv(path, nrows=max_records or None, compression="infer")
            rep["columns"] = [f"{c}: {t}" for c, t in df.dtypes.astype(str).items()]
            rep["rows_read"] = len(df)
        rep["head"] = df.head(5).astype(str).to_dict(orient="records")
        time_cols = [c for c in df.columns if any(k in c.lower() for k in ("time", "ts", "date"))]
        rep["time_columns_guess"] = time_cols
        for c in time_cols[:3]:
            s = df[c]
            try:
                parsed = pd.to_datetime(s, utc=True, errors="coerce") if s.dtype == object else pd.to_datetime(s, unit="ns", utc=True, errors="coerce") if s.dtype.kind in "iu" and s.astype("int64").abs().max() > 10**15 else pd.to_datetime(s, utc=True, errors="coerce")
                rep.setdefault("time_column_stats", {})[c] = {
                    "unparsed": int(parsed.isna().sum()),
                    "first": str(parsed.min()),
                    "last": str(parsed.max()),
                    "monotonic_increasing": bool(parsed.dropna().is_monotonic_increasing),
                    "duplicates": int(parsed.duplicated().sum()),
                }
            except Exception as e:  # noqa: BLE001
                rep.setdefault("time_column_stats", {})[c] = {"error": repr(e)}
        sym_cols = [c for c in df.columns if "symbol" in c.lower() or c.lower() in ("ticker", "instrument", "instrument_id")]
        for c in sym_cols[:2]:
            rep.setdefault("symbol_values", {})[c] = df[c].astype(str).value_counts().head(20).to_dict()
    except Exception as e:  # noqa: BLE001
        rep["error"] = repr(e)
    return rep


def print_report(r: dict) -> None:
    print("=" * 100)
    print(f"{r['path']}  ({human_bytes(r['size'])}, {r['format']})")
    if "error" in r:
        print(f"  ERROR: {r['error']}")
    if r["format"] == "dbn":
        m = r.get("metadata", {})
        print(f"  dataset={m.get('dataset')} schema={m.get('schema')} stype_in={m.get('stype_in')} stype_out={m.get('stype_out')} compression={m.get('compression')} dbn_v{m.get('dbn_version')}")
        print(f"  requested symbols: {m.get('symbols')}")
        print(f"  header range: {m.get('start')} -> {m.get('end')}   partial={m.get('partial')} not_found={m.get('not_found')}")
        print(f"  mappings in header: {r.get('mappings_count')}  sample: {json.dumps(r.get('mappings_sample'), default=str)[:600]}")
        s = r.get("stats", {})
        print(f"  records scanned: {s.get('records_scanned'):,}  complete={s.get('scan_complete')}  ({s.get('scan_seconds')}s)")
        print(f"  ts_event: {s.get('first_ts_event')} -> {s.get('last_ts_event')}  decreases={s.get('ts_event_decreases')} max_backstep_ms={s.get('max_ts_event_backstep_ms')}")
        print(f"  ts_recv:  {s.get('first_ts_recv')} -> {s.get('last_ts_recv')}  decreases={s.get('ts_recv_decreases')}")
        print(f"  fields: {r.get('record_fields')}")
        print(f"  rtype counts: {r.get('rtype_counts')}  action counts: {r.get('action_counts')}  side counts: {r.get('side_counts')}")
        print(f"  instruments: {r.get('instrument_count')}  days with records: {r.get('days_with_records')} ({r.get('first_day')} -> {r.get('last_day')})")
        miss = r.get("utc_days_without_records")
        if miss:
            print(f"  UTC days without records in span: {miss['count']}  e.g. {miss['sample'][:12]}")
        for iid, st in list(r.get("instruments", {}).items())[:20]:
            print(f"    id={iid:<10} symbol={st.get('symbol')!s:<14} records={st['records']:>12,} trades={st['trades']:>10,} price=[{st['min_price']}, {st['max_price']}]")
        if r.get("definitions_sample"):
            print("  definitions sample:")
            for d in r["definitions_sample"][:20]:
                print(f"    {d}")
        print("  first rows:")
        for row in r.get("sample_rows", [])[:3]:
            print(f"    {row}")
    else:
        print(f"  columns: {r.get('columns')}")
        print(f"  rows: {r.get('rows', r.get('rows_read'))}")
        print(f"  time columns: {r.get('time_column_stats')}")
        print(f"  symbols: {r.get('symbol_values')}")
        for row in r.get("head", [])[:3]:
            print(f"    {row}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="Folder (or single file) with Databento / CSV / Parquet data")
    ap.add_argument("--max-records", type=int, default=2_000_000, help="Records to scan per file (0 = all). Default 2,000,000")
    ap.add_argument("--json", type=Path, help="Write full JSON report to this path")
    args = ap.parse_args()

    root: Path = args.root
    if not root.exists():
        print(f"Not found: {root}", file=sys.stderr)
        return 2
    files = [root] if root.is_file() else find_files(root)
    print(f"Found {len(files)} candidate files under {root}")
    total = sum(f.stat().st_size for f in files)
    print(f"Total size: {human_bytes(total)}")
    reports = []
    for f in files:
        low = f.name.lower()
        try:
            if low.endswith(DBN_SUFFIXES):
                r = inspect_dbn(f, args.max_records)
            elif low.endswith(TABLE_SUFFIXES):
                r = inspect_table(f, args.max_records)
            else:
                r = {"path": str(f), "format": "json-sidecar", "size": f.stat().st_size, "content": f.read_text(errors="replace")[:3000]}
        except Exception as e:  # noqa: BLE001
            r = {"path": str(f), "format": "unknown", "size": f.stat().st_size, "error": repr(e)}
        reports.append(r)
        if r["format"] == "json-sidecar":
            print("=" * 100)
            print(f"{r['path']} (sidecar JSON)\n  {r['content'][:800]}")
        else:
            print_report(r)
    if args.json:
        args.json.write_text(json.dumps(reports, indent=2, default=str))
        print(f"\nJSON report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
