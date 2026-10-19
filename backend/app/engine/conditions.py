"""
Evaluate condition trees on closed bars. Operands at bar t use bar t (or
earlier with bars_ago); `crosses_*` also uses t-1. Shifts never cross a
contract roll: values from another segment are NaN.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..spec.schema import Compare, Group, Operand, StrategySpec


class Ctx:
    def __init__(self, bars: pd.DataFrame, ind: dict[str, np.ndarray], tick: float) -> None:
        self.bars = bars
        self.ind = ind
        self.tick = tick
        self.seg = bars["seg"].to_numpy()
        self.n = len(bars)

    def shift(self, x: np.ndarray, k: int) -> np.ndarray:
        if k == 0:
            return x
        out = np.full(self.n, np.nan)
        if k < self.n:
            out[k:] = x[:-k]
            same = np.zeros(self.n, dtype=bool)
            same[k:] = self.seg[k:] == self.seg[:-k]
            out[~same] = np.nan
        return out

    def operand(self, op: Operand, extra_shift: int = 0) -> np.ndarray:
        if op.kind == "indicator":
            base = self.ind[op.ref].astype(float)
        elif op.kind == "price":
            base = self.bars[op.field].to_numpy(float)
        else:
            base = np.full(self.n, float(op.value))
        v = self.shift(base, op.bars_ago + extra_shift)
        if op.multiplier != 1.0:
            v = v * op.multiplier
        if op.offset_ticks:
            v = v + op.offset_ticks * self.tick
        return v


def eval_compare(ctx: Ctx, c: Compare) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        if c.op == "touches":
            lvl = ctx.operand(c.right if c.left.kind == "price" else c.left)
            lo, hi = ctx.bars["low"].to_numpy(float), ctx.bars["high"].to_numpy(float)
            return (lo <= lvl) & (lvl <= hi)
        a, b = ctx.operand(c.left), ctx.operand(c.right)
        if c.op == "gt":
            return a > b
        if c.op == "lt":
            return a < b
        if c.op == "gte":
            return a >= b
        if c.op == "lte":
            return a <= b
        a1, b1 = ctx.operand(c.left, 1), ctx.operand(c.right, 1)
        if c.op == "crosses_above":
            return (a > b) & (a1 <= b1)
        if c.op == "crosses_below":
            return (a < b) & (a1 >= b1)
    raise ValueError(c.op)


def eval_group(ctx: Ctx, g: Group) -> np.ndarray:
    parts = [eval_compare(ctx, c) if isinstance(c, Compare) else eval_group(ctx, c) for c in g.conditions]
    if not parts:
        return np.zeros(ctx.n, dtype=bool)
    out = parts[0].copy()
    for p in parts[1:]:
        out = (out & p) if g.type == "all" else (out | p)
    return out


def evidence(ctx: Ctx, g: Group, i: int, spec: StrategySpec, path: str = "") -> list[dict]:
    """Operand values at bar i for each comparison: what made the rule true."""
    from ..summary.render import OP_TEXT, operand_text

    out = []
    for k, c in enumerate(g.conditions):
        if isinstance(c, Compare):
            res = bool(eval_compare(ctx, c)[i])
            row = {"rule": f"{operand_text(spec, c.left)} {OP_TEXT[c.op]} {operand_text(spec, c.right)}", "result": res,
                   "left": _f(ctx.operand(c.left)[i]), "right": _f(ctx.operand(c.right)[i]), "op": c.op}
            if c.op in ("crosses_above", "crosses_below"):
                row["left_prev"] = _f(ctx.operand(c.left, 1)[i])
                row["right_prev"] = _f(ctx.operand(c.right, 1)[i])
            out.append(row)
        else:
            out.append({"group": c.type, "result": bool(eval_group(ctx, c)[i]), "items": evidence(ctx, c, i, spec)})
    return out


def _f(x) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) or (hasattr(x, "__float__") and np.isnan(float(x))) else round(float(x), 6)
