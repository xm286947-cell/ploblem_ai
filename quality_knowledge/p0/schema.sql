PRAGMA foreign_keys = ON;

CREATE TABLE system_database_metadata (
    database_instance_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    initialization_state TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE system_initialization_run (
    initialization_run_id TEXT PRIMARY KEY,
    database_instance_id TEXT,
    state TEXT NOT NULL,
    outcome TEXT NOT NULL,
    diagnostic_id TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE seed_manifest (
    seed_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    release_id TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (seed_key, version),
    UNIQUE (seed_key, version, sha256)
);

CREATE TABLE product_config (
    product_id TEXT PRIMARY KEY,
    product_code TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    product_type TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE mapping_config (
    config_id TEXT PRIMARY KEY,
    business_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED', 'INVALID')),
    source_type TEXT NOT NULL,
    source_artifact_path TEXT,
    source_hash TEXT,
    content_hash TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (business_type, version)
);
CREATE UNIQUE INDEX uq_p0_mapping_active
    ON mapping_config (business_type) WHERE status = 'ACTIVE';

CREATE TABLE mapping_item (
    mapping_item_id TEXT PRIMARY KEY,
    config_id TEXT NOT NULL REFERENCES mapping_config(config_id) ON DELETE CASCADE,
    canonical_field TEXT NOT NULL,
    target_domain TEXT NOT NULL,
    target_field TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 0 CHECK (required IN (0, 1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    description_zh TEXT,
    display_order INTEGER NOT NULL,
    UNIQUE (config_id, target_domain, target_field)
);

CREATE TABLE mapping_alias (
    mapping_alias_id TEXT PRIMARY KEY,
    mapping_item_id TEXT NOT NULL REFERENCES mapping_item(mapping_item_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    UNIQUE (mapping_item_id, alias, alias_type)
);

CREATE TABLE mapping_validation_result (
    validation_result_id TEXT PRIMARY KEY,
    config_id TEXT NOT NULL REFERENCES mapping_config(config_id) ON DELETE CASCADE,
    level TEXT NOT NULL CHECK (level IN ('INFO', 'WARNING', 'ERROR')),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE mapping_activation_audit (
    activation_audit_id TEXT PRIMARY KEY,
    config_id TEXT NOT NULL REFERENCES mapping_config(config_id),
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE standard_field_catalog_version (
    catalog_version_id TEXT PRIMARY KEY,
    version_no INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'ACTIVE', 'RETIRED', 'INVALID')),
    base_version_id TEXT REFERENCES standard_field_catalog_version(catalog_version_id),
    source_mapping_config_id TEXT REFERENCES mapping_config(config_id),
    source_mapping_version INTEGER,
    source_artifact_sha256 TEXT,
    content_hash TEXT NOT NULL,
    created_by TEXT NOT NULL,
    approved_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT
);
CREATE UNIQUE INDEX uq_p0_catalog_active
    ON standard_field_catalog_version (status) WHERE status = 'ACTIVE';

CREATE TABLE standard_field_definition (
    field_definition_id TEXT PRIMARY KEY,
    catalog_version_id TEXT NOT NULL REFERENCES standard_field_catalog_version(catalog_version_id) ON DELETE CASCADE,
    target_domain TEXT NOT NULL,
    target_field TEXT NOT NULL,
    canonical_field TEXT NOT NULL,
    label_zh TEXT NOT NULL,
    description_zh TEXT NOT NULL,
    definition_status TEXT NOT NULL CHECK (definition_status IN ('NEEDS_ENRICHMENT', 'APPROVED')),
    data_type TEXT NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    sensitivity TEXT NOT NULL DEFAULT 'INTERNAL',
    semantic_fingerprint TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('PLC_BASELINE', 'CONTROLLED_ADDITION')),
    source_mapping_item_id TEXT REFERENCES mapping_item(mapping_item_id),
    display_order INTEGER NOT NULL,
    UNIQUE (catalog_version_id, target_domain, target_field),
    UNIQUE (catalog_version_id, semantic_fingerprint)
);

CREATE TABLE standard_field_alias (
    field_alias_id TEXT PRIMARY KEY,
    catalog_version_id TEXT NOT NULL REFERENCES standard_field_catalog_version(catalog_version_id) ON DELETE CASCADE,
    field_definition_id TEXT NOT NULL REFERENCES standard_field_definition(field_definition_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (catalog_version_id, normalized_alias)
);

CREATE TABLE standard_field_change_request (
    change_request_id TEXT PRIMARY KEY,
    catalog_version_id TEXT NOT NULL REFERENCES standard_field_catalog_version(catalog_version_id),
    proposed_domain TEXT NOT NULL,
    proposed_field TEXT NOT NULL,
    label_zh TEXT NOT NULL,
    definition_zh TEXT NOT NULL,
    data_type TEXT NOT NULL,
    business_example TEXT NOT NULL,
    reuse_rationale TEXT NOT NULL,
    candidate_fields_json TEXT NOT NULL,
    source_context_json TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    approved_by TEXT,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at TEXT
);

CREATE TABLE intake_session (
    intake_session_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES product_config(product_id),
    file_name TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    mapping_config_id TEXT NOT NULL REFERENCES mapping_config(config_id),
    catalog_version_id TEXT NOT NULL REFERENCES standard_field_catalog_version(catalog_version_id),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE intake_preview_snapshot (
    preview_token TEXT PRIMARY KEY,
    intake_session_id TEXT NOT NULL REFERENCES intake_session(intake_session_id) ON DELETE CASCADE,
    sheet_name TEXT NOT NULL,
    header_row INTEGER NOT NULL,
    data_start_row INTEGER NOT NULL,
    mapping_content_hash TEXT NOT NULL,
    catalog_content_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE import_batch (
    import_batch_id TEXT PRIMARY KEY,
    preview_token TEXT NOT NULL UNIQUE REFERENCES intake_preview_snapshot(preview_token),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE import_error (
    import_error_id TEXT PRIMARY KEY,
    import_batch_id TEXT NOT NULL REFERENCES import_batch(import_batch_id) ON DELETE CASCADE,
    source_row_number INTEGER,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE quality_issue (
    knowledge_id TEXT PRIMARY KEY,
    business_issue_id TEXT NOT NULL,
    product_id TEXT REFERENCES product_config(product_id),
    current_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE quality_issue_version (
    issue_version_id TEXT PRIMARY KEY,
    knowledge_id TEXT NOT NULL REFERENCES quality_issue(knowledge_id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    mapping_config_id TEXT NOT NULL REFERENCES mapping_config(config_id),
    mapping_config_version INTEGER NOT NULL,
    standard_catalog_version_id TEXT NOT NULL REFERENCES standard_field_catalog_version(catalog_version_id),
    normalized_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (knowledge_id, version_no),
    UNIQUE (knowledge_id, normalized_hash)
);

CREATE TABLE issue_source_raw (
    source_raw_id TEXT PRIMARY KEY,
    issue_version_id TEXT NOT NULL REFERENCES quality_issue_version(issue_version_id) ON DELETE CASCADE,
    import_batch_id TEXT REFERENCES import_batch(import_batch_id),
    source_file_sha256 TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    source_row_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (issue_version_id, source_file_sha256, sheet_name, row_number)
);

CREATE TABLE issue_normalized_snapshot (
    normalized_snapshot_id TEXT PRIMARY KEY,
    issue_version_id TEXT NOT NULL UNIQUE REFERENCES quality_issue_version(issue_version_id) ON DELETE CASCADE,
    snapshot_json TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE analysis_taxonomy_version (
    taxonomy_version_id TEXT PRIMARY KEY,
    version_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT,
    UNIQUE (version_no)
);
CREATE TABLE analysis_taxonomy_term (
    term_id TEXT PRIMARY KEY,
    taxonomy_version_id TEXT NOT NULL REFERENCES analysis_taxonomy_version(taxonomy_version_id) ON DELETE CASCADE,
    taxonomy_type TEXT NOT NULL,
    code TEXT NOT NULL,
    parent_code TEXT,
    label_zh TEXT NOT NULL,
    description_zh TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (taxonomy_version_id, taxonomy_type, code)
);

CREATE TABLE classification_mapping_config (
    classification_mapping_config_id TEXT PRIMARY KEY,
    business_type TEXT NOT NULL,
    side TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT,
    UNIQUE (business_type, side, version_no)
);
CREATE TABLE classification_mapping_item (
    classification_mapping_item_id TEXT PRIMARY KEY,
    classification_mapping_config_id TEXT NOT NULL REFERENCES classification_mapping_config(classification_mapping_config_id) ON DELETE CASCADE,
    source_path_json TEXT NOT NULL,
    target_axis TEXT NOT NULL,
    target_code TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE analysis_prompt_version (
    prompt_version_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    version_no INTEGER NOT NULL,
    status TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    output_contract_json TEXT NOT NULL,
    taxonomy_version_id TEXT NOT NULL REFERENCES analysis_taxonomy_version(taxonomy_version_id),
    model_params_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT,
    UNIQUE (stage, version_no)
);
CREATE UNIQUE INDEX uq_p0_analysis_prompt_active
    ON analysis_prompt_version (stage) WHERE status = 'ACTIVE';
CREATE TABLE insight_scoring_version (
    scoring_version_id TEXT PRIMARY KEY,
    version_no INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL,
    weights_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TEXT
);
CREATE UNIQUE INDEX uq_p0_insight_scoring_active
    ON insight_scoring_version ((status = 'ACTIVE')) WHERE status = 'ACTIVE';

CREATE TABLE analysis_set (
    analysis_set_id TEXT PRIMARY KEY,
    knowledge_id TEXT NOT NULL REFERENCES quality_issue(knowledge_id),
    issue_version_id TEXT NOT NULL REFERENCES quality_issue_version(issue_version_id),
    status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'PARTIAL_FAILED', 'FAILED')),
    contract_version TEXT NOT NULL,
    taxonomy_version_id TEXT NOT NULL REFERENCES analysis_taxonomy_version(taxonomy_version_id),
    classification_mapping_versions_json TEXT NOT NULL DEFAULT '{}',
    prompt_versions_json TEXT NOT NULL DEFAULT '{}',
    scoring_version_id TEXT REFERENCES insight_scoring_version(scoring_version_id),
    analysis_profile_json TEXT NOT NULL DEFAULT '{}',
    source_coverage_json TEXT NOT NULL DEFAULT '{}',
    classification_consistency TEXT,
    input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE (issue_version_id, input_hash)
);

CREATE TABLE analysis_stage_run (
    analysis_stage_run_id TEXT PRIMARY KEY,
    analysis_set_id TEXT NOT NULL REFERENCES analysis_set(analysis_set_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'FAILED', 'SKIPPED')),
    input_hash TEXT NOT NULL,
    model_name TEXT,
    prompt_version_id TEXT REFERENCES analysis_prompt_version(prompt_version_id),
    raw_response TEXT,
    parsed_result_json TEXT,
    validation_error TEXT,
    debug_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE (analysis_set_id, stage)
);

CREATE TABLE issue_analysis_value (
    analysis_value_id TEXT PRIMARY KEY,
    analysis_set_id TEXT NOT NULL REFERENCES analysis_set(analysis_set_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    value_path TEXT NOT NULL,
    value_json TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('SOURCE_DATA', 'AI_STANDARDIZED', 'AI_INFERRED', 'HUMAN_CONFIRMED')),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    UNIQUE (analysis_set_id, stage, value_path)
);

CREATE TABLE issue_analysis_tag (
    analysis_tag_id TEXT PRIMARY KEY,
    analysis_set_id TEXT NOT NULL REFERENCES analysis_set(analysis_set_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    axis TEXT NOT NULL,
    tag_code TEXT NOT NULL,
    tag_role TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('SOURCE_DATA', 'AI_STANDARDIZED', 'AI_INFERRED', 'HUMAN_CONFIRMED')),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    UNIQUE (analysis_set_id, stage, axis, tag_code, tag_role)
);

CREATE TABLE issue_mrc (
    issue_mrc_id TEXT PRIMARY KEY,
    analysis_set_id TEXT NOT NULL REFERENCES analysis_set(analysis_set_id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK (side IN ('OCCURRENCE', 'ESCAPE')),
    mrc_code TEXT NOT NULL,
    role TEXT NOT NULL,
    control_status TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('SOURCE_DATA', 'AI_STANDARDIZED', 'AI_INFERRED', 'HUMAN_CONFIRMED')),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    UNIQUE (analysis_set_id, side, mrc_code, role)
);

CREATE TABLE issue_capability_gap (
    issue_capability_gap_id TEXT PRIMARY KEY,
    analysis_set_id TEXT NOT NULL REFERENCES analysis_set(analysis_set_id) ON DELETE CASCADE,
    capability_axis TEXT NOT NULL CHECK (capability_axis IN ('QUALITY_ENGINEERING', 'QUALITY_MANAGEMENT')),
    capability_code TEXT NOT NULL,
    governance_scope TEXT NOT NULL,
    control_status TEXT NOT NULL,
    details_json TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('SOURCE_DATA', 'AI_STANDARDIZED', 'AI_INFERRED', 'HUMAN_CONFIRMED')),
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    UNIQUE (analysis_set_id, capability_axis, capability_code, governance_scope)
);

CREATE TABLE analysis_open_question (
    open_question_id TEXT PRIMARY KEY,
    analysis_set_id TEXT NOT NULL REFERENCES analysis_set(analysis_set_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    target_path TEXT NOT NULL,
    question_key TEXT NOT NULL,
    question_text TEXT NOT NULL,
    priority TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE (analysis_set_id, question_key)
);

CREATE TABLE analysis_evidence (
    evidence_id TEXT PRIMARY KEY,
    analysis_set_id TEXT NOT NULL REFERENCES analysis_set(analysis_set_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    target_path TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('SOURCE_DATA', 'AI_STANDARDIZED', 'AI_INFERRED', 'HUMAN_CONFIRMED')),
    source_ref TEXT NOT NULL,
    field_path TEXT,
    excerpt TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE TABLE human_analysis_revision (
    human_revision_id TEXT PRIMARY KEY,
    base_analysis_set_id TEXT NOT NULL REFERENCES analysis_set(analysis_set_id),
    revision_no INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'CONFIRMED', 'SUPERSEDED')),
    confirmed_by TEXT NOT NULL,
    base_input_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TEXT,
    UNIQUE (base_analysis_set_id, revision_no)
);

CREATE TABLE human_analysis_answer (
    human_answer_id TEXT PRIMARY KEY,
    human_revision_id TEXT NOT NULL REFERENCES human_analysis_revision(human_revision_id) ON DELETE CASCADE,
    target_path TEXT NOT NULL,
    question_key TEXT NOT NULL DEFAULT '',
    confirmation_status TEXT NOT NULL DEFAULT 'CONFIRMED'
        CHECK (confirmation_status IN ('PENDING', 'CONFIRMED', 'CORRECTED', 'UNRESOLVED', 'NOT_APPLICABLE')),
    original_value_json TEXT,
    confirmed_value_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    changes_insight INTEGER NOT NULL DEFAULT 0 CHECK (changes_insight IN (0, 1)),
    UNIQUE (human_revision_id, target_path)
);

CREATE TABLE human_confirmation_audit (
    confirmation_audit_id TEXT PRIMARY KEY,
    human_revision_id TEXT NOT NULL REFERENCES human_analysis_revision(human_revision_id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- P1: versioned risk cases and forward quality-risk assessments.
CREATE TABLE risk_case (
    risk_case_id TEXT PRIMARY KEY,
    risk_code TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    publication_level TEXT NOT NULL
        CHECK (publication_level IN ('INTERNAL_FULL','INTERNAL_REDACTED','EXTERNAL_PATTERN','DO_NOT_PUBLISH')),
    status TEXT NOT NULL CHECK (status IN ('DRAFT','PUBLISHED','RETIRED')),
    current_version_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE risk_case_version (
    risk_case_version_id TEXT PRIMARY KEY,
    risk_case_id TEXT NOT NULL REFERENCES risk_case(risk_case_id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    source_analysis_set_id TEXT NOT NULL REFERENCES analysis_set(analysis_set_id),
    taxonomy_version_id TEXT NOT NULL,
    case_json TEXT NOT NULL,
    search_text TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (risk_case_id, version_no),
    UNIQUE (risk_case_id, content_hash)
);

CREATE TABLE risk_case_issue_link (
    risk_case_id TEXT NOT NULL REFERENCES risk_case(risk_case_id) ON DELETE CASCADE,
    knowledge_id TEXT NOT NULL REFERENCES quality_issue(knowledge_id),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (risk_case_id, knowledge_id)
);

CREATE TABLE forward_assessment (
    assessment_id TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    product_id TEXT REFERENCES product_config(product_id),
    status TEXT NOT NULL CHECK (status IN ('DRAFT','READY','RUNNING','REVIEW_REQUIRED','COMPLETED','ARCHIVED')),
    current_version_no INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE forward_assessment_version (
    assessment_version_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES forward_assessment(assessment_id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    assessment_stage TEXT NOT NULL CHECK (assessment_stage IN ('REQUIREMENT','DESIGN','TEST','RELEASE')),
    material_type TEXT NOT NULL,
    material_name TEXT NOT NULL,
    material_hash TEXT NOT NULL,
    material_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('READY','REVIEW_REQUIRED','COMPLETED')),
    report_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (assessment_id, version_no),
    UNIQUE (assessment_id, material_hash, assessment_stage)
);

CREATE TABLE forward_risk_result (
    risk_result_id TEXT PRIMARY KEY,
    assessment_version_id TEXT NOT NULL REFERENCES forward_assessment_version(assessment_version_id) ON DELETE CASCADE,
    risk_case_version_id TEXT NOT NULL REFERENCES risk_case_version(risk_case_version_id),
    relevance REAL NOT NULL CHECK (relevance >= 0 AND relevance <= 1),
    coverage_status TEXT NOT NULL
        CHECK (coverage_status IN ('COVERED','PARTIAL','NOT_FOUND','INSUFFICIENT_INFO','NOT_APPLICABLE')),
    risk_level TEXT NOT NULL CHECK (risk_level IN ('HIGH','MEDIUM','LOW','UNKNOWN')),
    match_basis_json TEXT NOT NULL,
    existing_controls_json TEXT NOT NULL DEFAULT '[]',
    missing_controls_json TEXT NOT NULL DEFAULT '[]',
    open_questions_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING','CONFIRMED','CORRECTED','NOT_APPLICABLE')),
    review_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (assessment_version_id, risk_case_version_id)
);

CREATE INDEX idx_risk_case_status ON risk_case(status, publication_level);
CREATE INDEX idx_forward_assessment_status ON forward_assessment(status, updated_at);
CREATE INDEX idx_forward_risk_assessment ON forward_risk_result(assessment_version_id, risk_level);
