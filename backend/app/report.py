"""
Print a paste-ready summary of a finished backtest run, and export its trades to CSV.

    docker compose exec backtest python -m app.report            # latest finished run
    docker compose exec backtest python -m app.report --run 3    # a specific run
    docker compose exec backtest python -m app.report --list     # all runs

Read-only: it reads stored runs. It never runs or approves anything.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter

from . import db
from .config import get_settings
from .spec.schema import StrategySpec


def _money(x) -> str:
    return "—" if x is None else f"{'-' if x < 0 else ''}${abs(x):,.2f}"


def _pct(x) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def list_runs() -> str:
    rows = db.q("SELECT r.id, r.strategy_id, r.version, r.status, r.created_at, s.name FROM runs r JOIN strategies s ON s.id=r.strategy_id ORDER BY r.id")
    if not rows:
        return "No runs yet."
    return "\n".join(f"#{r['id']:<4} {r['status']:<8} {r['created_at'][:16]}  strategy {r['strategy_id']} v{r['version']}  {r['name']}" for r in rows)


def build(run_id: int | None = None, n_trades: int = 10) -> tuple[str, int]:
    if run_id is None:
        r = db.q1("SELECT id FROM runs WHERE status='done' ORDER BY id DESC LIMIT 1")
        if r is None:
            raise SystemExit("No finished runs yet. Approve a strategy and run a backtest in the app first.")
        run_id = r["id"]
    run = db.q1("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None:
        raise SystemExit(f"Run {run_id} not found.\n{list_runs()}")
    if run["status"] != "done":
        raise SystemExit(f"Run {run_id} is '{run['status']}', not finished.")
    res = db.lj(run["result_json"], {})
    strat = db.q1("SELECT name FROM strategies WHERE id=?", (run["strategy_id"],))
    ver = db.q1("SELECT spec_json FROM versions WHERE strategy_id=? AND version=?", (run["strategy_id"], run["version"]))
    spec = StrategySpec.model_validate(db.lj(ver["spec_json"]))
    m, dat = res.get("metrics", {}), res.get("data", {})
    trades = [db.lj(t["detail_json"]) for t in db.q("SELECT detail_json FROM trades WHERE run_id=? ORDER BY n", (run_id,))]

    L: list[str] = []
    L.append(f"=== Backtest run #{run_id} — {strat['name']} ===")
    L.append(f"Spec v{run['version']} hash {run['spec_hash'][:16]} | approved and executed {run['finished_at'] or ''}")
    if dat.get("synthetic"):
        L.append("!!! SYNTHETIC DATA — not real market data !!!")
    L.append(f"{spec.instrument} {spec.timeframe} {spec.session.type.upper() if spec.session else ''} | contract rule {spec.data.contract} | "
             f"trading {dat.get('trade_from')} -> {dat.get('trade_to')} (warm-up from {dat.get('warmup_from')})")
    L.append(f"Bars {dat.get('bars')} | intrabar replay: {dat.get('intrabar')} | rolls: " +
             (", ".join(f"{x['trade_date']} {x['from']}->{x['to']}" for x in dat.get("rolls", [])) or "none"))
    L.append(f"Costs: ${spec.costs.commission_per_side:.2f}/side, {spec.costs.slippage_ticks:g} tick slippage | same-bar: {spec.fills.same_bar_policy} | target fill: {spec.fills.limit_fill}")
    L.append("")
    if not m.get("trade_count"):
        L.append("No trades were taken.")
    else:
        L.append(f"Net profit (after costs): {_money(m['net_profit'])}   Trades: {m['trade_count']}   Win rate: {_pct(m['win_rate'])}")
        pf = m.get("profit_factor")
        L.append(f"Profit factor: {pf if pf is not None else 'n/a (' + str(m.get('profit_factor_note')) + ')'}   "
                 f"Expectancy: {_money(m['expectancy'])} / {m.get('expectancy_r')}R per trade   Max drawdown: {_money(m['max_drawdown'])}")
        L.append(f"Avg win {_money(m.get('avg_win'))}  Avg loss {_money(m.get('avg_loss'))}  Largest win {_money(m.get('largest_win'))}  Largest loss {_money(m.get('largest_loss'))}")
        L.append(f"Commission {_money(m.get('total_commission'))}  Slippage {_money(m.get('total_slippage'))}")
        for side in ("long", "short"):
            s = m.get(side) or {}
            if s.get("trade_count"):
                L.append(f"{side.capitalize()}: {s['trade_count']} trades, net {_money(s['net_profit'])}, win rate {_pct(s['win_rate'])}")
        L.append("Exit reasons: " + ", ".join(f"{k} {v}" for k, v in (m.get("exit_reasons") or {}).items()))
        L.append("Fills decided by: " + "; ".join(f"{k} ({v})" for k, v in (m.get("exit_resolutions") or {}).items()))
    sk = Counter(s["reason"] for s in res.get("skipped", []))
    if sk:
        L.append(f"Signals not taken ({res.get('skipped_total')}): " + "; ".join(f"{k} ({v})" for k, v in sk.most_common(6)))
    if trades:
        L.append("")
        L.append(" #   dir    signal (bar close)   fill      stop      target    exit      reason   net        R")
        pick = trades if len(trades) <= 2 * n_trades else trades[:n_trades] + [None] + trades[-n_trades:]
        for t in pick:
            if t is None:
                L.append(" ...")
                continue
            L.append(f"{t['n']:>3}  {t['direction']:<5}  {t['signal_time'][:16].replace('T', ' ')}  {t['entry_price']:<9} {t['initial_stop']:<9} "
                     f"{str(t['target']):<9} {t['exit_price']:<9} {t['exit_reason'][:8]:<8} {_money(t['net_pnl']):>10} {t.get('net_r')}")

    out_dir = get_settings().var_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"run-{run_id}-trades.csv"
    cols = ["n", "direction", "symbol", "trade_date", "signal_time", "entry_time", "planned_entry", "entry_price", "initial_stop",
            "stop_calc", "target", "target_calc", "exit_time", "exit_price", "exit_reason", "exit_resolution", "qty",
            "gross_pnl", "commission", "slippage_usd", "net_pnl", "r_multiple", "net_r", "mfe_r", "mae_r", "bars_held", "spec_hash"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            w.writerow(t)
    L.append("")
    L.append(f"All {len(trades)} trades exported to {csv_path}")
    return "\n".join(L), run_id


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=int, help="Run id (default: latest finished run)")
    ap.add_argument("--list", action="store_true", help="List runs and exit")
    ap.add_argument("--trades", type=int, default=10, help="Trades to show at the start and end (default 10)")
    a = ap.parse_args(argv)
    if a.list:
        print(list_runs())
        return 0
    text, _ = build(a.run, a.trades)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
