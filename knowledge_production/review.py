from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from repositories import JsonArtifactRepository

from .evaluation import KnowledgeEvaluationService
from .models import KnowledgeCandidate, KnowledgeEvaluation, ReviewAction, ReviewRecord


class KnowledgeReviewError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_EDITABLE_FIELDS = frozenset(
    {
        "title",
        "summary",
        "content",
        "device_type",
        "scope",
        "conditions",
        "limitations",
        "tags",
    }
)


class KnowledgeReviewService:
    """Immutable human review with explicit CONFIRM / EDIT / REJECT audit."""

    def __init__(self, repository: JsonArtifactRepository) -> None:
        self.repository = repository
        self.evaluations = KnowledgeEvaluationService(repository)

    def confirm(
        self,
        candidate_id: str,
        evaluation_id: str,
        *,
        reviewed_by: str,
        reviewed_at: datetime,
        review_note: str | None = None,
    ) -> ReviewRecord:
        candidate = self._load_candidate(candidate_id)
        evaluation = self._load_evaluation(candidate_id, evaluation_id)
        self._require_review_ready(evaluation)
        return self._commit_review(
            candidate,
            evaluation,
            action=ReviewAction.CONFIRM,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            review_note=review_note,
            edit_fields=[],
        )

    def edit(
        self,
        candidate_id: str,
        evaluation_id: str,
        edits: dict[str, Any],
        *,
        reviewed_by: str,
        reviewed_at: datetime,
        review_note: str | None = None,
    ) -> ReviewRecord:
        candidate = self._load_candidate(candidate_id)
        evaluation = self._load_evaluation(candidate_id, evaluation_id)
        self._require_review_ready(evaluation)

        unknown = sorted(set(edits) - _EDITABLE_FIELDS)
        if unknown:
            raise KnowledgeReviewError("REVIEW_EDIT_FIELD_NOT_ALLOWED")
        if not edits:
            raise KnowledgeReviewError("REVIEW_EDIT_EMPTY")

        try:
            effective = KnowledgeCandidate.model_validate(
                {
                    **candidate.model_dump(mode="json"),
                    **edits,
                }
            )
        except ValidationError as exc:
            raise KnowledgeReviewError("REVIEW_EDIT_INVALID") from exc

        effective_evaluation = self.evaluations.evaluate(effective)
        if not effective_evaluation.review_ready:
            raise KnowledgeReviewError("REVIEW_EDIT_NOT_REVIEW_READY")

        return self._commit_review(
            effective,
            effective_evaluation,
            action=ReviewAction.EDIT,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            review_note=review_note,
            edit_fields=sorted(edits),
            input_evaluation_id=evaluation.evaluation_id,
        )

    def reject(
        self,
        candidate_id: str,
        evaluation_id: str,
        *,
        reviewed_by: str,
        reviewed_at: datetime,
        review_note: str | None = None,
    ) -> ReviewRecord:
        candidate = self._load_candidate(candidate_id)
        evaluation = self._load_evaluation(candidate_id, evaluation_id)
        return self._commit_review(
            candidate,
            evaluation,
            action=ReviewAction.REJECT,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
            review_note=review_note,
            edit_fields=[],
        )

    def latest_review(self, candidate_id: str) -> ReviewRecord | None:
        head = self.repository.load(
            f"knowledge/production/review_heads/{candidate_id}.json"
        )
        if not isinstance(head, dict):
            return None
        review_id = str(head.get("review_id") or "")
        if not review_id:
            return None
        payload = self.repository.load(
            f"knowledge/production/reviews/{candidate_id}/{review_id}.json",
            required=True,
        )
        assert payload is not None
        return ReviewRecord.model_validate(payload)

    def load_effective_candidate(self, review: ReviewRecord) -> KnowledgeCandidate:
        if not review.effective_snapshot_ref:
            raise KnowledgeReviewError("REVIEW_SNAPSHOT_MISSING")
        payload = self.repository.load(review.effective_snapshot_ref, required=True)
        assert payload is not None
        try:
            return KnowledgeCandidate.model_validate(payload)
        except ValidationError as exc:
            raise KnowledgeReviewError("REVIEW_SNAPSHOT_INVALID") from exc

    def _commit_review(
        self,
        candidate: KnowledgeCandidate,
        evaluation: KnowledgeEvaluation,
        *,
        action: ReviewAction,
        reviewed_by: str,
        reviewed_at: datetime,
        review_note: str | None,
        edit_fields: list[str],
        input_evaluation_id: str | None = None,
    ) -> ReviewRecord:
        reviewer = reviewed_by.strip()
        if not reviewer:
            raise KnowledgeReviewError("REVIEWER_REQUIRED")

        review_id = self._review_id(
            candidate,
            action,
            reviewer,
            reviewed_at,
            review_note,
            evaluation.evaluation_id,
        )
        snapshot_ref = (
            "knowledge/production/review_snapshots/"
            f"{candidate.candidate_id}/{review_id}.json"
        )
        review = ReviewRecord(
            review_id=review_id,
            candidate_id=candidate.candidate_id,
            action=action,
            review_status=(
                "REJECTED" if action == ReviewAction.REJECT else "CONFIRMED"
            ),
            reviewed_by=reviewer,
            reviewed_at=reviewed_at,
            review_note=review_note,
            input_evaluation_id=(
                input_evaluation_id or evaluation.evaluation_id
            ),
            effective_evaluation_id=(
                None if action == ReviewAction.REJECT else evaluation.evaluation_id
            ),
            effective_snapshot_ref=snapshot_ref,
            edit_fields=edit_fields,
        )

        self._save_immutable(
            snapshot_ref,
            candidate.model_dump(mode="json"),
            "REVIEW_SNAPSHOT_CONFLICT",
        )
        review_ref = (
            f"knowledge/production/reviews/{candidate.candidate_id}/"
            f"{review.review_id}.json"
        )
        self._save_immutable(
            review_ref,
            review.model_dump(mode="json"),
            "REVIEW_ID_CONFLICT",
        )
        self.repository.save(
            f"knowledge/production/review_heads/{candidate.candidate_id}.json",
            {
                "candidate_id": candidate.candidate_id,
                "review_id": review.review_id,
                "review_status": review.review_status.value,
                "reviewed_at": review.reviewed_at.isoformat(),
            },
        )
        return review

    def _load_candidate(self, candidate_id: str) -> KnowledgeCandidate:
        payload = self.repository.load(
            f"knowledge/production/candidates/{candidate_id}.json",
            required=True,
        )
        assert payload is not None
        try:
            return KnowledgeCandidate.model_validate(payload)
        except ValidationError as exc:
            raise KnowledgeReviewError("KNOWLEDGE_CONTRACT_INVALID") from exc

    def _load_evaluation(
        self,
        candidate_id: str,
        evaluation_id: str,
    ) -> KnowledgeEvaluation:
        payload = self.repository.load(
            (
                "knowledge/production/evaluations/"
                f"{candidate_id}/{evaluation_id}.json"
            ),
            required=True,
        )
        assert payload is not None
        try:
            evaluation = KnowledgeEvaluation.model_validate(payload)
        except ValidationError as exc:
            raise KnowledgeReviewError("EVALUATION_INVALID") from exc
        if evaluation.candidate_id != candidate_id:
            raise KnowledgeReviewError("EVALUATION_CANDIDATE_MISMATCH")
        return evaluation

    @staticmethod
    def _require_review_ready(evaluation: KnowledgeEvaluation) -> None:
        if not evaluation.review_ready:
            raise KnowledgeReviewError("CANDIDATE_NOT_REVIEW_READY")

    def _save_immutable(
        self,
        path: str,
        payload: dict[str, Any],
        conflict_code: str,
    ) -> None:
        existing = self.repository.load(path)
        if existing is not None and existing != payload:
            raise KnowledgeReviewError(conflict_code)
        self.repository.save(path, payload)

    @staticmethod
    def _review_id(
        candidate: KnowledgeCandidate,
        action: ReviewAction,
        reviewer: str,
        reviewed_at: datetime,
        review_note: str | None,
        evaluation_id: str,
    ) -> str:
        material = {
            "candidate": candidate.model_dump(mode="json"),
            "action": action.value,
            "reviewed_by": reviewer,
            "reviewed_at": reviewed_at.isoformat(),
            "review_note": review_note,
            "evaluation_id": evaluation_id,
        }
        digest = hashlib.sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return "KPR-" + digest[:24]
