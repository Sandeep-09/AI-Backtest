from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, jobs, strategies
from .config import get_settings
from .data import catalog, ingest, validate

@asynccontextmanager
async def lifespan(_app):
    try:
        catalog.scan()
    except Exception:  # noqa: BLE001
        pass
    yield


app = FastAPI(title="AI-Backtest", lifespan=lifespan)


@app.exception_handler(strategies.GateError)
def _gate(_, exc: strategies.GateError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(KeyError)
def _nf(_, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": f"not found: {exc}"})


@app.exception_handler(ValueError)
def _bad(_, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)[:2000]})


# ----- health / data ------------------------------------------------------------------
@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "llm": {"mode": s.llm_mode, "model": s.llm_model if s.llm_mode == "openrouter" else None,
                                "configured": bool(s.openrouter_api_key)},
            "data_dir": str(s.data_dir), "data_dir_exists": s.data_dir.exists()}


@app.get("/api/data/files")
def files():
    return {"files": catalog.list_files()}


@app.post("/api/data/scan")
def scan():
    return {"files": catalog.scan()}


class IngestReq(BaseModel):
    fingerprints: list[str] | None = None
    reingest: bool = False


@app.post("/api/data/ingest")
def start_ingest(req: IngestReq):
    rows = catalog.list_files()
    todo = [f for f in rows if (req.fingerprints is None or f["fingerprint"] in req.fingerprints)
            and f["status"] in (("new", "error", "ingested") if req.reingest else ("new", "error"))]
    # Definitions first so instrument ids resolve to symbols.
    todo.sort(key=lambda f: (0 if f.get("schema") == "definition" else 1, f["path"]))

    def work(job: jobs.Job):
        done, failed = 0, []
        total_bytes = sum(f["size"] for f in todo) or 1
        seen = 0
        class _FileProgress:
            """Maps one file's progress into the overall job, weighted by file size."""

            def __init__(self, base: int, size: int) -> None:
                self.base, self.size = base, size

            def progress(self, frac, message=None, force=False):
                overall = None if frac is None else (self.base + frac * self.size) / total_bytes * 0.95
                job.progress(overall, message, force)

            def note(self, message):
                job.note(message)

        for k, f in enumerate(todo):
            job.progress(seen / total_bytes * 0.95, f"[{k + 1}/{len(todo)}] Reading {Path(f['path']).name}", force=True)
            try:
                ingest.ingest_file(f["fingerprint"], _FileProgress(seen, f["size"]))
                done += 1
            except Exception as e:  # noqa: BLE001
                failed.append({"file": f["path"], "error": str(e)})
            seen += f["size"]
        job.progress(0.97, "Validating coverage and gaps", force=True)
        roots = list(strategies.coverage().keys())
        for r in roots:
            validate.validate_root(r)
        return {"ingested": done, "failed": failed, "roots": roots}

    return {"job_id": jobs.submit("ingest", work), "files": len(todo)}


@app.get("/api/data/coverage")
def coverage():
    return {"coverage": strategies.coverage()}


@app.get("/api/data/issues")
def issues():
    return {"issues": db.issues()}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


# ----- strategies ---------------------------------------------------------------------
class CreateReq(BaseModel):
    text: str
    name: str | None = None


class MsgReq(BaseModel):
    text: str


class PatchOp(BaseModel):
    path: str
    value: object = None


class PatchReq(BaseModel):
    ops: list[PatchOp]
    note: str | None = None


class HashReq(BaseModel):
    spec_hash: str


@app.get("/api/strategies")
def list_strategies():
    rows = db.q("SELECT s.id, s.name, s.created_at, MAX(v.version) AS latest FROM strategies s JOIN versions v ON v.strategy_id=s.id GROUP BY s.id ORDER BY s.id DESC")
    return {"items": [dict(r) for r in rows]}


@app.post("/api/strategies")
def create_strategy(req: CreateReq):
    if not req.text.strip():
        raise HTTPException(422, "Describe the strategy first.")
    sid = strategies.create(req.text, req.name)
    return strategies.state(sid)


@app.get("/api/strategies/{sid}")
def get_strategy(sid: int, version: int | None = None):
    return strategies.state(sid, version)


@app.post("/api/strategies/{sid}/messages")
def post_message(sid: int, req: MsgReq):
    if not req.text.strip():
        raise HTTPException(422, "Empty message.")
    return strategies.message(sid, req.text)


@app.post("/api/strategies/{sid}/patch")
def patch_strategy(sid: int, req: PatchReq):
    return strategies.patch(sid, [o.model_dump() for o in req.ops], req.note)


@app.post("/api/strategies/{sid}/versions/{v}/unsupported/{idx}/ack")
def ack(sid: int, v: int, idx: int):
    return strategies.acknowledge_unsupported(sid, v, idx)


@app.post("/api/strategies/{sid}/versions/{v}/preview")
def preview(sid: int, v: int):
    return strategies.run_preview(sid, v)


@app.post("/api/strategies/{sid}/versions/{v}/approve")
def approve(sid: int, v: int, req: HashReq):
    return strategies.approve(sid, v, req.spec_hash)


@app.post("/api/strategies/{sid}/versions/{v}/runs")
def run(sid: int, v: int, req: HashReq):
    return strategies.start_run(sid, v, req.spec_hash)


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    return strategies.get_run(run_id)


@app.get("/api/runs/{run_id}/trades")
def trades(run_id: int, q: str | None = None, direction: str | None = None, outcome: str | None = None, limit: int = 100, offset: int = 0):
    return strategies.list_trades(run_id, q, direction, outcome, min(limit, 1000), offset)


@app.get("/api/runs/{run_id}/trades/{n}")
def trade(run_id: int, n: int):
    return strategies.trade_detail(run_id, n)


# ----- frontend -------------------------------------------------------------------------
_static = get_settings().static_dir
if _static.exists():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = _static / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(_static / "index.html")

