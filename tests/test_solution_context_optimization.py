from __future__ import annotations

import json
from pathlib import Path

from builder.solution_context import build_solution_payload, payload_chars
from builder.solution_analyzer import SolutionAnalyzer
from builder.m83_solution_runner import run_m83_solution
from parser.common import write_json


def _context() -> dict:
    huge = "RAW" * 5000
    return {
        "query_id": "Q1",
        "case_id": "C1",
        "query": {
            "standard_query": {
                "problem": {"phenomenon": {"effective": "电机抖动"}},
                "classification": {"cause_level1": {"effective": "软件问题"}},
                "solution": {"current_solution": {"effective": "优化逻辑"}},
            },
            "retrieval_profile": {"large": huge},
        },
        "candidate": {"case_id": "C1", "rank": 1, "score": 0.9},
        "case": {
            "standard_case": {
                "root_cause": {"effective": "状态机缺陷"},
                "corrective_actions": ["修正状态机"],
            },
            "enriched_case": {"preventive_actions": ["增加测试"]},
            "retrieval_document": {"text": huge},
            "raw_evidence": {"sections": [huge], "unclassified_blocks": [huge]},
            "embedding_metadata": {"dimensions": 1536},
        },
        "evidence": {"retrieval_text": huge, "sections": [huge], "unclassified_blocks": [huge]},
        "quality": {"status": "COMPLETE", "missing_sources": [], "quality_flags": []},
    }


def _similarity(score: int = 88) -> dict:
    return {
        "analysis_status": "SUCCESS",
        "analysis": {
            "overall_score": score,
            "overall_level": "HIGH",
            "dimensions": {"root_cause": {"score": score, "reason": "根因接近"}},
            "key_similarities": ["根因相似"],
            "key_differences": [],
            "evidence_gaps": [],
        },
    }


def test_solution_payload_removes_large_duplicate_artifacts() -> None:
    context = _context()
    compact = build_solution_payload(context, _similarity())
    text = json.dumps(compact, ensure_ascii=False)
    assert "retrieval_profile" not in text
    assert "retrieval_document" not in text
    assert "raw_evidence" not in text
    assert "embedding_metadata" not in text
    assert "unclassified_blocks" not in text
    assert "电机抖动" in text
    assert "状态机缺陷" in text
    assert "修正状态机" in text
    full = {
        "query": context["query"], "candidate": context["candidate"],
        "historical_case": context["case"], "evidence": context["evidence"],
        "quality": context["quality"], "similarity_analysis": _similarity(),
    }
    assert payload_chars(compact) < payload_chars(full) / 5


def test_solution_ai_inherits_common_model_config(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    for rel in ["prompts/solution_analyzer.md", "schema/solution_analysis.schema.json"]:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((project / rel).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config/model.yaml").write_text(
        "ai:\n  enabled: true\n  base_url: http://example\n  model: base-model\n  max_tokens: 4096\n"
        "solution_ai:\n  max_tokens: 2200\n  timeout_seconds: 180\n",
        encoding="utf-8",
    )
    analyzer = SolutionAnalyzer(tmp_path, mock=True)
    assert analyzer.ai_cfg["model"] == "base-model"
    assert analyzer.ai_cfg["max_tokens"] == 2200
    assert analyzer.ai_cfg["timeout_seconds"] == 180


def test_m83_filters_to_top_n_after_similarity(tmp_path: Path) -> None:
    project = Path(__file__).resolve().parents[1]
    for rel in ["prompts/solution_analyzer.md", "schema/solution_analysis.schema.json", "tests/samples/mock_solution_response.json"]:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((project / rel).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config/model.yaml").write_text(
        "ai:\n  enabled: false\n"
        "solution_ai:\n  candidate_top_n: 2\n  min_similarity_score: 0\n"
        "parallel_ai:\n  enabled: false\n  max_workers: 1\n",
        encoding="utf-8",
    )
    for i, score in enumerate([90, 80, 70], 1):
        cid = f"C{i}"
        ctx = _context()
        ctx["case_id"] = cid
        ctx["candidate"]["case_id"] = cid
        write_json(tmp_path / f"knowledge/analysis_context/Q1/{cid}.json", ctx)
        write_json(tmp_path / f"knowledge/similarity_analysis/Q1/{cid}.json", _similarity(score))
    (tmp_path / "output/logs").mkdir(parents=True, exist_ok=True)
    result = run_m83_solution(tmp_path, query_id="Q1", mock=True, overwrite=True)
    assert result["total"] == 2
    assert result["filtered_candidates"] == 1
    assert result["success"] == 2
    assert result["prompt_chars_total"] > 0
    assert result["prompt_reduction_percent"] > 0
