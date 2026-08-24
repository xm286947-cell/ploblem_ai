"""Clean P0 quality-capability insight aggregation and server-side drill-down."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable


class P0InsightError(RuntimeError):
    """Stable insight-service error without any legacy result fallback."""


@dataclass(frozen=True)
class ScopeRecord:
    knowledge_id: str
    business_issue_id: str
    product_id: str | None
    product_code: str | None
    business_type: str
    issue_version_id: str
    analysis_set_id: str | None
    analysis_status: str | None
    snapshot: dict[str, Any]
    stage_results: dict[str, Any]
    tags: list[dict[str, Any]]
    mrc: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    source_types: list[str]
    human_answers: list[dict[str, Any]]


class P0InsightService:
    """Aggregates only the clean P0 V2 projection tables and active scoring seed."""

    _VALID_FILTERS = {
        "product", "business_type", "month", "domain", "lifecycle",
        "issue_domain", "lifecycle_phase",
    }
    _SOURCE_BUCKETS = ("SOURCE_DATA", "AI_STANDARDIZED", "AI_INFERRED", "HUMAN_CONFIRMED")

    def __init__(self, repository: Any):
        self.repository = repository

    def overview(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_filters = self._normalize_filters(filters)
        scoring = self.repository.get_active_scoring_version()
        records = self._scope_records(normalized_filters)
        scope_hash = self._scope_hash(normalized_filters, scoring, records)
        contradictions = self._contradictions(records, scoring, scope_hash)
        return {
            "analysis_scope_hash": scope_hash,
            "scope": normalized_filters,
            "scoring_version": self._scoring_metadata(scoring),
            "scoring_methodology": {
                "components": scoring["weights_json"],
                "aggregation": "每个能力缺口按问题计算七分量加权分，按矛盾聚合平均分；同一问题同一矛盾只计一次。",
            },
            "quality_engineering_top3": contradictions["QUALITY_ENGINEERING"][:3],
            "quality_management_top3": contradictions["QUALITY_MANAGEMENT"][:3],
            "matrices": {
                "mrc_x_capability": self._mrc_capability_matrix(records),
                "lifecycle_x_capability": self._lifecycle_capability_matrix(records),
            },
            "coverage": self._coverage(records),
        }

    def business_contradictions(self, filters: dict[str, Any]) -> dict[str, Any]:
        """Stable P0 API-facing name for the scoped contradiction dashboard."""

        return self.overview(filters)

    def drill_down(
        self,
        contradiction_key: str,
        analysis_scope_hash: str,
        *,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Server-side exact drill-down; callers never provide flat issue IDs."""

        if page < 1 or page_size < 1 or page_size > 200:
            raise P0InsightError("INSIGHT_PAGINATION_INVALID")
        normalized_filters = self._normalize_filters(filters)
        scoring = self.repository.get_active_scoring_version()
        records = self._scope_records(normalized_filters)
        current_hash = self._scope_hash(normalized_filters, scoring, records)
        if current_hash != analysis_scope_hash:
            raise P0InsightError("INSIGHT_SCOPE_CHANGED")
        axis, capability_code, governance_scope = self._parse_contradiction_key(contradiction_key)
        selected: list[dict[str, Any]] = []
        for record in records:
            if record.analysis_status != "COMPLETED":
                continue
            for gap in self._effective_gaps(record):
                if (
                    gap["capability_axis"] == axis
                    and gap["capability_code"] == capability_code
                    and gap["governance_scope"] == governance_scope
                ):
                    selected.append(self._drill_item(record, gap))
                    break
        selected.sort(key=lambda item: (item["business_issue_id"], item["knowledge_id"]))
        start = (page - 1) * page_size
        return {
            "contradiction_key": contradiction_key,
            "analysis_scope_hash": current_hash,
            "total": len(selected),
            "page": page,
            "page_size": page_size,
            "items": selected[start:start + page_size],
        }

    def drilldown(
        self,
        contradiction_key: str,
        analysis_scope_hash: str,
        filters: dict[str, Any],
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Stable P0 API-facing exact drill-down without client-side issue IDs."""

        return self.drill_down(
            contradiction_key,
            analysis_scope_hash,
            filters=filters,
            page=page,
            page_size=page_size,
        )

    def _scope_records(self, filters: dict[str, Any]) -> list[ScopeRecord]:
        """Load every current issue in scope; no implicit 100/1000-row limit."""

        with self.repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT issue.knowledge_id, issue.business_issue_id, issue.product_id,
                       product.product_code, mapping.business_type, version.issue_version_id,
                       snapshot.snapshot_json, analysis.analysis_set_id, analysis.status
                  FROM quality_issue AS issue
                  JOIN quality_issue_version AS version
                    ON version.issue_version_id = issue.current_version_id
                  JOIN issue_normalized_snapshot AS snapshot
                    ON snapshot.issue_version_id = version.issue_version_id
                  JOIN mapping_config AS mapping ON mapping.config_id = version.mapping_config_id
                  LEFT JOIN product_config AS product ON product.product_id = issue.product_id
                  LEFT JOIN analysis_set AS analysis ON analysis.analysis_set_id = (
                        SELECT candidate.analysis_set_id FROM analysis_set AS candidate
                         WHERE candidate.issue_version_id = version.issue_version_id
                         ORDER BY candidate.rowid DESC LIMIT 1
                  )
                 ORDER BY issue.knowledge_id
                """
            ).fetchall()
            results: list[ScopeRecord] = []
            for row in rows:
                snapshot = json.loads(row["snapshot_json"])
                if not self._matches_filters(row, snapshot, filters, connection):
                    continue
                analysis_set_id = row["analysis_set_id"]
                if analysis_set_id is None:
                    results.append(ScopeRecord(
                        knowledge_id=row["knowledge_id"], business_issue_id=row["business_issue_id"],
                        product_id=row["product_id"], product_code=row["product_code"],
                        business_type=row["business_type"], issue_version_id=row["issue_version_id"],
                        analysis_set_id=None, analysis_status=None, snapshot=snapshot,
                        stage_results={}, tags=[], mrc=[], gaps=[], source_types=[], human_answers=[],
                    ))
                    continue
                results.append(self._record_with_analysis(connection, row, snapshot))
        return results

    def _matches_filters(self, row: Any, snapshot: dict[str, Any], filters: dict[str, Any], connection: Any) -> bool:
        product = filters.get("product")
        if product and product not in {row["product_id"], row["product_code"]}:
            return False
        if filters.get("business_type") and filters["business_type"] != row["business_type"]:
            return False
        issue_fact = snapshot.get("ISSUE_FACT", {})
        if filters.get("month") and str(issue_fact.get("month", "")) != filters["month"]:
            return False
        analysis_set_id = row["analysis_set_id"]
        if analysis_set_id and filters.get("domain"):
            has_domain = connection.execute(
                """SELECT 1 FROM issue_analysis_tag WHERE analysis_set_id = ? AND axis = 'DOMAIN'
                     AND tag_code = ? LIMIT 1""",
                (analysis_set_id, filters["domain"]),
            ).fetchone()
            if has_domain is None:
                return False
        elif filters.get("domain"):
            return False
        if analysis_set_id and filters.get("lifecycle"):
            has_lifecycle = connection.execute(
                """SELECT 1 FROM issue_analysis_tag WHERE analysis_set_id = ? AND axis = 'LIFECYCLE'
                     AND tag_code = ? LIMIT 1""",
                (analysis_set_id, filters["lifecycle"]),
            ).fetchone()
            if has_lifecycle is None:
                return False
        elif filters.get("lifecycle"):
            return False
        return True

    def _record_with_analysis(self, connection: Any, row: Any, snapshot: dict[str, Any]) -> ScopeRecord:
        analysis_set_id = row["analysis_set_id"]
        stages = {
            item["stage"]: json.loads(item["parsed_result_json"])
            for item in connection.execute(
                """SELECT stage, parsed_result_json FROM analysis_stage_run
                     WHERE analysis_set_id = ? AND status = 'COMPLETED'
                       AND parsed_result_json IS NOT NULL""",
                (analysis_set_id,),
            )
        }
        tags = [dict(item) for item in connection.execute(
            "SELECT * FROM issue_analysis_tag WHERE analysis_set_id = ?", (analysis_set_id,)
        )]
        mrc = [dict(item) for item in connection.execute(
            "SELECT * FROM issue_mrc WHERE analysis_set_id = ?", (analysis_set_id,)
        )]
        gaps = [self._decode_gap(item) for item in connection.execute(
            "SELECT * FROM issue_capability_gap WHERE analysis_set_id = ?", (analysis_set_id,)
        )]
        source_types = [item[0] for item in connection.execute(
            """SELECT source_type FROM issue_analysis_value WHERE analysis_set_id = ?
               UNION ALL SELECT source_type FROM issue_analysis_tag WHERE analysis_set_id = ?
               UNION ALL SELECT source_type FROM issue_mrc WHERE analysis_set_id = ?
               UNION ALL SELECT source_type FROM issue_capability_gap WHERE analysis_set_id = ?""",
            (analysis_set_id, analysis_set_id, analysis_set_id, analysis_set_id),
        )]
        revision = connection.execute(
            """SELECT human_revision_id FROM human_analysis_revision
                 WHERE base_analysis_set_id = ? AND status = 'CONFIRMED'
                 ORDER BY revision_no DESC LIMIT 1""",
            (analysis_set_id,),
        ).fetchone()
        human_answers = []
        if revision:
            human_answers = [
                {**dict(answer), "confirmed_value_json": json.loads(answer["confirmed_value_json"])}
                for answer in connection.execute(
                    """SELECT * FROM human_analysis_answer
                         WHERE human_revision_id = ?
                           AND confirmation_status IN ('CONFIRMED', 'CORRECTED')""",
                    (revision["human_revision_id"],),
                )
            ]
            source_types.extend("HUMAN_CONFIRMED" for _ in human_answers)
        return ScopeRecord(
            knowledge_id=row["knowledge_id"], business_issue_id=row["business_issue_id"],
            product_id=row["product_id"], product_code=row["product_code"],
            business_type=row["business_type"], issue_version_id=row["issue_version_id"],
            analysis_set_id=analysis_set_id, analysis_status=row["status"], snapshot=snapshot,
            stage_results=stages, tags=tags, mrc=mrc, gaps=gaps,
            source_types=source_types, human_answers=human_answers,
        )

    @staticmethod
    def _decode_gap(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["details_json"] = json.loads(result["details_json"])
        return result

    def _contradictions(self, records: list[ScopeRecord], scoring: dict[str, Any], scope_hash: str) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[tuple[str, str, str], list[tuple[ScopeRecord, dict[str, Any]]]] = defaultdict(list)
        for record in records:
            if record.analysis_status != "COMPLETED":
                continue
            for gap in self._effective_gaps(record):
                grouped[(gap["capability_axis"], gap["capability_code"], gap["governance_scope"])].append((record, gap))
        output = {"QUALITY_ENGINEERING": [], "QUALITY_MANAGEMENT": []}
        labels = self._capability_labels()
        total_sample_count = len(records)
        completed_analysis_count = sum(record.analysis_status == "COMPLETED" for record in records)
        for (axis, code, governance_scope), items in grouped.items():
            component_rows = [self._components(record, gap) for record, gap in items]
            breakdown = self._average_components(component_rows, scoring["weights_json"])
            key = self._contradiction_key(axis, code, governance_scope)
            unique_records = {record.knowledge_id: record for record, _ in items}
            control_distribution: dict[str, int] = defaultdict(int)
            for _, gap in items:
                control_distribution[gap["control_status"]] += 1
            output[axis].append({
                "contradiction_key": key,
                "analysis_scope_hash": scope_hash,
                "capability_axis": axis,
                "capability_code": code,
                "capability_label_zh": labels.get((axis, code), code),
                "governance_scope": governance_scope,
                "issue_count": len(unique_records),
                "total_sample_count": total_sample_count,
                "completed_analysis_count": completed_analysis_count,
                "source_coverage_rate": self._source_coverage_rate(unique_records.values()),
                "human_confirmation_rate": self._human_confirmation_rate(unique_records.values()),
                "control_status_distribution": dict(sorted(control_distribution.items())),
                "score": breakdown["total_weighted_score"],
                "component_breakdown": breakdown,
            })
        for axis in output:
            output[axis].sort(key=lambda item: (-item["score"], -item["issue_count"], item["contradiction_key"]))
        return output

    def _components(self, record: ScopeRecord, gap: dict[str, Any]) -> dict[str, float]:
        issue_fact = record.snapshot.get("ISSUE_FACT", {})
        severity = self._severity_score(issue_fact.get("severity"))
        recurrence = self._recurrence_score(record.stage_results.get("recurrence", {}))
        customer_impact = self._evidence_value_score(record.stage_results.get("recurrence", {}).get("customer_impact"))
        control = self._control_score(gap.get("control_status"))
        capability_gap = {"P0": 1.0, "P1": 0.65, "P2": 0.35}.get(gap["details_json"].get("priority"), 0.5)
        evidence_confidence = float(gap.get("confidence") or 0.0)
        consistency = {"CONFIRMED": 1.0, "PENDING_CONFIRMATION": 0.5}.get(
            self._analysis_consistency(record), 0.25
        )
        return {
            "severity": severity,
            "recurrence": recurrence,
            "customer_impact": customer_impact,
            "control_effectiveness": control,
            "capability_gap": capability_gap,
            "evidence_confidence": evidence_confidence,
            "classification_consistency": consistency,
        }

    @staticmethod
    def _severity_score(value: Any) -> float:
        return {"H": 1.0, "HIGH": 1.0, "M": 0.65, "MEDIUM": 0.65, "L": 0.35, "LOW": 0.35}.get(
            str(value or "").upper(), 0.5
        )

    @staticmethod
    def _recurrence_score(result: dict[str, Any]) -> float:
        return {"HIGH": 1.0, "MEDIUM": 0.65, "LOW": 0.35}.get(result.get("recurrence_risk_level"), 0.5)

    @staticmethod
    def _evidence_value_score(value: Any) -> float:
        if not isinstance(value, dict) or not value.get("value"):
            return 0.5
        return min(1.0, max(0.0, float(value.get("confidence") or 0.5)))

    @staticmethod
    def _control_score(status: str | None) -> float:
        return {
            "NOT_DEFINED": 1.0, "DEFINED_NOT_EXECUTED": 0.85, "EXECUTED_INSUFFICIENT": 0.70,
            "EFFECT_NOT_VERIFIED": 0.55, "EFFECTIVE": 0.10, "UNKNOWN": 0.50,
        }.get(status or "UNKNOWN", 0.50)

    @staticmethod
    def _analysis_consistency(record: ScopeRecord) -> str:
        # A confirmed human revision is evidence of reviewed consistency.
        return "CONFIRMED" if record.human_answers else "PENDING_CONFIRMATION"

    @staticmethod
    def _average_components(rows: list[dict[str, float]], weights: dict[str, float]) -> dict[str, Any]:
        if not rows:
            return {"components": {}, "weighted_components": {}, "total_weighted_score": 0.0}
        components = {key: sum(row[key] for row in rows) / len(rows) for key in weights}
        weighted = {key: components[key] * float(weights[key]) for key in weights}
        return {
            "components": components,
            "weighted_components": weighted,
            "total_weighted_score": sum(weighted.values()),
        }

    def _mrc_capability_matrix(self, records: Iterable[ScopeRecord]) -> list[dict[str, Any]]:
        cells: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for record in records:
            if record.analysis_status != "COMPLETED":
                continue
            primary_mrc = {
                item["mrc_code"]
                for item in self._effective_mrc(record)
                if item["role"] == "PRIMARY"
            }
            for gap in self._effective_gaps(record):
                for code in primary_mrc:
                    cells[(code, gap["capability_axis"], gap["capability_code"])].add(record.knowledge_id)
        return [
            {"mrc_code": mrc, "capability_axis": axis, "capability_code": code, "issue_count": len(issues)}
            for (mrc, axis, code), issues in sorted(cells.items())
        ]

    def _lifecycle_capability_matrix(self, records: Iterable[ScopeRecord]) -> list[dict[str, Any]]:
        cells: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for record in records:
            if record.analysis_status != "COMPLETED":
                continue
            lifecycle = {tag["tag_code"] for tag in record.tags if tag["axis"] == "LIFECYCLE"}
            for gap in self._effective_gaps(record):
                for stage in lifecycle:
                    cells[(stage, gap["capability_axis"], gap["capability_code"])].add(record.knowledge_id)
        return [
            {"lifecycle_code": lifecycle, "capability_axis": axis, "capability_code": code, "issue_count": len(issues)}
            for (lifecycle, axis, code), issues in sorted(cells.items())
        ]

    @staticmethod
    def _effective_mrc(record: ScopeRecord) -> list[dict[str, Any]]:
        """Overlay only the latest confirmed MRC answers onto immutable AI rows."""

        effective = [dict(item) for item in record.mrc]
        replacements = {
            answer["target_path"]: answer["confirmed_value_json"]
            for answer in record.human_answers
            if answer["target_path"] in {"occurrence.mrc.primary", "escape.mrc.primary"}
        }
        for side in ("OCCURRENCE", "ESCAPE"):
            target_path = f"{side.lower()}.mrc.primary"
            if target_path not in replacements:
                continue
            replacement = replacements[target_path]
            code = replacement.get("mrc_code", replacement.get("code")) if isinstance(replacement, dict) else replacement
            if not isinstance(code, str) or not code:
                continue
            original = next(
                (item for item in effective if item["side"] == side and item["role"] == "PRIMARY"),
                None,
            )
            if original is None:
                effective.append({
                    "side": side, "role": "PRIMARY", "mrc_code": code,
                    "control_status": "UNKNOWN", "source_type": "HUMAN_CONFIRMED", "confidence": 1.0,
                })
            else:
                original["mrc_code"] = code
                original["source_type"] = "HUMAN_CONFIRMED"
                original["confidence"] = 1.0
        return effective

    @staticmethod
    def _effective_gaps(record: ScopeRecord) -> list[dict[str, Any]]:
        """Overlay latest confirmed gap answers while retaining base AI rows in storage."""

        effective = [
            {**item, "details_json": dict(item["details_json"])}
            for item in record.gaps
        ]
        for answer in record.human_answers:
            path = answer["target_path"]
            if not path.startswith("capability_gaps."):
                continue
            parts = path.split(".")
            if len(parts) != 4:
                continue
            _, axis, code, governance_scope = parts
            original = next(
                (
                    item for item in effective
                    if item["capability_axis"] == axis
                    and item["capability_code"] == code
                    and item["governance_scope"] == governance_scope
                ),
                None,
            )
            if original is None:
                continue
            replacement = answer["confirmed_value_json"]
            if isinstance(replacement, dict):
                details = replacement.get("details", replacement.get("details_json", replacement))
                if isinstance(details, dict):
                    original["details_json"].update(details)
                original["capability_axis"] = replacement.get("capability_axis", original["capability_axis"])
                original["capability_code"] = replacement.get("capability_code", original["capability_code"])
                original["governance_scope"] = replacement.get("governance_scope", original["governance_scope"])
                original["control_status"] = replacement.get("control_status", original["control_status"])
            elif isinstance(replacement, str) and replacement:
                original["details_json"]["gap_description"] = replacement
            original["source_type"] = "HUMAN_CONFIRMED"
            original["confidence"] = 1.0
        return effective

    def _capability_labels(self) -> dict[tuple[str, str], str]:
        taxonomy_type = {
            "QUALITY_ENGINEERING": "ENGINEERING_CAPABILITY",
            "QUALITY_MANAGEMENT": "MANAGEMENT_CAPABILITY",
        }
        fallback = {
            "TEST_VERIFICATION": "测试与验证能力",
            "EMBEDDED_HW_SW_CO_DESIGN": "嵌入式软硬协同能力",
            "CHANGE_IMPACT": "变更影响管理能力",
            "CONFIGURATION_MANAGEMENT": "配置管理能力",
            "REQUIREMENT_ENGINEERING": "需求工程能力",
            "SYSTEM_ARCHITECTURE": "系统架构能力",
            "DETAILED_DESIGN": "详细设计能力",
            "SOFTWARE_IMPLEMENTATION": "软件实现能力",
            "HARDWARE_DESIGN": "硬件设计能力",
            "MECHANICAL_DESIGN": "机械设计能力",
        }
        labels: dict[tuple[str, str], str] = {}
        with self.repository.connect() as connection:
            rows = connection.execute(
                """SELECT term.taxonomy_type, term.code, term.label_zh
                     FROM analysis_taxonomy_term AS term
                     JOIN analysis_taxonomy_version AS version
                       ON version.taxonomy_version_id = term.taxonomy_version_id
                    WHERE version.status = 'ACTIVE' AND term.enabled = 1"""
            ).fetchall()
        for row in rows:
            for axis, expected_type in taxonomy_type.items():
                if row["taxonomy_type"] == expected_type:
                    label = row["label_zh"]
                    labels[(axis, row["code"])] = (
                        label if any("\u4e00" <= char <= "\u9fff" for char in label)
                        else fallback.get(row["code"], label)
                    )
        return labels

    @staticmethod
    def _source_coverage_rate(records: Iterable[ScopeRecord]) -> float:
        materialized = list(records)
        if not materialized:
            return 0.0
        return sum("SOURCE_DATA" in record.source_types for record in materialized) / len(materialized)

    @staticmethod
    def _human_confirmation_rate(records: Iterable[ScopeRecord]) -> float:
        materialized = list(records)
        if not materialized:
            return 0.0
        return sum(bool(record.human_answers) for record in materialized) / len(materialized)

    def _coverage(self, records: list[ScopeRecord]) -> dict[str, Any]:
        total = len(records)
        completed = sum(record.analysis_status == "COMPLETED" for record in records)
        partial = sum(record.analysis_status == "PARTIAL_FAILED" for record in records)
        unanalysed = total - completed - partial
        source_counts = {source_type: 0 for source_type in self._SOURCE_BUCKETS}
        classified_issues = 0
        for record in records:
            for source_type in record.source_types:
                if source_type in source_counts:
                    source_counts[source_type] += 1
            if record.tags or record.mrc or record.gaps:
                classified_issues += 1
        source_total = sum(source_counts.values())
        source_ai_human_counts = {
            "SOURCE": source_counts["SOURCE_DATA"],
            "AI": source_counts["AI_STANDARDIZED"] + source_counts["AI_INFERRED"],
            "HUMAN": source_counts["HUMAN_CONFIRMED"],
        }
        return {
            "total_issues": total,
            "completed": completed,
            "partial_failed": partial,
            "unanalysed": unanalysed,
            "classification_covered_issues": classified_issues,
            "classification_coverage": (classified_issues / total) if total else 0.0,
            "source_type_counts": source_counts,
            "source_type_ratios": {
                key: (value / source_total) if source_total else 0.0 for key, value in source_counts.items()
            },
            "source_ai_human_counts": source_ai_human_counts,
            "source_ai_human_ratios": {
                key: (value / source_total) if source_total else 0.0
                for key, value in source_ai_human_counts.items()
            },
        }

    @classmethod
    def _normalize_filters(cls, filters: dict[str, Any] | None) -> dict[str, str]:
        supplied = filters or {}
        unknown = sorted(set(supplied) - cls._VALID_FILTERS)
        if unknown:
            raise P0InsightError(f"INSIGHT_FILTER_UNKNOWN:{','.join(unknown)}")
        normalized = {key: str(value).strip() for key, value in supplied.items() if str(value).strip()}
        for public_name, internal_name in (("issue_domain", "domain"), ("lifecycle_phase", "lifecycle")):
            if public_name in normalized:
                if internal_name in normalized and normalized[internal_name] != normalized[public_name]:
                    raise P0InsightError(f"INSIGHT_FILTER_CONFLICT:{public_name}")
                normalized[internal_name] = normalized.pop(public_name)
        return normalized

    def _scope_hash(self, filters: dict[str, str], scoring: dict[str, Any], records: list[ScopeRecord]) -> str:
        payload = {
            "filters": filters,
            "scoring_version_id": scoring["scoring_version_id"],
            "scoring_hash": scoring["content_hash"],
            "issue_versions": [
                (
                    record.knowledge_id, record.issue_version_id, record.analysis_set_id,
                    record.analysis_status, [
                        (answer["target_path"], answer["confirmed_value_json"])
                        for answer in record.human_answers
                    ],
                )
                for record in records
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _scoring_metadata(scoring: dict[str, Any]) -> dict[str, Any]:
        return {
            "scoring_version_id": scoring["scoring_version_id"],
            "version_no": scoring["version_no"],
            "content_hash": scoring["content_hash"],
            "weights": scoring["weights_json"],
        }

    @staticmethod
    def _contradiction_key(axis: str, code: str, governance_scope: str) -> str:
        return f"{axis}:{code}:{governance_scope}"

    @staticmethod
    def _parse_contradiction_key(value: str) -> tuple[str, str, str]:
        parts = value.split(":")
        if len(parts) != 3 or parts[0] not in {"QUALITY_ENGINEERING", "QUALITY_MANAGEMENT"}:
            raise P0InsightError("CONTRADICTION_KEY_INVALID")
        return parts[0], parts[1], parts[2]

    @staticmethod
    def _drill_item(record: ScopeRecord, gap: dict[str, Any]) -> dict[str, Any]:
        issue_fact = record.snapshot.get("ISSUE_FACT", {})
        return {
            "knowledge_id": record.knowledge_id,
            "business_issue_id": record.business_issue_id,
            "product_id": record.product_id,
            "product_code": record.product_code,
            "business_type": record.business_type,
            "month": issue_fact.get("month"),
            "severity": issue_fact.get("severity"),
            "analysis_status": record.analysis_status,
            "gap": {
                "capability_axis": gap["capability_axis"],
                "capability_code": gap["capability_code"],
                "governance_scope": gap["governance_scope"],
                "details": gap["details_json"],
            },
        }


class InsightService(P0InsightService):
    """Public P0 insight-service name reserved for the later API adapter."""
