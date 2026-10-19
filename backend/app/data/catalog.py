"""Discover data files and read their headers (cheap, no full read)."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from .. import db
from ..config import get_settings

DBN_SUFFIXES = (".dbn", ".dbn.zst")
TABLE_SUFFIXES = (".csv", ".csv.gz", ".csv.zst", ".parquet")

TICK_SCHEMAS = {"mbo", "trades", "tbbo", "mbp-1", "mbp-10"}
BAR_SCHEMAS = {"ohlcv-1s": "1s", "ohlcv-1m": "1m", "ohlcv-1h": "1h", "ohlcv-1d": "1d"}
DEFINITION_SCHEMAS = {"definition"}


def fingerprint(p: Path) -> str:
    st = p.stat()
    return hashlib.sha1(f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]


def file_cache_dir(fp: str) -> Path:
    return get_settings().cache_dir / "files" / fp


def _read_dbn_header(p: Path) -> dict:
    import databento as dbt

    store = dbt.DBNStore.from_file(p)
    md = store.metadata
    schema = str(store.schema) if store.schema is not None else None
    raw_ids: dict[str, list[int]] = {}
    if store.stype_in is not None and str(store.stype_in) == "raw_symbol":
        for raw, ivs in (md.mappings or {}).items():
            for iv in ivs:
                if str(iv.get("symbol", "")).isdigit():
                    raw_ids.setdefault(raw, []).append(int(iv["symbol"]))
    return {
        "raw_symbol_ids": raw_ids,
        "dataset": store.dataset,
        "schema": schema,
        "stype_in": str(store.stype_in) if store.stype_in is not None else None,
        "stype_out": str(store.stype_out),
        "symbols": list(store.symbols or []),
        "start_ns": int(md.start),
        "end_ns": int(md.end) if md.end is not None else None,
        "compression": str(store.compression),
        "mappings_count": sum(len(v) for v in (md.mappings or {}).values()),
        "synthetic": store.dataset == "SYNTHETIC.TEST",
        "kind": ("ticks" if schema in TICK_SCHEMAS else "bars" if schema in BAR_SCHEMAS else
                 "definitions" if schema in DEFINITION_SCHEMAS else "unsupported"),
    }


def _read_table_header(p: Path) -> dict:
    import pandas as pd

    if p.suffix == ".parquet":
        import pyarrow.parquet as pq

        cols = [f.name for f in pq.ParquetFile(p).schema_arrow]
    else:
        cols = list(pd.read_csv(p, nrows=5, compression="infer").columns)
    low = [c.lower() for c in cols]
    has_ohlc = all(any(x == k for x in low) for k in ("open", "high", "low", "close"))
    has_tick = "price" in low
    return {"columns": cols, "kind": "bars" if has_ohlc else "ticks" if has_tick else "unsupported",
            "schema": "table-ohlc" if has_ohlc else "table-ticks" if has_tick else None, "synthetic": "synthetic" in p.name.lower()}


def symbol_registry() -> dict[int, str]:
    """instrument_id -> raw symbol, learned from every raw-symbol file header and definition file."""
    reg: dict[int, str] = {}
    for r in db.q("SELECT meta_json FROM files"):
        meta = db.lj(r["meta_json"], {})
        for raw, ids in (meta.get("raw_symbol_ids") or {}).items():
            for i in ids:
                reg[int(i)] = raw
    p = get_settings().cache_dir / "definitions.json"
    if p.exists():
        import json

        for k, v in json.loads(p.read_text()).items():
            if v.get("raw_symbol"):
                reg.setdefault(int(k), v["raw_symbol"])
    return reg


def scan() -> list[dict]:
    s = get_settings()
    root = s.data_dir
    found: dict[str, Path] = {}
    if root.exists():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                low = name.lower()
                if low.endswith(DBN_SUFFIXES) or low.endswith(TABLE_SUFFIXES):
                    p = Path(dirpath) / name
                    found[fingerprint(p)] = p

    known = {r["fingerprint"]: r for r in db.q("SELECT * FROM files")}
    # Files that vanished or changed on disk: drop their cache.
    for fp, r in known.items():
        if fp not in found:
            shutil.rmtree(file_cache_dir(fp), ignore_errors=True)
            db.ex("DELETE FROM files WHERE fingerprint=?", (fp,))
            db.clear_issues("file", fp)
    for fp, p in found.items():
        if fp in known:
            continue
        low = p.name.lower()
        st = p.stat()
        try:
            meta = _read_dbn_header(p) if low.endswith(DBN_SUFFIXES) else _read_table_header(p)
            fmt = "dbn" if low.endswith(DBN_SUFFIXES) else "table"
            status = "new" if meta["kind"] != "unsupported" else "unsupported"
        except Exception as e:  # noqa: BLE001
            meta, fmt, status = {"error": repr(e)}, "unknown", "error"
            db.add_issue("file", fp, "blocking", "unreadable", f"Cannot read {p.name}: {e}")
        db.ex("INSERT OR REPLACE INTO files(fingerprint, path, size, mtime, format, schema, dataset, meta_json, status, ingest_json, updated_at)"
              " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (fp, str(p), st.st_size, st.st_mtime, fmt, meta.get("schema"), meta.get("dataset"), db.j(meta), status, None, db.now()))
        if status == "unsupported":
            db.add_issue("file", fp, "info", "unsupported_schema",
                         f"{p.name}: schema '{meta.get('schema')}' isn't used by the backtester (needs trades/MBO/MBP/TBBO, OHLCV bars, or definitions).")
    return list_files()


def list_files() -> list[dict]:
    rows = db.q("SELECT * FROM files ORDER BY path")
    out = []
    for r in rows:
        out.append({"fingerprint": r["fingerprint"], "path": r["path"], "name": Path(r["path"]).name, "size": r["size"],
                    "format": r["format"], "schema": r["schema"], "dataset": r["dataset"], "status": r["status"],
                    "meta": db.lj(r["meta_json"], {}), "ingest": db.lj(r["ingest_json"])})
    return out
