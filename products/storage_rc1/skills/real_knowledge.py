from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from storage_life.knowledge_release import KnowledgeReleaseConsumer
from storage_life.lifetime_engine import LifetimeAssessmentRequest
from .runtime_adapter import StorageDomainSkillAdapter


ROOT = Path(__file__).resolve().parent
PACK_DIR = ROOT / "packs"
INVENTORY = ROOT / "material_inventory.yaml"

PACK_FILES = {
    "PACK_WRITE_GOVERNANCE": "write_governance.yaml",
    "PACK_LIFETIME_ENGINEERING": "lifetime_engineering.yaml",
    "PACK_DIAGNOSTIC_VALIDATION": "diagnostic_validation.yaml",
    "PACK_CHANGE_IMPACT": "change_impact.yaml",
}

PRIORITY_SOURCE_GROUPS = {
    "NAND_RAW_FLASH": ["EK-001", "EK-002", "EK-003", "EK-005", "EK-011", "EK-012", "EK-013"],
    "EMMC_STANDARD_HEALTH": ["EK-007", "EK-025"],
    "NVME_SMART_HEALTH": ["EK-018", "EK-019"],
    "LINUX_WRITE_PATH": ["EK-004", "EK-020", "EK-021", "EK-022", "EK-028"],
    "SSD_ENDURANCE_WORKLOAD": ["EK-015", "EK-016", "EK-017", "EK-026", "EK-027"],
}


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@dataclass
class RealKnowledgeAssessmentService:
    consumer: KnowledgeReleaseConsumer | Any
    adapter: StorageDomainSkillAdapter

    @classmethod
    def current(cls) -> "RealKnowledgeAssessmentService":
        consumer = KnowledgeReleaseConsumer.current()
        return cls(consumer=consumer, adapter=StorageDomainSkillAdapter(knowledge_consumer=consumer))

    def _inventory(self) -> dict[str, Any]:
        return _load_yaml(INVENTORY)

    def _packs(self) -> dict[str, dict[str, Any]]:
        return {pack_id: _load_yaml(PACK_DIR / filename) for pack_id, filename in PACK_FILES.items()}

    def _formal_objects(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        status = self.consumer.status()
        if not status.get("available") or status.get("status") != "READY":
            return status, []
        result = self.consumer.query(
            "",
            top_k=50,
            knowledge_release_version=status.get("knowledge_release_version"),
        )
        return status, list(result.get("results") or [])

    @staticmethod
    def _source_complete(source: dict[str, Any], evidence: dict[str, Any] | None) -> bool:
        version = str(source.get("source_version") or source.get("revision") or "").strip()
        url = str(source.get("official_url") or source.get("uri") or "").strip()
        title = str(source.get("title") or "").strip()
        publisher = str(source.get("publisher") or "").strip()
        locator = (evidence or {}).get("locator") or {}
        return bool(
            title
            and publisher
            and version
            and "待核" not in version
            and url
            and "待核" not in url
            and locator.get("type")
            and locator.get("value")
        )

    def _match_materials(self, obj: dict[str, Any], materials: list[dict[str, Any]]) -> list[str]:
        source_rows = obj.get("source_references") or []
        matched: list[str] = []
        for source in source_rows:
            st = _norm(source.get("title"))
            sp = _norm(source.get("publisher"))
            for material in materials:
                mt = _norm(material.get("title"))
                mp = _norm(material.get("publisher"))
                title_match = bool(st and mt and (st in mt or mt in st))
                publisher_match = bool(sp and mp and (sp in mp or mp in sp))
                if title_match and publisher_match:
                    mid = str(material.get("material_id") or "")
                    if mid and mid not in matched:
                        matched.append(mid)
        return matched

    def _object_trace(self, obj: dict[str, Any]) -> dict[str, Any]:
        evidences = obj.get("evidence") or []
        sources = obj.get("source_references") or []
        evidence_by_id = {str(x.get("evidence_id") or ""): x for x in evidences}
        source_by_ref = {str(x.get("source_ref") or ""): x for x in sources}
        steps = []
        complete = True
        for evidence_id in obj.get("evidence_refs") or []:
            evidence = evidence_by_id.get(str(evidence_id))
            if evidence is None:
                complete = False
                steps.append({"evidence_id": evidence_id, "status": "MISSING"})
                continue
            source = evidence.get("source") or {}
            source_ref = None
            for candidate_ref, candidate in source_by_ref.items():
                if (
                    str(candidate.get("source_id") or "") == str(source.get("source_id") or "")
                    and str(candidate.get("source_version") or "") == str(source.get("revision") or "")
                ):
                    source_ref = candidate_ref
                    break
            if source_ref is None and obj.get("source_refs"):
                source_ref = str(obj["source_refs"][0])
            source_row = source_by_ref.get(source_ref or "") or {}
            source_complete = self._source_complete(source_row, evidence)
            complete = complete and source_complete
            steps.append({
                "evidence_id": evidence_id,
                "evidence_status": (evidence.get("metadata") or {}).get("evidence_status"),
                "locator": evidence.get("locator"),
                "source_ref": source_ref,
                "source": {
                    "publisher": source_row.get("publisher") or source.get("metadata", {}).get("publisher"),
                    "title": source_row.get("title") or source.get("metadata", {}).get("title"),
                    "version": source_row.get("source_version") or source.get("revision"),
                    "official_url": source_row.get("official_url") or source.get("uri"),
                    "archive_ref": source_row.get("source_ref_uri"),
                },
                "source_trace_complete": source_complete,
            })
        return {
            "object_id": obj.get("object_id"),
            "title": obj.get("title"),
            "knowledge_release_version": obj.get("knowledge_release_version"),
            "trace_complete": complete and bool(steps),
            "steps": steps,
        }

    def readiness(self) -> dict[str, Any]:
        inventory = self._inventory()
        materials = list(inventory.get("materials") or [])
        material_by_id = {str(x.get("material_id")): x for x in materials}
        packs = self._packs()
        release_status, objects = self._formal_objects()

        object_rows = []
        for obj in objects:
            material_refs = self._match_materials(obj, materials)
            trace = self._object_trace(obj)
            object_rows.append({**obj, "_material_refs": material_refs, "_trace": trace})

        pack_results: dict[str, Any] = {}
        for pack_id, pack in packs.items():
            pack_materials = [str(x) for x in pack.get("material_refs") or []]
            selected = [
                obj for obj in object_rows
                if set(obj["_material_refs"]) & set(pack_materials)
            ]
            covered_materials = sorted({
                mid for obj in selected for mid in obj["_material_refs"] if mid in pack_materials
            })
            evidence_backed = [obj for obj in selected if obj.get("evidence_refs") and obj["_trace"]["trace_complete"]]
            verified_sources = []
            for obj in selected:
                if obj["_trace"]["trace_complete"]:
                    verified_sources.extend(obj["_material_refs"])
            verified_sources = sorted(set(verified_sources))

            if pack_id == "PACK_WRITE_GOVERNANCE":
                critical = PRIORITY_SOURCE_GROUPS["LINUX_WRITE_PATH"] + PRIORITY_SOURCE_GROUPS["SSD_ENDURANCE_WORKLOAD"]
            elif pack_id == "PACK_LIFETIME_ENGINEERING":
                critical = (
                    PRIORITY_SOURCE_GROUPS["NAND_RAW_FLASH"]
                    + PRIORITY_SOURCE_GROUPS["EMMC_STANDARD_HEALTH"]
                    + PRIORITY_SOURCE_GROUPS["NVME_SMART_HEALTH"]
                    + PRIORITY_SOURCE_GROUPS["SSD_ENDURANCE_WORKLOAD"]
                )
            elif pack_id == "PACK_DIAGNOSTIC_VALIDATION":
                critical = (
                    PRIORITY_SOURCE_GROUPS["NAND_RAW_FLASH"]
                    + PRIORITY_SOURCE_GROUPS["EMMC_STANDARD_HEALTH"]
                    + PRIORITY_SOURCE_GROUPS["NVME_SMART_HEALTH"]
                )
            else:
                critical = sorted(set(
                    PRIORITY_SOURCE_GROUPS["NAND_RAW_FLASH"]
                    + PRIORITY_SOURCE_GROUPS["EMMC_STANDARD_HEALTH"]
                    + PRIORITY_SOURCE_GROUPS["NVME_SMART_HEALTH"]
                    + PRIORITY_SOURCE_GROUPS["LINUX_WRITE_PATH"]
                    + PRIORITY_SOURCE_GROUPS["SSD_ENDURANCE_WORKLOAD"]
                ))
            critical = [x for x in critical if x in pack_materials]
            missing_critical = [x for x in critical if x not in covered_materials]

            if selected and not missing_critical and len(covered_materials) == len(pack_materials):
                readiness = "READY"
            elif selected:
                readiness = "PARTIAL"
            else:
                readiness = "BLOCKED"

            catalog_verified = [
                mid for mid in pack_materials
                if mid in material_by_id
                and str(material_by_id[mid].get("verify_status") or "").upper() in {"VERIFIED", "PASS", "CONFIRMED"}
            ]
            pack_results[pack_id] = {
                "status": readiness,
                "formal_knowledge_object_count": len(selected),
                "formal_knowledge_object_ids": [str(x.get("object_id")) for x in selected],
                "source_material_coverage": {
                    "covered": len(covered_materials),
                    "total_declared": len(pack_materials),
                    "ratio": round(len(covered_materials) / len(pack_materials), 4) if pack_materials else 0.0,
                    "material_refs": covered_materials,
                },
                "evidence_coverage": {
                    "covered_objects": len(evidence_backed),
                    "total_objects": len(selected),
                    "ratio": round(len(evidence_backed) / len(selected), 4) if selected else 0.0,
                },
                "release_trace_verified_sources": {
                    "count": len(verified_sources),
                    "material_refs": verified_sources,
                },
                "catalog_verified_sources": {
                    "count": len(catalog_verified),
                    "total_declared": len(pack_materials),
                    "ratio": round(len(catalog_verified) / len(pack_materials), 4) if pack_materials else 0.0,
                },
                "missing_critical_knowledge": missing_critical,
                "unsupported_questions": list(pack.get("unsupported_questions") or []),
                "recommended_next_owner": (
                    "Knowledge Production / Source Verification"
                    if missing_critical else "Storage Product / Test"
                ),
            }

        traces = [obj["_trace"] for obj in object_rows]
        return {
            "contract_version": "storage-real-knowledge-readiness/v0.1",
            "knowledge_release": release_status,
            "formal_knowledge_object_count": len(objects),
            "evidence_drilldown": {
                "status": "PASS" if traces and all(x["trace_complete"] for x in traces) else "PARTIAL",
                "object_traces": traces,
            },
            "packs": pack_results,
            "priority_source_groups": {
                name: {
                    "material_refs": ids,
                    "covered_by_current_release": sorted({
                        mid for obj in object_rows for mid in obj["_material_refs"] if mid in ids
                    }),
                }
                for name, ids in PRIORITY_SOURCE_GROUPS.items()
            },
        }

    def real_golden(self) -> dict[str, Any]:
        readiness = self.readiness()

        rg01 = self.adapter.query_pack(
            "PACK_WRITE_GOVERNANCE",
            "high-frequency small writes cache writeback WAL fsync journaling write amplification controls",
        )
        rg02 = self.adapter.query_pack(
            "PACK_LIFETIME_ENGINEERING",
            "SSD NVMe Percentage Used Data Units Written TBW DWPD endurance consumption",
            device_type="SSD",
        )
        rg03 = self.adapter.query_pack(
            "PACK_DIAGNOSTIC_VALIDATION",
            "eMMC DEVICE_LIFE_TIME_EST_TYP_A DEVICE_LIFE_TIME_EST_TYP_B PRE_EOL acquisition semantics",
            device_type="eMMC",
        )
        rg04 = self.adapter.query_pack(
            "PACK_DIAGNOSTIC_VALIDATION",
            "NAND P/E page erase block erase count wear distribution ECC bad block",
            device_type="NAND",
        )
        rg05 = self.adapter.query_pack(
            "PACK_CHANGE_IMPACT",
            "NVMe Percentage Used change impact software monitoring validation",
            device_type="SSD",
        )

        def result(case_id: str, query: dict[str, Any], *, additional_missing: list[str],
                   supported: list[str], unsupported: list[str], formula_gap: bool = False) -> dict[str, Any]:
            items = list(query.get("items") or [])
            evidence_refs = list(query.get("evidence_refs") or [])
            missing = list(query.get("missing_information") or []) + list(additional_missing)
            return {
                "case_id": case_id,
                "status": "PASS" if items and not missing else "PARTIAL",
                "supported_by_current_release": supported,
                "unsupported_or_missing": sorted(set(unsupported + missing)),
                "knowledge_refs": list(query.get("knowledge_refs") or []),
                "evidence_refs": evidence_refs,
                "direct_answer_boundary": (
                    "Only statements directly supported by the listed Formal Knowledge objects/evidence may be presented as fact."
                ),
                "fact_derived_hypothesis_separation": {
                    "facts": [str(x.get("content") or x.get("summary") or x.get("title") or "") for x in items],
                    "derived": [],
                    "ai_hypotheses": [],
                    "unknowns": sorted(set(unsupported + missing)),
                },
                "formula_gap": formula_gap,
                "formula_task_required": formula_gap,
            }

        cases = {
            "RG01": result(
                "RG01", rg01,
                additional_missing=["FORMAL_WRITE_GOVERNANCE_KNOWLEDGE_NOT_RELEASED"],
                supported=[],
                unsupported=[
                    "high-frequency small-write mechanism chain",
                    "cache/write-coalescing/WAL/fsync engineering limits",
                    "evidence-backed validation recommendations",
                ],
            ),
            "RG02": result(
                "RG02", rg02,
                additional_missing=[
                    "CONFIRMED_DEVICE_ENDURANCE_FACTS_REQUIRED",
                    "RUNTIME_WRITE_OBSERVATIONS_REQUIRED",
                    "FORMAL_TBW_DWPD_DUW_KNOWLEDGE_NOT_RELEASED",
                ],
                supported=["NVMe Percentage Used released semantics"] if rg02.get("items") else [],
                unsupported=[
                    "TBW/DWPD/Data Units Written full evidence-backed assessment",
                    "target-service-life daily write budget",
                ],
                formula_gap=False,
            ),
            "RG03": result(
                "RG03", rg03,
                additional_missing=["FORMAL_EMMC_LIFE_TIME_A_B_PRE_EOL_KNOWLEDGE_NOT_RELEASED"],
                supported=[],
                unsupported=[
                    "Life Time A/B released semantics",
                    "PRE_EOL released semantics",
                    "evidence-backed interpretation boundary",
                ],
            ),
            "RG04": result(
                "RG04", rg04,
                additional_missing=["FORMAL_RAW_NAND_DIAGNOSTIC_KNOWLEDGE_NOT_RELEASED"],
                supported=[],
                unsupported=[
                    "P/E and erase-block evidence-backed interpretation",
                    "erase-count/wear-distribution semantics",
                    "ECC/bad-block evidence-backed validation guidance",
                ],
            ),
            "RG05": result(
                "RG05", rg05,
                additional_missing=[
                    "TWO_PRODUCT_APPROVED_DEVICE_PROFILES_REQUIRED",
                    "FORMAL_CHANGE_IMPACT_KNOWLEDGE_INCOMPLETE",
                ],
                supported=["NVMe Percentage Used monitoring semantics"] if rg05.get("items") else [],
                unsupported=[
                    "full parameter-delta to lifetime/software/test impact chain",
                    "replacement approval/rejection",
                ],
            ),
        }
        return {
            "contract_version": "storage-real-golden/v0.1",
            "overall_status": "PARTIAL",
            "cases": cases,
            "formal_knowledge_object_count": readiness["formal_knowledge_object_count"],
            "evidence_drilldown": readiness["evidence_drilldown"],
            "formula_gap": False,
            "formula_task_required": False,
            "decision_boundary": "NO_AUTO_REPLACEMENT_DECISION",
            "unknown_not_safe": True,
        }

    def execute_skill(self, skill_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if skill_id == "storage-write-governance":
            return self.adapter.execute_write_governance(
                device_type=str(payload.get("device_type") or ""),
                user_context=dict(payload.get("user_context") or {}),
                workload_software_facts=list(payload.get("workload_software_facts") or []),
            )
        if skill_id == "storage-diagnostic-validation":
            return self.adapter.execute_diagnostic_validation(
                device_type=str(payload.get("device_type") or ""),
                target_question=str(payload.get("target_question") or ""),
                diagnostic_capabilities=list(payload.get("diagnostic_capabilities") or []),
                runtime_observations=list(payload.get("runtime_observations") or []),
            )
        if skill_id == "storage-change-impact":
            return self.adapter.execute_change_impact(
                device_type=str(payload.get("device_type") or ""),
                parameter_delta=list(payload.get("parameter_delta") or []),
                question=str(payload.get("question") or "device change impact"),
            )
        if skill_id == "storage-lifetime-budget":
            request = LifetimeAssessmentRequest(**dict(payload.get("assessment_request") or {}))
            return self.adapter.execute_lifetime_budget(
                request=request,
                requested_metric=str(payload.get("requested_metric") or ""),
                target_service_life=dict(payload.get("target_service_life") or {}),
            )
        raise ValueError(f"UNKNOWN_STORAGE_SKILL:{skill_id}")
