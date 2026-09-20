from __future__ import annotations

from runtime.contracts import SourceRef


class SourceIdentityChangedError(Exception):
    code = "SOURCE_IDENTITY_CHANGED"

    def __init__(self, expected: SourceRef, actual: SourceRef):
        super().__init__(
            "source identity changed: "
            f"expected {expected.source_id}/{expected.fingerprint}, "
            f"got {actual.source_id}/{actual.fingerprint}"
        )
        self.expected = expected
        self.actual = actual


class SourceIdentityProvider:
    @staticmethod
    def fingerprint(source: SourceRef) -> str:
        return source.fingerprint

    @staticmethod
    def validate_same_identity(
        expected: SourceRef,
        actual: SourceRef,
    ) -> None:
        if (
            expected.source_id != actual.source_id
            or expected.fingerprint != actual.fingerprint
        ):
            raise SourceIdentityChangedError(expected, actual)
