#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality_knowledge.materials import MaterialRepository
from quality_knowledge.repositories.v1_repository import IssueKnowledgeRepository
from quality_knowledge.scenarios import ScenarioRepository
from quality_knowledge.web.app import create_legacy_quality_issue_router


FIXTURE_VERSION = "quality-scenario-w4-functional-fixture/v1"
G5_BUSINESS_KEY = "ITR20261051005"


CASES: tuple[dict[str, Any], ...] = (
    {
        "case_id": "G1_COMPLETE",
        "business_key": "ITR20261051001",
        "title": "异常掉电后关键配方参数丢失",
        "product_model": "PLC-X200",
        "industry": "新能源",
        "customer": "客户A",
        "assessment_result": "需要形成质量场景并纳入回归",
        "resolution_root": "掉电窗口内参数持久化顺序错误，恢复时读取到未提交版本",
        "resolution_solution": "增加双副本提交标记、掉电保护和启动恢复一致性校验",
        "with_missed_test": True,
    },
    {
        "case_id": "G2_NO_MISSED_TEST",
        "business_key": "ITR20261051002",
        "title": "通信链路波动后设备重连超时",
        "product_model": "PLC-X300",
        "industry": "汽车制造",
        "customer": "客户B",
        "assessment_result": "根因和措施已明确，暂无有效漏测分析",
        "resolution_root": "重连状态机在连续链路抖动时未清理旧会话定时器",
        "resolution_solution": "重构重连状态机并增加会话超时清理",
        "with_missed_test": False,
    },
    {
        "case_id": "G3_CONFLICT",
        "business_key": "ITR20261051003",
        "title": "升级后部分历史工程无法正常加载",
        "product_model": "PLC-X400",
        "industry": "装备制造",
        "customer": "客户C",
        "assessment_result": "彻底解决来源存在冲突，必须人工裁决",
        "resolution_root": "版本迁移映射缺失",
        "resolution_solution": "补齐迁移映射",
        "with_missed_test": False,
        "conflicting_resolution": True,
    },
    {
        "case_id": "G4_DUPLICATE_GENERATE",
        "business_key": "ITR20261051004",
        "title": "高频写入后诊断日志占满存储空间",
        "product_model": "PLC-X500",
        "industry": "半导体",
        "customer": "客户D",
        "assessment_result": "用于验证同一冻结来源重复生成不产生重复 Candidate",
        "resolution_root": "日志轮转阈值未覆盖高频异常场景",
        "resolution_solution": "增加按容量和时间双阈值轮转及异常限流",
        "with_missed_test": True,
    },
    {
        "case_id": "G5_SOURCE_REVISION",
        "business_key": G5_BUSINESS_KEY,
        "title": "长时间运行后任务调度抖动导致周期超时",
        "product_model": "PLC-X600",
        "industry": "轨道交通",
        "customer": "客户E",
        "assessment_result": "用于验证来源修订后的重新分析 lineage",
        "resolution_root": "后台诊断任务在长稳运行后与控制周期争用 CPU",
        "resolution_solution": "限制后台任务预算并按优先级调度",
        "with_missed_test": True,
    },
)


def _manifest_path(db_path: Path) -> Path:
    return db_path.with_suffix(db_path.suffix + ".fixture.json")


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _create_issue(repo: IssueKnowledgeRepository, case: dict[str, Any]) -> tuple[str, str]:
    business_key = case["business_key"]
    knowledge_id = "QK-W4-" + case["case_id"]
    version_id = knowledge_id + "-V1"
    normalized = {
        "issue_fact": {
            "title": case["title"],
            "description": case["title"],
            "impact": "影响客户连续业务或关键数据正确性",
            "severity": "MAJOR",
            "issue_type": "SOFTWARE",
            "industry": case["industry"],
            "customer": case["customer"],
            "month": "10",
            "year": "2026",
            "product": "PLC",
            "platform": case["product_model"],
            "issue_domain": "SOFTWARE",
        },
        "product_context": {"product": "PLC", "product_model": case["product_model"]},
        "occurrence": {},
        "escape": {},
        "solution": {},
        "verification": {},
        "product_extension": {},
    }
    with repo.connect() as connection:
        connection.execute(
            """INSERT INTO quality_issue(
                   knowledge_id,business_type,business_issue_id,current_version_id,status
               ) VALUES(?,?,?,?,?)
               ON CONFLICT(knowledge_id) DO NOTHING""",
            (knowledge_id, "PLC", business_key, version_id, "ACTIVE"),
        )
        connection.execute(
            """INSERT INTO quality_issue_version(
                   issue_version_id,knowledge_id,version_no,normalized_source_hash,
                   title,description,impact,severity,issue_type,is_defect,
                   industry,customer,month,year,year_source,month_source,
                   product,platform,issue_domain,issue_domain_source,normalized_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(issue_version_id) DO NOTHING""",
            (
                version_id,
                knowledge_id,
                1,
                _hash(normalized),
                case["title"],
                case["title"],
                "影响客户连续业务或关键数据正确性",
                "MAJOR",
                "SOFTWARE",
                "YES",
                case["industry"],
                case["customer"],
                "10",
                "2026",
                "ISSUE_ID",
                "SOURCE_DATA",
                "PLC",
                case["product_model"],
                "SOFTWARE",
                "FIXTURE",
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        connection.execute(
            "UPDATE quality_issue SET current_version_id=? WHERE knowledge_id=?",
            (version_id, knowledge_id),
        )
    return knowledge_id, version_id


def _save_analysis(
    repo: IssueKnowledgeRepository,
    *,
    knowledge_id: str,
    version_id: str,
    analysis_type: str,
    result: dict[str, Any],
    run_suffix: str = "V1",
) -> str:
    run_id = f"RUN-W4-{analysis_type.upper()}-{knowledge_id}-{run_suffix}"
    repo.start_analysis_run(
        {
            "analysis_run_id": run_id,
            "knowledge_id": knowledge_id,
            "issue_version_id": version_id,
            "analysis_type": analysis_type,
            "model_provider": "W4_FUNCTIONAL_FIXTURE",
            "prompt_name": "FIXTURE_SOURCE_FACT_ONLY",
            "prompt_version": "1",
            "schema_version": "1",
            "engine_version": "fixture",
            "status": "RUNNING",
            "input_hash": _hash(result),
        }
    )
    repo.save_analysis_result(knowledge_id, version_id, run_id, analysis_type, result)
    repo.finish_analysis_run(run_id, "COMPLETED", model_name="fixture-source-fact")
    return run_id


def _assessment_raw(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "问题信息_问题描述": case["title"],
        "问题信息_产品编码": "PLC",
        "问题信息_产品型号": case["product_model"],
        "问题信息_客户行业": case["industry"],
        "问题信息_客户名称": case["customer"],
        "问题信息_IPMT": "工业自动化IPMT",
        "问题信息_SPDT": "PLC平台SPDT",
        "问题信息_问题发生阶段": "客户现场运行",
        "问题信息_问题领域": "软件",
        "数据运营_KPI计入月份": "2026-10",
        "考核信息_考核状态": "已考核",
        "考核信息_考核结论": case["assessment_result"],
        "责任信息_责任部门": "软件平台部",
        "责任信息_责任人": "W4测试责任人",
    }


def _itr_raw(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "问题信息_ITR单号": case["business_key"],
        "ITR单号": case["business_key"],
        "问题信息_问题描述": case["title"],
        "问题信息_故障现象描述": case["title"],
        "问题信息_产品编码": "PLC",
        "问题信息_产品型号": case["product_model"],
        "问题信息_客户行业": case["industry"],
        "问题信息_客户名称": case["customer"],
        "问题信息_IPMT": "工业自动化IPMT",
        "问题信息_SPDT": "PLC平台SPDT",
        "问题信息_问题发生阶段": "客户现场运行",
        "问题信息_问题发生时间": "2026-10-05 10:00:00",
    }


def _resolution_raw(case: dict[str, Any], *, revision: int = 1) -> dict[str, Any]:
    suffix = "" if revision == 1 else "；复核后补充边界条件并更新验证策略"
    return {
        "问题信息_问题描述": case["title"],
        "问题信息_产品编码": "PLC",
        "问题信息_产品型号": case["product_model"],
        "问题信息_客户行业": case["industry"],
        "问题信息_客户名称": case["customer"],
        "问题信息_IPMT": "工业自动化IPMT",
        "问题信息_SPDT": "PLC平台SPDT",
        "问题信息_问题发生阶段": "客户现场运行",
        "问题信息_问题原因定位": case["resolution_root"] + suffix,
        "技术根因分析与纠正_TRC根因": case["resolution_root"] + suffix,
        "问题处理结果_问题解决方案": case["resolution_solution"] + suffix,
        "验证结果": "专项回归通过，关键边界场景已补充" if revision > 1 else "专项回归通过",
    }


def _missed_test_raw(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "ITR单号": case["business_key"],
        "问题信息_问题描述": case["title"],
        "漏测分析_遗漏测试项": "缺少异常边界、持续运行或连续扰动组合场景",
        "漏测分析_遗漏原因": "测试设计只覆盖单次正常恢复路径，未覆盖连续异常和状态残留",
        "漏测分析_补充验证": "增加故障注入、重复恢复、长稳和资源压力组合回归",
    }


def _add_conflicting_resolution_group(materials: MaterialRepository) -> None:
    with materials.connect() as connection:
        connection.execute(
            """INSERT OR IGNORE INTO data_group(
                   group_id,group_code,group_name,material_type,enabled,
                   include_ai,include_insight,include_report
               ) VALUES(?,?,?,?,1,0,0,0)""",
            (
                "DG-ITR-CS-CONFLICT",
                "ITR-CS-CONFLICT",
                "彻底解决单冲突来源（W4测试）",
                "ITR_CS",
            ),
        )


def build_fixture(db_path: Path, *, reset: bool = False) -> dict[str, Any]:
    db_path = db_path.expanduser().resolve()
    manifest_path = _manifest_path(db_path)
    if reset and db_path.exists():
        if not manifest_path.is_file():
            raise RuntimeError("REFUSE_RESET_NON_FIXTURE_DB")
        db_path.unlink()
        manifest_path.unlink(missing_ok=True)
    if db_path.exists():
        raise RuntimeError("FIXTURE_DB_ALREADY_EXISTS_USE_RESET")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Initialize the complete mature Quality host schema so the fixture can be
    # bound read-only through Overall's strict legacy DB compatibility gate.
    # This still creates only source/business-side structures; no QSV1 lifecycle
    # outcome is pre-seeded.
    bootstrap_router = create_legacy_quality_issue_router(
        db_path,
        initialize_schema=True,
        # The fixture is the separately bound mature/source database only.
        # QSV1 lifecycle state belongs to the Overall primary P0 database at
        # runtime, so do not initialize the V1 store in this supporting DB.
        qsv1_db_path=None,
    )
    # The bootstrap router owns closures over several SQLite-backed services.
    # Drop that temporary object graph immediately so Windows can relocate or
    # delete the fixture DB after build without lingering file handles.
    del bootstrap_router
    gc.collect()
    issues = IssueKnowledgeRepository(db_path)
    materials = MaterialRepository(db_path)
    ScenarioRepository(db_path)
    _add_conflicting_resolution_group(materials)

    manifest: dict[str, Any] = {
        "contract": FIXTURE_VERSION,
        "synthetic_business_data": True,
        "direct_qsv1_candidate_write": False,
        "direct_qsv1_publish_write": False,
        # Persist only a manifest-relative locator. Absolute CI/build paths are
        # not portable after ZIP extraction on another machine.
        "database": db_path.name,
        "database_path_semantics": "MANIFEST_RELATIVE",
        "cases": [],
        "g5_resolution_revision": 1,
    }

    for index, case in enumerate(CASES, start=1):
        knowledge_id, version_id = _create_issue(issues, case)
        assessment_id, _ = materials.add_material(
            materials.group("SW-OPS"),
            case["business_key"],
            _assessment_raw(case),
            "W4_FUNCTIONAL_FIXTURE.xlsx",
            "软件考核",
            index + 1,
        )
        itr_id, _ = materials.add_material(
            materials.group("ITR"),
            case["business_key"],
            _itr_raw(case),
            "W4_FUNCTIONAL_FIXTURE.xlsx",
            "ITR",
            index + 1,
        )
        resolution_id, _ = materials.add_material(
            materials.group("ITR-CS"),
            case["business_key"] + "CS",
            _resolution_raw(case),
            "W4_FUNCTIONAL_FIXTURE.xlsx",
            "彻底解决单",
            index + 1,
        )
        missed_test_id = ""
        if case.get("with_missed_test"):
            missed_test_id, _ = materials.add_material(
                materials.group("ESCAPE"),
                case["business_key"],
                _missed_test_raw(case),
                "W4_FUNCTIONAL_FIXTURE.xlsx",
                "漏测分析",
                index + 1,
            )
            _save_analysis(
                issues,
                knowledge_id=knowledge_id,
                version_id=version_id,
                analysis_type="escape",
                result={
                    "escape_cause_summary": "测试设计未覆盖连续异常、状态残留和长稳组合条件",
                    "verification_gap": "缺少异常边界 + 长稳 + 重复恢复的组合场景",
                    "expected_detection_stage": "系统测试",
                },
            )
        _save_analysis(
            issues,
            knowledge_id=knowledge_id,
            version_id=version_id,
            analysis_type="occurrence",
            result={
                "root_cause_summary": case["resolution_root"],
            },
        )

        conflict_material_id = ""
        if case.get("conflicting_resolution"):
            conflict_group = materials.group("ITR-CS-CONFLICT")
            conflict_material_id, _ = materials.add_material(
                conflict_group,
                case["business_key"] + "CS",
                {
                    **_resolution_raw(case),
                    "问题信息_问题原因定位": "另一路彻底解决记录给出不同根因：第三方插件兼容矩阵错误",
                    "技术根因分析与纠正_TRC根因": "第三方插件兼容矩阵错误",
                    "问题处理结果_问题解决方案": "采用另一套兼容性修复策略",
                },
                "W4_FUNCTIONAL_FIXTURE_CONFLICT.xlsx",
                "彻底解决单冲突",
                index + 1,
            )

        manifest["cases"].append(
            {
                "case_id": case["case_id"],
                "business_key": case["business_key"],
                "knowledge_id": knowledge_id,
                "issue_version_id": version_id,
                "software_assessment_material_id": assessment_id,
                "itr_material_id": itr_id,
                "resolution_material_id": resolution_id,
                "missed_test_material_id": missed_test_id,
                "conflict_resolution_material_id": conflict_material_id,
                "expected_source_status": {
                    "SOFTWARE_ASSESSMENT": "PRESENT",
                    "RESOLUTION": "CONFLICT" if case.get("conflicting_resolution") else "PRESENT",
                    "ITR": "PRESENT",
                    "MISSED_TEST": "PRESENT" if case.get("with_missed_test") else "MISSING",
                },
            }
        )

    materials.refresh_links()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Repositories intentionally do not own long-lived connections, but some
    # SQLite cursors/connections can stay in cyclic garbage after schema-heavy
    # fixture setup.  Release those transient objects before returning so a
    # Fresh Extract relocation can remove the source DB on Windows.
    del issues
    del materials
    gc.collect()
    return manifest



def augment_fixture(db_path: Path) -> dict[str, Any]:
    """Add the controlled G1-G5 source fixtures to an existing mature DB copy.

    This is intentionally for copied test databases only. It never resets or
    deletes existing product data and it never writes QSV1 Candidate/Publish
    outcomes directly.
    """
    db_path = db_path.expanduser().resolve()
    if not db_path.is_file():
        raise RuntimeError("AUGMENT_TARGET_DB_NOT_FOUND")

    manifest_path = _manifest_path(db_path)
    bootstrap_router = create_legacy_quality_issue_router(
        db_path,
        initialize_schema=True,
        qsv1_db_path=None,
    )
    del bootstrap_router
    gc.collect()

    issues = IssueKnowledgeRepository(db_path)
    materials = MaterialRepository(db_path)
    ScenarioRepository(db_path)
    _add_conflicting_resolution_group(materials)

    manifest: dict[str, Any] = {
        "contract": FIXTURE_VERSION,
        "synthetic_business_data": True,
        "augmented_existing_db_copy": True,
        "direct_qsv1_candidate_write": False,
        "direct_qsv1_publish_write": False,
        "database": db_path.name,
        "database_path_semantics": "MANIFEST_RELATIVE",
        "cases": [],
        "g5_resolution_revision": 1,
    }

    for index, case in enumerate(CASES, start=1):
        knowledge_id, version_id = _create_issue(issues, case)
        assessment_id, _ = materials.add_material(
            materials.group("SW-OPS"),
            case["business_key"],
            _assessment_raw(case),
            "W4_FUNCTIONAL_FIXTURE.xlsx",
            "软件考核",
            index + 1,
        )
        itr_id, _ = materials.add_material(
            materials.group("ITR"),
            case["business_key"],
            _itr_raw(case),
            "W4_FUNCTIONAL_FIXTURE.xlsx",
            "ITR",
            index + 1,
        )
        resolution_id, _ = materials.add_material(
            materials.group("ITR-CS"),
            case["business_key"] + "CS",
            _resolution_raw(case),
            "W4_FUNCTIONAL_FIXTURE.xlsx",
            "彻底解决单",
            index + 1,
        )
        missed_test_id = ""
        if case.get("with_missed_test"):
            missed_test_id, _ = materials.add_material(
                materials.group("ESCAPE"),
                case["business_key"],
                _missed_test_raw(case),
                "W4_FUNCTIONAL_FIXTURE.xlsx",
                "漏测分析",
                index + 1,
            )
            _save_analysis(
                issues,
                knowledge_id=knowledge_id,
                version_id=version_id,
                analysis_type="escape",
                result={
                    "escape_cause_summary": "测试设计未覆盖连续异常、状态残留和长稳组合条件",
                    "verification_gap": "缺少异常边界 + 长稳 + 重复恢复的组合场景",
                    "expected_detection_stage": "系统测试",
                },
                run_suffix="AUG",
            )
        _save_analysis(
            issues,
            knowledge_id=knowledge_id,
            version_id=version_id,
            analysis_type="occurrence",
            result={"root_cause_summary": case["resolution_root"]},
            run_suffix="AUG",
        )

        conflict_material_id = ""
        if case.get("conflicting_resolution"):
            conflict_group = materials.group("ITR-CS-CONFLICT")
            conflict_material_id, _ = materials.add_material(
                conflict_group,
                case["business_key"] + "CS",
                {
                    **_resolution_raw(case),
                    "问题信息_问题原因定位": "另一路彻底解决记录给出不同根因：第三方插件兼容矩阵错误",
                    "技术根因分析与纠正_TRC根因": "第三方插件兼容矩阵错误",
                    "问题处理结果_问题解决方案": "采用另一套兼容性修复策略",
                },
                "W4_FUNCTIONAL_FIXTURE_CONFLICT.xlsx",
                "彻底解决单冲突",
                index + 1,
            )

        manifest["cases"].append(
            {
                "case_id": case["case_id"],
                "business_key": case["business_key"],
                "knowledge_id": knowledge_id,
                "issue_version_id": version_id,
                "software_assessment_material_id": assessment_id,
                "itr_material_id": itr_id,
                "resolution_material_id": resolution_id,
                "missed_test_material_id": missed_test_id,
                "conflict_resolution_material_id": conflict_material_id,
                "expected_source_status": {
                    "SOFTWARE_ASSESSMENT": "PRESENT",
                    "RESOLUTION": "CONFLICT" if case.get("conflicting_resolution") else "PRESENT",
                    "ITR": "PRESENT",
                    "MISSED_TEST": "PRESENT" if case.get("with_missed_test") else "MISSING",
                },
            }
        )

    materials.refresh_links()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    del issues
    del materials
    gc.collect()
    return manifest


def advance_g5(db_path: Path) -> dict[str, Any]:
    db_path = db_path.expanduser().resolve()
    manifest_path = _manifest_path(db_path)
    if not db_path.is_file() or not manifest_path.is_file():
        raise RuntimeError("W4_FIXTURE_NOT_FOUND")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("contract") != FIXTURE_VERSION:
        raise RuntimeError("W4_FIXTURE_CONTRACT_MISMATCH")

    case = next(item for item in CASES if item["case_id"] == "G5_SOURCE_REVISION")
    materials = MaterialRepository(db_path)
    material_id, action = materials.add_material(
        materials.group("ITR-CS"),
        case["business_key"] + "CS",
        _resolution_raw(case, revision=2),
        "W4_FUNCTIONAL_FIXTURE_REV2.xlsx",
        "彻底解决单",
        2,
    )
    materials.refresh_links()
    manifest["g5_resolution_revision"] = 2
    manifest["g5_revision_material_id"] = material_id
    manifest["g5_revision_action"] = action
    # Repair older packaged manifests that captured an absolute CI path.
    # The command-line --db argument is the runtime source of truth.
    manifest["database"] = db_path.name
    manifest["database_path_semantics"] = "MANIFEST_RELATIVE"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    del materials
    gc.collect()
    return manifest


def _counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as connection:
        def c(sql: str) -> int:
            return int(connection.execute(sql).fetchone()[0] or 0)
        return {
            "quality_issue": c("SELECT COUNT(*) FROM quality_issue"),
            "software_assessment": c(
                "SELECT COUNT(*) FROM source_material WHERE material_type='SOFTWARE_OPERATION'"
            ),
            "itr_source": c("SELECT COUNT(*) FROM source_material WHERE material_type='ITR_SOURCE'"),
            "resolution": c("SELECT COUNT(*) FROM source_material WHERE material_type='ITR_CS'"),
            "missed_test_material": c(
                "SELECT COUNT(*) FROM source_material WHERE material_type='ESCAPE_ANALYSIS'"
            ),
            "analysis_run": c("SELECT COUNT(*) FROM analysis_run WHERE status='COMPLETED'"),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build explicit W4 functional Source Fact fixtures without writing QSV1 outcomes."
    )
    parser.add_argument(
        "--db",
        default="validation/quality_scenario_w4_fixture.db",
        help="Dedicated validation SQLite database path.",
    )
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--advance-g5", action="store_true")
    parser.add_argument("--augment-existing", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    if args.advance_g5:
        manifest = advance_g5(db_path)
        print("W4_FIXTURE_ACTION=ADVANCE_G5_SOURCE_REVISION")
    elif args.augment_existing:
        manifest = augment_fixture(db_path)
        print("W4_FIXTURE_ACTION=AUGMENT_EXISTING_DB_COPY")
    else:
        manifest = build_fixture(db_path, reset=args.reset)
        print("W4_FIXTURE_ACTION=BUILD")

    print(f"W4_FIXTURE_CONTRACT={manifest['contract']}")
    print("SYNTHETIC_BUSINESS_DATA=YES")
    print("DIRECT_QSV1_CANDIDATE_WRITE=NO")
    print("DIRECT_QSV1_PUBLISH_WRITE=NO")
    print(f"FIXTURE_DB={db_path}")
    print(f"FIXTURE_MANIFEST={_manifest_path(db_path)}")
    for key, value in _counts(db_path).items():
        print(f"{key.upper()}_COUNT={value}")
    print("W4_FUNCTIONAL_FIXTURE=READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
