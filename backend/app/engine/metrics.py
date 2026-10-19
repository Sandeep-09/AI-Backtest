"""Performance metrics, computed only from the trade ledger."""
from __future__ import annotations

from collections import Counter

import numpy as np


def compute(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trade_count": 0, "net_profit": 0.0, "note": "No trades were taken."}
    net = np.array([t["net_pnl"] for t in trades], float)
    wins, losses = net[net > 0], net[net < 0]
    gp, gl = float(wins.sum()), float(losses.sum())
    eq = np.cumsum(net)
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    dd = eq - peak
    i_trough = int(np.argmin(dd)) if len(dd) else 0
    rs = [t["net_r"] for t in trades if t.get("net_r") is not None]

    def side(d: str) -> dict:
        x = np.array([t["net_pnl"] for t in trades if t["direction"] == d], float)
        if not len(x):
            return {"trade_count": 0}
        return {"trade_count": int(len(x)), "net_profit": round(float(x.sum()), 2), "win_rate": round(float((x > 0).mean()), 4)}

    return {
        "trade_count": n,
        "net_profit": round(float(net.sum()), 2),
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "total_commission": round(sum(t["commission"] for t in trades), 2),
        "total_slippage": round(sum(t["slippage_usd"] for t in trades), 2),
        "win_rate": round(float((net > 0).mean()), 4),
        "wins": int((net > 0).sum()), "losses": int((net < 0).sum()), "breakeven": int((net == 0).sum()),
        "profit_factor": round(gp / -gl, 3) if gl < 0 else None,
        "profit_factor_note": None if gl < 0 else "undefined: no losing trades",
        "expectancy": round(float(net.mean()), 2),
        "expectancy_r": round(float(np.mean(rs)), 3) if rs else None,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else None,
        "largest_win": round(float(net.max()), 2), "largest_loss": round(float(net.min()), 2),
        "max_drawdown": round(float(dd.min()), 2),
        "max_drawdown_at_trade": int(trades[i_trough]["n"]) if len(dd) and dd.min() < 0 else None,
        "long": side("long"), "short": side("short"),
        "exit_reasons": dict(Counter(t["exit_reason"] for t in trades)),
        "exit_resolutions": dict(Counter(t["exit_resolution"] for t in trades)),
    }


def equity_series(trades: list[dict]) -> list[dict]:
    eq, peak, out = 0.0, 0.0, []
    for t in trades:
        eq += t["net_pnl"]
        peak = max(peak, eq)
        out.append({"n": t["n"], "ts": t["exit_ts"], "equity": round(eq, 2), "drawdown": round(eq - peak, 2)})
    return out
