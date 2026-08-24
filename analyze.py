from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from builder.m71_query_runner import run_m71_query
from builder.query_enricher import run_m72_pipeline
from builder.m73_profile_runner import run_m73_profile
from builder.m73_retriever_runner import run_m73_retrieve
from builder.m81_candidate_runner import run_m81_load
from builder.m82_similarity_runner import run_m82_similarity
from builder.m83_solution_runner import run_m83_solution
from builder.m84_repeat_runner import run_m84_decision
from builder.m85_delivery_runner import run_m85_delivery
from builder.query_artifact_cleaner import clean_orphan_query_artifacts, clean_query_artifacts, raw_query_ids
from common.config_loader import ConfigError, ConfigLoader
from common.workspace import WorkspaceError, WorkspaceManager

ROOT = Path(__file__).resolve().parent


class AnalysisRunError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="REPEAT_CASE_ENGINE 一键分析入口")
    parser.add_argument("--input", help="待分析问题Excel；默认读取config/repeat_case.yaml")
    parser.add_argument("--query-id", "--case", dest="query_id", default=None, help="只处理指定Query ID（--case为兼容别名）")
    parser.add_argument("--top-k", type=int, default=None, help="候选案例数量")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有中间结果")
    parser.add_argument("--force", "-f", action="store_true", help="强制重跑；等价于--overwrite")
    parser.add_argument("--resume", action="store_true", help="增量续跑（默认行为，显式参数便于脚本调用）")
    parser.add_argument(
        "--stage",
        choices=["all", "query", "retrieval", "candidate", "similarity", "solution", "repeat", "delivery"],
        default="all",
        help="只运行指定阶段；默认all",
    )
    parser.add_argument("--debug", action="store_true", help="打印运行参数与阶段结果摘要")
    parser.add_argument("--skip-ai", action="store_true", help="跳过AI，生成降级结果")
    parser.add_argument("--mock", action="store_true", help="使用本地Mock AI响应")
    parser.add_argument("--check-only", action="store_true", help="仅检查配置、目录和Knowledge")
    return parser


def _stage(name: str, func: Callable[..., dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    print(f"[RUN ] {name}")
    result = func(**kwargs)
    failed = int(result.get("failed", result.get("failed_count", 0)) or 0)
    errors = result.get("errors") or []
    if failed > 0:
        detail = errors[0] if errors else "unknown error"
        raise AnalysisRunError(f"{name}失败: failed={failed}, detail={detail}")
    print(f"[ OK ] {name}")
    return result


def _resolve_input(loader: ConfigLoader, cli_value: str | None) -> Path:
    if cli_value:
        path = Path(cli_value).expanduser()
        return path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    return loader.path("repeat_case", "analysis.input_excel", "input/new_cases.xlsx")


def _archive_reports(paths: Any, run_id: str) -> Path | None:
    source_root = ROOT / "knowledge/repeat_analysis"
    if not source_root.exists():
        return None
    target_root = paths.reports_dir / run_id
    copied = 0
    for report in source_root.glob("*/report.*"):
        query_id = report.parent.name
        destination = target_root / query_id / report.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(report, destination)
        copied += 1
    return target_root if copied else None


def _print_check(config: dict[str, Any], workspace: WorkspaceManager, input_path: Path) -> int:
    status = workspace.knowledge_status()
    print("=" * 56)
    print("REPEAT_CASE_ENGINE")
    print(f"Engine Version : {config.get('version', 'UNKNOWN')}")
    print(f"Input Excel    : {input_path} ({'OK' if input_path.exists() else 'NOT FOUND'})")
    print(f"Knowledge      : {'OK' if status['ready'] else 'NOT READY'}")
    print(f"Cases          : {status['case_count']}")
    print(f"Retrieval Docs : {status['retrieval_doc_count']}")
    print("=" * 56)
    return 0 if input_path.exists() and status["ready"] else 2


def main() -> int:
    args = build_parser().parse_args()
    started = time.perf_counter()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        loader = ConfigLoader(ROOT)
        config = loader.load("repeat_case")
        workspace = WorkspaceManager(ROOT, config)
        paths = workspace.initialize()
        input_path = _resolve_input(loader, args.input)

        if args.check_only:
            return _print_check(config, workspace, input_path)
        if not input_path.exists():
            raise AnalysisRunError(f"待分析Excel不存在: {input_path}")
        workspace.assert_knowledge_ready()

        analysis_cfg = config.get("analysis") or {}
        overwrite = bool(args.force or args.overwrite or analysis_cfg.get("overwrite", False))
        skip_ai = bool(args.skip_ai or analysis_cfg.get("skip_ai", False))
        mock_ai = bool(args.mock or analysis_cfg.get("mock_ai", False))
        top_k = args.top_k if args.top_k is not None else int(analysis_cfg.get("top_k", 5))

        results: dict[str, Any] = {
            "run_id": run_id,
            "input": str(input_path),
            "stage": args.stage,
            "force": overwrite,
            "query_id": args.query_id or "",
            "stages": {},
        }

        if args.debug:
            print(f"[DEBUG] stage={args.stage}, force={overwrite}, query_id={args.query_id or 'ALL'}, top_k={top_k}")

        if args.stage in {"all", "query"}:
            results["stages"]["m71"] = _stage(
                "M7.1 Query Parser", run_m71_query, root=ROOT, input_path=input_path, overwrite=overwrite
            )
            valid_query_ids = raw_query_ids(ROOT)
            selected_query_ids = [args.query_id] if args.query_id else valid_query_ids
            results["cleanup"] = {
                "selected": clean_query_artifacts(ROOT, selected_query_ids),
                "orphans": clean_orphan_query_artifacts(ROOT, valid_query_ids),
            }
            results["stages"]["m72"] = _stage(
                "M7.2 Query Enricher", run_m72_pipeline, root=ROOT, query_id=args.query_id,
                overwrite=overwrite, mock=mock_ai, skip_ai=skip_ai, from_stage="normalize",
            )

        if args.stage in {"all", "retrieval"}:
            results["stages"]["m73_profile"] = _stage(
                "M7.3 Retrieval Profile", run_m73_profile, root=ROOT, query_id=args.query_id, overwrite=overwrite
            )
            results["stages"]["m73_retrieve"] = _stage(
                "M7.3 Retriever", run_m73_retrieve, root=ROOT, query_id=args.query_id,
                top_k=top_k, overwrite=overwrite,
            )

        if args.stage in {"all", "candidate"}:
            results["stages"]["m81"] = _stage(
                "M8.1 Candidate Loader", run_m81_load, root=ROOT, query_id=args.query_id,
                top_k=top_k, overwrite=overwrite,
            )

        if args.stage in {"all", "similarity"}:
            results["stages"]["m82"] = _stage(
                "M8.2 Similarity", run_m82_similarity, root=ROOT, query_id=args.query_id,
                case_id=None, overwrite=overwrite, mock=mock_ai, skip_ai=skip_ai,
            )

        if args.stage in {"all", "solution"}:
            results["stages"]["m83"] = _stage(
                "M8.3 Solution", run_m83_solution, root=ROOT, query_id=args.query_id,
                case_id=None, overwrite=overwrite, mock=mock_ai, skip_ai=skip_ai,
            )

        if args.stage in {"all", "repeat"}:
            results["stages"]["m84"] = _stage(
                "M8.4 Decision", run_m84_decision, root=ROOT, query_id=args.query_id,
                overwrite=overwrite, mock=mock_ai, skip_ai=skip_ai,
            )

        if args.stage in {"all", "delivery"}:
            results["stages"]["m85"] = _stage(
                "M8.5 Delivery", run_m85_delivery, root=ROOT, query_id=args.query_id,
                overwrite=overwrite,
            )

        archive_enabled = bool((config.get("report") or {}).get("archive_by_run", True))
        archive_dir = _archive_reports(paths, run_id) if archive_enabled else None
        results["archive_dir"] = str(archive_dir) if archive_dir else ""
        results["elapsed_seconds"] = round(time.perf_counter() - started, 3)

        summary_path = paths.logs_dir / f"analyze_{run_id}.json"
        summary_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[DONE] 分析完成，耗时 {results['elapsed_seconds']} 秒")
        print(f"[DONE] 报告目录: {archive_dir or (ROOT / 'knowledge/repeat_analysis')}")
        print(f"[DONE] 运行摘要: {summary_path}")
        return 0
    except (ConfigError, WorkspaceError, AnalysisRunError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[ERROR] 未预期异常: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
