import copy

from .conftest import FULL_SPEC


def test_ingest_resolves_symbols_and_flags_issues(client):
    cov = client.get("/api/data/coverage").json()["coverage"]
    nq = cov["NQ"]
    syms = {c["symbol"] for c in nq["contracts"]}
    assert "NQZ6" in syms and any(s.startswith("NQ id") for s in syms)
    assert nq["intrabar"] == ["tick"]
    assert nq["rolls"]["most_active_same_day"][0]["trade_date"] == "2026-09-15"
    assert nq["rolls"]["front_by_prior_volume"][0]["trade_date"] == "2026-09-16"
    codes = {i["code"] for i in client.get("/api/data/issues").json()["issues"]}
    assert {"ts_recv_order", "intraday_gaps", "multiple_contracts", "synthetic"} <= codes


def test_full_flow_describe_modify_preview_approve_run(client):
    r = client.post("/api/strategies", json={"text": "Use the 10 EMA and 20 EMA with a 1:3 risk-to-reward target."})
    assert r.status_code == 200
    st = r.json()
    sid = st["strategy"]["id"]
    assert not st["gate"]["can_approve"]
    assert any(q["id"] == "entry-trigger" for q in st["questions"])

    # Answer everything through the editable controls (as the UI does), except the price source.
    spec = copy.deepcopy(FULL_SPEC)
    ops = [{"path": k, "value": spec[k]} for k in ("instrument", "timeframe", "session", "display_timezone", "directions",
                                                   "stop", "sizing", "indicators")]
    ops += [{"path": "entry.long", "value": spec["entry"]["long"]}, {"path": "entry.short", "value": spec["entry"]["short"]},
            {"path": "entry.order", "value": spec["entry"]["order"]},
            {"path": "management.flat_at_session_end", "value": True},
            {"path": "costs.commission_per_side", "value": 2.5}, {"path": "costs.slippage_ticks", "value": 1},
            {"path": "fills.same_bar_policy", "value": "intrabar_then_stop_first"}, {"path": "fills.limit_fill", "value": "trade_through"},
            {"path": "data", "value": spec["data"]}]
    st = client.post(f"/api/strategies/{sid}/patch", json={"ops": ops}).json()
    v = st["version"]["version"]
    assert v == 2
    assert [q for q in st["questions"] if q["severity"] == "required"] == []
    assert "run the visual preview for this version first" in st["gate"]["reasons"]

    # Approval is refused before the preview.
    r = client.post(f"/api/strategies/{sid}/versions/{v}/approve", json={"spec_hash": st["version"]["spec_hash"]})
    assert r.status_code == 409

    st = client.post(f"/api/strategies/{sid}/versions/{v}/preview").json()
    pv = st["preview"]
    assert pv["ok"] and pv["source"] == "data"
    assert pv["examples"]["long"] and pv["examples"]["short"]
    assert st["gate"]["can_approve"], st["gate"]["reasons"]

    # Approving with a stale hash is refused.
    assert client.post(f"/api/strategies/{sid}/versions/{v}/approve", json={"spec_hash": "0" * 64}).status_code == 409
    h = st["version"]["spec_hash"]
    st = client.post(f"/api/strategies/{sid}/versions/{v}/approve", json={"spec_hash": h}).json()
    assert st["approval"] and st["approval"]["spec_hash"] == h

    run = client.post(f"/api/strategies/{sid}/versions/{v}/runs", json={"spec_hash": h}).json()
    from app import jobs

    j = jobs.wait(run["job_id"], 120)
    assert j["status"] == "done", j
    res = client.get(f"/api/runs/{run['run_id']}").json()
    m = res["result"]["metrics"]
    assert m["trade_count"] > 0 and "profit_factor" in m and "expectancy" in m
    trades = client.get(f"/api/runs/{run['run_id']}/trades", params={"q": "short"}).json()
    assert trades["total"] > 0 and all(t["direction"] == "short" for t in trades["items"])
    # Preview examples are exactly trades of the backtest.
    first_long = pv["examples"]["long"]["trade"]
    all_tr = client.get(f"/api/runs/{run['run_id']}/trades", params={"limit": 1000}).json()["items"]
    match = [t for t in all_tr if t["entry_time"] == first_long["entry_time"] and t["direction"] == "long"]
    assert match and match[0]["net_pnl"] == first_long["net_pnl"]
    d = client.get(f"/api/runs/{run['run_id']}/trades/1").json()
    assert d["hash_matches_trade"] and d["spec_hash"] == h
    assert d["trade"]["evidence"] and d["chart"]["candles"] and d["rules"]
    assert set(d["indicators_at_signal"]) == {"ema10", "ema20"}

    # Any change makes a new, unapproved version; running it is refused.
    st2 = client.post(f"/api/strategies/{sid}/patch", json={"ops": [{"path": "target", "value": {"type": "r_multiple", "r": 2}}]}).json()
    v2 = st2["version"]["version"]
    assert v2 == 3 and st2["approval"] is None and not st2["gate"]["can_approve"]
    changed = [s["key"] for s in st2["summary"] if s["changed"]]
    assert changed == ["target"]
    r = client.post(f"/api/strategies/{sid}/versions/{v2}/runs", json={"spec_hash": st2["version"]["spec_hash"]})
    assert r.status_code == 409


def test_offline_parser_entry_condition_via_chat(client):
    st = client.post("/api/strategies", json={"text": "NQ 5 min. Go long when the close crosses above the 20 EMA."}).json()
    spec = st["spec"]
    assert spec["instrument"] == "NQ" and spec["timeframe"] == "5m"
    cond = spec["entry"]["long"]["when"]["conditions"][0]
    assert cond["op"] == "crosses_above" and cond["right"]["ref"] == "ema20"
    sid = st["strategy"]["id"]
    st = client.post(f"/api/strategies/{sid}/messages", json={"text": "Use a 1:2 target instead"}).json()
    assert st["spec"]["target"]["r"] == 2 and st["version"]["version"] == 2
    assert [s["key"] for s in st["summary"] if s["changed"]] == ["target"]
