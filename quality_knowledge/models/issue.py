from __future__ import annotations
from typing import Any
from pydantic import Field
from models.common import VersionedDTO, Metadata

class IssueIdentity(VersionedDTO):
    knowledge_id: str
    case_id: str
    business_type: str
    issue_id: str
    source_record_id: str

class IssueSource(VersionedDTO):
    source_file: str = ""
    source_sheet: str = ""
    source_row: int | None = None
    source_import_batch: str = ""
    raw_record_ref: str = ""
    source_hash: str = ""

class IssueFact(VersionedDTO):
    title: str = ""
    description: str = ""
    impact: str = ""
    severity: str = ""
    issue_type: str = ""
    is_defect: str = ""
    month: str = ""
    year: str = ""
    industry: str = ""
    customer: str = ""
    department: str = ""
    business_group: str = ""
    platform: str = ""
    product: str = ""
    module: str = ""
    issue_domain: str = "AUTO"
    issue_domain_source: str = "AI"

class ProductContext(VersionedDTO):
    product: str = ""
    product_series: str = ""
    platform: str = ""
    module: str = ""
    business_group: str = ""
    feature_l1: str = ""
    feature_l2: str = ""
    feature_l3: str = ""
    feature_l4: str = ""
    fa_feature: str = ""
    fa_l1_feature: str = ""
    layer: str = ""
    layer_category: str = ""

class OccurrenceFact(VersionedDTO):
    original_reason: str = ""
    root_cause_original: str = ""
    cause_l1: str = ""
    cause_l2: str = ""
    cause_l3: str = ""
    cause_l4: str = ""

class EscapeFact(VersionedDTO):
    is_escape: str = ""
    escape_type: str = ""
    original_reason: str = ""
    root_cause_original: str = ""
    escape_l1: str = ""
    escape_l2: str = ""
    escape_l3: str = ""
    escape_l4: str = ""

class SolutionFact(VersionedDTO):
    original_solution: str = ""
    corrective_action: str = ""
    improvement_action: str = ""
    management_action: str = ""
    technical_action: str = ""
    reusable_action: str = ""

class VerificationFact(VersionedDTO):
    existing_case: str = ""
    mandatory_test: str = ""
    automated: str = ""
    mandatory_not_automated: str = ""
    extracted_test_scenario: str = ""

class QualityIssueDTO(VersionedDTO):
    identity: IssueIdentity
    source: IssueSource
    issue_fact: IssueFact
    product_context: ProductContext
    occurrence: OccurrenceFact
    escape: EscapeFact
    solution: SolutionFact
    verification: VerificationFact = Field(default_factory=VerificationFact)
    product_extension: dict[str, Any] = Field(default_factory=dict)
    raw_record: dict[str, Any] = Field(default_factory=dict)
    metadata: Metadata = Field(default_factory=Metadata)
