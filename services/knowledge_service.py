from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repositories import JsonArtifactRepository


@dataclass(frozen=True)
class KnowledgeArtifacts:
    case_id: str
    retrieval_document: dict[str, Any] | None
    enriched_case: dict[str, Any] | None
    standard_case: dict[str, Any] | None
    raw_evidence: dict[str, Any] | None
    embedding_record: dict[str, Any] | None
    paths: dict[str, Path | None]


@dataclass(frozen=True)
class AnalysisArtifacts:
    query_id: str
    case_id: str
    analysis_context: dict[str, Any]
    similarity_analysis: dict[str, Any] | None
    solution_analysis: dict[str, Any] | None
    paths: dict[str, Path | None]


class KnowledgeService:
    """Read coherent knowledge artifacts without exposing storage layout to callers."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository

    def load_query_inputs(self, query_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        standard_query = self.repository.load(
            f"knowledge/standard_query/{query_id}.json", required=True
        )
        retrieval_profile = self.repository.load(
            f"knowledge/retrieval_profile/{query_id}.json", required=True
        )
        assert standard_query is not None and retrieval_profile is not None
        return standard_query, retrieval_profile

    def load_case_artifacts(
        self,
        case_id: str,
        retrieval_doc_path: str | Path | None = None,
    ) -> KnowledgeArtifacts:
        retrieval_path = self.repository.resolve(
            retrieval_doc_path or f"knowledge/retrieval_docs/{case_id}.json"
        )
        retrieval_document = self.repository.load(retrieval_path)
        source_case_path = str((retrieval_document or {}).get("source_case_path") or "").strip()

        enriched_path = self.repository.first_existing(
            [
                source_case_path,
                f"knowledge/enriched_case/{case_id}.json",
            ]
            if source_case_path
            else [f"knowledge/enriched_case/{case_id}.json"]
        )
        standard_path = self.repository.first_existing(
            [f"knowledge/standard_case/{case_id}.json"]
        )
        raw_evidence_path = self.repository.first_existing(
            [f"knowledge/raw_evidence/{case_id}.json"]
        )
        embedding_path = self.repository.first_existing(
            [f"knowledge/embeddings/{case_id}.json"]
        )

        return KnowledgeArtifacts(
            case_id=case_id,
            retrieval_document=retrieval_document,
            enriched_case=self.repository.load(enriched_path) if enriched_path else None,
            standard_case=self.repository.load(standard_path) if standard_path else None,
            raw_evidence=self.repository.load(raw_evidence_path) if raw_evidence_path else None,
            embedding_record=self.repository.load(embedding_path) if embedding_path else None,
            paths={
                "retrieval_document": retrieval_path if retrieval_path.is_file() else None,
                "enriched_case": enriched_path,
                "standard_case": standard_path,
                "raw_evidence": raw_evidence_path,
                "embedding": embedding_path,
            },
        )

    def list_analysis_contexts(
        self,
        query_id: str | None = None,
        case_id: str | None = None,
    ) -> list[Path]:
        if query_id and case_id:
            path = self.repository.resolve(f"knowledge/analysis_context/{query_id}/{case_id}.json")
            return [path] if path.is_file() else []
        if query_id:
            return self.repository.list(f"knowledge/analysis_context/{query_id}")
        root = self.repository.resolve("knowledge/analysis_context")
        if not root.exists():
            return []
        return sorted(path for path in root.glob("*/*.json") if path.is_file())

    def load_analysis_artifacts(
        self,
        query_id: str,
        case_id: str,
        *,
        context_path: str | Path | None = None,
    ) -> AnalysisArtifacts:
        resolved_context = self.repository.resolve(
            context_path or f"knowledge/analysis_context/{query_id}/{case_id}.json"
        )
        context = self.repository.load(resolved_context, required=True)
        assert context is not None
        similarity_path = self.repository.resolve(
            f"knowledge/similarity_analysis/{query_id}/{case_id}.json"
        )
        solution_path = self.repository.resolve(
            f"knowledge/solution_analysis/{query_id}/{case_id}.json"
        )
        return AnalysisArtifacts(
            query_id=query_id,
            case_id=case_id,
            analysis_context=context,
            similarity_analysis=self.repository.load(similarity_path),
            solution_analysis=self.repository.load(solution_path),
            paths={
                "analysis_context": resolved_context,
                "similarity_analysis": similarity_path if similarity_path.is_file() else None,
                "solution_analysis": solution_path if solution_path.is_file() else None,
            },
        )

    def save_similarity_analysis(
        self, query_id: str, case_id: str, payload: dict[str, Any]
    ) -> Path:
        return self.repository.save(
            f"knowledge/similarity_analysis/{query_id}/{case_id}.json", payload
        )

    def save_solution_analysis(
        self, query_id: str, case_id: str, payload: dict[str, Any]
    ) -> Path:
        return self.repository.save(
            f"knowledge/solution_analysis/{query_id}/{case_id}.json", payload
        )

    def save_repeat_analysis(self, query_id: str, payload: dict[str, Any]) -> Path:
        return self.repository.save(
            f"knowledge/repeat_analysis/{query_id}/repeat_analysis.json", payload
        )


    # Quality Knowledge extension (M1): storage remains behind repository/service boundary.
    @classmethod
    def with_quality_repository(cls, repository: JsonArtifactRepository, issue_repository):
        service = cls(repository)
        service.issue_repository = issue_repository
        return service

    def import_issue_records(self, excel_path, business_type, *, sheet=None, batch_id=None):
        from quality_knowledge.services import IssueImportService
        if not hasattr(self, "issue_repository"):
            raise RuntimeError("IssueKnowledgeRepository is not configured")
        return IssueImportService(self.issue_repository).import_excel(excel_path, business_type, sheet=sheet, batch_id=batch_id)

    def query_issues(self, filters=None, limit=100):
        if not hasattr(self, "issue_repository"):
            raise RuntimeError("IssueKnowledgeRepository is not configured")
        return self.issue_repository.query(filters or {}, limit=limit)

    # M4: publish Quality Issue Knowledge through the existing retrieval contract.
    def get_issue(self, knowledge_id: str):
        if not hasattr(self, "issue_repository"):
            raise RuntimeError("IssueKnowledgeRepository is not configured")
        return self.issue_repository.get(knowledge_id)

    def find_issue_by_business_id(self, business_type: str, issue_id: str):
        if not hasattr(self, "issue_repository"):
            raise RuntimeError("IssueKnowledgeRepository is not configured")
        return self.issue_repository.query({"business_type": business_type, "issue_id": issue_id}, limit=100)

    def get_issue_analysis(self, knowledge_id: str):
        if not hasattr(self, "issue_repository"):
            raise RuntimeError("IssueKnowledgeRepository is not configured")
        return {
            "occurrence": self.issue_repository.latest_analysis(knowledge_id, "occurrence"),
            "escape": self.issue_repository.latest_analysis(knowledge_id, "escape"),
            "recurrence": self.issue_repository.latest_analysis(knowledge_id, "recurrence"),
            "capability_gaps": self.issue_repository.query_capability_gaps({"knowledge_id": knowledge_id}, limit=100),
        }

    def publish_issue_to_retrieval(self, knowledge_id: str, *, overwrite: bool = False):
        from quality_knowledge.services import QualityKnowledgeRetrievalAdapter
        if not hasattr(self, "issue_repository"):
            raise RuntimeError("IssueKnowledgeRepository is not configured")
        return QualityKnowledgeRetrievalAdapter(self.issue_repository).publish(self, knowledge_id, overwrite=overwrite)

    def publish_issues_to_retrieval(self, filters=None, *, limit: int = 1000, overwrite: bool = False):
        if not hasattr(self, "issue_repository"):
            raise RuntimeError("IssueKnowledgeRepository is not configured")
        rows = self.issue_repository.query(filters or {}, limit=limit)
        results=[]
        for row in rows:
            results.append(self.publish_issue_to_retrieval(row["knowledge_id"], overwrite=overwrite))
        return results
