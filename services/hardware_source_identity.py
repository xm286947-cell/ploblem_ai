"""Deterministic Hardware R1 source identity parsing.

SPIKE02 only: filename + content identity. No Agent, tree mapping, search,
revision semantics, OCR, or vision.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_CASE_NAME = re.compile(r"^\s*(A\d{4,})\s*[-—–_]\s*(.+?)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class HardwareSourceIdentity:
    business_case_id: str | None
    raw_title: str
    source_id: str
    identity_status: str
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "business_case_id": self.business_case_id,
            "raw_title": self.raw_title,
            "source_id": self.source_id,
            "identity_status": self.identity_status,
            "warnings": list(self.warnings),
        }


def parse_source_identity(file_name: str, source_id: str) -> HardwareSourceIdentity:
    """Parse business identity without inventing a business case id.

    Unrecognized filenames remain usable as source documents and are marked
    NEEDS_REVIEW. source_id must already be the SHA256 of the source bytes.
    """
    name = Path(str(file_name or "").replace("\\", "/")).name
    stem = Path(name).stem.strip()
    digest = str(source_id or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("SOURCE_ID_SHA256_REQUIRED")

    warnings: list[str] = []
    match = _CASE_NAME.match(stem)
    if match:
        case_id = match.group(1).upper()
        title = match.group(2).strip()
        if title:
            return HardwareSourceIdentity(case_id, title, digest, "PARSED", ())

    title = stem or name or "UNTITLED_SOURCE"
    warnings.append("BUSINESS_CASE_ID_NOT_DERIVED")
    return HardwareSourceIdentity(
        business_case_id=None,
        raw_title=title,
        source_id=digest,
        identity_status="NEEDS_REVIEW",
        warnings=tuple(warnings),
    )


__all__ = ["HardwareSourceIdentity", "parse_source_identity"]
