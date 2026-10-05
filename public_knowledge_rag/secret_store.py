from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path


class SecretStore:
    """Service-local write-only secret storage with environment fallback."""

    ENV_NAME = "OPENAI_COMPATIBLE_API_KEY"
    FIELD = "openai_compatible_api_key"

    def __init__(self, data_dir: Path):
        configured_path = os.getenv("PKR_UI_SECRET_PATH")
        self.path = Path(configured_path) if configured_path else data_dir / "secrets.local.json"

    def local_key(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            if os.name == "posix":
                os.chmod(self.path, 0o600)
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Local provider secret storage cannot be read safely.") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Local provider secret storage has an invalid format.")
        key = value.get(self.FIELD)
        if key is not None and (not isinstance(key, str) or not key.strip()):
            raise RuntimeError("Local provider secret storage has an invalid credential value.")
        return key

    def environment_key(self) -> str | None:
        value = os.getenv(self.ENV_NAME)
        return value if value else None

    def effective_key(self, *, candidate_key: str = "", clear_local: bool = False) -> str | None:
        if candidate_key:
            return candidate_key
        if clear_local:
            return self.environment_key()
        return self.local_key() or self.environment_key()

    def source(self) -> str:
        if self.local_key():
            return "LOCAL_UI_OVERRIDE"
        if self.environment_key():
            return "ENV"
        return "MISSING"

    @staticmethod
    def fingerprint(key: str | None) -> str:
        return hashlib.sha256((key or "").encode("utf-8")).hexdigest()

    def write(self, key: str) -> None:
        if not key:
            raise ValueError("Credential cannot be empty.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp-" + secrets.token_hex(8))
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({self.FIELD: key}, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, self.path)
            if os.name == "posix":
                os.chmod(self.path, 0o600)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
