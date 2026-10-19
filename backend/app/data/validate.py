"""Store-level checks: missing sessions, intraday gaps, partial roll days, odd bars."""
from __future__ import annotations

import pandas as pd

from .. import db
from ..instruments import CONTRACTS
from .ingest import day_str
from .store import all_bars, choose_contracts

GAP_MINUTES = {"NQ": 5, "MNQ": 5, "GC": 15, "MGC": 15}


def _calendar(root: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        import pandas_market_calendars as mcal

        return mcal.get_calendar(CONTRACTS[root].calendar).schedule(start, end)
    except Exception:  # noqa: BLE001
        return None


def validate_root(root: str) -> dict:
    db.clear_issues("data", root)
    bars, overlaps = all_bars(root)
    if bars.empty:
        return {}
    summary: dict = {"root": root, "gaps": [], "missing_sessions": [], "partial_days": []}
    if bars["synthetic"].any():
        n = bars.loc[bars["synthetic"], "file"].nunique()
        db.add_issue("data", root, "warning", "synthetic", f"{root}: {n} file(s) are SYNTHETIC test data, not real market data. Results on them mean nothing.")
    for o in overlaps:
        db.add_issue("data", root, "warning", "overlap",
                     f"{root} {o['symbol']} trade date {o['trade_date']} appears in {len(o['files'])} files; using {o['kept']} (more data).", o)
    days = sorted(bars["day"].unique().tolist())
    first, last = day_str(days[0]), day_str(days[-1])
    cal = _calendar(root, first, last)
    if cal is not None:
        expected = {str(d.date()) for d in cal.index}
        have = {day_str(d) for d in days}
        missing = sorted(expected - have)
        summary["missing_sessions"] = missing
        if missing:
            db.add_issue("data", root, "warning", "missing_sessions",
                         f"{root}: {len(missing)} exchange trading day(s) between {first} and {last} have no data: {', '.join(missing[:10])}{'…' if len(missing) > 10 else ''}",
                         missing)
        extra = sorted(have - expected)
        if extra:
            db.add_issue("data", root, "info", "non_calendar_days", f"{root}: data on {len(extra)} date(s) not in the exchange calendar: {', '.join(extra[:10])}", extra)

    # Gaps are checked on the most active contract each day (what a backtest would normally trade).
    ch = choose_contracts(bars, "most_active_same_day")
    thr = GAP_MINUTES.get(root, 10)
    gaps = []
    for d, iid in ch.by_day.items():
        b = bars[(bars["day"] == d) & (bars["iid"] == iid)]
        if len(b) < 2:
            continue
        ts = b["ts"].to_numpy()
        sym = b["symbol"].iloc[0]
        open_ns = close_ns = None
        if cal is not None and day_str(d) in {str(x.date()) for x in cal.index}:
            row = cal.loc[pd.Timestamp(day_str(d))]
            open_ns, close_ns = int(row["market_open"].value), int(row["market_close"].value)
            if ts[0] - open_ns > thr * 60e9:
                gaps.append({"trade_date": day_str(d), "symbol": sym, "start": str(pd.Timestamp(open_ns, tz="UTC")),
                             "end": str(pd.Timestamp(int(ts[0]), tz="UTC")), "minutes": round((ts[0] - open_ns) / 60e9), "where": "session start"})
                summary["partial_days"].append(day_str(d))
        diffs = (ts[1:] - ts[:-1]) / 60e9
        for i in [i for i, x in enumerate(diffs) if x > thr]:
            a, b2 = int(ts[i]), int(ts[i + 1])
            # Skip the scheduled daily halt.
            if close_ns is not None and a >= close_ns:
                continue
            gaps.append({"trade_date": day_str(d), "symbol": sym, "start": str(pd.Timestamp(a, tz="UTC")),
                         "end": str(pd.Timestamp(b2, tz="UTC")), "minutes": round(diffs[i]), "where": "intraday"})
    summary["gaps"] = gaps
    if gaps:
        worst = sorted(gaps, key=lambda g: -g["minutes"])[:5]
        db.add_issue("data", root, "warning", "intraday_gaps",
                     f"{root}: {len(gaps)} gap(s) longer than {thr} min with no trades in the most active contract. Largest: " +
                     "; ".join(f"{g['trade_date']} {g['minutes']} min ({g['where']}, {g['symbol']})" for g in worst), gaps)
    # Roll days with more than one contract (e.g. gap-fill downloads).
    dv = bars.groupby(["day", "symbol"])["volume"].sum().reset_index()
    multi = dv.groupby("day").filter(lambda g: len(g) > 1)
    if not multi.empty:
        detail = [{"trade_date": day_str(d), "contracts": {r.symbol: int(r.volume) for r in g.itertuples()}} for d, g in multi.groupby("day")]
        db.add_issue("data", root, "info", "multiple_contracts",
                     f"{root}: {len(detail)} trade date(s) have more than one contract (roll days). The strategy's contract rule decides which one is traded: " +
                     "; ".join(f"{x['trade_date']}: " + ", ".join(f"{k} vol {v:,}" for k, v in x["contracts"].items()) for x in detail[:4]), detail)
    # Bars with impossible values.
    bad = bars[(bars["low"] > bars["high"]) | (bars["low"] <= 0)]
    if len(bad):
        db.add_issue("data", root, "blocking", "bad_bars", f"{root}: {len(bad)} bar(s) have low > high or non-positive prices.", bad.head(10).to_dict(orient="records"))
    return summary
