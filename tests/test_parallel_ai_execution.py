from __future__ import annotations

import threading
import time
from pathlib import Path

from builder.parallel_execution import ParallelExecutionConfig, ordered_map
from builder.m82_similarity_runner import run_m82_similarity
from parser.common import write_json


def test_ordered_map_preserves_input_order_and_uses_multiple_threads() -> None:
    thread_ids: set[int] = set()
    lock = threading.Lock()

    def worker(value: int) -> int:
        with lock:
            thread_ids.add(threading.get_ident())
        time.sleep(0.03)
        return value * 10

    result = ordered_map(range(6), worker, ParallelExecutionConfig(enabled=True, max_workers=3))
    assert result == [0, 10, 20, 30, 40, 50]
    assert len(thread_ids) >= 2


def test_ordered_map_serial_mode_uses_one_thread() -> None:
    thread_ids: set[int] = set()

    def worker(value: int) -> int:
        thread_ids.add(threading.get_ident())
        return value

    assert ordered_map([1, 2, 3], worker, ParallelExecutionConfig(enabled=False, max_workers=4)) == [1, 2, 3]
    assert len(thread_ids) == 1


def _prepare_similarity_project(root: Path, count: int = 4) -> None:
    project = Path(__file__).resolve().parents[1]
    for rel in [
        "config/model.yaml",
        "prompts/similarity_analyzer.md",
        "schema/similarity_analysis.schema.json",
        "tests/samples/mock_similarity_response.json",
    ]:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((project / rel).read_text(encoding="utf-8"), encoding="utf-8")
    for p in [root / "knowledge/analysis_context/Q1", root / "knowledge/similarity_analysis", root / "output/logs"]:
        p.mkdir(parents=True, exist_ok=True)
    for index in range(1, count + 1):
        write_json(root / f"knowledge/analysis_context/Q1/C{index}.json", {
            "context_version": "M8.1-C1", "query_id": "Q1", "case_id": f"C{index}",
            "query": {"standard_query": {"problem": {}}, "retrieval_profile": {}},
            "candidate": {"rank": index, "score": 0.88},
            "case": {"standard_case": {}, "enriched_case": {}, "retrieval_document": {}, "raw_evidence": {}, "embedding_metadata": {}},
            "evidence": {"available_sources": [], "retrieval_text": "", "report_filename": "", "matched_report_path": "", "sections": [], "unclassified_blocks": []},
            "source_paths": {"candidate_file": "output/candidate_cases/Q1.json"},
            "quality": {"status": "COMPLETE", "missing_sources": [], "quality_flags": []},
            "generated_at": "2026-01-01T00:00:00+00:00",
        })


def test_m82_runner_reports_parallel_execution(tmp_path: Path) -> None:
    _prepare_similarity_project(tmp_path)
    result = run_m82_similarity(tmp_path, query_id="Q1", mock=True, overwrite=True)
    assert result["success"] == 4
    assert result["execution_mode"] == "parallel"
    assert result["parallel_workers"] == 4
    for index in range(1, 5):
        assert (tmp_path / f"knowledge/similarity_analysis/Q1/C{index}.json").exists()
