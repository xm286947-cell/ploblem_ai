"""QS-MVP-04 Review / Confirm / Publish product service.

The service owns product-level mutation rules above the frozen Quality Scenario
V1 repository. It deliberately does not introduce approval-workflow states.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quality_knowledge.quality_scenario_v1 import (
    QualityScenarioFieldsV1,
    QualityScenarioV1,
    ScenarioActor,
    ScenarioConfirmationMetadata,
    ScenarioReviewMetadata,
    ScenarioReviewStatus,
    ScenarioStatus,
    ScenarioVersionMetadata,
    utc_now,
)
from quality_knowledge.quality_scenario_v1_store import QualityScenarioV1Repository


@dataclass(frozen=True)
class ScenarioMutationResult:
    scenario: QualityScenarioV1
    action: str
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "changed": self.changed,
            "scenario": self.scenario.model_dump(mode="json"),
        }


class QualityScenarioV1WorkflowService:
    """Human review, confirmation and publish service for Quality Scenario V1."""

    EDITABLE_FIELDS = frozenset(QualityScenarioFieldsV1.model_fields)
    EXTRA_REVIEW_FIELDS = frozenset({"blockers", "missing_information"})

    def __init__(self, repository: QualityScenarioV1Repository):
        self.repository = repository

    def get(self, scenario_id: str) -> QualityScenarioV1:
        item = self.repository.get(scenario_id)
        if item is None:
            raise ValueError("QUALITY_SCENARIO_V1_NOT_FOUND")
        return item

    def list_scenarios(
        self,
        *,
        product_code: str = "",
        lifecycle_stage_code: str = "",
        business_activity_code: str = "",
        quality_concern_code: str = "",
        status: ScenarioStatus | str | None = None,
        q: str = "",
    ) -> list[QualityScenarioV1]:
        status_value = ScenarioStatus(status) if status else None
        return self.repository.list(
            product_code=product_code,
            lifecycle_stage_code=lifecycle_stage_code,
            business_activity_code=business_activity_code,
            quality_concern_code=quality_concern_code,
            status=status_value,
            q=q,
        )

    @staticmethod
    def _expect_version(item: QualityScenarioV1, expected_scenario_version: int) -> None:
        if expected_scenario_version <= 0:
            raise ValueError("EXPECTED_SCENARIO_VERSION_REQUIRED")
        if item.scenario_version != expected_scenario_version:
            raise ValueError("SCENARIO_VERSION_CONFLICT")

    @staticmethod
    def _version(
        item: QualityScenarioV1,
        *,
        change_summary: str,
        published_at: str = "",
    ) -> ScenarioVersionMetadata:
        return ScenarioVersionMetadata(
            created_by=item.version.created_by,
            created_at=item.version.created_at,
            updated_at=utc_now(),
            published_at=published_at or item.version.published_at,
            parent_scenario_version=item.scenario_version,
            change_summary=change_summary,
        )

    @staticmethod
    def _validated(item: QualityScenarioV1, updates: dict[str, Any]) -> QualityScenarioV1:
        payload=item.model_dump(mode="json")
        payload.update(updates)
        return QualityScenarioV1.model_validate(payload)

    @staticmethod
    def _review_signature(item: QualityScenarioV1) -> dict[str, Any]:
        payload=item.model_dump(mode="json")
        payload.pop("scenario_version", None)
        payload.pop("version", None)
        return payload

    def review_candidate(
        self,
        scenario_id: str,
        *,
        expected_scenario_version: int,
        patch: dict[str, Any] | None = None,
        review_status: ScenarioReviewStatus | str = ScenarioReviewStatus.PENDING,
        reviewer: str = "",
        reviewed_at: str = "",
        comment: str = "",
    ) -> ScenarioMutationResult:
        current=self.get(scenario_id)
        if current.status != ScenarioStatus.CANDIDATE:
            raise ValueError("SCENARIO_REVIEW_REQUIRES_CANDIDATE")
        self._expect_version(current, expected_scenario_version)

        status=ScenarioReviewStatus(review_status)
        if status == ScenarioReviewStatus.REJECTED:
            raise ValueError("SCENARIO_REJECT_USE_REJECT_ENTRYPOINT")
        review=ScenarioReviewMetadata(
            review_status=status,
            reviewer=reviewer,
            reviewed_at=reviewed_at or (utc_now() if status != ScenarioReviewStatus.PENDING else ""),
            comment=comment,
        )

        requested=patch or {}
        unknown=set(requested)-self.EDITABLE_FIELDS-self.EXTRA_REVIEW_FIELDS
        if unknown:
            raise ValueError("SCENARIO_REVIEW_FIELD_NOT_EDITABLE:" + ",".join(sorted(unknown)))

        updates={key:value for key,value in requested.items()}
        updates["review"]=review.model_dump(mode="json")
        proposed_payload=current.model_dump(mode="json")
        proposed_payload.update(updates)
        proposed_payload["scenario_version"]=current.scenario_version
        proposed_payload["version"]=current.version.model_dump(mode="json")
        proposed=QualityScenarioV1.model_validate(proposed_payload)

        if self._review_signature(proposed) == self._review_signature(current):
            return ScenarioMutationResult(current, "REVIEW", False)

        saved=self._validated(
            proposed,
            {
                "scenario_version": current.scenario_version + 1,
                "version": self._version(
                    current,
                    change_summary="Candidate human review/update",
                ).model_dump(mode="json"),
            },
        )
        saved=self.repository.save(
            saved,
            actor=ScenarioActor.HUMAN,
            expected_scenario_version=current.scenario_version,
        )
        return ScenarioMutationResult(saved, "REVIEW", True)

    @staticmethod
    def _same_confirmation(
        item: QualityScenarioV1,
        *,
        quality_confirmed_by: str,
        technical_confirmed_by: str,
        confirmation_note: str,
    ) -> bool:
        return (
            item.confirmation.quality_confirmed_by == quality_confirmed_by.strip()
            and item.confirmation.technical_confirmed_by == technical_confirmed_by.strip()
            and item.confirmation.confirmation_note == confirmation_note
        )

    def confirm(
        self,
        scenario_id: str,
        *,
        expected_scenario_version: int,
        quality_confirmed_by: str,
        technical_confirmed_by: str,
        confirmation_note: str = "",
        quality_confirmed_at: str = "",
        technical_confirmed_at: str = "",
    ) -> ScenarioMutationResult:
        current=self.get(scenario_id)
        quality_actor=quality_confirmed_by.strip()
        technical_actor=technical_confirmed_by.strip()

        if current.status in {ScenarioStatus.CONFIRMED, ScenarioStatus.PUBLISHED}:
            if self._same_confirmation(
                current,
                quality_confirmed_by=quality_actor,
                technical_confirmed_by=technical_actor,
                confirmation_note=confirmation_note,
            ):
                return ScenarioMutationResult(current, "CONFIRM", False)
            raise ValueError("SCENARIO_CONFIRM_STATE_CONFLICT")

        if current.status != ScenarioStatus.CANDIDATE:
            raise ValueError("SCENARIO_CONFIRM_REQUIRES_CANDIDATE")
        self._expect_version(current, expected_scenario_version)

        confirmation=ScenarioConfirmationMetadata(
            quality_confirmed_by=quality_actor,
            quality_confirmed_at=quality_confirmed_at or (utc_now() if quality_actor else ""),
            technical_confirmed_by=technical_actor,
            technical_confirmed_at=technical_confirmed_at or (utc_now() if technical_actor else ""),
            confirmation_note=confirmation_note,
        )
        confirmed=self._validated(
            current,
            {
                "status": ScenarioStatus.CONFIRMED.value,
                "scenario_version": current.scenario_version + 1,
                "confirmation": confirmation.model_dump(mode="json"),
                "version": self._version(
                    current,
                    change_summary="Human review and dual-role confirmation",
                ).model_dump(mode="json"),
            },
        )
        confirmed=self.repository.save(
            confirmed,
            actor=ScenarioActor.HUMAN,
            expected_scenario_version=current.scenario_version,
        )
        return ScenarioMutationResult(confirmed, "CONFIRM", True)

    def reject(
        self,
        scenario_id: str,
        *,
        expected_scenario_version: int,
        reviewer: str,
        comment: str,
        reviewed_at: str = "",
    ) -> ScenarioMutationResult:
        current=self.get(scenario_id)
        reviewer=reviewer.strip()
        comment=comment.strip()
        if not reviewer or not comment:
            raise ValueError("SCENARIO_REJECT_REVIEWER_COMMENT_REQUIRED")
        if current.status == ScenarioStatus.REJECTED:
            if current.review.reviewer == reviewer and current.review.comment == comment:
                return ScenarioMutationResult(current, "REJECT", False)
            raise ValueError("SCENARIO_REJECT_STATE_CONFLICT")
        if current.status != ScenarioStatus.CANDIDATE:
            raise ValueError("SCENARIO_REJECT_REQUIRES_CANDIDATE")
        self._expect_version(current, expected_scenario_version)

        review=ScenarioReviewMetadata(
            review_status=ScenarioReviewStatus.REJECTED,
            reviewer=reviewer,
            reviewed_at=reviewed_at or utc_now(),
            comment=comment,
        )
        rejected=self._validated(
            current,
            {
                "status": ScenarioStatus.REJECTED.value,
                "scenario_version": current.scenario_version + 1,
                "review": review.model_dump(mode="json"),
                "version": self._version(
                    current,
                    change_summary="Candidate rejected by human review",
                ).model_dump(mode="json"),
            },
        )
        rejected=self.repository.save(
            rejected,
            actor=ScenarioActor.HUMAN,
            expected_scenario_version=current.scenario_version,
        )
        return ScenarioMutationResult(rejected, "REJECT", True)

    def publish(
        self,
        scenario_id: str,
        *,
        expected_scenario_version: int,
        published_by: str = "",
        published_at: str = "",
    ) -> ScenarioMutationResult:
        current=self.get(scenario_id)
        if current.status == ScenarioStatus.PUBLISHED:
            return ScenarioMutationResult(current, "PUBLISH", False)
        if current.status != ScenarioStatus.CONFIRMED:
            raise ValueError("SCENARIO_PUBLISH_REQUIRES_CONFIRMED")
        self._expect_version(current, expected_scenario_version)

        timestamp=published_at or utc_now()
        summary="Published Quality Scenario V1"
        if published_by.strip():
            summary += f" by {published_by.strip()}"
        published=self._validated(
            current,
            {
                "status": ScenarioStatus.PUBLISHED.value,
                "scenario_version": current.scenario_version + 1,
                "version": self._version(
                    current,
                    change_summary=summary,
                    published_at=timestamp,
                ).model_dump(mode="json"),
            },
        )
        published=self.repository.save(
            published,
            actor=ScenarioActor.SYSTEM,
            expected_scenario_version=current.scenario_version,
        )
        return ScenarioMutationResult(published, "PUBLISH", True)


__all__=[
    "ScenarioMutationResult",
    "QualityScenarioV1WorkflowService",
]
