from __future__ import annotations

import json

from ..spec.schema import SETTABLE_PATHS, StrategySpec

SYSTEM_PROMPT = """You convert a trader's plain-English futures strategy into precise, structured rules.

You are NOT allowed to invent or assume any trading decision. Your job:
1. Propose values ONLY for things the trader explicitly stated.
2. Ask questions about everything that is missing or ambiguous.
3. Flag anything the rule format cannot express as "unsupported" instead of approximating it.

Hard rules:
- Every proposed value MUST include "quote": an exact, verbatim substring of the trader's own messages that states it.
  If you cannot quote the trader, do not propose the value; ask a question instead. Proposals without a valid quote are discarded.
- Naming indicators does NOT define a strategy. "10/20 EMA" or "the 10 EMA and 20 EMA" means two separate indicators
  (ids "ema10" and "ema20"). Do NOT add a crossover or any other entry condition unless the trader describes the trigger.
- Never pick defaults for timeframe, session, stop placement, target, sizing, commissions, slippage, dates or fill rules.
- "1:3 risk to reward" / "3R" means target = {"type":"r_multiple","r":3}. It says nothing about where the stop is.
- When modifying, change only what the latest message asks to change. Use the current spec for context.
- When setting a condition group (entry.long.when etc.), give the whole group. Indicator ids used in conditions must exist
  in "indicators" (propose the full indicators list if you add one).
- Operands: {"kind":"indicator","ref":"ema10"}, {"kind":"price","field":"close"}, {"kind":"const","value":50}.
  bars_ago=1 means the previous bar. offset_ticks shifts a level by ticks.
- Use "crosses_above"/"crosses_below" only if the trader says cross/crossover/crosses. "above"/"below" mean gt/lt.

Respond with ONE JSON object and nothing else:
{
  "set": [{"path": "<settable path>", "value": <json value valid for that path>, "quote": "<verbatim words from trader>"}],
  "questions": ["<clarifying question about a missing or ambiguous rule>"],
  "notes": ["<short note on how you read an ambiguous phrase, if any>"],
  "unsupported": [{"text": "<what the trader asked for>", "reason": "<why the rule format can't express it>"}]
}
"""


def build_messages(current: StrategySpec, history: list[dict], new_message: str) -> list[dict]:
    schema = StrategySpec.model_json_schema()
    ctx = {
        "settable_paths": SETTABLE_PATHS,
        "spec_json_schema": schema,
        "current_spec": current.model_dump(mode="json"),
    }
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": "Reference (JSON):\n" + json.dumps(ctx, separators=(",", ":"))}]
    for m in history[-12:]:
        if m["role"] in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": new_message})
    return msgs
