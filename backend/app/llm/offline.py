"""
Offline rule parser: a small, predictable keyword reader.

Used when no OpenRouter key is configured, and in tests. It is far less
capable than the LLM and the UI says so. It follows the same contract:
every value carries the exact words it was read from, and anything it
doesn't understand stays unset.
"""
from __future__ import annotations

import re

from ..spec.schema import StrategySpec

TF_MAP = {1: "1m", 2: "2m", 3: "3m", 5: "5m", 10: "10m", 15: "15m", 30: "30m"}
HR_MAP = {1: "1h", 2: "2h", 4: "4h"}


def _m(pattern: str, text: str):
    return re.search(pattern, text, re.I)


def offline_interpret(current: StrategySpec, message: str) -> dict:
    t = message
    sets: list[dict] = []
    notes: list[str] = []

    def put(path, value, quote):
        sets.append({"path": path, "value": value, "quote": quote})

    # Instrument
    m = _m(r"\b(MNQ|NQ|MGC|GC)\b", t) or _m(r"\b(nasdaq|gold)\b", t)
    if m:
        v = m.group(1).upper()
        put("instrument", {"NASDAQ": "NQ", "GOLD": "GC"}.get(v, v), m.group(0))

    # Timeframe
    m = _m(r"\b(\d{1,2})\s*-?\s*(?:min(?:ute)?s?|m)\b(?!\s*(?:ema|sma))", t)
    if m and int(m.group(1)) in TF_MAP:
        put("timeframe", TF_MAP[int(m.group(1))], m.group(0))
    m2 = _m(r"\b(\d)\s*-?\s*(?:hours?|hr|h)\b", t)
    if m2 and int(m2.group(1)) in HR_MAP:
        put("timeframe", HR_MAP[int(m2.group(1))], m2.group(0))

    # Indicators (each number becomes its own indicator; no relationship implied)
    inds = [i.model_dump(mode="json") for i in current.indicators]
    have = {i["id"] for i in inds}
    found: list[tuple[str, int, str]] = []
    for mm in re.finditer(r"\b(\d{1,3})\s*(?:/|and|&|,)\s*(\d{1,3})\s*-?\s*(ema|sma)s?\b", t, re.I):
        found += [(mm.group(3).lower(), int(mm.group(1)), mm.group(0)), (mm.group(3).lower(), int(mm.group(2)), mm.group(0))]
    for mm in re.finditer(r"\b(\d{1,3})\s*-?\s*(?:period\s+)?(ema|sma)\b", t, re.I):
        found.append((mm.group(2).lower(), int(mm.group(1)), mm.group(0)))
    for mm in re.finditer(r"\b(ema|sma)\s*\(?\s*(\d{1,3})\b", t, re.I):
        found.append((mm.group(1).lower(), int(mm.group(2)), mm.group(0)))
    for mm in re.finditer(r"\batr\s*\(?\s*(\d{1,3})\b", t, re.I):
        found.append(("atr", int(mm.group(1)), mm.group(0)))
    quote_for_inds = None
    for typ, n, q in found:
        iid = f"{typ}{n}"
        if iid not in have:
            ind = {"id": iid, "type": typ, "period": n}
            if typ in ("ema", "sma"):
                ind["source"] = None  # price source not stated -> ask
            inds.append(ind)
            have.add(iid)
            quote_for_inds = quote_for_inds or q
    if _m(r"\bvwap\b", t) and "vwap" not in have:
        inds.append({"id": "vwap", "type": "vwap"})
        quote_for_inds = quote_for_inds or _m(r"\bvwap\b", t).group(0)
    if quote_for_inds:
        put("indicators", inds, quote_for_inds)
    if _m(r"\bon\s+(?:the\s+)?close\b|\bclosing price", t) and inds:
        q = _m(r"\bon\s+(?:the\s+)?close\b|\bclosing price", t).group(0)
        put("indicators", [dict(i, source="close") if i["type"] in ("ema", "sma") else i for i in inds], q)

    # Risk:reward target
    m = _m(r"\b1\s*:\s*(\d+(?:\.\d+)?)\b", t) or _m(r"\b(\d+(?:\.\d+)?)\s*R\b(?!\w)", t) or \
        _m(r"\b(\d+(?:\.\d+)?)\s*(?:x|times)\s+(?:the\s+)?risk\b", t)
    if m:
        put("target", {"type": "r_multiple", "r": float(m.group(1))}, m.group(0))
    if _m(r"\bno (?:profit )?target\b", t):
        put("target", {"type": "none"}, _m(r"\bno (?:profit )?target\b", t).group(0))

    # Directions
    for pat, v in ((r"\blong only\b", "long"), (r"\bshort only\b", "short"),
                   (r"\b(?:long and short|both directions|longs and shorts)\b", "both")):
        mm = _m(pat, t)
        if mm:
            put("directions", v, mm.group(0))

    # Session
    mm = _m(r"\b(?:rth|regular trading hours|regular hours)\b", t)
    if mm:
        put("session", {"type": "rth", "tz": "America/New_York"}, mm.group(0))
    mm = _m(r"\b(?:eth|globex session|overnight session|full session|24 ?hours?)\b", t)
    if mm:
        put("session", {"type": "eth", "tz": "America/New_York"}, mm.group(0))

    # Entry conditions: "long/buy when close crosses above the 10 ema"
    for side, verbs in (("long", r"(?:go long|long|buy)"), ("short", r"(?:go short|short|sell)")):
        mm = _m(verbs + r"\s+(?:when|if)\s+(?:the\s+)?(close|price|candle closes?)\s+(crosses above|crosses below|closes above|closes below|is above|is below|above|below)\s+(?:the\s+)?(\d{1,3})\s*-?\s*(ema|sma)", t)
        if mm:
            verb = mm.group(2).lower()
            op = {"crosses above": "crosses_above", "crosses below": "crosses_below"}.get(verb, "gt" if "above" in verb else "lt")
            ref = f"{mm.group(4).lower()}{mm.group(3)}"
            put(f"entry.{side}.when", {"type": "all", "conditions": [
                {"type": "compare", "op": op, "left": {"kind": "price", "field": "close"}, "right": {"kind": "indicator", "ref": ref}}]},
                mm.group(0))
            if ref not in have:
                notes.append(f"'{ref}' is used in a condition but wasn't listed as an indicator.")

    # Entry order
    mm = _m(r"\bnext (?:bar|candle)(?:'s)? open\b|\bopen of the next (?:bar|candle)\b", t)
    if mm:
        put("entry.order", {"type": "market_next_open", "valid_bars": 1}, mm.group(0))

    # Stop
    mm = _m(r"\bstop\w*\s+(?:at|below|under|above|beyond)\s+(?:the\s+)?signal (?:bar|candle)(?:'s)?\s+(?:low|high|extreme)", t)
    if mm:
        off = _m(r"(\d+)\s*ticks?\s+(?:below|above|beyond)", t)
        put("stop", {"type": "signal_bar_extreme", "offset_ticks": int(off.group(1)) if off else 0}, mm.group(0))
    mm = _m(r"\b(\d{1,4})\s*-?\s*ticks?\s+stop\b|\bstop(?:\s+loss)?\s+(?:of\s+)?(\d{1,4})\s*ticks?\b", t)
    if mm:
        put("stop", {"type": "fixed_ticks", "ticks": int(mm.group(1) or mm.group(2))}, mm.group(0))

    # Sizing
    mm = _m(r"\b(\d{1,3})\s+contracts?\b", t)
    if mm:
        put("sizing", {"type": "fixed_contracts", "contracts": int(mm.group(1))}, mm.group(0))

    # Costs
    mm = _m(r"\$?\s*(\d+(?:\.\d+)?)\s*(?:per side|/side|commission)", t)
    if mm:
        put("costs.commission_per_side", float(mm.group(1)), mm.group(0))
    mm = _m(r"\b(\d+(?:\.\d+)?)\s*ticks?\s+(?:of\s+)?slippage\b", t)
    if mm:
        put("costs.slippage_ticks", float(mm.group(1)), mm.group(0))

    # Session-end
    mm = _m(r"\bflat (?:at|by) (?:the )?(?:end of (?:the )?(?:day|session)|close)\b", t)
    if mm:
        put("management.flat_at_session_end", True, mm.group(0))

    # Dates
    mm = _m(r"\b(\d{4}-\d{2}-\d{2})\s*(?:to|-|through|until)\s*(\d{4}-\d{2}-\d{2})\b", t)
    if mm:
        put("data.start", mm.group(1), mm.group(0))
        put("data.end", mm.group(2), mm.group(0))

    if not sets:
        notes.append("The offline parser didn't recognise any rules in that message. Use the editable controls, "
                      "or configure OPENROUTER_API_KEY for full AI interpretation.")
    return {"set": sets, "questions": [], "notes": notes, "unsupported": []}
