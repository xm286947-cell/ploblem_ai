from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repositories import JsonArtifactRepository
from services import KnowledgeService


class CandidateLoader:
    """把检索候选及其案例证据聚合成后续AI可直接消费的Analysis Context。"""

    VERSION = "M8.1-C1"

    def __init__(
        self,
        root: Path,
        knowledge_service: KnowledgeService | None = None,
    ) -> None:
        self.root = root.resolve()
        self.repository = JsonArtifactRepository(self.root)
        self.knowledge_service = knowledge_service or KnowledgeService(self.repository)

    def _relative(self, path: Path | None) -> str:
        return self.repository.relative(path) if path is not None else ""

    def load(self, query_id: str, candidate: dict[str, Any], standard_query: dict[str, Any], retrieval_profile: dict[str, Any], source_candidate_file: Path) -> dict[str, Any]:
        case_id = str(candidate.get("case_id") or "").strip()
        if not case_id:
            raise ValueError("候选案例缺少case_id")

        artifacts = self.knowledge_service.load_case_artifacts(
            case_id,
            str(candidate.get("retrieval_doc_path") or "").strip() or None,
        )
        retrieval_doc = artifacts.retrieval_document
        enriched_case = artifacts.enriched_case
        standard_case = artifacts.standard_case
        raw_evidence = artifacts.raw_evidence
        embedding_record = artifacts.embedding_record

        missing: list[str] = []
        for name, value in (
            ("retrieval_document", retrieval_doc),
            ("enriched_case", enriched_case),
            ("standard_case", standard_case),
            ("raw_evidence", raw_evidence),
        ):
            if value is None:
                missing.append(name)

        evidence_sources = []
        if standard_case is not None:
            evidence_sources.append("STANDARD_CASE")
        if enriched_case is not None:
            evidence_sources.append("ENRICHED_CASE")
        if retrieval_doc is not None:
            evidence_sources.append("RETRIEVAL_DOCUMENT")
        if raw_evidence is not None:
            evidence_sources.append("RAW_EVIDENCE")

        paths = artifacts.paths
        return {
            "context_version": self.VERSION,
            "query_id": query_id,
            "case_id": case_id,
            "query": {
                "standard_query": standard_query,
                "retrieval_profile": retrieval_profile,
            },
            "candidate": candidate,
            "case": {
                "standard_case": standard_case,
                "enriched_case": enriched_case,
                "retrieval_document": retrieval_doc,
                "raw_evidence": raw_evidence,
                "embedding_metadata": {
                    "model": (embedding_record or {}).get("model", ""),
                    "dimensions": (embedding_record or {}).get("dimensions", 0),
                    "content_hash": (embedding_record or {}).get("content_hash", ""),
                },
            },
            "evidence": {
                "available_sources": evidence_sources,
                "retrieval_text": str((retrieval_doc or {}).get("text") or ""),
                "report_filename": str((raw_evidence or {}).get("report_filename") or ""),
                "matched_report_path": str((raw_evidence or {}).get("matched_report_path") or ""),
                "sections": (raw_evidence or {}).get("sections", []),
                "unclassified_blocks": (raw_evidence or {}).get("unclassified_blocks", []),
            },
            "source_paths": {
                "candidate_file": self._relative(source_candidate_file),
                "standard_query": f"knowledge/standard_query/{query_id}.json",
                "retrieval_profile": f"knowledge/retrieval_profile/{query_id}.json",
                "standard_case": self._relative(paths["standard_case"]),
                "enriched_case": self._relative(paths["enriched_case"]),
                "retrieval_document": self._relative(paths["retrieval_document"]),
                "raw_evidence": self._relative(paths["raw_evidence"]),
                "embedding": self._relative(paths["embedding"]),
            },
            "quality": {
                "status": "COMPLETE" if not missing else "PARTIAL",
                "missing_sources": missing,
                "quality_flags": list(dict.fromkeys((candidate.get("quality_flags") or []) + (["ANALYSIS_CONTEXT_INCOMPLETE"] if missing else []))),
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
