"""
Strategy lifecycle: describe -> versions -> preview -> approve -> run.

Every change creates a new immutable version. Approval and runs are tied to a
version *and* its spec hash; a run is refused unless the hash it would execute
equals the approved one.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

from . import assumptions, db, jobs
from .data import store
from .engine import chart, runner
from .llm.interpret import apply_proposals, interpret
from .spec import completeness
from .spec.ops import apply_set, changed_top_paths, diff_specs, spec_hash
from .spec.schema import SETTABLE_PATHS, StrategySpec
from .summary.render import render, render_text


class GateError(Exception):
    pass


# ----- coverage cache ------------------------------------------------------------------
_cov_lock = threading.Lock()
_cov: dict = {"key": None, "value": {}}


def coverage() -> dict:
    key = tuple((r["fingerprint"], r["status"], r["updated_at"]) for r in db.q("SELECT fingerprint, status, updated_at FROM files ORDER BY fingerprint"))
    with _cov_lock:
        if _cov["key"] != key:
            _cov["value"] = store.coverage()
            _cov["key"] = key
        return _cov["value"]


# ----- versions ------------------------------------------------------------------------
def _row_to_version(r) -> dict:
    return {"id": r["id"], "strategy_id": r["strategy_id"], "version": r["version"], "spec": db.lj(r["spec_json"]),
            "spec_hash": r["spec_hash"], "parent_version": r["parent_version"], "source": r["source"],
            "changes": db.lj(r["change_json"], []), "notes": db.lj(r["notes_json"], {}), "created_at": r["created_at"]}


def latest_version(sid: int) -> dict:
    r = db.q1("SELECT * FROM versions WHERE strategy_id=? ORDER BY version DESC LIMIT 1", (sid,))
    if not r:
        raise KeyError(f"strategy {sid} not found")
    return _row_to_version(r)


def get_version(sid: int, v: int) -> dict:
    r = db.q1("SELECT * FROM versions WHERE strategy_id=? AND version=?", (sid, v))
    if not r:
        raise KeyError(f"strategy {sid} version {v} not found")
    return _row_to_version(r)


def _new_version(sid: int, spec: StrategySpec, parent: dict | None, source: str, notes: dict) -> dict:
    before = StrategySpec.model_validate(parent["spec"]) if parent else None
    changes = diff_specs(before, spec)
    ver = (parent["version"] + 1) if parent else 1
    db.ex("INSERT INTO versions(strategy_id, version, spec_json, spec_hash, parent_version, source, change_json, notes_json, created_at)"
          " VALUES (?,?,?,?,?,?,?,?,?)",
          (sid, ver, db.j(spec.model_dump(mode="json")), spec_hash(spec), parent["version"] if parent else None, source,
           db.j(changes), db.j(notes), db.now()))
    return get_version(sid, ver)


def _user_texts(sid: int) -> list[str]:
    return [r["content"] for r in db.q("SELECT content FROM messages WHERE strategy_id=? AND role='user' ORDER BY id", (sid,))]


def _history(sid: int) -> list[dict]:
    return [{"role": r["role"], "content": r["content"]} for r in db.q("SELECT role, content FROM messages WHERE strategy_id=? ORDER BY id", (sid,))]


def _interp_payload(res) -> dict:
    return {"engine": res.engine, "model": res.model, "proposals": [p.to_dict() for p in res.proposals], "questions": res.questions,
            "notes": res.notes, "unsupported": [dict(u, acknowledged=False) for u in res.unsupported], "error": res.error}


def _assistant_text(payload: dict) -> str:
    applied = [p for p in payload["proposals"] if p["status"] == "applied"]
    parts = []
    if payload.get("error"):
        parts.append(payload["error"])
    if applied:
        parts.append("Updated: " + "; ".join(f"{p['label']} (from “{p['quote']}”)" for p in applied))
    conf = [p for p in payload["proposals"] if p["status"] == "needs_confirmation"]
    if conf:
        parts.append(f"{len(conf)} suggestion(s) need your confirmation.")
    if payload["unsupported"]:
        parts.append("Can't be expressed: " + "; ".join(u.get("text", "") for u in payload["unsupported"]))
    if not parts:
        parts.append("No rule changes recognised.")
    return " ".join(parts)


def create(text: str, name: str | None = None) -> int:
    sid = db.ex("INSERT INTO strategies(name, created_at) VALUES (?,?)", (name or text.strip()[:60], db.now()))
    empty = StrategySpec()
    db.ex("INSERT INTO messages(strategy_id, version, role, content, meta_json, created_at) VALUES (?,?,?,?,?,?)",
          (sid, 1, "user", text, None, db.now()))
    res = interpret(empty, [], text)
    new = apply_proposals(empty, res, _user_texts(sid))
    payload = _interp_payload(res)
    v = _new_version(sid, new, None, "description", {"interpretation": payload})
    db.ex("INSERT INTO messages(strategy_id, version, role, content, meta_json, created_at) VALUES (?,?,?,?,?,?)",
          (sid, v["version"], "assistant", _assistant_text(payload), db.j(payload), db.now()))
    return sid


def import_interpretation(payload: dict) -> int:
    """
    Load an interpretation written outside the app (e.g. by Claude in a chat).
    The user's own words are stored as messages and every proposal goes through the
    same quote check as the in-app AI: a value without a verbatim quote is not applied.
    """
    from .llm.interpret import Interpretation, Proposal

    texts = [str(t) for t in payload.get("messages") or [] if str(t).strip()]
    if not texts:
        raise ValueError("an interpretation needs the user's own words in 'messages'")
    sid = db.ex("INSERT INTO strategies(name, created_at) VALUES (?,?)", (payload.get("name") or texts[0][:60], db.now()))
    for t in texts:
        db.ex("INSERT INTO messages(strategy_id, version, role, content, meta_json, created_at) VALUES (?,?,?,?,?,?)",
              (sid, 1, "user", t, None, db.now()))
    res = Interpretation(engine=str(payload.get("engine") or "external"), model=payload.get("model"),
                         proposals=[Proposal(path=str(p["path"]), value=p.get("value"), quote=p.get("quote"))
                                    for p in payload.get("proposals") or [] if isinstance(p, dict) and "path" in p],
                         questions=[str(q) for q in payload.get("questions") or []],
                         notes=[str(n) for n in payload.get("notes") or []],
                         unsupported=[u if isinstance(u, dict) else {"text": str(u), "reason": ""} for u in payload.get("unsupported") or []])
    empty = StrategySpec()
    new = apply_proposals(empty, res, _user_texts(sid))
    out = _interp_payload(res)
    v = _new_version(sid, new, None, "import", {"interpretation": out})
    db.ex("INSERT INTO messages(strategy_id, version, role, content, meta_json, created_at) VALUES (?,?,?,?,?,?)",
          (sid, v["version"], "assistant", _assistant_text(out), db.j(out), db.now()))
    return sid


def message(sid: int, text: str) -> dict:
    cur = latest_version(sid)
    spec = StrategySpec.model_validate(cur["spec"])
    hist = _history(sid)
    db.ex("INSERT INTO messages(strategy_id, version, role, content, meta_json, created_at) VALUES (?,?,?,?,?,?)",
          (sid, cur["version"], "user", text, None, db.now()))
    res = interpret(spec, hist, text)
    new = apply_proposals(spec, res, _user_texts(sid))
    payload = _interp_payload(res)
    if spec_hash(new) != cur["spec_hash"] or payload["unsupported"]:
        v = _new_version(sid, new, cur, "chat", {"interpretation": payload})
    else:
        v = cur
    db.ex("INSERT INTO messages(strategy_id, version, role, content, meta_json, created_at) VALUES (?,?,?,?,?,?)",
          (sid, v["version"], "assistant", _assistant_text(payload), db.j(payload), db.now()))
    return state(sid)


def patch(sid: int, ops: list[dict], note: str | None = None) -> dict:
    cur = latest_version(sid)
    spec = StrategySpec.model_validate(cur["spec"])
    for op in ops:
        if op["path"] not in SETTABLE_PATHS:
            raise ValueError(f"'{op['path']}' is not settable")
        spec = apply_set(spec, op["path"], op.get("value"))
    if spec_hash(spec) == cur["spec_hash"]:
        return state(sid)
    _new_version(sid, spec, cur, "edit", {"edit_note": note, "ops": ops})
    return state(sid)


def acknowledge_unsupported(sid: int, version: int, index: int) -> dict:
    v = get_version(sid, version)
    notes = v["notes"]
    items = notes.get("interpretation", {}).get("unsupported", [])
    if 0 <= index < len(items):
        items[index]["acknowledged"] = True
        db.ex("UPDATE versions SET notes_json=? WHERE strategy_id=? AND version=?", (db.j(notes), sid, version))
    return state(sid, version)


def _open_unsupported(sid: int, version: int) -> list[dict]:
    """Unsupported requests raised at or before this version that the user hasn't acknowledged."""
    out = []
    for r in db.q("SELECT version, notes_json FROM versions WHERE strategy_id=? AND version<=? ORDER BY version", (sid, version)):
        for k, u in enumerate((db.lj(r["notes_json"], {}).get("interpretation") or {}).get("unsupported", [])):
            if not u.get("acknowledged"):
                out.append(dict(u, version=r["version"], index=k))
    return out


def latest_preview(sid: int, version: int, h: str) -> dict | None:
    r = db.q1("SELECT * FROM previews WHERE strategy_id=? AND version=? AND spec_hash=? ORDER BY id DESC LIMIT 1", (sid, version, h))
    return db.lj(r["result_json"]) if r else None


def approval(sid: int, version: int, h: str) -> dict | None:
    r = db.q1("SELECT * FROM approvals WHERE strategy_id=? AND version=? AND spec_hash=? ORDER BY id DESC LIMIT 1", (sid, version, h))
    if not r:
        return None
    return {"id": r["id"], "approved_at": r["approved_at"], "spec_hash": r["spec_hash"], "assumptions": db.lj(r["assumptions_json"])}


def gate(sid: int, v: dict, spec: StrategySpec, qs: list, cov: dict) -> dict:
    reasons = []
    req = completeness.required_open(qs)
    if req:
        reasons.append(f"{len(req)} rule(s) still undecided")
    uns = _open_unsupported(sid, v["version"])
    if uns:
        reasons.append(f"{len(uns)} unsupported request(s) need acknowledging")
    pv = latest_preview(sid, v["version"], v["spec_hash"])
    if not pv or not pv.get("ok"):
        reasons.append("run the visual preview for this version first")
    elif pv.get("source") != "data":
        reasons.append("the preview must use your data (set instrument and dates with data available)")
    cv = cov.get(spec.instrument or "")
    if spec.instrument and not cv:
        reasons.append(f"no ingested data for {spec.instrument}")
    if cv and spec.data.start and spec.data.end:
        if str(spec.data.end) < cv["first_day"] or str(spec.data.start) > cv["last_day"]:
            reasons.append(f"date range is outside the available data ({cv['first_day']} to {cv['last_day']})")
    blocking = [i for i in db.issues() if i["severity"] == "blocking" and (i["scope"] == "file" or i["ref"] == spec.instrument)]
    if blocking:
        reasons.append(f"{len(blocking)} blocking data issue(s)")
    return {"can_approve": not reasons, "reasons": reasons, "blocking_issues": blocking, "open_unsupported": uns}


def state(sid: int, version: int | None = None) -> dict:
    s = db.q1("SELECT * FROM strategies WHERE id=?", (sid,))
    if not s:
        raise KeyError(sid)
    v = get_version(sid, version) if version else latest_version(sid)
    spec = StrategySpec.model_validate(v["spec"])
    cov = coverage()
    qs = completeness.check(spec, cov)
    changed = changed_top_paths(v["changes"]) if v["parent_version"] else set()
    sections = render(spec)
    for sec in sections:
        sec["changed"] = bool(v["parent_version"]) and any(p in changed or any(c.startswith(p + ".") or c == p for c in changed) for p in sec["paths"])
    versions = [{"version": r["version"], "spec_hash": r["spec_hash"], "source": r["source"], "created_at": r["created_at"],
                 "changes": len(db.lj(r["change_json"], [])),
                 "approved": bool(db.q1("SELECT 1 FROM approvals WHERE strategy_id=? AND version=? AND spec_hash=?", (sid, r["version"], r["spec_hash"])))}
                for r in db.q("SELECT version, spec_hash, source, created_at, change_json FROM versions WHERE strategy_id=? ORDER BY version", (sid,))]
    msgs = [{"role": r["role"], "content": r["content"], "version": r["version"], "meta": db.lj(r["meta_json"]), "created_at": r["created_at"]}
            for r in db.q("SELECT * FROM messages WHERE strategy_id=? ORDER BY id", (sid,))]
    g = gate(sid, v, spec, qs, cov)
    ap = approval(sid, v["version"], v["spec_hash"])
    runs = [{"id": r["id"], "status": r["status"], "version": r["version"], "created_at": r["created_at"], "job_id": r["job_id"]}
            for r in db.q("SELECT id, status, version, created_at, job_id FROM runs WHERE strategy_id=? ORDER BY id DESC", (sid,))]
    return {
        "strategy": {"id": sid, "name": s["name"], "created_at": s["created_at"]},
        "version": {k: v[k] for k in ("version", "spec_hash", "parent_version", "source", "created_at")},
        "is_latest": v["version"] == latest_version(sid)["version"],
        "spec": v["spec"], "summary": sections, "changes": v["changes"],
        "questions": [q.to_dict() for q in qs],
        "interpretation": v["notes"].get("interpretation"),
        "messages": msgs, "versions": versions,
        "preview_requirements": runner.preview_requirements(spec),
        "preview": latest_preview(sid, v["version"], v["spec_hash"]),
        "assumptions": assumptions.build(spec, cov),
        "gate": g, "approval": ap, "runs": runs,
    }


def run_preview(sid: int, version: int) -> dict:
    v = get_version(sid, version)
    spec = StrategySpec.model_validate(v["spec"])
    cov = coverage()
    has = bool(spec.instrument and cov.get(spec.instrument))
    res = runner.preview(spec, v["spec_hash"], has)
    db.ex("INSERT INTO previews(strategy_id, version, spec_hash, result_json, created_at) VALUES (?,?,?,?,?)",
          (sid, version, v["spec_hash"], db.j(res), db.now()))
    return state(sid, version)


def approve(sid: int, version: int, expected_hash: str) -> dict:
    v = get_version(sid, version)
    if v["spec_hash"] != expected_hash:
        raise GateError("The rules changed since you reviewed them. Review the latest version and preview it again.")
    spec = StrategySpec.model_validate(v["spec"])
    cov = coverage()
    g = gate(sid, v, spec, completeness.check(spec, cov), cov)
    if not g["can_approve"]:
        raise GateError("Cannot approve yet: " + "; ".join(g["reasons"]))
    snap = {"assumptions": assumptions.build(spec, cov), "summary": render_text(spec), "spec": v["spec"]}
    db.ex("INSERT INTO approvals(strategy_id, version, spec_hash, assumptions_json, approved_at) VALUES (?,?,?,?,?)",
          (sid, version, v["spec_hash"], db.j(snap), db.now()))
    return state(sid, version)


# ----- runs ------------------------------------------------------------------------------
_run_cache: OrderedDict = OrderedDict()


def start_run(sid: int, version: int, expected_hash: str) -> dict:
    v = get_version(sid, version)
    ap = approval(sid, version, v["spec_hash"])
    if ap is None:
        raise GateError("This version is not approved.")
    if expected_hash != v["spec_hash"] or ap["spec_hash"] != v["spec_hash"]:
        raise GateError("Spec hash mismatch: the rules to execute differ from the approved rules.")
    spec = StrategySpec.model_validate(v["spec"])
    run_id = db.ex("INSERT INTO runs(strategy_id, version, spec_hash, approval_id, job_id, status, created_at) VALUES (?,?,?,?,?,?,?)",
                   (sid, version, v["spec_hash"], ap["id"], None, "queued", db.now()))

    def work(job: jobs.Job) -> dict:
        db.ex("UPDATE runs SET status='running' WHERE id=?", (run_id,))
        job.note(f"Backtesting {spec.instrument} v{version} ({v['spec_hash'][:12]})")
        # Re-read and re-check the approved spec at execution time.
        again = get_version(sid, version)
        if again["spec_hash"] != ap["spec_hash"]:
            raise GateError("spec hash changed before execution")
        try:
            res = runner.backtest(spec, v["spec_hash"], progress=lambda f: job.progress(0.05 + 0.85 * f, "Simulating bars"))
        except Exception:
            db.ex("UPDATE runs SET status='error', finished_at=? WHERE id=?", (db.now(), run_id))
            raise
        prep = res["prep"]
        job.progress(0.92, f"Saving {len(res['trades'])} trades", force=True)
        rows = []
        for t in res["trades"]:
            search = " ".join(str(x) for x in (t["n"], t["direction"], t["symbol"], t["trade_date"], t["exit_reason"],
                                                t["entry_time"], t["exit_time"], t["exit_resolution"], "win" if t["net_pnl"] > 0 else "loss"))
            rows.append((run_id, t["n"], t["direction"], t["symbol"], t["entry_ts"], t["exit_ts"], t["net_pnl"], t["exit_reason"],
                         search.lower(), db.j(t)))
        with db.tx() as c:
            c.executemany("INSERT INTO trades(run_id, n, direction, symbol, entry_ts, exit_ts, net_pnl, exit_reason, search_text, detail_json)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        result = {
            "metrics": res["metrics"], "equity": res["equity"], "counters": res["counters"],
            "skipped": res["skipped"][:1000], "skipped_total": len(res["skipped"]),
            "data": {"warmup_from": prep.warmup_from, "trade_from": prep.trade_from, "trade_to": prep.trade_to,
                     "rolls": prep.choice.rolls if prep.choice else [], "skipped_days": prep.choice.skipped_days if prep.choice else [],
                     "intrabar": prep.intrabar_kind, "base_res": prep.base_res, "synthetic": prep.synthetic, "notes": prep.notes,
                     "bars": len(prep.bars)},
            "assumptions": ap["assumptions"],
        }
        db.ex("UPDATE runs SET status='done', result_json=?, finished_at=? WHERE id=?", (db.j(result), db.now(), run_id))
        _run_cache[run_id] = res["sim"]
        while len(_run_cache) > 3:
            _run_cache.popitem(last=False)
        return {"run_id": run_id, "trades": len(res["trades"])}

    job_id = jobs.submit("backtest", work)
    db.ex("UPDATE runs SET job_id=? WHERE id=?", (job_id, run_id))
    return {"run_id": run_id, "job_id": job_id}


def get_run(run_id: int) -> dict:
    r = db.q1("SELECT * FROM runs WHERE id=?", (run_id,))
    if not r:
        raise KeyError(run_id)
    v = get_version(r["strategy_id"], r["version"])
    return {"id": r["id"], "strategy_id": r["strategy_id"], "version": r["version"], "spec_hash": r["spec_hash"],
            "status": r["status"], "job": jobs.get(r["job_id"]) if r["job_id"] else None, "created_at": r["created_at"],
            "finished_at": r["finished_at"], "result": db.lj(r["result_json"]), "spec": v["spec"],
            "summary": render(StrategySpec.model_validate(v["spec"]))}


def list_trades(run_id: int, q: str | None, direction: str | None, outcome: str | None, limit: int, offset: int) -> dict:
    where, args = ["run_id=?"], [run_id]
    if q:
        for word in q.lower().split():
            where.append("search_text LIKE ?")
            args.append(f"%{word}%")
    if direction in ("long", "short"):
        where.append("direction=?")
        args.append(direction)
    if outcome == "win":
        where.append("net_pnl>0")
    elif outcome == "loss":
        where.append("net_pnl<=0")
    w = " AND ".join(where)
    total = db.q1(f"SELECT COUNT(*) AS c FROM trades WHERE {w}", tuple(args))["c"]
    rows = db.q(f"SELECT detail_json FROM trades WHERE {w} ORDER BY n LIMIT ? OFFSET ?", tuple(args + [limit, offset]))
    keep = ("n", "direction", "symbol", "trade_date", "qty", "entry_time", "entry_price", "initial_stop", "target", "exit_time",
            "exit_price", "exit_reason", "exit_resolution", "net_pnl", "net_r", "commission", "slippage_usd", "bars_held")
    return {"total": total, "items": [{k: t.get(k) for k in keep} for t in (db.lj(r["detail_json"]) for r in rows)]}


def _sim_for_run(run_id: int):
    if run_id in _run_cache:
        _run_cache.move_to_end(run_id)
        return _run_cache[run_id]
    r = db.q1("SELECT * FROM runs WHERE id=?", (run_id,))
    v = get_version(r["strategy_id"], r["version"])
    spec = StrategySpec.model_validate(v["spec"])
    from .engine.simulate import Simulator

    prep, ib = runner.build(spec)
    sim = Simulator(spec, prep, ib, v["spec_hash"])
    _run_cache[run_id] = sim
    while len(_run_cache) > 3:
        _run_cache.popitem(last=False)
    return sim


def trade_detail(run_id: int, n: int) -> dict:
    r = db.q1("SELECT detail_json FROM trades WHERE run_id=? AND n=?", (run_id, n))
    if not r:
        raise KeyError(n)
    t = db.lj(r["detail_json"])
    sim = _sim_for_run(run_id)
    run = db.q1("SELECT * FROM runs WHERE id=?", (run_id,))
    v = get_version(run["strategy_id"], run["version"])
    spec = sim.spec
    a, z = max(0, t["signal_i"] - 60), t["exit_i"] + 25
    # Indicator values at the signal bar.
    ind_at_signal = {i.id: (None if sim.ind[i.id][t["signal_i"]] != sim.ind[i.id][t["signal_i"]] else round(float(sim.ind[i.id][t["signal_i"]]), 6))
                     for i in spec.indicators}
    return {"trade": t, "chart": chart.window(spec, sim.b, sim.ind, a, z), "overlay": chart.trade_overlay(spec, t),
            "indicators_at_signal": ind_at_signal, "rules": render(spec), "version": run["version"], "spec_hash": v["spec_hash"],
            "hash_matches_trade": t.get("spec_hash") == v["spec_hash"]}
