"""
Strategy interpretation: the AI proposes, deterministic code verifies.

Each proposal must carry a verbatim quote from the user's own messages.
Proposals whose quote can't be found, or whose value doesn't validate,
are not applied; they are returned as items for the user to confirm.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import get_settings
from ..spec.ops import PathError, apply_set
from ..spec.schema import SETTABLE_PATHS, StrategySpec
from .offline import offline_interpret
from .prompts import build_messages


@dataclass
class Proposal:
    path: str
    value: Any
    quote: str | None
    status: str = "pending"  # applied | needs_confirmation | invalid
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"path": self.path, "label": SETTABLE_PATHS.get(self.path, self.path), "value": self.value,
                "quote": self.quote, "status": self.status, "reason": self.reason}


@dataclass
class Interpretation:
    engine: str
    model: str | None
    proposals: list[Proposal] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)
    raw: str | None = None
    error: str | None = None


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"[^\w:/.%$'-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def quote_found(quote: str | None, user_texts: list[str]) -> bool:
    if not quote or len(quote.strip()) < 2:
        return False
    q = _norm(quote)
    return any(q in _norm(t) for t in user_texts)


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in model reply")
    return json.loads(text[start:end + 1])


def _call_openrouter(messages: list[dict]) -> str:
    s = get_settings()
    r = httpx.post(
        f"{s.openrouter_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {s.openrouter_api_key}", "HTTP-Referer": "https://github.com/Sandeep-09/AI-Backtest",
                 "X-Title": "AI-Backtest"},
        json={"model": s.llm_model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def _llm(current: StrategySpec, history: list[dict], message: str) -> tuple[dict, str]:
    msgs = build_messages(current, history, message)
    last_err = None
    for _ in range(2):
        raw = _call_openrouter(msgs)
        try:
            return _extract_json(raw), raw
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            msgs = msgs + [{"role": "assistant", "content": raw},
                           {"role": "user", "content": f"That was not valid JSON ({e}). Reply with only the JSON object."}]
    raise ValueError(f"model did not return valid JSON: {last_err}")


def interpret(current: StrategySpec, history: list[dict], message: str) -> Interpretation:
    s = get_settings()
    if s.llm_mode == "offline-parser":
        out = offline_interpret(current, message)
        res = Interpretation(engine="offline-parser", model=None, raw=json.dumps(out))
    else:
        res = Interpretation(engine="openrouter", model=s.llm_model)
        try:
            out, raw = _llm(current, history, message)
            res.raw = raw
        except Exception as e:  # noqa: BLE001
            res.error = f"AI request failed: {e}"
            return res
    for item in out.get("set", []) or []:
        if not isinstance(item, dict) or "path" not in item:
            continue
        res.proposals.append(Proposal(path=str(item["path"]), value=item.get("value"), quote=item.get("quote")))
    res.questions = [str(q) for q in (out.get("questions") or []) if q]
    res.notes = [str(n) for n in (out.get("notes") or []) if n]
    res.unsupported = [u if isinstance(u, dict) else {"text": str(u), "reason": ""} for u in (out.get("unsupported") or [])]
    return res


def apply_proposals(current: StrategySpec, res: Interpretation, user_texts: list[str]) -> StrategySpec:
    """Apply proposals that are quote-backed and valid; mark the rest. Returns the new spec."""
    spec = current
    for p in res.proposals:
        if p.path not in SETTABLE_PATHS:
            p.status, p.reason = "invalid", "not a settable field"
            continue
        if not quote_found(p.quote, user_texts):
            p.status = "needs_confirmation"
            p.reason = "The AI could not point to where you said this. Confirm it or ignore it."
            continue
        try:
            spec = apply_set(spec, p.path, p.value)
            p.status = "applied"
        except (PathError, ValueError) as e:
            p.status, p.reason = "invalid", f"value rejected by the rule schema: {str(e)[:300]}"
    return spec
