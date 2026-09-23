from __future__ import annotations

import json
from pathlib import Path

from builder.m81_candidate_runner import run_m81_load
from parser.common import write_json


def _prepare(root: Path, count: int = 2, complete: bool = True) -> None:
    for path in [
        root / "output/candidate_cases",
        root / "output/logs",
        root / "knowledge/standard_query",
        root / "knowledge/retrieval_profile",
        root / "knowledge/retrieval_docs",
        root / "knowledge/enriched_case",
        root / "knowledge/standard_case",
        root / "knowledge/raw_evidence",
        root / "knowledge/embeddings",
        root / "schema",
    ]:
        path.mkdir(parents=True, exist_ok=True)
    source_schema = Path(__file__).resolve().parents[1] / "schema/analysis_context.schema.json"
    (root / "schema/analysis_context.schema.json").write_text(source_schema.read_text(encoding="utf-8"), encoding="utf-8")
    write_json(root / "knowledge/standard_query/Q1.json", {"query_id": "Q1"})
    write_json(root / "knowledge/retrieval_profile/Q1.json", {"query_id": "Q1"})
    results = []
    for i in range(count):
        cid = f"C{i+1}"
        results.append({"rank": i + 1, "case_id": cid, "score": 0.9 - i * 0.1, "retrieval_doc_path": f"knowledge/retrieval_docs/{cid}.json", "quality_flags": []})
        write_json(root / f"knowledge/retrieval_docs/{cid}.json", {"case_id": cid, "source_case_path": f"knowledge/enriched_case/{cid}.json", "text": f"案例{cid}"})
        if complete:
            write_json(root / f"knowledge/enriched_case/{cid}.json", {"metadata": {"case_id": cid}})
            write_json(root / f"knowledge/standard_case/{cid}.json", {"metadata": {"case_id": cid}})
            write_json(root / f"knowledge/raw_evidence/{cid}.json", {"report_filename": f"{cid}.pdf", "sections": [], "unclassified_blocks": []})
    write_json(root / "output/candidate_cases/Q1.json", {"query_id": "Q1", "results": results})


def test_load_complete_context(tmp_path: Path) -> None:
    _prepare(tmp_path, count=2, complete=True)
    result = run_m81_load(tmp_path, query_id="Q1", overwrite=True)
    assert result["success"] == 2
    assert result["failed"] == 0
    context = json.loads((tmp_path / "knowledge/analysis_context/Q1/C1.json").read_text(encoding="utf-8"))
    assert context["quality"]["status"] == "COMPLETE"
    assert context["candidate"]["rank"] == 1


def test_top_k_and_partial(tmp_path: Path) -> None:
    _prepare(tmp_path, count=3, complete=False)
    result = run_m81_load(tmp_path, query_id="Q1", top_k=1, overwrite=True)
    assert result["success"] == 1
    assert result["partial"] == 1
    assert len(list((tmp_path / "knowledge/analysis_context/Q1").glob("*.json"))) == 1


def test_missing_candidate_file(tmp_path: Path) -> None:
    (tmp_path / "output/candidate_cases").mkdir(parents=True)
    (tmp_path / "output/logs").mkdir(parents=True)
    result = run_m81_load(tmp_path, query_id="NONE")
    assert result["failed"] == 1
