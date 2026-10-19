"""Hashing, path updates and diffs for StrategySpec."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .schema import SETTABLE_PATHS, StrategySpec


def canonical_json(spec: StrategySpec) -> str:
    return json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def spec_hash(spec: StrategySpec) -> str:
    return hashlib.sha256(canonical_json(spec).encode()).hexdigest()


class PathError(ValueError):
    pass


def apply_set(spec: StrategySpec, path: str, value: Any) -> StrategySpec:
    """Return a new spec with `path` set to `value`. Validates the whole result."""
    if path not in SETTABLE_PATHS:
        raise PathError(f"'{path}' is not a settable field")
    data = copy.deepcopy(spec.model_dump(mode="json"))
    parts = path.split(".")
    node = data
    for p in parts[:-1]:
        if node.get(p) is None:
            node[p] = {}
        node = node[p]
    node[parts[-1]] = value
    return StrategySpec.model_validate(data)


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = {}
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        # Indicators are keyed by id so reordering is not a change.
        if obj and all(isinstance(x, dict) and "id" in x for x in obj):
            for x in obj:
                out.update(_flatten(x, f"{prefix}[{x['id']}]"))
        elif not obj:
            out[prefix] = []
        else:
            for i, x in enumerate(obj):
                out.update(_flatten(x, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def diff_specs(before: StrategySpec | None, after: StrategySpec) -> list[dict]:
    a = _flatten(before.model_dump(mode="json")) if before else {}
    b = _flatten(after.model_dump(mode="json"))
    changes = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if va != vb:
            changes.append({"path": k, "before": va, "after": vb})
    return changes


def changed_top_paths(changes: list[dict]) -> set[str]:
    """Normalise leaf paths like 'entry.long.when.conditions[0].op' to section roots."""
    roots = set()
    for c in changes:
        p = c["path"]
        head = p.split("[")[0]
        segs = head.split(".")
        roots.add(segs[0])
        if len(segs) > 1:
            roots.add(".".join(segs[:2]))
        if len(segs) > 2:
            roots.add(".".join(segs[:3]))
    return roots
