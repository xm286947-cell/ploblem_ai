from __future__ import annotations

import threading
import uuid
import json
import sqlite3
from datetime import datetime
from pathlib import Path


class BatchAnalysisJobManager:
    """Thread-safe batch runner with an optional SQLite-backed job ledger."""

    def __init__(self, db_path=None):
        self._jobs = {}
        self._lock = threading.Lock()
        self._db_path = Path(db_path) if db_path else None
        if self._db_path:
            with sqlite3.connect(self._db_path) as connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS qc_batch_analysis_job(
                    job_id TEXT PRIMARY KEY,status TEXT NOT NULL,total INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,failed INTEGER NOT NULL DEFAULT 0,
                    processed INTEGER NOT NULL DEFAULT 0,concurrency INTEGER NOT NULL,
                    created_at TEXT NOT NULL,started_at TEXT,completed_at TEXT,error TEXT,
                    items_json TEXT NOT NULL DEFAULT '[]',result_json TEXT)""")

    def _persist(self, job):
        if not self._db_path:
            return
        with sqlite3.connect(self._db_path) as connection:
            connection.execute("""INSERT OR REPLACE INTO qc_batch_analysis_job(
                job_id,status,total,completed,failed,processed,concurrency,created_at,
                started_at,completed_at,error,items_json,result_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                job["job_id"],job["status"],job["total"],job["completed"],job["failed"],
                job["processed"],job["concurrency"],job["created_at"],job.get("started_at"),
                job.get("completed_at"),job.get("error"),json.dumps(job.get("items") or [],ensure_ascii=False),
                json.dumps(job.get("result"),ensure_ascii=False) if job.get("result") is not None else None,
            ))

    def _load(self, job_id):
        if not self._db_path:
            return None
        with sqlite3.connect(self._db_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM qc_batch_analysis_job WHERE job_id=?",(job_id,)).fetchone()
        if not row:
            return None
        job=dict(row)
        job["items"]=json.loads(job.pop("items_json") or "[]")
        raw=job.pop("result_json")
        if raw is not None:
            job["result"]=json.loads(raw)
        return job

    def start(self, knowledge_ids, runner, *, concurrency=2, **kwargs):
        job_id = "BAJ-" + uuid.uuid4().hex
        job = {
            "job_id": job_id, "status": "QUEUED", "total": len(knowledge_ids),
            "completed": 0, "failed": 0, "processed": 0, "concurrency": concurrency,
            "created_at": datetime.now().isoformat(timespec="seconds"), "items": [],
        }
        with self._lock:
            self._jobs[job_id] = job
            self._persist(job)

        def progress(item):
            with self._lock:
                current = self._jobs[job_id]
                current["processed"] += 1
                current["completed"] += int(item.get("status") == "COMPLETED")
                current["failed"] += int(item.get("status") == "FAILED")
                current["items"].append({k: item.get(k) for k in ("knowledge_id", "status", "failed_stage", "error", "duration_ms")})
                self._persist(current)

        def execute():
            with self._lock:
                self._jobs[job_id]["status"] = "RUNNING"
                self._jobs[job_id]["started_at"] = datetime.now().isoformat(timespec="seconds")
                self._persist(self._jobs[job_id])
            try:
                result = runner(knowledge_ids, concurrency=concurrency, progress_callback=progress, **kwargs)
                with self._lock:
                    self._jobs[job_id].update({
                        "status": "COMPLETED" if not result.get("failed") else "PARTIAL",
                        "result": result, "completed_at": datetime.now().isoformat(timespec="seconds"),
                    })
                    self._persist(self._jobs[job_id])
            except Exception as error:
                with self._lock:
                    self._jobs[job_id].update({
                        "status": "FAILED", "error": str(error),
                        "completed_at": datetime.now().isoformat(timespec="seconds"),
                    })
                    self._persist(self._jobs[job_id])

        threading.Thread(target=execute, name=job_id, daemon=True).start()
        return dict(job)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                return dict(job)
        return self._load(job_id)
