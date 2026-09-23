from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
import hashlib
import json
import unicodedata


def normalize_scalar(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float, bool)):
        return str(value)
    return str(value).strip()


def normalize_filename(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").strip()
    return " ".join(text.split()).lower()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data: dict, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2 if pretty else None),
        encoding="utf-8",
    )
