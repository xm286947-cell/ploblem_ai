"""Versioned risk cases and deterministic P1-A forward assessment."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any


class ForwardRiskError(RuntimeError):
    """Stable P1 error code."""


class ForwardRiskService:
    STAGES = {"REQUIREMENT", "DESIGN", "TEST", "RELEASE"}
    COVERAGE = {"COVERED", "PARTIAL", "NOT_FOUND", "INSUFFICIENT_INFO", "NOT_APPLICABLE"}

    def __init__(self, repository: Any):
        self.repository = repository

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _tokens(text: str) -> set[str]:
        text = text.lower()
        words = set(re.findall(r"[a-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", text))
        for word in list(words):
            if re.fullmatch(r"[\u4e00-\u9fff]+", word):
                words.update(word[index:index + 2] for index in range(len(word) - 1))
        return words

    @classmethod
    def _control_is_negated(cls, material: str, control: str) -> bool:
        control_tokens = cls._tokens(control)
        if not control_tokens:
            return False
        for match in re.finditer(r"(?:尚未|未曾|未|没有|缺少|缺失|尚缺|待补充|待建立|待定义)[^。；;\n]{0,28}", material):
            segment_tokens = cls._tokens(match.group(0))
            if len(segment_tokens & control_tokens) / max(len(control_tokens), 1) >= 0.25:
                return True
        return False

    @staticmethod
    def _fact(snapshot: dict[str, Any]) -> dict[str, Any]:
        return snapshot.get("ISSUE_FACT") or snapshot.get("issue_fact") or snapshot

    @staticmethod
    def _value_map(analysis: dict[str, Any]) -> dict[str, Any]:
        return {row["value_path"]: row.get("value_json") for row in analysis.get("values", [])}

    @staticmethod
    def _pick(values: dict[str, Any], *needles: str) -> str:
        for path, value in values.items():
            if any(needle in path.lower() for needle in needles):
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    for key in ("value", "description", "summary", "reason"):
                        if value.get(key):
                            return str(value[key]).strip()
        return ""

    @classmethod
    def _merge_case(cls, current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = json.loads(cls._dump(current))
        for key in (
            "products", "domains", "lifecycle_phases", "occurrence_mrc", "escape_mrc",
            "preventive_controls", "verification_scenarios", "related_issues", "evidence",
        ):
            seen = {cls._dump(value) for value in merged.get(key, [])}
            merged.setdefault(key, [])
            for value in incoming.get(key, []):
                marker = cls._dump(value)
                if marker not in seen:
                    merged[key].append(value); seen.add(marker)
        gap_keys = {
            (row.get("axis"), row.get("code"), row.get("scope"))
            for row in merged.get("capability_gaps", [])
        }
        for gap in incoming.get("capability_gaps", []):
            key = (gap.get("axis"), gap.get("code"), gap.get("scope"))
            if key not in gap_keys:
                merged.setdefault("capability_gaps", []).append(gap); gap_keys.add(key)
        boundary = merged.setdefault("applicability_boundary", {})
        for key in ("products", "domains", "lifecycle_phases"):
            boundary.setdefault(key, [])
            for value in incoming.get("applicability_boundary", {}).get(key, []):
                if value not in boundary[key]:
                    boundary[key].append(value)
        severity = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        if severity.get(incoming.get("risk_level"), 0) > severity.get(merged.get("risk_level"), 0):
            merged["risk_level"] = incoming["risk_level"]
        for key in ("trigger_conditions", "failure_mechanism", "customer_impact"):
            if not merged.get(key) and incoming.get(key):
                merged[key] = incoming[key]
        return merged

    def publish_issue(
        self,
        knowledge_id: str,
        *,
        publication_level: str = "INTERNAL_FULL",
        published_by: str,
        merge_into_risk_case_id: str = "",
    ) -> dict[str, Any]:
        issue = self.repository.get_issue(knowledge_id)
        analysis = self.repository.get_latest_analysis_set(knowledge_id)
        if issue is None:
            raise ForwardRiskError("RISK_CASE_SOURCE_ISSUE_NOT_FOUND")
        if analysis is None or analysis["status"] != "COMPLETED":
            raise ForwardRiskError("RISK_CASE_REQUIRES_COMPLETED_ANALYSIS")
        if not published_by.strip():
            raise ForwardRiskError("RISK_CASE_PUBLISHER_REQUIRED")
        if publication_level not in {"INTERNAL_FULL", "INTERNAL_REDACTED", "EXTERNAL_PATTERN", "DO_NOT_PUBLISH"}:
            raise ForwardRiskError("RISK_CASE_PUBLICATION_LEVEL_INVALID")
        merge_target = None
        if merge_into_risk_case_id:
            with self.repository.connect() as connection:
                merge_target = connection.execute(
                    """SELECT risk.*,version.case_json,version.search_text,version.version_no
                         FROM risk_case risk JOIN risk_case_version version
                           ON version.risk_case_version_id=risk.current_version_id
                        WHERE risk.risk_case_id=? AND risk.status='PUBLISHED'""",
                    (merge_into_risk_case_id,),
                ).fetchone()
            if merge_target is None:
                raise ForwardRiskError("RISK_CASE_MERGE_TARGET_NOT_FOUND")
            publication_level = merge_target["publication_level"]

        fact = self._fact(issue["normalized_snapshot"])
        values = self._value_map(analysis)
        tags = analysis.get("tags", [])
        gaps = analysis.get("capability_gaps", [])
        controls: list[str] = []
        validation: list[str] = []
        for gap in gaps:
            details = gap.get("details_json") or {}
            if not isinstance(details, dict):
                continue
            for key in ("first_action", "recommended_action", "control", "management_action", "engineering_action"):
                value = details.get(key)
                if isinstance(value, str) and value.strip() and value.strip() not in controls:
                    controls.append(value.strip())
            for key in ("validation_scenario", "verification_scenario", "validation_metric"):
                value = details.get(key)
                if isinstance(value, str) and value.strip() and value.strip() not in validation:
                    validation.append(value.strip())
        source_title = str(fact.get("title") or fact.get("description") or issue["business_issue_id"])
        case = {
            "risk_name": source_title,
            "products": [value for value in [fact.get("product"), fact.get("platform")] if value],
            "domains": [row["tag_code"] for row in tags if row["axis"] == "DOMAIN"],
            "lifecycle_phases": [row["tag_code"] for row in tags if row["axis"] == "LIFECYCLE"],
            "trigger_conditions": self._pick(values, "trigger", "condition", "scenario"),
            "failure_mechanism": self._pick(values, "failure_mechanism", "root_cause", "mechanism"),
            "occurrence_mrc": [row["mrc_code"] for row in analysis.get("mrc", []) if row["side"] == "OCCURRENCE"],
            "escape_mrc": [row["mrc_code"] for row in analysis.get("mrc", []) if row["side"] == "ESCAPE"],
            "customer_impact": str(fact.get("impact") or self._pick(values, "customer_impact", "impact")),
            "capability_gaps": [{
                "axis": row["capability_axis"], "code": row["capability_code"],
                "scope": row["governance_scope"], "control_status": row["control_status"],
                "details": row.get("details_json") or {},
            } for row in gaps],
            "preventive_controls": controls,
            "verification_scenarios": validation,
            "applicability_boundary": {
                "products": [fact.get("product")] if fact.get("product") else [],
                "domains": [row["tag_code"] for row in tags if row["axis"] == "DOMAIN"],
                "lifecycle_phases": [row["tag_code"] for row in tags if row["axis"] == "LIFECYCLE"],
            },
            "related_issues": [knowledge_id],
            "evidence": [{"source_ref": row["source_ref"], "excerpt": row["excerpt"]} for row in analysis.get("evidence", [])],
            "risk_level": str(fact.get("severity") or "UNKNOWN").upper(),
        }
        case["risk_level"] = {
            "H": "HIGH", "HIGH": "HIGH", "M": "MEDIUM", "MEDIUM": "MEDIUM",
            "L": "LOW", "LOW": "LOW",
        }.get(case["risk_level"], "UNKNOWN")
        generalized_title = (
            case["failure_mechanism"]
            or (f"{case['capability_gaps'][0]['code']} 风险" if case["capability_gaps"] else "历史质量风险模式")
        )
        if publication_level == "INTERNAL_REDACTED":
            case["risk_name"] = generalized_title
            case["related_issues"] = [
                "ISSUE-" + hashlib.sha256(knowledge_id.encode()).hexdigest()[:8].upper()
            ]
            case["evidence"] = []
        elif publication_level == "EXTERNAL_PATTERN":
            pattern_codes = case["occurrence_mrc"] + case["escape_mrc"] + [
                gap["code"] for gap in case["capability_gaps"]
            ]
            case["risk_name"] = " / ".join(pattern_codes[:3]) or "历史质量风险模式"
            case["products"] = []
            case["trigger_conditions"] = "适用边界内发生相关变更或异常场景"
            case["failure_mechanism"] = " / ".join(
                case["occurrence_mrc"] + case["escape_mrc"]
            ) or "待外部评审补充通用失效机理"
            case["customer_impact"] = f"风险等级：{case['risk_level']}"
            case["related_issues"] = []
            case["evidence"] = []
            case["preventive_controls"] = [
                f"{gap['axis']} / {gap['code']} 控制" for gap in case["capability_gaps"]
            ]
            case["verification_scenarios"] = []
            case["applicability_boundary"]["products"] = []
            for gap in case["capability_gaps"]:
                gap["details"] = {}
        if merge_target is not None:
            case = self._merge_case(json.loads(merge_target["case_json"]), case)
        published_title = case["risk_name"]
        include_source_text = publication_level in {"INTERNAL_FULL", "DO_NOT_PUBLISH"}
        search_text = "\n".join([
            published_title, str(fact.get("description") or "") if include_source_text else "",
            case["trigger_conditions"], case["failure_mechanism"],
            case["customer_impact"], " ".join(case["products"] + case["domains"] + case["lifecycle_phases"]),
            " ".join(case["occurrence_mrc"] + case["escape_mrc"] + controls + validation),
            " ".join(f"{gap['axis']} {gap['code']}" for gap in case["capability_gaps"]),
        ])
        if merge_target is not None:
            search_text = merge_target["search_text"] + "\n" + search_text
        content_hash = hashlib.sha256(self._dump(case).encode()).hexdigest()
        risk_code = (merge_target["risk_code"] if merge_target is not None else
                     "RISK-" + hashlib.sha256(knowledge_id.encode()).hexdigest()[:12].upper())
        with self.repository.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = connection.execute(
                "SELECT * FROM risk_case WHERE risk_case_id=?" if merge_target is not None
                else "SELECT * FROM risk_case WHERE risk_code = ?",
                (merge_into_risk_case_id if merge_target is not None else risk_code,),
            ).fetchone()
            if record is None:
                risk_case_id = f"RC-{uuid.uuid4().hex}"
                connection.execute(
                    """INSERT INTO risk_case(risk_case_id,risk_code,title,publication_level,status)
                       VALUES (?,?,?,?,?)""",
                    (risk_case_id, risk_code, published_title, publication_level,
                     "DRAFT" if publication_level == "DO_NOT_PUBLISH" else "PUBLISHED"),
                )
                version_no = 1
            else:
                risk_case_id = record["risk_case_id"]
                duplicate = connection.execute(
                    "SELECT risk_case_version_id,version_no FROM risk_case_version WHERE risk_case_id=? AND content_hash=?",
                    (risk_case_id, content_hash),
                ).fetchone()
                if duplicate:
                    connection.commit()
                    return {"risk_case_id": risk_case_id, "risk_case_version_id": duplicate["risk_case_version_id"],
                            "version_no": duplicate["version_no"], "outcome": "ALREADY_PUBLISHED"}
                version_no = connection.execute(
                    "SELECT COALESCE(MAX(version_no),0)+1 FROM risk_case_version WHERE risk_case_id=?", (risk_case_id,)
                ).fetchone()[0]
            version_id = f"RCV-{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO risk_case_version(
                       risk_case_version_id,risk_case_id,version_no,content_hash,source_analysis_set_id,
                       taxonomy_version_id,case_json,search_text,created_by
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (version_id, risk_case_id, version_no, content_hash, analysis["analysis_set_id"],
                 analysis["taxonomy_version_id"], self._dump(case), search_text, published_by.strip()),
            )
            connection.execute(
                """INSERT OR REPLACE INTO risk_case_issue_link(risk_case_id,knowledge_id,evidence_json)
                   VALUES (?,?,?)""", (risk_case_id, knowledge_id, self._dump(case["evidence"])),
            )
            connection.execute(
                """UPDATE risk_case SET current_version_id=?,title=?,publication_level=?,
                       status=?,updated_at=CURRENT_TIMESTAMP WHERE risk_case_id=?""",
                (version_id, published_title, publication_level,
                 "DRAFT" if publication_level == "DO_NOT_PUBLISH" else "PUBLISHED", risk_case_id),
            )
            connection.commit()
        return {"risk_case_id": risk_case_id, "risk_case_version_id": version_id,
                "version_no": version_no, "outcome": "MERGED" if merge_target is not None else "PUBLISHED"}

    def list_cases(
        self, *, q: str = "", publication_level: str = "", product: str = "",
        domain: str = "", lifecycle: str = "", mrc: str = "", capability: str = "",
    ) -> dict[str, Any]:
        query = """SELECT risk.risk_case_id,risk.risk_code,risk.title,risk.publication_level,
                            risk.status,risk.created_at,risk.updated_at,
                            version.case_json,version.content_hash,version.version_no
                     FROM risk_case risk JOIN risk_case_version version
                       ON version.risk_case_version_id=risk.current_version_id
                    WHERE risk.status='PUBLISHED'"""
        params: list[Any] = []
        if publication_level:
            query += " AND risk.publication_level=?"
            params.append(publication_level)
        if q:
            query += " AND (risk.title LIKE ? OR version.search_text LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        query += " ORDER BY risk.updated_at DESC"
        with self.repository.connect() as connection:
            items = []
            for row in connection.execute(query, params):
                item = dict(row); item["case"] = json.loads(item.pop("case_json"))
                case = item["case"]
                if product and product not in case.get("products", []):
                    continue
                if domain and domain not in case.get("domains", []):
                    continue
                if lifecycle and lifecycle not in case.get("lifecycle_phases", []):
                    continue
                if mrc and mrc not in case.get("occurrence_mrc", []) + case.get("escape_mrc", []):
                    continue
                if capability and capability not in [row.get("code") for row in case.get("capability_gaps", [])]:
                    continue
                items.append(item)
        return {"items": items, "total": len(items)}

    def create_assessment(
        self, *, project_name: str, product_code: str, assessment_stage: str,
        material_type: str, material_name: str, material_text: str, created_by: str,
    ) -> dict[str, Any]:
        stage = assessment_stage.upper()
        if stage not in self.STAGES:
            raise ForwardRiskError("ASSESSMENT_STAGE_INVALID")
        if not project_name.strip() or not material_text.strip() or not created_by.strip():
            raise ForwardRiskError("ASSESSMENT_INPUT_INCOMPLETE")
        material_hash = hashlib.sha256(material_text.strip().encode()).hexdigest()
        with self.repository.connect() as connection:
            product = connection.execute(
                "SELECT product_id FROM product_config WHERE product_code=? AND enabled=1", (product_code.upper(),)
            ).fetchone()
            if product is None:
                raise ForwardRiskError("ASSESSMENT_PRODUCT_NOT_FOUND")
            assessment_id = f"FA-{uuid.uuid4().hex}"
            version_id = f"FAV-{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO forward_assessment(
                       assessment_id,project_name,product_id,status,current_version_no,created_by
                   ) VALUES (?,?,?,'READY',1,?)""",
                (assessment_id, project_name.strip(), product["product_id"], created_by.strip()),
            )
            connection.execute(
                """INSERT INTO forward_assessment_version(
                       assessment_version_id,assessment_id,version_no,assessment_stage,material_type,
                       material_name,material_hash,material_text,status
                   ) VALUES (?,?,1,?,?,?,?,?,'READY')""",
                (version_id, assessment_id, stage, material_type.strip() or "TEXT",
                 material_name.strip() or "未命名材料", material_hash, material_text.strip()),
            )
            connection.commit()
        return self.evaluate(assessment_id, version_id)

    def reassess(
        self, assessment_id: str, *, assessment_stage: str, material_type: str,
        material_name: str, material_text: str, created_by: str,
    ) -> dict[str, Any]:
        stage = assessment_stage.upper()
        if stage not in self.STAGES:
            raise ForwardRiskError("ASSESSMENT_STAGE_INVALID")
        if not material_text.strip() or not created_by.strip():
            raise ForwardRiskError("ASSESSMENT_INPUT_INCOMPLETE")
        material_hash = hashlib.sha256(material_text.strip().encode()).hexdigest()
        with self.repository.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            assessment = connection.execute(
                "SELECT * FROM forward_assessment WHERE assessment_id=?", (assessment_id,)
            ).fetchone()
            if assessment is None:
                raise ForwardRiskError("ASSESSMENT_NOT_FOUND")
            duplicate = connection.execute(
                """SELECT assessment_version_id FROM forward_assessment_version
                    WHERE assessment_id=? AND material_hash=? AND assessment_stage=?""",
                (assessment_id, material_hash, stage),
            ).fetchone()
            if duplicate is not None:
                raise ForwardRiskError("ASSESSMENT_MATERIAL_UNCHANGED")
            version_no = connection.execute(
                "SELECT COALESCE(MAX(version_no),0)+1 FROM forward_assessment_version WHERE assessment_id=?",
                (assessment_id,),
            ).fetchone()[0]
            version_id = f"FAV-{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO forward_assessment_version(
                       assessment_version_id,assessment_id,version_no,assessment_stage,material_type,
                       material_name,material_hash,material_text,status
                   ) VALUES (?,?,?,?,?,?,?,?,'READY')""",
                (version_id, assessment_id, version_no, stage, material_type.strip() or "TEXT",
                 material_name.strip() or "未命名材料", material_hash, material_text.strip()),
            )
            connection.execute(
                """UPDATE forward_assessment SET current_version_no=?,status='READY',
                       updated_at=CURRENT_TIMESTAMP WHERE assessment_id=?""",
                (version_no, assessment_id),
            )
            connection.commit()
        return self.evaluate(assessment_id, version_id)

    def evaluate(self, assessment_id: str, assessment_version_id: str) -> dict[str, Any]:
        with self.repository.connect() as connection:
            version = connection.execute(
                """SELECT version.*,assessment.project_name,product.product_code,product.product_name
                     FROM forward_assessment_version version
                     JOIN forward_assessment assessment USING(assessment_id)
                     JOIN product_config product USING(product_id)
                    WHERE version.assessment_version_id=? AND assessment.assessment_id=?""",
                (assessment_version_id, assessment_id),
            ).fetchone()
            if version is None:
                raise ForwardRiskError("ASSESSMENT_VERSION_NOT_FOUND")
            cases = connection.execute(
                """SELECT risk.risk_case_id,risk.title,risk.publication_level,version.*
                     FROM risk_case risk JOIN risk_case_version version
                       ON version.risk_case_version_id=risk.current_version_id
                    WHERE risk.status='PUBLISHED' AND risk.publication_level!='DO_NOT_PUBLISH'"""
            ).fetchall()
            connection.execute("DELETE FROM forward_risk_result WHERE assessment_version_id=?", (assessment_version_id,))
            material = version["material_text"]
            material_tokens = self._tokens(material)
            results = []
            for row in cases:
                case = json.loads(row["case_json"])
                case_tokens = self._tokens(row["search_text"])
                overlap = material_tokens & case_tokens
                union = material_tokens | case_tokens
                lexical = len(overlap) / max(len(union), 1)
                product_bonus = 0.18 if version["product_code"].lower() in row["search_text"].lower() else 0.0
                stage_bonus = 0.10 if version["assessment_stage"] in case.get("lifecycle_phases", []) else 0.0
                relevance = min(1.0, lexical * 2.2 + product_bonus + stage_bonus)
                if relevance < 0.08:
                    continue
                controls = [str(value) for value in case.get("preventive_controls", []) if value]
                matched_controls = []
                for control in controls:
                    control_tokens = self._tokens(control)
                    overlap_ratio = len(control_tokens & material_tokens) / max(len(control_tokens), 1)
                    if overlap_ratio >= 0.45 and not self._control_is_negated(material, control):
                        matched_controls.append(control)
                if len(material.strip()) < 50:
                    coverage = "INSUFFICIENT_INFO"
                elif not controls or not matched_controls:
                    coverage = "NOT_FOUND"
                elif len(matched_controls) == len(controls):
                    coverage = "COVERED"
                else:
                    coverage = "PARTIAL"
                missing_controls = [control for control in controls if control not in matched_controls]
                open_questions = []
                if coverage == "INSUFFICIENT_INFO":
                    open_questions.append("材料信息不足，请补充边界、异常场景、控制措施和验证准则。")
                elif coverage in {"NOT_FOUND", "PARTIAL"}:
                    open_questions.append("请确认缺失控制是否已在其他设计或测试材料中定义。")
                risk_level = case.get("risk_level") if case.get("risk_level") in {"HIGH", "MEDIUM", "LOW"} else "UNKNOWN"
                display_keywords = sorted(
                    token for token in overlap
                    if (re.fullmatch(r"[a-z0-9_\-]+", token) and len(token) >= 3)
                    or (re.fullmatch(r"[\u4e00-\u9fff]+", token) and 4 <= len(token) <= 16)
                )[:12]
                matched_dimensions = []
                if product_bonus:
                    matched_dimensions.append(f"产品一致：{version['product_code']}")
                if stage_bonus:
                    matched_dimensions.append(f"生命周期一致：{version['assessment_stage']}")
                mechanism = str(case.get("failure_mechanism") or "")
                mechanism_tokens = self._tokens(mechanism)
                if mechanism and len(mechanism_tokens & material_tokens) / max(len(mechanism_tokens), 1) >= 0.25:
                    matched_dimensions.append(f"失效机理相似：{mechanism}")
                basis = {
                    "matched_keywords": display_keywords,
                    "matched_dimensions": matched_dimensions,
                    "same_product": bool(product_bonus),
                    "same_lifecycle": bool(stage_bonus),
                    "mechanism": case.get("failure_mechanism"),
                    "applicability_boundary": case.get("applicability_boundary", {}),
                }
                result_id = f"FRR-{uuid.uuid4().hex}"
                confidence = min(0.95, 0.45 + relevance / 2)
                connection.execute(
                    """INSERT INTO forward_risk_result(
                           risk_result_id,assessment_version_id,risk_case_version_id,relevance,
                           coverage_status,risk_level,match_basis_json,existing_controls_json,
                           missing_controls_json,open_questions_json,confidence
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (result_id, assessment_version_id, row["risk_case_version_id"], relevance,
                     coverage, risk_level, self._dump(basis), self._dump(matched_controls),
                     self._dump(missing_controls), self._dump(open_questions), confidence),
                )
                results.append({
                    "risk_result_id": result_id, "risk_case_id": row["risk_case_id"],
                    "risk_case_version_id": row["risk_case_version_id"], "risk_name": row["title"],
                    "publication_level": row["publication_level"], "relevance": round(relevance, 4),
                    "coverage_status": coverage, "risk_level": risk_level, "match_basis": basis,
                    "existing_controls": matched_controls, "missing_controls": missing_controls,
                    "open_questions": open_questions, "confidence": round(confidence, 4), "case": case,
                })
            results.sort(key=lambda item: (-item["relevance"], item["risk_name"]))
            summary = {status: sum(item["coverage_status"] == status for item in results) for status in self.COVERAGE}
            report = {
                "assessment_id": assessment_id, "assessment_version_id": assessment_version_id,
                "version_no": version["version_no"], "material_hash": version["material_hash"],
                "project_name": version["project_name"], "product_code": version["product_code"],
                "product_name": version["product_name"], "assessment_stage": version["assessment_stage"],
                "material_name": version["material_name"], "matched_risk_count": len(results),
                "coverage_summary": summary, "risks": results,
            }
            previous = connection.execute(
                """SELECT version_no,report_json FROM forward_assessment_version
                    WHERE assessment_id=? AND version_no<? ORDER BY version_no DESC LIMIT 1""",
                (assessment_id, version["version_no"]),
            ).fetchone()
            if previous is not None:
                previous_report = json.loads(previous["report_json"] or "{}")
                previous_risks = {row["risk_case_id"]: row for row in previous_report.get("risks", [])}
                current_risks = {row["risk_case_id"]: row for row in results}
                changed = []
                for risk_case_id in sorted(previous_risks.keys() & current_risks.keys()):
                    before, after = previous_risks[risk_case_id], current_risks[risk_case_id]
                    if (before.get("coverage_status"), before.get("risk_level")) != (
                        after.get("coverage_status"), after.get("risk_level")
                    ):
                        changed.append({
                            "risk_case_id": risk_case_id,
                            "risk_name": after.get("risk_name"),
                            "coverage_before": before.get("coverage_status"),
                            "coverage_after": after.get("coverage_status"),
                            "risk_level_before": before.get("risk_level"),
                            "risk_level_after": after.get("risk_level"),
                        })
                report["version_comparison"] = {
                    "previous_version_no": previous["version_no"],
                    "new_risks": [current_risks[key]["risk_name"] for key in sorted(current_risks.keys() - previous_risks.keys())],
                    "resolved_risks": [previous_risks[key]["risk_name"] for key in sorted(previous_risks.keys() - current_risks.keys())],
                    "changed_risks": changed,
                }
            status = "REVIEW_REQUIRED" if any(item["coverage_status"] != "COVERED" for item in results) else "COMPLETED"
            connection.execute(
                "UPDATE forward_assessment_version SET status=?,report_json=? WHERE assessment_version_id=?",
                (status, self._dump(report), assessment_version_id),
            )
            connection.execute(
                "UPDATE forward_assessment SET status=?,updated_at=CURRENT_TIMESTAMP WHERE assessment_id=?",
                (status, assessment_id),
            )
            connection.commit()
        return report

    def get_assessment(self, assessment_id: str) -> dict[str, Any]:
        with self.repository.connect() as connection:
            assessment = connection.execute(
                """SELECT assessment.*,product.product_code,product.product_name
                     FROM forward_assessment assessment JOIN product_config product USING(product_id)
                    WHERE assessment_id=?""", (assessment_id,),
            ).fetchone()
            if assessment is None:
                raise ForwardRiskError("ASSESSMENT_NOT_FOUND")
            version = connection.execute(
                "SELECT * FROM forward_assessment_version WHERE assessment_id=? ORDER BY version_no DESC LIMIT 1",
                (assessment_id,),
            ).fetchone()
            reviews = connection.execute(
                """SELECT risk_result_id,review_status,review_json
                     FROM forward_risk_result WHERE assessment_version_id=?""",
                (version["assessment_version_id"],),
            ).fetchall()
        result = dict(assessment); result["version"] = dict(version)
        report = json.loads(result["version"].pop("report_json") or "{}")
        review_by_id = {
            row["risk_result_id"]: {
                "review_status": row["review_status"],
                "review": json.loads(row["review_json"] or "{}"),
            }
            for row in reviews
        }
        for risk in report.get("risks", []):
            risk.update(review_by_id.get(risk.get("risk_result_id"), {}))
        result["version"]["report"] = report
        return result

    def review_result(self, risk_result_id: str, *, status: str, note: str, reviewed_by: str) -> dict[str, Any]:
        status = status.upper()
        if status not in {"CONFIRMED", "CORRECTED", "NOT_APPLICABLE"} or not reviewed_by.strip():
            raise ForwardRiskError("RISK_REVIEW_INPUT_INVALID")
        with self.repository.connect() as connection:
            row = connection.execute(
                """SELECT result.assessment_version_id,version.assessment_id
                     FROM forward_risk_result result
                     JOIN forward_assessment_version version USING(assessment_version_id)
                    WHERE risk_result_id=?""",
                (risk_result_id,),
            ).fetchone()
            if row is None:
                raise ForwardRiskError("RISK_RESULT_NOT_FOUND")
            review = {"note": note.strip(), "reviewed_by": reviewed_by.strip()}
            connection.execute(
                "UPDATE forward_risk_result SET review_status=?,review_json=? WHERE risk_result_id=?",
                (status, self._dump(review), risk_result_id),
            )
            pending = connection.execute(
                """SELECT COUNT(*) FROM forward_risk_result
                    WHERE assessment_version_id=? AND review_status='PENDING'""",
                (row["assessment_version_id"],),
            ).fetchone()[0]
            if pending == 0:
                connection.execute(
                    "UPDATE forward_assessment_version SET status='COMPLETED' WHERE assessment_version_id=?",
                    (row["assessment_version_id"],),
                )
                connection.execute(
                    "UPDATE forward_assessment SET status='COMPLETED',updated_at=CURRENT_TIMESTAMP WHERE assessment_id=?",
                    (row["assessment_id"],),
                )
            connection.commit()
        return {"risk_result_id": risk_result_id, "review_status": status, "review": review}
