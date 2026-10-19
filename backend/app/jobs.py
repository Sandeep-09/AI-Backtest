"""Background jobs (data ingest, backtests) with progress stored in SQLite."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from . import db

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")


class Job:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self._last = 0.0
        self.log: list[str] = []

    def progress(self, frac: float | None, message: str | None = None, force: bool = False) -> None:
        t = time.time()
        if not force and t - self._last < 0.4:
            return
        self._last = t
        if message:
            self.log.append(f"{db.now()} {message}")
            self.log = self.log[-200:]
        db.ex("UPDATE jobs SET progress=COALESCE(?, progress), message=COALESCE(?, message), log_json=?, updated_at=? WHERE id=?",
              (frac, message, db.j(self.log), db.now(), self.id))

    def note(self, message: str) -> None:
        self.progress(None, message, force=True)


def submit(kind: str, fn: Callable[[Job], Any]) -> str:
    job_id = uuid.uuid4().hex[:12]
    db.ex("INSERT INTO jobs(id, kind, status, progress, message, log_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
          (job_id, kind, "queued", 0.0, "Queued", "[]", db.now(), db.now()))

    def run() -> None:
        job = Job(job_id)
        db.ex("UPDATE jobs SET status='running', updated_at=? WHERE id=?", (db.now(), job_id))
        try:
            result = fn(job)
            job.log.append(f"{db.now()} Finished")
            db.ex("UPDATE jobs SET status='done', progress=1.0, message='Finished', log_json=?, result_json=?, updated_at=? WHERE id=?",
                  (db.j(job.log), db.j(result), db.now(), job_id))
        except Exception as e:  # noqa: BLE001
            job.log.append(f"{db.now()} ERROR {e!r}")
            db.ex("UPDATE jobs SET status='error', message=?, log_json=?, result_json=?, updated_at=? WHERE id=?",
                  (f"Failed: {e}", db.j(job.log), db.j({"traceback": traceback.format_exc()[-4000:]}), db.now(), job_id))

    _pool.submit(run)
    return job_id


def get(job_id: str) -> dict | None:
    r = db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not r:
        return None
    return {"id": r["id"], "kind": r["kind"], "status": r["status"], "progress": r["progress"], "message": r["message"],
            "log": db.lj(r["log_json"], []), "result": db.lj(r["result_json"]), "created_at": r["created_at"],
            "updated_at": r["updated_at"]}


def wait(job_id: str, timeout: float = 120) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = get(job_id)
        if j and j["status"] in ("done", "error"):
            return j
        time.sleep(0.05)
    raise TimeoutError(job_id)


_ = threading  # keep import for type checkers
