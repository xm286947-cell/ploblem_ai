"""W3-02: durable cross-process case capacity for one shared Workbench SQLite DB.

This is a bounded CASE scheduler gate, not an Agent Runtime or Provider.
Slots have no automatic TTL reaping: an unknown in-flight Provider outcome must
not silently be replayed or oversubscribed after a process crash.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Event, Thread
from typing import Any, Callable, Iterator
from uuid import uuid4


MAX_ACTIVE_CASES = 4


class W3BatchCancelled(RuntimeError):
    code = "W3_BATCH_CANCELLED"


class W3CapacityTimeout(RuntimeError):
    code = "W3_CAPACITY_WAIT_TIMEOUT"


class HardwareW3CapacityGate:
    """Atomic leases shared by all processes using the *same* Workbench DB."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).resolve()
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hardware_w3_case_lease (
                    token TEXT PRIMARY KEY,
                    slot INTEGER NOT NULL UNIQUE CHECK(slot BETWEEN 0 AND 3),
                    item_id TEXT NOT NULL UNIQUE,
                    source_key TEXT UNIQUE,
                    acquired_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL
                )
                """
            )
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @staticmethod
    def source_key(item: dict[str, Any]) -> str | None:
        case_id = str(item.get("business_case_id") or "").strip()
        source_id = str(item.get("source_id") or "").strip()
        if not case_id or not source_id:
            return None
        return hashlib.sha256(f"{case_id}\n{source_id}".encode("utf-8")).hexdigest()

    def _try_acquire(self, item_id: str, source_key: str | None) -> str | None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT slot,item_id,source_key FROM hardware_w3_case_lease"
            ).fetchall()
            if any(
                row["item_id"] == item_id
                or (source_key is not None and row["source_key"] == source_key)
                for row in rows
            ):
                connection.rollback()
                return None
            occupied = {int(row["slot"]) for row in rows}
            available = next(
                (slot for slot in range(MAX_ACTIVE_CASES) if slot not in occupied),
                None,
            )
            if available is None:
                connection.rollback()
                return None
            token = uuid4().hex
            now = time.time()
            connection.execute(
                "INSERT INTO hardware_w3_case_lease("
                "token,slot,item_id,source_key,acquired_at,heartbeat_at"
                ") VALUES(?,?,?,?,?,?)",
                (token, available, item_id, source_key, now, now),
            )
            connection.commit()
            return token

    def heartbeat(self, token: str) -> bool:
        with closing(self._connect()) as connection:
            updated = connection.execute(
                "UPDATE hardware_w3_case_lease SET heartbeat_at=? WHERE token=?",
                (time.time(), token),
            )
            connection.commit()
            return updated.rowcount == 1

    def release(self, token: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "DELETE FROM hardware_w3_case_lease WHERE token=?", (token,)
            )
            connection.commit()

    @contextmanager
    def lease(
        self,
        item: dict[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
        max_wait_seconds: float = 300,
        poll_seconds: float = 0.05,
    ) -> Iterator[None]:
        """Acquire capacity before any Provider call; fail closed on timeout."""
        item_id = str(item["item_id"])
        stop_at = time.monotonic() + max_wait_seconds
        token = None
        while token is None:
            if cancelled is not None and cancelled():
                raise W3BatchCancelled()
            token = self._try_acquire(item_id, self.source_key(item))
            if token is not None:
                break
            if time.monotonic() >= stop_at:
                raise W3CapacityTimeout()
            time.sleep(min(poll_seconds, max(0.0, stop_at - time.monotonic())))

        stop_heartbeat = Event()

        def beat() -> None:
            while not stop_heartbeat.wait(2.0):
                try:
                    if not self.heartbeat(token):
                        return
                except sqlite3.Error:
                    # Never reclaim a slot merely because a heartbeat failed.
                    # Explicit stop/reconcile is the only release after crash.
                    pass

        thread = Thread(target=beat, daemon=True, name="hardware-w3-lease-heartbeat")
        thread.start()
        try:
            yield
        finally:
            stop_heartbeat.set()
            thread.join(timeout=3)
            self.release(token)

    def leases(self) -> list[dict[str, Any]]:
        """Read-only operations view; never exposes secrets/Provider contents."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT slot,item_id,acquired_at,heartbeat_at "
                "FROM hardware_w3_case_lease ORDER BY slot"
            ).fetchall()
            return [dict(row) for row in rows]

    def reconcile_confirmed_stopped(
        self,
        batch_id: str,
        *,
        confirmed_stopped: bool,
        stale_seconds: float = 120,
    ) -> list[str]:
        """Operator-only crash recovery: no Provider replay, no auto retry.

        Caller must independently confirm old workers stopped. A recent
        heartbeat blocks reclamation even if this flag is true. The affected
        cases become RUNTIME_BLOCKED requiring explicit reconciliation.
        """
        if not confirmed_stopped:
            raise ValueError("W3_WORKER_STOP_CONFIRMATION_REQUIRED")
        if stale_seconds < 1:
            raise ValueError("W3_STALE_GRACE_INVALID")
        cutoff = time.time() - stale_seconds
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT i.item_id,l.token
                FROM hardware_r1_batch_item i
                JOIN hardware_w3_case_lease l ON l.item_id=i.item_id
                WHERE i.batch_id=? AND i.orchestration_status='RUNNING'
                  AND l.heartbeat_at < ?
                """,
                (batch_id, cutoff),
            ).fetchall()
            recovered: list[str] = []
            for row in rows:
                connection.execute(
                    """
                    UPDATE hardware_r1_batch_item
                    SET orchestration_status='RUNTIME_BLOCKED',
                        error_code='W3_INTERRUPTED_REQUIRES_RECONCILIATION',
                        updated_at=?
                    WHERE item_id=? AND orchestration_status='RUNNING'
                    """,
                    (str(time.time()), row["item_id"]),
                )
                connection.execute(
                    "DELETE FROM hardware_w3_case_lease WHERE token=?",
                    (row["token"],),
                )
                recovered.append(str(row["item_id"]))
            connection.commit()
        return recovered
