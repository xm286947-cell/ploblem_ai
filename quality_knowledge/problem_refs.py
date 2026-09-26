"""Public source-problem/ITR reference contract.

This module is deliberately a thin boundary.  It defines the business
identity used by Major consumers without exposing a repository, database
primary key, or source record identifier.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping


CONTRACT_VERSION = "source-problem-itr-ref/v1"
PUBLIC_REF_TYPE = "ITR"
_ITR_PATTERN = re.compile(r"^ITR[0-9A-Z_-]+$")
_ITR_CS_PATTERN = re.compile(r"^ITR[0-9A-Z_-]+CS$")


def normalize_itr(value: Any) -> str:
    """Return the single canonical spelling for an ITR business key."""

    text = re.sub(r"\s+", "", str(value or "")).upper()
    if _ITR_CS_PATTERN.fullmatch(text):
        text = text[:-2]
    return text


def _is_valid_public_ref(value: str) -> bool:
    return bool(_ITR_PATTERN.fullmatch(value))


class SourceProblemItrRefError(ValueError):
    """Base error with a stable public error code."""

    code = "SOURCE_PROBLEM_ITR_REF_ERROR"

    def __init__(self, message: str | None = None):
        super().__init__(message or self.code)


class InvalidSourceProblemItrRef(SourceProblemItrRefError):
    code = "INVALID_REF"


class SourceProblemItrNotFound(SourceProblemItrRefError):
    code = "REF_VALID_BUT_SOURCE_NOT_FOUND"


@dataclass(frozen=True, slots=True)
class SourceProblemItrRefV1:
    """Public DTO for one existing Problem / ITR identity."""

    public_ref: str
    source_status: str = "RESOLVED"
    source_refs: tuple[str, ...] = ()
    contract_version: str = CONTRACT_VERSION
    ref_type: str = PUBLIC_REF_TYPE

    def __post_init__(self) -> None:
        canonical = normalize_itr(self.public_ref)
        if not _is_valid_public_ref(canonical):
            raise InvalidSourceProblemItrRef()
        if self.contract_version != CONTRACT_VERSION or self.ref_type != PUBLIC_REF_TYPE:
            raise ValueError("SOURCE_PROBLEM_ITR_REF_CONTRACT_MISMATCH")
        object.__setattr__(self, "public_ref", canonical)
        refs = tuple(normalize_itr(value) for value in (self.source_refs or (canonical,)))
        if not refs or any(not _is_valid_public_ref(value) for value in refs):
            raise InvalidSourceProblemItrRef()
        object.__setattr__(self, "source_refs", refs)

    @property
    def canonical_itr(self) -> str:
        return self.public_ref

    @classmethod
    def from_input(cls, value: Any) -> "SourceProblemItrRefV1":
        canonical = normalize_itr(value)
        if not _is_valid_public_ref(canonical):
            raise InvalidSourceProblemItrRef()
        return cls(public_ref=canonical, source_refs=(canonical,))

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "ref_type": self.ref_type,
            "public_ref": self.public_ref,
            "canonical_itr": self.canonical_itr,
            "source_status": self.source_status,
            "source_refs": list(self.source_refs),
        }


SourceLookup = Callable[[str], Mapping[str, Any] | None]


class SourceProblemItrRefResolver:
    """Resolve a canonical public ref through an injected public lookup."""

    def __init__(self, source_lookup: SourceLookup | None = None):
        self._source_lookup = source_lookup

    @staticmethod
    def normalize(value: Any) -> str:
        return normalize_itr(value)

    @staticmethod
    def validate(value: Any) -> SourceProblemItrRefV1:
        return SourceProblemItrRefV1.from_input(value)

    def resolve(
        self,
        value: Any,
        *,
        source_lookup: SourceLookup | None = None,
    ) -> SourceProblemItrRefV1:
        ref = SourceProblemItrRefV1.from_input(value)
        lookup = source_lookup or self._source_lookup
        if lookup is None:
            return ref
        source = lookup(ref.public_ref)
        if not source:
            raise SourceProblemItrNotFound()
        return SourceProblemItrRefV1(
            public_ref=ref.public_ref,
            source_status=str(source.get("source_status") or source.get("status") or "RESOLVED").upper(),
            source_refs=(ref.public_ref,),
        )

    def trace(
        self,
        value: Any,
        *,
        source_lookup: SourceLookup | None = None,
    ) -> dict[str, Any]:
        return self.resolve(value, source_lookup=source_lookup).to_dict()


__all__ = [
    "CONTRACT_VERSION",
    "PUBLIC_REF_TYPE",
    "InvalidSourceProblemItrRef",
    "SourceProblemItrNotFound",
    "SourceProblemItrRefError",
    "SourceProblemItrRefResolver",
    "SourceProblemItrRefV1",
    "normalize_itr",
]
