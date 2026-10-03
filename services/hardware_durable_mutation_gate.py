"""Cross-process Durable Mutation Freeze shared by backup and Hardware APIs."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from services.hardware_startup_coordinator import HardwareStartupError, _StartupLock


class HardwareDurableMutationError(RuntimeError):
    def __init__(self, code: str, *, http_status: int = 409):
        self.code = code
        self.http_status = http_status
        super().__init__(code)


class HardwareApplicationLock:
    """Exclusive lifetime lease proving the Hardware application is offline."""

    def __init__(self, data_root: str | Path):
        root = Path(data_root).expanduser().resolve(strict=False)
        # Keep the live-process lock outside the replaceable Data Root so an
        # offline restore can atomically rename the root on Windows as well.
        self.path = root.parent / f".{root.name}.hardware_application.lock"
        self._lock: _StartupLock | None = None

    def acquire(self) -> None:
        if self._lock is not None:
            return
        lock = _StartupLock(self.path)
        try:
            lock.__enter__()
        except HardwareStartupError as error:
            raise HardwareDurableMutationError("HARDWARE_APP_ALREADY_RUNNING") from error
        self._lock = lock

    def release(self) -> None:
        lock, self._lock = self._lock, None
        if lock is not None:
            lock.__exit__(None, None, None)

    def __enter__(self) -> "HardwareApplicationLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()


class HardwareDurableMutationGate:
    """Serialize Hardware writes with freeze transitions across processes.

    A mutation holds the same OS lock as a freeze transition for its full
    request lifetime. FREEZING/FROZEN state is durable, so a crashed backup
    cannot silently reopen writes on restart.
    """

    CONTRACT_VERSION = "hardware-durable-mutation-gate/v1"

    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root).expanduser().resolve(strict=False)
        self.state_path = self.data_root / "manifest" / "durable_mutation_gate.json"
        self.lock_path = self.data_root / "manifest" / "durable_mutation_gate.lock"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            try:
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                # Windows does not support fsync on a directory handle.
                pass
        except OSError as error:
            raise HardwareDurableMutationError("MUTATION_GATE_STATE_WRITE_FAILED", http_status=503) from error
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"contract_version": self.CONTRACT_VERSION, "state": "OPEN"}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise HardwareDurableMutationError("MUTATION_GATE_STATE_INVALID", http_status=503) from error
        if (
            not isinstance(value, dict)
            or value.get("contract_version") != self.CONTRACT_VERSION
            or value.get("state") not in {"OPEN", "FREEZING", "FROZEN"}
        ):
            raise HardwareDurableMutationError("MUTATION_GATE_STATE_INVALID", http_status=503)
        return value

    @contextmanager
    def mutation(self) -> Iterator[None]:
        lock = _StartupLock(self.lock_path)
        try:
            lock.__enter__()
        except HardwareStartupError as error:
            raise HardwareDurableMutationError("DURABLE_MUTATION_BUSY") from error
        try:
            state = self._read_state()
            if state["state"] != "OPEN":
                raise HardwareDurableMutationError("DURABLE_MUTATION_FROZEN")
            yield
        finally:
            lock.__exit__(None, None, None)

    @contextmanager
    def freeze(self, *, operation_id: str, reason: str) -> Iterator[dict[str, Any]]:
        lock = _StartupLock(self.lock_path)
        try:
            lock.__enter__()
        except HardwareStartupError as error:
            raise HardwareDurableMutationError("BACKUP_LOCKED") from error
        try:
            current = self._read_state()
            if current["state"] != "OPEN":
                raise HardwareDurableMutationError("DURABLE_MUTATION_FROZEN", http_status=503)
            freezing = {
                "contract_version": self.CONTRACT_VERSION,
                "state": "FREEZING",
                "operation_id": operation_id,
                "reason": reason,
                "updated_at": self._now(),
            }
            self._write_json(self.state_path, freezing)
            frozen = {**freezing, "state": "FROZEN", "updated_at": self._now()}
            self._write_json(self.state_path, frozen)
        finally:
            lock.__exit__(None, None, None)

        try:
            yield frozen
        finally:
            thaw_lock = _StartupLock(self.lock_path)
            try:
                thaw_lock.__enter__()
                self._write_json(
                    self.state_path,
                    {
                        "contract_version": self.CONTRACT_VERSION,
                        "state": "OPEN",
                        "operation_id": operation_id,
                        "reason": reason,
                        "updated_at": self._now(),
                    },
                )
            finally:
                thaw_lock.__exit__(None, None, None)

    def status(self) -> dict[str, Any]:
        return dict(self._read_state())


__all__ = [
    "HardwareApplicationLock",
    "HardwareDurableMutationError",
    "HardwareDurableMutationGate",
]
