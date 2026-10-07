"""Shared canonical Major Case/Event identity resolution for source intake."""
from __future__ import annotations

from typing import Any, Iterable
import sqlite3
import uuid

from quality_knowledge.problem_refs import SourceProblemItrRefV1, normalize_itr


IDENTITY_CONFLICT = "CASE_IDENTITY_CONFLICT"


class MajorCaseIdentityConflict(ValueError):
    """Identity evidence points at multiple Cases or Events."""

    code = IDENTITY_CONFLICT

    def __init__(self) -> None:
        super().__init__(self.code)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _canonical_itrs(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        canonical = normalize_itr(value)
        if canonical and canonical not in result:
            result.append(canonical)
    return result


class MajorCaseIdentityResolver:
    """Resolves and binds Case identity aliases in the existing Major schema.

    Case ownership can be established by a registered identity alias or by an
    existing Event with the same group-scoped canonical ITR. The resolver
    validates all ownership evidence before creating a Case or binding aliases.
    It deliberately does not merge Cases or infer identity from titles/files.
    """

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    @staticmethod
    def _aliases(
        *,
        igr: str,
        source_key: str,
        canonical_itrs: list[str],
    ) -> list[tuple[str, str]]:
        aliases: list[tuple[str, str]] = []
        if source_key:
            aliases.append(("SOURCE_KEY", source_key))
        if igr:
            aliases.append(("IGR", igr))
        aliases.extend(("ITR", itr) for itr in canonical_itrs)
        return list(dict.fromkeys(aliases))

    @staticmethod
    def _owning_case_ids(
        connection: sqlite3.Connection,
        *,
        group_code: str,
        aliases: list[tuple[str, str]],
        canonical_itrs: list[str],
    ) -> set[str]:
        owners: set[str] = set()
        for identity_type, identity_value in aliases:
            row = connection.execute(
                """SELECT case_id FROM kb_case_identity
                   WHERE group_code=? AND identity_type=? AND identity_value=?""",
                (group_code, identity_type, identity_value),
            ).fetchone()
            if row:
                owners.add(str(row["case_id"]))

        if canonical_itrs:
            rows = connection.execute(
                """SELECT case_id,standard_itr FROM kb_event
                   WHERE group_code=? AND standard_itr<>''""",
                (group_code,),
            ).fetchall()
            itr_set = set(canonical_itrs)
            owners.update(
                str(row["case_id"])
                for row in rows
                if normalize_itr(row["standard_itr"]) in itr_set
            )
        return owners

    @staticmethod
    def _assert_unique_itr_events(
        connection: sqlite3.Connection,
        *,
        group_code: str,
        canonical_itrs: list[str],
    ) -> None:
        if not canonical_itrs:
            return
        rows = connection.execute(
            """SELECT case_id,standard_itr FROM kb_event
               WHERE group_code=? AND standard_itr<>''""",
            (group_code,),
        ).fetchall()
        itr_set = set(canonical_itrs)
        counts: dict[str, int] = {}
        for row in rows:
            itr = normalize_itr(row["standard_itr"])
            if itr in itr_set:
                counts[itr] = counts.get(itr, 0) + 1
        if any(count > 1 for count in counts.values()):
            raise MajorCaseIdentityConflict()

    @staticmethod
    def _resolve_or_create_event_in_transaction(
        connection: sqlite3.Connection,
        case: sqlite3.Row | dict,
        *,
        standard_itr: str = "",
        internal_event_key: str = "",
        title: str = "",
    ) -> dict:
        canonical_itr = (
            SourceProblemItrRefV1.from_input(standard_itr).public_ref
            if str(standard_itr or "").strip()
            else ""
        )
        if canonical_itr:
            rows = connection.execute(
                """SELECT * FROM kb_event
                   WHERE group_code=? AND standard_itr<>''""",
                (case["group_code"],),
            ).fetchall()
            matching = [
                dict(row)
                for row in rows
                if normalize_itr(row["standard_itr"]) == canonical_itr
            ]
            if len(matching) > 1 or any(item["case_id"] != case["case_id"] for item in matching):
                raise MajorCaseIdentityConflict()
            if matching:
                return matching[0]

        event_key = str(internal_event_key or canonical_itr or "").strip() or _id("INTERNAL")
        existing = connection.execute(
            "SELECT * FROM kb_event WHERE case_id=? AND internal_event_key=?",
            (case["case_id"], event_key),
        ).fetchone()
        if existing:
            if canonical_itr and normalize_itr(existing["standard_itr"]) != canonical_itr:
                raise MajorCaseIdentityConflict()
            return dict(existing)

        event_id = _id("KEVT")
        connection.execute(
            """INSERT INTO kb_event(
                 event_id,case_id,standard_itr,internal_event_key,event_title,group_code)
               VALUES(?,?,?,?,?,?)""",
            (event_id, case["case_id"], canonical_itr, event_key, str(title or ""), case["group_code"]),
        )
        row = connection.execute(
            "SELECT * FROM kb_event WHERE event_id=?",
            (event_id,),
        ).fetchone()
        return dict(row)

    def inspect(
        self,
        *,
        group_code: str,
        igr: str = "",
        source_key: str = "",
        standard_itrs: Iterable[Any] = (),
    ) -> dict | None:
        """Read-only ownership check; raises on split identity ownership."""

        group_code = str(group_code or "").strip()
        igr = str(igr or "").strip()
        source_key = str(source_key or "").strip()
        canonical_itrs = _canonical_itrs(standard_itrs)
        aliases = self._aliases(
            igr=igr,
            source_key=source_key,
            canonical_itrs=canonical_itrs,
        )
        with self.repository.connect() as connection:
            self._assert_unique_itr_events(
                connection,
                group_code=group_code,
                canonical_itrs=canonical_itrs,
            )
            owners = self._owning_case_ids(
                connection,
                group_code=group_code,
                aliases=aliases,
                canonical_itrs=canonical_itrs,
            )
            if len(owners) > 1:
                raise MajorCaseIdentityConflict()
            if not owners:
                return None
            row = connection.execute(
                "SELECT * FROM kb_case WHERE case_id=? AND group_code=?",
                (next(iter(owners)), group_code),
            ).fetchone()
        if not row:
            raise MajorCaseIdentityConflict()
        return dict(row)

    def resolve_or_create_case(
        self,
        *,
        group_code: str,
        title: str,
        domain: str = "",
        igr: str = "",
        source_key: str = "",
        standard_itrs: Iterable[Any] = (),
        legacy_case_id: str = "",
    ) -> dict[str, Any]:
        """Resolve one owning Case or create it, then atomically bind aliases."""

        group_code = str(group_code or "").strip()
        title = str(title or "").strip()
        domain = str(domain or "").strip()
        igr = str(igr or "").strip()
        source_key = str(source_key or "").strip()
        canonical_itrs = _canonical_itrs(standard_itrs)
        aliases = self._aliases(
            igr=igr,
            source_key=source_key,
            canonical_itrs=canonical_itrs,
        )
        with self.repository.transaction() as connection:
            self._assert_unique_itr_events(
                connection,
                group_code=group_code,
                canonical_itrs=canonical_itrs,
            )
            owners = self._owning_case_ids(
                connection,
                group_code=group_code,
                aliases=aliases,
                canonical_itrs=canonical_itrs,
            )
            if len(owners) > 1:
                raise MajorCaseIdentityConflict()

            created = not owners
            if owners:
                case_id = next(iter(owners))
                case_row = connection.execute(
                    "SELECT * FROM kb_case WHERE case_id=? AND group_code=?",
                    (case_id, group_code),
                ).fetchone()
                if not case_row:
                    raise MajorCaseIdentityConflict()
                existing_igrs = {
                    str(row["identity_value"])
                    for row in connection.execute(
                        """SELECT identity_value FROM kb_case_identity
                           WHERE case_id=? AND group_code=? AND identity_type='IGR'""",
                        (case_id, group_code),
                    )
                }
                if igr and existing_igrs and igr not in existing_igrs:
                    raise MajorCaseIdentityConflict()
            else:
                if not group_code or not title:
                    raise ValueError("CASE_TITLE_AND_GROUP_REQUIRED")
                case_id = _id("KCASE")
                connection.execute(
                    """INSERT INTO kb_case(
                         case_id,case_type,title,domain,group_code,legacy_case_id)
                       VALUES(?,'MAJOR_REVIEW',?,?,?,?)""",
                    (case_id, title, domain, group_code, str(legacy_case_id or "").strip()),
                )

            # Recheck each unique alias inside the write transaction before
            # binding; no other Case can claim an alias between lookup/write.
            for identity_type, identity_value in aliases:
                owner = connection.execute(
                    """SELECT case_id FROM kb_case_identity
                       WHERE group_code=? AND identity_type=? AND identity_value=?""",
                    (group_code, identity_type, identity_value),
                ).fetchone()
                if owner and str(owner["case_id"]) != case_id:
                    raise MajorCaseIdentityConflict()

            primary_alias = ("IGR", igr) if igr else (("SOURCE_KEY", source_key) if source_key else None)
            existing_primary = connection.execute(
                "SELECT identity_type,identity_value FROM kb_case_identity WHERE case_id=? AND is_primary=1 LIMIT 1",
                (case_id,),
            ).fetchone()
            promote_primary = bool(primary_alias) and (
                created
                or not existing_primary
                or primary_alias[0] == "IGR"
            )
            if promote_primary:
                connection.execute(
                    "UPDATE kb_case_identity SET is_primary=0 WHERE case_id=?",
                    (case_id,),
                )

            for identity_type, identity_value in aliases:
                is_primary = int(promote_primary and (identity_type, identity_value) == primary_alias)
                connection.execute(
                    """INSERT OR IGNORE INTO kb_case_identity(
                         identity_id,case_id,group_code,identity_type,identity_value,is_primary)
                       VALUES(?,?,?,?,?,?)""",
                    (_id("KID"), case_id, group_code, identity_type, identity_value, is_primary),
                )
                if is_primary:
                    connection.execute(
                        """UPDATE kb_case_identity SET is_primary=1
                           WHERE case_id=? AND group_code=? AND identity_type=? AND identity_value=?""",
                        (case_id, group_code, identity_type, identity_value),
                    )

            case_row = connection.execute(
                "SELECT * FROM kb_case WHERE case_id=?",
                (case_id,),
            ).fetchone()
            events = [
                self._resolve_or_create_event_in_transaction(
                    connection,
                    case_row,
                    standard_itr=itr,
                    internal_event_key=itr,
                    title=itr,
                )
                for itr in canonical_itrs
            ]
            return {
                "case": dict(case_row),
                "events": events,
                "created": created,
                "reused": not created,
            }

    def bind_identity(
        self,
        case_id: str,
        group_code: str,
        identity_type: str,
        value: str,
        *,
        primary: bool = False,
    ) -> None:
        value = str(value or "").strip()
        if not value:
            return
        with self.repository.transaction() as connection:
            case = connection.execute(
                "SELECT case_id FROM kb_case WHERE case_id=? AND group_code=?",
                (case_id, group_code),
            ).fetchone()
            if not case:
                raise KeyError(case_id)
            if identity_type == "IGR":
                existing = connection.execute(
                    """SELECT identity_value FROM kb_case_identity
                       WHERE case_id=? AND group_code=? AND identity_type='IGR'""",
                    (case_id, group_code),
                ).fetchone()
                if existing and existing["identity_value"] != value:
                    raise MajorCaseIdentityConflict()
            owner = connection.execute(
                """SELECT case_id FROM kb_case_identity
                   WHERE group_code=? AND identity_type=? AND identity_value=?""",
                (group_code, identity_type, value),
            ).fetchone()
            if owner and str(owner["case_id"]) != case_id:
                raise MajorCaseIdentityConflict()
            if primary:
                connection.execute(
                    "UPDATE kb_case_identity SET is_primary=0 WHERE case_id=?",
                    (case_id,),
                )
            connection.execute(
                """INSERT INTO kb_case_identity(
                     identity_id,case_id,group_code,identity_type,identity_value,is_primary)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(case_id,identity_type,identity_value)
                   DO UPDATE SET is_primary=MAX(kb_case_identity.is_primary,excluded.is_primary)""",
                (_id("KID"), case_id, group_code, identity_type, value, int(primary)),
            )

    def identity_owner(self, group_code: str, identity_type: str, value: str) -> str | None:
        if not str(value or "").strip():
            return None
        with self.repository.connect() as connection:
            row = connection.execute(
                """SELECT case_id FROM kb_case_identity
                   WHERE group_code=? AND identity_type=? AND identity_value=?""",
                (group_code, identity_type, str(value).strip()),
            ).fetchone()
        return str(row["case_id"]) if row else None

    def resolve_or_create_event(
        self,
        case_id: str,
        *,
        standard_itr: str = "",
        internal_event_key: str = "",
        title: str = "",
    ) -> dict:
        """Reuse the one group-scoped ITR Event, or create it once."""

        canonical_itr = normalize_itr(standard_itr)
        with self.repository.transaction() as connection:
            case = connection.execute(
                "SELECT * FROM kb_case WHERE case_id=?",
                (case_id,),
            ).fetchone()
            if not case:
                raise KeyError(case_id)
            return self._resolve_or_create_event_in_transaction(
                connection,
                case,
                standard_itr=canonical_itr,
                internal_event_key=internal_event_key,
                title=title,
            )
