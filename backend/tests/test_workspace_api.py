from app import jobs


def _ready(client):
    items = client.get("/api/templates").json()["items"]
    files = {i["file"] for i in items}
    assert {"ema_vwap_strong_candle_NQ.json", "ema_vwap_strong_candle_GC.json"} <= files
    st = client.post("/api/templates/ema_vwap_strong_candle_NQ.json").json()
    assert st["interpretation"]["engine"] == "claude-manual"
    sid = st["strategy"]["id"]
    inds = [dict(i, source="close") if i["type"] == "ema" else i for i in st["spec"]["indicators"]]
    ops = [{"path": "indicators", "value": inds}, {"path": "display_timezone", "value": "America/New_York"},
           {"path": "data", "value": {"contract": "most_active_same_day", "start": "2026-09-08", "end": "2026-09-17"}},
           {"path": "fills.same_bar_policy", "value": "intrabar_then_stop_first"}, {"path": "fills.limit_fill", "value": "trade_through"}]
    return client.post(f"/api/strategies/{sid}/patch", json={"ops": ops}).json()


def test_template_404_and_short_lines(client):
    assert client.post("/api/templates/../../etc/passwd").status_code in (404, 405)
    st = _ready(client)
    shorts = {s["key"]: s["short"] for s in st["summary"]}
    assert shorts["stop"].startswith("Lower of EMA60")
    assert shorts["target"].startswith("3R")


def test_preview_returns_full_window_and_all_trades(client):
    st = _ready(client)
    sid, v = st["strategy"]["id"], st["version"]["version"]
    pv = client.post(f"/api/strategies/{sid}/versions/{v}/preview").json()["preview"]
    wc = pv["window_chart"]
    assert len(wc["t"]) == len(wc["o"]) == len(wc["c"]) > 100
    assert {i["id"] for i in wc["ind"]} >= {"ema10", "ema60", "vwap"}
    assert all(len(i["v"]) == len(wc["t"]) for i in wc["ind"])
    assert pv["trades"] and all(str(t["n"]) in pv["trade_details"] for t in pv["trades"])


def test_run_chart_has_every_trade(client):
    st = _ready(client)
    sid, v, h = st["strategy"]["id"], st["version"]["version"], st["version"]["spec_hash"]
    client.post(f"/api/strategies/{sid}/versions/{v}/preview")
    client.post(f"/api/strategies/{sid}/versions/{v}/approve", json={"spec_hash": h})
    run = client.post(f"/api/strategies/{sid}/versions/{v}/runs", json={"spec_hash": h}).json()
    assert jobs.wait(run["job_id"], 120)["status"] == "done"
    ch = client.get(f"/api/runs/{run['run_id']}/chart").json()
    total = client.get(f"/api/runs/{run['run_id']}/trades").json()["total"]
    assert len(ch["trades"]) == total and len(ch["chart"]["t"]) > 100
