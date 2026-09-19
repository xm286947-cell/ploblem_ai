from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from builder.m2_runner import run_m2
from builder.m3_runner import run_m3
from builder.m4_runner import run_m4
from builder.m5_runner import run_m5
from builder.m6_runner import run_m6
from builder.m7_runner import run_m7
from builder.m71_query_runner import run_m71_query
from builder.m72_normalizer_runner import run_m72_normalizer
from builder.m72_ai_runner import run_m72_ai
from builder.m72_standard_builder_runner import run_m72_standard_builder
from builder.query_enricher import run_m72_pipeline
from builder.m73_profile_runner import run_m73_profile
from builder.m73_retriever_runner import run_m73_retrieve
from builder.m81_candidate_runner import run_m81_load
from builder.m82_similarity_runner import run_m82_similarity
from builder.m83_solution_runner import run_m83_solution
from builder.m84_repeat_runner import run_m84_decision
from builder.m85_delivery_runner import run_m85_delivery
from builder.pipeline_runner import run_all
from builder.analysis_pipeline_runner import run_analysis_pipeline
from builder.local_batch_runner import run_local_batch
from builder.validators import SchemaValidationError, validate_json_file


ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="REPEAT_CASE_ENGINE V2.3 M8.5")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-case", help="校验Standard Case JSON")
    validate.add_argument("json_file")
    validate.add_argument("--schema", default=str(ROOT / "schema/standard_case.schema.json"))

    m2 = subparsers.add_parser("run-m2", help="运行Excel解析与报告匹配")
    m2.add_argument("--excel", default=None)
    m2.add_argument("--reports-dir", default=None)

    m3 = subparsers.add_parser("run-m3", help="解析PDF并更新raw_evidence")
    m3.add_argument("--case-id", default=None)

    m4 = subparsers.add_parser("run-m4", help="生成纯事实Standard Case")
    m4.add_argument("--case-id", default=None)

    m5 = subparsers.add_parser("run-m5", help="AI增强Standard Case")
    m5.add_argument("--case-id", default=None)
    m5.add_argument("--mock", action="store_true")
    m5.add_argument("--overwrite", action="store_true")

    m6 = subparsers.add_parser("run-m6", help="构建检索文档、Embedding、Manifest和索引")
    m6.add_argument("--case-id", default=None)
    m6.add_argument("--overwrite", action="store_true")

    m71 = subparsers.add_parser("run-m7-query", help="解析new_cases.xlsx并生成Raw Query")
    m71.add_argument("--input", default=None, help="待分析问题Excel，默认input/new_cases.xlsx")
    m71.add_argument("--overwrite", action="store_true", help="覆盖已有Raw Query JSON")

    m72n = subparsers.add_parser("run-m72-normalize", help="将Raw Query标准化为Normalized Query")
    m72n.add_argument("--query-id", default=None)
    m72n.add_argument("--overwrite", action="store_true")

    m72a = subparsers.add_parser("run-m72-ai", help="将Normalized Query进行AI增强")
    m72a.add_argument("--query-id", default=None)
    m72a.add_argument("--overwrite", action="store_true")
    m72a.add_argument("--mock", action="store_true", help="使用本地Mock响应")
    m72a.add_argument("--skip-ai", action="store_true", help="跳过AI并生成降级Enriched Query")

    m72b = subparsers.add_parser("run-m72-build", help="将Enriched Query组装为Standard Query")
    m72b.add_argument("--query-id", default=None)
    m72b.add_argument("--overwrite", action="store_true")

    m72 = subparsers.add_parser("run-m72", help="运行Query Enricher完整Pipeline")
    m72.add_argument("--query-id", default=None, help="仅处理指定Query ID")
    m72.add_argument("--overwrite", action="store_true", help="覆盖各阶段已有输出")
    m72.add_argument("--mock", action="store_true", help="AI阶段使用本地Mock响应")
    m72.add_argument("--skip-ai", action="store_true", help="跳过AI并降级构建Standard Query")
    m72.add_argument(
        "--from-stage",
        choices=["normalize", "ai", "build"],
        default="normalize",
        help="从指定阶段开始执行并继续到Builder",
    )

    m73p = subparsers.add_parser("run-m73-profile", help="从Standard Query生成Retrieval Profile")
    m73p.add_argument("--query-id", default=None)
    m73p.add_argument("--overwrite", action="store_true")

    m73r = subparsers.add_parser("run-m73-retrieve", help="使用Retrieval Profile调用现有Retriever")
    m73r.add_argument("--query-id", default=None)
    m73r.add_argument("--top-k", type=int, default=None)
    m73r.add_argument("--overwrite", action="store_true")

    m81 = subparsers.add_parser("run-m81-load", help="加载候选案例并生成Analysis Context")
    m81.add_argument("--query-id", default=None)
    m81.add_argument("--top-k", type=int, default=None)
    m81.add_argument("--overwrite", action="store_true")

    m82 = subparsers.add_parser("run-m82-similarity", help="分析新问题与候选历史案例的多维相似性")
    m82.add_argument("--query-id", default=None)
    m82.add_argument("--case-id", default=None)
    m82.add_argument("--overwrite", action="store_true")
    m82.add_argument("--mock", action="store_true", help="使用本地Mock响应")
    m82.add_argument("--skip-ai", action="store_true", help="跳过AI并生成UNKNOWN降级结果")

    m83 = subparsers.add_parser("run-m83-solution", help="分析历史案例解决方案有效性与复用价值")
    m83.add_argument("--query-id", default=None)
    m83.add_argument("--case-id", default=None)
    m83.add_argument("--overwrite", action="store_true")
    m83.add_argument("--mock", action="store_true", help="使用本地Mock响应")
    m83.add_argument("--skip-ai", action="store_true", help="跳过AI并生成UNKNOWN降级结果")

    m84 = subparsers.add_parser("run-m84-decision", help="综合相似性与解决方案分析，生成重复问题判定")
    m84.add_argument("--query-id", default=None)
    m84.add_argument("--overwrite", action="store_true")
    m84.add_argument("--mock", action="store_true", help="使用本地Mock响应")
    m84.add_argument("--skip-ai", action="store_true", help="跳过AI并生成证据不足降级结果")

    m85 = subparsers.add_parser("run-m85-delivery", help="从RepeatAnalysis生成正式Report JSON与Markdown交付件")
    m85.add_argument("--query-id", default=None)
    m85.add_argument("--overwrite", action="store_true")

    m7 = subparsers.add_parser("run-m7", help="检索相似历史案例")
    m7.add_argument("--text", required=True, help="新问题描述")
    m7.add_argument("--top-k", type=int, default=None)
    m7.add_argument("--ipmt", default="")
    m7.add_argument("--spdt", default="")
    m7.add_argument("--department", default="")
    m7.add_argument("--product", default="")
    m7.add_argument("--domain", default="")
    m7.add_argument("--cause-level1", default="")
    m7.add_argument("--cause-level2", default="")
    m7.add_argument("--cause-description", default="", help="原因描述、TRC、根因或失效机制")
    m7.add_argument("--solution", default="", help="纠正、预防或可复用解决措施")

    analysis_cmd = subparsers.add_parser("run-analysis", help="端到端运行新问题重复案例分析链路")
    analysis_cmd.add_argument("--input", default=None, help="待分析问题Excel，默认input/new_cases.xlsx")
    analysis_cmd.add_argument("--query-id", default=None, help="仅处理指定Query ID")
    analysis_cmd.add_argument("--from-stage", choices=["parse", "enrich", "profile", "retrieve", "load", "similarity", "solution", "decision", "delivery"], default="parse")
    analysis_cmd.add_argument("--top-k", type=int, default=None)
    analysis_cmd.add_argument("--overwrite", action="store_true")
    analysis_cmd.add_argument("--mock", action="store_true", help="AI阶段使用本地Mock响应")
    analysis_cmd.add_argument("--skip-ai", action="store_true", help="跳过AI并生成降级结果")

    batch = subparsers.add_parser("run-batch", help="本地真实案例批量运行、断点续跑与诊断")
    batch.add_argument("--input", default=None, help="待分析问题Excel，默认input/new_cases.xlsx")
    batch.add_argument("--run-id", default=None, help="指定Run ID；续跑时必填")
    batch.add_argument("--resume", action="store_true", help="续跑已有Run")
    batch.add_argument("--retry-failed", action="store_true", help="续跑时仅重跑失败Query")
    batch.add_argument("--top-k", type=int, default=None)
    batch.add_argument("--no-overwrite", action="store_true", help="不覆盖阶段已有结果")
    batch.add_argument("--mock", action="store_true", help="AI阶段使用本地Mock")
    batch.add_argument("--skip-ai", action="store_true", help="跳过AI并生成降级结果")
    batch.add_argument("--include-sensitive-debug", action="store_true", help="在Run目录复制敏感中间数据；默认关闭")

    ki = subparsers.add_parser("knowledge-import", help="Design V1.0 M1: 增量导入质量问题（支持自动业务识别/Issue Version）")
    ki.add_argument("--input", required=True, help="源Excel文件")
    ki.add_argument("--business-type", default=None, choices=["HMI","PLC","IFA"], help="可选；不传则自动识别")
    ki.add_argument("--sheet", default=None)
    ki.add_argument("--db", default=str(ROOT / "knowledge/quality_issue_v1.db"))

    kw = subparsers.add_parser("knowledge-web", help="Design V1.0 M2: 启动Quality Issue Knowledge Web")
    kw.add_argument("--db", default=str(ROOT / "knowledge/quality_issue_v1.db"))
    kw.add_argument("--host", default="127.0.0.1")
    kw.add_argument("--port", type=int, default=8080)
    kw.add_argument("--debug", action="store_true")

    major_init = subparsers.add_parser("major-knowledge-init", help="REQ-022 初始化独立重大复盘知识库")
    major_init.add_argument("--db", default=str(ROOT / "knowledge/major_knowledge/knowledge.sqlite3"))
    major_init.add_argument("--attachments", default=str(ROOT / "knowledge/major_knowledge/attachments"))

    major_ingest = subparsers.add_parser("major-case-ingest", help="REQ-022 导入PDF/DOCX并提取知识（合成/脱敏材料）")
    major_ingest.add_argument("--db", default=str(ROOT / "knowledge/major_knowledge/knowledge.sqlite3"))
    major_ingest.add_argument("--attachments", default=str(ROOT / "knowledge/major_knowledge/attachments"))
    major_ingest.add_argument("--business-db", default="", help="可选，只读业务库；省略时不自动命中ITR")
    major_ingest.add_argument("--input", required=True)
    major_ingest.add_argument("--title", required=True)
    major_ingest.add_argument("--group", required=True)
    major_ingest.add_argument("--domain", default="")
    major_ingest.add_argument("--itr", action="append", default=[])

    major_benchmark = subparsers.add_parser("major-knowledge-benchmark", help="REQ-022 运行合成数据库性能检查（不含AI）")
    major_benchmark.add_argument("--db", required=True)
    major_benchmark.add_argument("--attachments", required=True)
    major_benchmark.add_argument("--events", type=int, default=10000)
    major_benchmark.add_argument("--fragments", type=int, default=100000)
    major_benchmark.add_argument("--iterations", type=int, default=40)
    major_benchmark.add_argument("--report", default="")

    major_backup = subparsers.add_parser("major-knowledge-backup", help="REQ-022 一致性备份独立知识库与附件")
    major_backup.add_argument("--db", required=True)
    major_backup.add_argument("--attachments", required=True)
    major_backup.add_argument("--output", required=True)

    major_restore = subparsers.add_parser("major-knowledge-restore", help="REQ-022 校验并恢复到全新目标目录")
    major_restore.add_argument("--backup", required=True)
    major_restore.add_argument("--db", required=True)
    major_restore.add_argument("--attachments", required=True)

    p0_init = subparsers.add_parser("knowledge-p0-init", help="初始化干净的 Quality Capability P0 数据库")
    p0_init.add_argument("--db", default=str(ROOT / "knowledge/quality_capability_p0.db"))
    p0_init.add_argument(
        "--manifest",
        default=str(ROOT / "quality_knowledge/config/p0_seed_manifest.json"),
        help="冻结的 P0 Seed Manifest",
    )
    p0_init.add_argument(
        "--plc-seed",
        default=str(ROOT / "quality_knowledge/config/plc_fields.yaml"),
        help="PLC 标准字段基线 Seed",
    )
    p0_init.add_argument(
        "--legacy-backup-source",
        default=None,
        help="可选旧库文件；只做字节复制和 SHA-256 校验，绝不作为 SQLite 打开",
    )
    p0_init.add_argument("--backup-directory", default=None)
    p0_init.add_argument("--report", default=None, help="可选初始化结果 JSON")

    p0_status = subparsers.add_parser("knowledge-p0-status", help="校验 P0 数据库是否为 READY")
    p0_status.add_argument("--db", default=str(ROOT / "knowledge/quality_capability_p0.db"))
    p0_status.add_argument(
        "--manifest",
        default=str(ROOT / "quality_knowledge/config/p0_seed_manifest.json"),
    )
    p0_status.add_argument(
        "--plc-seed",
        default=str(ROOT / "quality_knowledge/config/plc_fields.yaml"),
    )

    p0_web = subparsers.add_parser("knowledge-p0-web", help="启动干净 P0 数据库的 V2 Web/API")
    p0_web.add_argument("--db", default=str(ROOT / "knowledge/quality_capability_p0.db"))
    p0_web.add_argument("--host", default="127.0.0.1")
    p0_web.add_argument("--port", type=int, default=8080)

    p1_init = subparsers.add_parser("knowledge-p1-init", help="初始化 Quality Capability P1 数据库")
    p1_init.add_argument("--db", default=str(ROOT / "knowledge/quality_capability_p1.db"))
    p1_init.add_argument("--manifest", default=str(ROOT / "quality_knowledge/config/p0_seed_manifest.json"))
    p1_init.add_argument("--plc-seed", default=str(ROOT / "quality_knowledge/config/plc_fields.yaml"))
    p1_init.add_argument("--legacy-backup-source", default=None)
    p1_init.add_argument("--backup-directory", default=None)
    p1_init.add_argument("--report", default=None)

    p1_status = subparsers.add_parser("knowledge-p1-status", help="校验 P1 数据库是否为 READY")
    p1_status.add_argument("--db", default=str(ROOT / "knowledge/quality_capability_p1.db"))
    p1_status.add_argument("--manifest", default=str(ROOT / "quality_knowledge/config/p0_seed_manifest.json"))
    p1_status.add_argument("--plc-seed", default=str(ROOT / "quality_knowledge/config/plc_fields.yaml"))

    p1_web = subparsers.add_parser("knowledge-p1-web", help="启动已初始化的 P1 Web/API")
    p1_web.add_argument("--db", default=str(ROOT / "knowledge/quality_capability_p1.db"))
    p1_web.add_argument("--host", default="127.0.0.1")
    p1_web.add_argument("--port", type=int, default=8080)

    p1_start = subparsers.add_parser("knowledge-p1-start", help="首次自动初始化并启动 P1（推荐入口）")
    p1_start.add_argument("--db", default=str(ROOT / "knowledge/quality_capability_p1.db"))
    p1_start.add_argument("--manifest", default=str(ROOT / "quality_knowledge/config/p0_seed_manifest.json"))
    p1_start.add_argument("--plc-seed", default=str(ROOT / "quality_knowledge/config/plc_fields.yaml"))
    p1_start.add_argument("--host", default="127.0.0.1")
    p1_start.add_argument("--port", type=int, default=8080)

    kmig = subparsers.add_parser("knowledge-release-migrate", help="P3: 历史数据库 Dry Run → Report → Apply")
    kmig.add_argument("--source", help="历史 SQLite 数据库；Dry Run 必填")
    kmig.add_argument("--target", help="P3 目标 SQLite 数据库；Dry Run 必填")
    kmig.add_argument("--report", required=True, help="迁移报告 JSON")
    kmig.add_argument("--apply", action="store_true", help="按已有成功报告执行迁移")

    kq = subparsers.add_parser("knowledge-query", help="Design V1.0 M1: 查询Current Version")
    kq.add_argument("--db", default=str(ROOT / "knowledge/quality_issue_v1.db"))
    kq.add_argument("--business-type", default=None, choices=["HMI","PLC","IFA"])
    kq.add_argument("--business-issue-id", default=None)
    kq.add_argument("--product", default=None)
    kq.add_argument("--platform", default=None)
    kq.add_argument("--limit", type=int, default=100)

    ka = subparsers.add_parser("knowledge-analyze", help="Design V1.0 M3: AI分析Current Issue Version")
    ka.add_argument("--db", default=str(ROOT / "knowledge/quality_issue_v1.db"))
    ka.add_argument("--knowledge-id", default=None)
    ka.add_argument("--business-type", default=None, choices=["HMI","PLC","IFA"])
    ka.add_argument("--only-missing", action="store_true", help="兼容参数；当前默认即复用已完成阶段")
    ka.add_argument("--force", action="store_true", help="强制重新分析当前Issue Version的已完成阶段")

    kac = subparsers.add_parser("knowledge-ai-check", help="检查Quality Issue AI model.yaml配置与连通性")
    kac.add_argument("--live", action="store_true", help="实际调用一次模型接口")

    ks = subparsers.add_parser("knowledge-stats", help="Design V1.0 M4: Current Version + Latest Valid Analysis统计")
    ks.add_argument("--db", default=str(ROOT / "knowledge/quality_issue_v1.db"))
    ks.add_argument("--business-type", default=None, choices=["HMI","PLC","IFA"])
    ks.add_argument("--limit", type=int, default=20)

    ke = subparsers.add_parser("knowledge-export", help="Design V1.0 M4: 导出Current Knowledge/AI结果")
    ke.add_argument("--db", default=str(ROOT / "knowledge/quality_issue_v1.db"))
    ke.add_argument("--output", required=True)
    ke.add_argument("--format", choices=["xlsx","csv"], default="xlsx")
    ke.add_argument("--dataset", choices=["issues","capability_gaps"], default="issues")
    ke.add_argument("--business-type", default=None, choices=["HMI","PLC","IFA"])

    kmm = subparsers.add_parser("knowledge-mapping-migrate", help="Addendum 01 A2: legacy YAML mapping migration")
    src=kmm.add_mutually_exclusive_group(required=True)
    src.add_argument("--all", action="store_true", help="migrate HMI/PLC/IFA config YAML")
    src.add_argument("--file", default=None, help="single legacy YAML file")
    kmm.add_argument("--business", default=None, help="产品编码；不传时迁移默认产品配置")
    mode=kmm.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    kmm.add_argument("--db", default=str(ROOT / "knowledge/quality_issue_v1.db"))

    qki = subparsers.add_parser("run-quality-knowledge-import", help="导入HMI/PLC/IFA质量问题到SQLite Knowledge")
    qki.add_argument("--input", required=True, help="源Excel文件")
    qki.add_argument("--business-type", required=True, choices=["HMI","PLC","IFA"])
    qki.add_argument("--sheet", default=None)
    qki.add_argument("--db", default=str(ROOT / "knowledge/quality_issue.db"))

    qka = subparsers.add_parser("run-quality-issue-analysis", help="对SQLite质量问题执行M2四阶段AI分析")
    qka.add_argument("--db", default=str(ROOT / "knowledge/quality_issue.db"))
    qka.add_argument("--knowledge-id", default=None)
    qka.add_argument("--business-type", default=None, choices=["HMI","PLC","IFA"])
    qka.add_argument("--only-missing", action="store_true")
    qka.add_argument("--overwrite", action="store_true")
    qka.add_argument("--include-customer-name", action="store_true")

    qkq = subparsers.add_parser("query-quality-knowledge", help="查询SQLite质量问题Knowledge")
    qkq.add_argument("--db", default=str(ROOT / "knowledge/quality_issue.db"))
    qkq.add_argument("--business-type", default=None)
    qkq.add_argument("--issue-id", default=None)
    qkq.add_argument("--product", default=None)
    qkq.add_argument("--platform", default=None)
    qkq.add_argument("--is-escape", default=None)
    qkq.add_argument("--occurrence-l1", default=None)
    qkq.add_argument("--escape-l1", default=None)
    qkq.add_argument("--gap-dimension", default=None, choices=["TECHNICAL","MANAGEMENT","GOVERNANCE"])
    qkq.add_argument("--gap-category", default=None)
    qkq.add_argument("--recurrence-risk-level", default=None, choices=["HIGH","MEDIUM","LOW","UNKNOWN"])
    qkq.add_argument("--month", default=None)
    qkq.add_argument("--severity", default=None)
    qkq.add_argument("--limit", type=int, default=100)

    qks = subparsers.add_parser("stats-quality-knowledge", help="M3质量问题Knowledge聚合统计")
    qks.add_argument("--db", default=str(ROOT / "knowledge/quality_issue.db"))
    qks.add_argument("--business-type", default=None, choices=["HMI","PLC","IFA"])
    qks.add_argument("--limit", type=int, default=20)

    qke = subparsers.add_parser("export-quality-knowledge", help="M3导出质量问题Knowledge为CSV/XLSX")
    qke.add_argument("--db", default=str(ROOT / "knowledge/quality_issue.db"))
    qke.add_argument("--output", required=True)
    qke.add_argument("--format", choices=["xlsx","csv"], default="xlsx")
    qke.add_argument("--dataset", choices=["issues","capability_gaps"], default="issues", help="CSV导出数据集；XLSX忽略此参数并导出多Sheet")
    qke.add_argument("--business-type", default=None, choices=["HMI","PLC","IFA"])
    qke.add_argument("--product", default=None)
    qke.add_argument("--platform", default=None)
    qke.add_argument("--gap-dimension", default=None, choices=["TECHNICAL","MANAGEMENT","GOVERNANCE"])
    qke.add_argument("--gap-category", default=None)

    qkr = subparsers.add_parser("publish-quality-knowledge-retrieval", help="M4将Quality Knowledge发布到现有Repeat Case Retrieval契约")
    qkr.add_argument("--db", default=str(ROOT / "knowledge/quality_issue.db"))
    qkr.add_argument("--knowledge-id", default=None)
    qkr.add_argument("--business-type", default=None, choices=["HMI","PLC","IFA"])
    qkr.add_argument("--limit", type=int, default=1000)
    qkr.add_argument("--overwrite", action="store_true")

    all_cmd = subparsers.add_parser("run-all", help="运行M2至M6")
    all_cmd.add_argument("--excel", default=None)
    all_cmd.add_argument("--reports-dir", default=None)
    all_cmd.add_argument("--with-ai", action="store_true")
    all_cmd.add_argument("--mock-ai", action="store_true")
    all_cmd.add_argument("--overwrite-ai", action="store_true")
    all_cmd.add_argument("--with-index", action="store_true")
    all_cmd.add_argument("--overwrite-index", action="store_true")

    return parser


def _print(result: dict) -> int:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "validate-case":
        try:
            validate_json_file(args.json_file, args.schema)
        except FileNotFoundError as exc:
            print(f"[ERROR] 文件不存在: {exc}", file=sys.stderr)
            return 2
        except SchemaValidationError as exc:
            print(f"[INVALID]\n{exc}", file=sys.stderr)
            return 1
        print("[VALID] Standard Case校验通过")
        return 0

    try:
        if args.command in {"knowledge-p0-init", "knowledge-p0-status", "knowledge-p1-init", "knowledge-p1-status"}:
            from quality_knowledge.p0.initializer import P0InitializationError, P0Initializer

            initializer = P0Initializer(manifest_path=args.manifest, plc_seed_path=args.plc_seed)
            try:
                if args.command in {"knowledge-p0-status", "knowledge-p1-status"}:
                    return _print(initializer.verify_ready(args.db))
                result = initializer.initialize(
                    args.db,
                    legacy_backup_source=args.legacy_backup_source,
                    backup_directory=args.backup_directory,
                )
            except P0InitializationError as error:
                result = {
                    "outcome": "INITIALIZATION_BLOCKED",
                    "error": error.code,
                    "diagnostic": error.diagnostic.as_dict(),
                }
                if args.command in {"knowledge-p0-init", "knowledge-p1-init"} and args.report:
                    report_path = Path(args.report)
                    report_path.parent.mkdir(parents=True, exist_ok=True)
                    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
                return 4
            if args.report:
                report_path = Path(args.report)
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return _print(result)
        if args.command == "knowledge-release-migrate":
            from quality_knowledge.release_migration import ReleaseMigration
            migration = ReleaseMigration()
            if args.apply:
                return _print(migration.apply(args.report))
            if not args.source or not args.target:
                raise ValueError("--source and --target are required for Dry Run")
            return _print(migration.dry_run(args.source, args.target, args.report))
        if args.command in {"knowledge-p0-web", "knowledge-p1-web"}:
            import uvicorn
            from quality_knowledge.web import create_p0_app

            uvicorn.run(create_p0_app(args.db), host=args.host, port=args.port)
            return 0
        if args.command == "knowledge-p1-start":
            import uvicorn
            from quality_knowledge.p0.initializer import P0Initializer
            from quality_knowledge.web import create_p0_app

            initializer = P0Initializer(manifest_path=args.manifest, plc_seed_path=args.plc_seed)
            db_path = Path(args.db)
            if db_path.exists():
                initializer.verify_ready(db_path)
            else:
                initializer.initialize(db_path)
            uvicorn.run(create_p0_app(db_path), host=args.host, port=args.port)
            return 0
        if args.command == "knowledge-import":
            from quality_knowledge.repositories import IssueKnowledgeRepository
            from quality_knowledge.services import KnowledgeIssueService
            repo=IssueKnowledgeRepository(args.db)
            return _print(KnowledgeIssueService(repo).import_file(args.input,args.business_type,sheet=args.sheet))
        if args.command == "knowledge-web":
            import uvicorn
            from quality_knowledge.web import create_app
            uvicorn.run(create_app(args.db),host=args.host,port=args.port)
            return 0
        if args.command == "major-knowledge-init":
            from quality_knowledge.major_cases import MajorKnowledgeRepository
            repository = MajorKnowledgeRepository(args.db, args.attachments)
            return _print({"status": "READY", "schema_version": repository.schema_version(), "database": str(Path(args.db)), "attachments": str(Path(args.attachments))})
        if args.command == "major-case-ingest":
            from quality_knowledge.major_cases import MajorCaseService, MajorKnowledgeRepository, SqliteBusinessSourceGateway
            from quality_knowledge.major_cases.sources import NullBusinessSourceGateway
            repository = MajorKnowledgeRepository(args.db, args.attachments)
            gateway = SqliteBusinessSourceGateway(args.business_db) if args.business_db else NullBusinessSourceGateway()
            service = MajorCaseService(repository, gateway)
            case = service.create_case(args.title, args.group, args.domain)
            intake = service.ingest(case["case_id"], args.input, current_itrs=args.itr)
            extraction = service.extract(case["case_id"], intake["version_id"]) if intake.get("parse_status") != "FAILED" else None
            return _print({"case": case, "intake": intake, "extraction": extraction})
        if args.command == "major-knowledge-benchmark":
            from quality_knowledge.major_cases import MajorKnowledgeRepository
            from quality_knowledge.major_cases.performance import run_synthetic_benchmark
            result = run_synthetic_benchmark(MajorKnowledgeRepository(args.db, args.attachments), event_count=args.events, fragment_count=args.fragments, iterations=args.iterations)
            if args.report:
                report = Path(args.report); report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return _print(result)
        if args.command == "major-knowledge-backup":
            from quality_knowledge.major_cases import MajorKnowledgeRepository
            from quality_knowledge.major_cases.backup import create_backup
            return _print(create_backup(MajorKnowledgeRepository(args.db, args.attachments), args.output))
        if args.command == "major-knowledge-restore":
            from quality_knowledge.major_cases.backup import restore_backup
            return _print(restore_backup(args.backup, args.db, args.attachments))
        if args.command == "knowledge-query":
            from quality_knowledge.repositories import IssueKnowledgeRepository
            from quality_knowledge.services import KnowledgeIssueService
            repo=IssueKnowledgeRepository(args.db); svc=KnowledgeIssueService(repo)
            filters={k:v for k,v in {"business_type":args.business_type,"business_issue_id":args.business_issue_id,"product":args.product,"platform":args.platform}.items() if v is not None}
            rows=svc.query_issues(filters,args.limit); return _print({"count":len(rows),"items":rows})
        if args.command == "knowledge-ai-check":
            from quality_knowledge.ai_diagnostics import check_ai
            return _print(check_ai(ROOT, live=args.live))
        if args.command == "knowledge-analyze":
            from quality_knowledge.repositories import IssueKnowledgeRepository
            from quality_knowledge.services import KnowledgeIssueService
            svc=KnowledgeIssueService(IssueKnowledgeRepository(args.db))
            if args.knowledge_id:return _print(svc.run_issue_analysis(args.knowledge_id,ROOT,only_missing=True,force=args.force))
            ids=[x["knowledge_id"] for x in svc.query_issues({"business_type":args.business_type} if args.business_type else {},100000)]
            return _print(svc.run_batch_analysis(ids,ROOT,only_missing=True,force=args.force))
        if args.command == "knowledge-stats":
            from quality_knowledge.repositories import IssueKnowledgeRepository
            from quality_knowledge.services import KnowledgeIssueService
            svc=KnowledgeIssueService(IssueKnowledgeRepository(args.db))
            return _print(svc.get_statistics(args.business_type,args.limit))
        if args.command == "knowledge-export":
            from quality_knowledge.repositories import IssueKnowledgeRepository
            from quality_knowledge.services import KnowledgeIssueService
            svc=KnowledgeIssueService(IssueKnowledgeRepository(args.db))
            filters={"business_type":args.business_type} if args.business_type else {}
            return _print(svc.export_issues(args.output,format=args.format,filters=filters,dataset=args.dataset))
        if args.command == "knowledge-mapping-migrate":
            from quality_knowledge.mapping import MappingConfigurationRepository
            from quality_knowledge.mapping.migration import LegacyYamlMappingMigrator
            mig=LegacyYamlMappingMigrator(MappingConfigurationRepository(args.db))
            if args.all:
                results=[]
                for bt in ("HMI","PLC","IFA"):
                    results.append(mig.migrate(ROOT / "quality_knowledge" / "config" / f"{bt.lower()}_fields.yaml",bt,dry_run=args.dry_run))
                return _print({"count":len(results),"results":results})
            return _print(mig.migrate(args.file,args.business,dry_run=args.dry_run))
        if args.command == "run-quality-knowledge-import":
            from quality_knowledge.repositories import SqliteIssueKnowledgeRepository
            from quality_knowledge.services import IssueImportService
            repo = SqliteIssueKnowledgeRepository(args.db)
            return _print(IssueImportService(repo).import_excel(args.input, args.business_type, sheet=args.sheet))
        if args.command == "run-quality-issue-analysis":
            from quality_knowledge.repositories import SqliteIssueKnowledgeRepository
            from quality_knowledge.services import QualityIssueAnalysisService
            repo = SqliteIssueKnowledgeRepository(args.db)
            svc = QualityIssueAnalysisService(repo, ROOT, include_customer_name=args.include_customer_name)
            if args.knowledge_id:
                return _print(svc.analyze_one(args.knowledge_id, only_missing=args.only_missing, overwrite=args.overwrite))
            rows = repo.query({"business_type": args.business_type} if args.business_type else {}, limit=100000)
            items=[]
            for row in rows:
                try: items.append(svc.analyze_one(row["knowledge_id"], only_missing=args.only_missing, overwrite=args.overwrite))
                except Exception as exc: items.append({"knowledge_id":row["knowledge_id"],"status":"FAILED","error":str(exc)})
            return _print({"total":len(items),"success":sum(x.get("status")=="SUCCESS" for x in items),"items":items})
        if args.command == "query-quality-knowledge":
            from quality_knowledge.repositories import SqliteIssueKnowledgeRepository
            repo = SqliteIssueKnowledgeRepository(args.db)
            filters = {k:v for k,v in {
                "business_type":args.business_type,"issue_id":args.issue_id,"product":args.product,"platform":args.platform,
                "is_escape":args.is_escape,"occurrence_l1":args.occurrence_l1,"escape_l1":args.escape_l1,
                "gap_dimension":args.gap_dimension,"gap_category":args.gap_category,
                "recurrence_risk_level":args.recurrence_risk_level,"month":args.month,"severity":args.severity
            }.items() if v is not None}
            return _print({"count": len(rows := repo.query(filters, limit=args.limit)), "items": rows})
        if args.command == "stats-quality-knowledge":
            from quality_knowledge.repositories import SqliteIssueKnowledgeRepository
            from quality_knowledge.services import IssueQueryService
            repo = SqliteIssueKnowledgeRepository(args.db)
            return _print(IssueQueryService(repo).statistics(business_type=args.business_type, limit=args.limit))
        if args.command == "export-quality-knowledge":
            from quality_knowledge.repositories import SqliteIssueKnowledgeRepository
            from quality_knowledge.services import QualityKnowledgeExportService
            repo = SqliteIssueKnowledgeRepository(args.db)
            svc = QualityKnowledgeExportService(repo)
            filters={k:v for k,v in {"business_type":args.business_type,"product":args.product,"platform":args.platform,"gap_dimension":args.gap_dimension,"gap_category":args.gap_category}.items() if v is not None}
            if args.format == "csv": return _print(svc.export_csv(args.output, filters=filters, dataset=args.dataset))
            return _print(svc.export_xlsx(args.output, filters=filters))
        if args.command == "publish-quality-knowledge-retrieval":
            from quality_knowledge.repositories import SqliteIssueKnowledgeRepository
            from repositories import JsonArtifactRepository
            from services import KnowledgeService
            issue_repo = SqliteIssueKnowledgeRepository(args.db)
            svc = KnowledgeService.with_quality_repository(JsonArtifactRepository(ROOT), issue_repo)
            if args.knowledge_id:
                return _print(svc.publish_issue_to_retrieval(args.knowledge_id, overwrite=args.overwrite))
            filters={"business_type": args.business_type} if args.business_type else {}
            items=svc.publish_issues_to_retrieval(filters, limit=args.limit, overwrite=args.overwrite)
            return _print({"total":len(items),"published":sum(x.get("status")=="PUBLISHED" for x in items),"skipped":sum(x.get("status")=="SKIPPED" for x in items),"items":items})
        if args.command == "run-m2":
            return _print(run_m2(ROOT, excel_path=args.excel, reports_dir=args.reports_dir))
        if args.command == "run-m3":
            return _print(run_m3(ROOT, case_id=args.case_id))
        if args.command == "run-m4":
            return _print(run_m4(ROOT, case_id=args.case_id))
        if args.command == "run-m5":
            return _print(run_m5(ROOT, case_id=args.case_id, mock=args.mock, overwrite=args.overwrite))
        if args.command == "run-m6":
            return _print(run_m6(ROOT, case_id=args.case_id, overwrite=args.overwrite))
        if args.command == "run-m7-query":
            return _print(run_m71_query(ROOT, input_path=args.input, overwrite=args.overwrite))
        if args.command == "run-m72-normalize":
            return _print(run_m72_normalizer(ROOT, query_id=args.query_id, overwrite=args.overwrite))
        if args.command == "run-m72-ai":
            return _print(run_m72_ai(ROOT, query_id=args.query_id, overwrite=args.overwrite, mock=args.mock, skip_ai=args.skip_ai))
        if args.command == "run-m72-build":
            return _print(run_m72_standard_builder(ROOT, query_id=args.query_id, overwrite=args.overwrite))
        if args.command == "run-m72":
            return _print(run_m72_pipeline(
                ROOT,
                query_id=args.query_id,
                overwrite=args.overwrite,
                mock=args.mock,
                skip_ai=args.skip_ai,
                from_stage=args.from_stage,
            ))
        if args.command == "run-m73-profile":
            return _print(run_m73_profile(ROOT, query_id=args.query_id, overwrite=args.overwrite))
        if args.command == "run-m73-retrieve":
            return _print(run_m73_retrieve(ROOT, query_id=args.query_id, top_k=args.top_k, overwrite=args.overwrite))
        if args.command == "run-m81-load":
            return _print(run_m81_load(ROOT, query_id=args.query_id, top_k=args.top_k, overwrite=args.overwrite))
        if args.command == "run-m82-similarity":
            return _print(run_m82_similarity(
                ROOT, query_id=args.query_id, case_id=args.case_id,
                overwrite=args.overwrite, mock=args.mock, skip_ai=args.skip_ai,
            ))
        if args.command == "run-m83-solution":
            return _print(run_m83_solution(
                ROOT, query_id=args.query_id, case_id=args.case_id,
                overwrite=args.overwrite, mock=args.mock, skip_ai=args.skip_ai,
            ))
        if args.command == "run-m84-decision":
            return _print(run_m84_decision(
                ROOT, query_id=args.query_id, overwrite=args.overwrite,
                mock=args.mock, skip_ai=args.skip_ai,
            ))
        if args.command == "run-m85-delivery":
            return _print(run_m85_delivery(ROOT, query_id=args.query_id, overwrite=args.overwrite))
        if args.command == "run-m7":
            return _print(run_m7(
                ROOT,
                query_text=args.text,
                top_k=args.top_k,
                ipmt=args.ipmt,
                spdt=args.spdt,
                responsible_department_level2=args.department,
                product=args.product,
                domain=args.domain,
                cause_level1=args.cause_level1,
                cause_level2=args.cause_level2,
                cause_description=args.cause_description,
                solution=args.solution,
            ))
        if args.command == "run-analysis":
            return _print(run_analysis_pipeline(
                ROOT, input_path=args.input, query_id=args.query_id,
                from_stage=args.from_stage, top_k=args.top_k,
                overwrite=args.overwrite, mock=args.mock, skip_ai=args.skip_ai,
            ))
        if args.command == "run-batch":
            return _print(run_local_batch(
                ROOT,
                input_path=args.input,
                run_id=args.run_id,
                resume=args.resume,
                retry_failed=args.retry_failed,
                top_k=args.top_k,
                overwrite=not args.no_overwrite,
                mock=args.mock,
                skip_ai=args.skip_ai,
                include_sensitive_debug=args.include_sensitive_debug,
            ))
        if args.command == "run-all":
            return _print(run_all(
                ROOT,
                excel_path=args.excel,
                reports_dir=args.reports_dir,
                with_ai=args.with_ai,
                mock_ai=args.mock_ai,
                overwrite_ai=args.overwrite_ai,
                with_index=args.with_index,
                overwrite_index=args.overwrite_index,
            ))
    except Exception as exc:
        print(f"[ERROR] {args.command}运行失败: {exc}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
