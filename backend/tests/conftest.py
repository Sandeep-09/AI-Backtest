import os
from pathlib import Path

import pytest

os.environ["LLM_MOCK"] = "1"


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    from app.data.synthetic import write_demo_folder

    d = tmp_path_factory.mktemp("data")
    facts = write_demo_folder(d)
    (d / "facts.txt").write_text(str(facts["roll_date"]))
    return d


@pytest.fixture(scope="session")
def facts(data_dir):
    from app.data.synthetic import write_vps_like_folder  # noqa: F401  (facts are deterministic for the default seed)
    import datetime as dt
    return {"roll_date": dt.date.fromisoformat((data_dir / "facts.txt").read_text().strip())}


@pytest.fixture(scope="session")
def app_env(data_dir, tmp_path_factory):
    from app.config import Settings, reset_settings

    var = tmp_path_factory.mktemp("var")
    reset_settings(Settings(data_dir=data_dir, var_dir=var, llm_mock=True, openrouter_api_key=None))
    from app.data import catalog, ingest, validate
    from app import strategies

    for f in catalog.scan():
        if f["status"] == "new":
            ingest.ingest_file(f["fingerprint"])
    for r in strategies.coverage():
        validate.validate_root(r)
    return var


@pytest.fixture(scope="session")
def client(app_env):
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


FULL_SPEC = {
    "instrument": "NQ", "timeframe": "5m", "session": {"type": "eth", "tz": "America/New_York"},
    "display_timezone": "America/New_York", "directions": "both",
    "indicators": [{"id": "ema10", "type": "ema", "period": 10, "source": "close"},
                   {"id": "ema20", "type": "ema", "period": 20, "source": "close"}],
    "entry": {"long": {"when": {"type": "all", "conditions": [{"type": "compare", "op": "crosses_above",
                                                                "left": {"kind": "indicator", "ref": "ema10"},
                                                                "right": {"kind": "indicator", "ref": "ema20"}}]}},
              "short": {"when": {"type": "all", "conditions": [{"type": "compare", "op": "crosses_below",
                                                                 "left": {"kind": "indicator", "ref": "ema10"},
                                                                 "right": {"kind": "indicator", "ref": "ema20"}}]}},
              "order": {"type": "market_next_open"}},
    "stop": {"type": "signal_bar_extreme", "offset_ticks": 1}, "target": {"type": "r_multiple", "r": 3},
    "management": {"flat_at_session_end": True}, "sizing": {"type": "fixed_contracts", "contracts": 1},
    "costs": {"commission_per_side": 2.5, "slippage_ticks": 1},
    "fills": {"same_bar_policy": "intrabar_then_stop_first", "limit_fill": "trade_through"},
    "data": {"contract": "most_active_same_day", "start": "2026-09-09", "end": "2026-09-17"},
}
