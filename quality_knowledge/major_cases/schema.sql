PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS kb_schema_version(
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kb_case(
  case_id TEXT PRIMARY KEY,
  case_type TEXT NOT NULL DEFAULT 'MAJOR_REVIEW',
  title TEXT NOT NULL,
  domain TEXT NOT NULL DEFAULT '',
  group_code TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT'
    CHECK(status IN ('DRAFT','EXTRACTING','PENDING_REVIEW','ACTIVE','ARCHIVED')),
  legacy_case_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS kb_document(
  document_id TEXT PRIMARY KEY,
  group_code TEXT NOT NULL,
  logical_name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS kb_document_version(
  version_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES kb_document(document_id),
  version_no INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  media_type TEXT NOT NULL,
  attachment_path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  parser_version TEXT NOT NULL DEFAULT '',
  parse_status TEXT NOT NULL DEFAULT 'PENDING',
  parse_warnings_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(document_id,version_no),
  UNIQUE(document_id,content_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_document_group_hash
ON kb_document_version(content_hash, document_id);

CREATE TABLE IF NOT EXISTS kb_case_document(
  case_id TEXT NOT NULL REFERENCES kb_case(case_id),
  version_id TEXT NOT NULL REFERENCES kb_document_version(version_id),
  document_role TEXT NOT NULL DEFAULT 'PRIMARY'
    CHECK(document_role IN ('PRIMARY','SUPPLEMENT','REFERENCE')),
  linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(case_id,version_id)
);

CREATE TABLE IF NOT EXISTS kb_event(
  event_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES kb_case(case_id),
  standard_itr TEXT NOT NULL DEFAULT '',
  internal_event_key TEXT NOT NULL,
  event_title TEXT NOT NULL DEFAULT '',
  group_code TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(case_id,internal_event_key)
);

CREATE TABLE IF NOT EXISTS kb_source_link(
  source_link_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES kb_case(case_id),
  event_id TEXT REFERENCES kb_event(event_id),
  source_system TEXT NOT NULL,
  source_type TEXT NOT NULL,
  record_id TEXT NOT NULL,
  source_group TEXT NOT NULL,
  source_version TEXT NOT NULL DEFAULT '',
  standard_itr TEXT NOT NULL DEFAULT '',
  relation_role TEXT NOT NULL
    CHECK(relation_role IN ('CURRENT_EVENT','HISTORICAL_REFERENCE','UNCERTAIN')),
  match_status TEXT NOT NULL
    CHECK(match_status IN ('LINKED','PENDING','CONFLICT','NOT_FOUND','STALE','SOURCE_UNAVAILABLE')),
  snapshot_json TEXT NOT NULL DEFAULT '{}',
  checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  confirmed_at TEXT,
  UNIQUE(case_id,source_system,source_type,record_id,relation_role)
);

CREATE TABLE IF NOT EXISTS kb_fragment(
  fragment_id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL REFERENCES kb_document_version(version_id),
  ordinal INTEGER NOT NULL,
  section_path TEXT NOT NULL DEFAULT '',
  location_type TEXT NOT NULL,
  location_ref TEXT NOT NULL,
  fragment_type TEXT NOT NULL DEFAULT 'TEXT',
  text_content TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(version_id,ordinal)
);

CREATE TABLE IF NOT EXISTS kb_skill_version(
  skill_version_id TEXT PRIMARY KEY,
  skill_code TEXT NOT NULL,
  version INTEGER NOT NULL,
  material_types_json TEXT NOT NULL,
  required_sections_json TEXT NOT NULL,
  auxiliary_sections_json TEXT NOT NULL DEFAULT '[]',
  output_schema_json TEXT NOT NULL,
  prompt_rules TEXT NOT NULL,
  tag_dictionary_json TEXT NOT NULL DEFAULT '{}',
  input_budget INTEGER NOT NULL,
  output_budget INTEGER NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  config_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(skill_code,version),
  UNIQUE(skill_code,config_hash)
);

CREATE TABLE IF NOT EXISTS kb_run(
  run_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES kb_case(case_id),
  version_id TEXT REFERENCES kb_document_version(version_id),
  run_type TEXT NOT NULL,
  skill_version_id TEXT REFERENCES kb_skill_version(skill_version_id),
  model_profile TEXT NOT NULL DEFAULT '',
  input_hash TEXT NOT NULL,
  state TEXT NOT NULL
    CHECK(state IN ('QUEUED','RUNNING','PARTIAL','FAILED','COMPLETED','CANCELLED')),
  coverage_total INTEGER NOT NULL DEFAULT 0,
  coverage_processed INTEGER NOT NULL DEFAULT 0,
  error_code TEXT NOT NULL DEFAULT '',
  error_detail TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(case_id,run_type,input_hash)
);

CREATE TABLE IF NOT EXISTS kb_step(
  step_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES kb_run(run_id),
  step_code TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  state TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  input_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  error_detail TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  completed_at TEXT,
  UNIQUE(run_id,step_code),
  UNIQUE(idempotency_key)
);

CREATE TABLE IF NOT EXISTS kb_entry(
  entry_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES kb_case(case_id),
  event_id TEXT REFERENCES kb_event(event_id),
  entry_type TEXT NOT NULL,
  current_revision_id TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING'
    CHECK(status IN ('PENDING','CONFIRMED','CORRECTED','REJECTED','MISSING')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS kb_entry_revision(
  revision_id TEXT PRIMARY KEY,
  entry_id TEXT NOT NULL REFERENCES kb_entry(entry_id),
  revision_no INTEGER NOT NULL,
  content TEXT NOT NULL,
  applicability TEXT NOT NULL DEFAULT '',
  limitations TEXT NOT NULL DEFAULT '',
  assertion_kind TEXT NOT NULL
    CHECK(assertion_kind IN ('FACT','AI_INFERENCE','UNKNOWN','HUMAN_REVISION')),
  origin TEXT NOT NULL,
  model_profile TEXT NOT NULL DEFAULT '',
  skill_version_id TEXT REFERENCES kb_skill_version(skill_version_id),
  created_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(entry_id,revision_no)
);

CREATE TABLE IF NOT EXISTS kb_evidence(
  evidence_id TEXT PRIMARY KEY,
  revision_id TEXT NOT NULL REFERENCES kb_entry_revision(revision_id),
  fragment_id TEXT REFERENCES kb_fragment(fragment_id),
  source_link_id TEXT REFERENCES kb_source_link(source_link_id),
  locator TEXT NOT NULL,
  excerpt TEXT NOT NULL DEFAULT '',
  CHECK(fragment_id IS NOT NULL OR source_link_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS kb_tag(
  tag_id TEXT PRIMARY KEY,
  namespace TEXT NOT NULL,
  code TEXT NOT NULL,
  label TEXT NOT NULL,
  UNIQUE(namespace,code)
);

CREATE TABLE IF NOT EXISTS kb_tag_link(
  tag_link_id TEXT PRIMARY KEY,
  tag_id TEXT NOT NULL REFERENCES kb_tag(tag_id),
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('CANDIDATE','ACTIVE','REJECTED')),
  derived_from TEXT NOT NULL DEFAULT '',
  UNIQUE(tag_id,target_type,target_id)
);

CREATE TABLE IF NOT EXISTS kb_repeat_result(
  repeat_result_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES kb_run(run_id),
  current_event_id TEXT NOT NULL REFERENCES kb_event(event_id),
  candidate_event_id TEXT REFERENCES kb_event(event_id),
  candidate_case_id TEXT REFERENCES kb_case(case_id),
  decision TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  legacy_result_json TEXT NOT NULL DEFAULT '{}',
  report_json TEXT NOT NULL DEFAULT '{}',
  report_markdown TEXT NOT NULL DEFAULT '',
  review_status TEXT NOT NULL DEFAULT 'PENDING',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kb_review(
  review_id TEXT PRIMARY KEY,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  action TEXT NOT NULL,
  before_json TEXT NOT NULL DEFAULT '{}',
  after_json TEXT NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL DEFAULT '',
  reviewer TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kb_legacy_import(
  import_id TEXT PRIMARY KEY,
  legacy_case_id TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  preview_json TEXT NOT NULL,
  imported_case_id TEXT REFERENCES kb_case(case_id),
  state TEXT NOT NULL DEFAULT 'PREVIEW',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(legacy_case_id,source_hash)
);

CREATE INDEX IF NOT EXISTS idx_kb_case_group_status ON kb_case(group_code,status,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_case_group_updated ON kb_case(group_code,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_document_hash ON kb_document_version(content_hash);
CREATE INDEX IF NOT EXISTS idx_kb_event_group_itr ON kb_event(group_code,standard_itr);
CREATE INDEX IF NOT EXISTS idx_kb_source_group_itr ON kb_source_link(source_group,standard_itr,match_status);
CREATE INDEX IF NOT EXISTS idx_kb_source_record ON kb_source_link(source_system,source_type,record_id);
CREATE INDEX IF NOT EXISTS idx_kb_fragment_version_ordinal ON kb_fragment(version_id,ordinal);
CREATE INDEX IF NOT EXISTS idx_kb_entry_case_type ON kb_entry(case_id,entry_type,status);
CREATE INDEX IF NOT EXISTS idx_kb_run_state ON kb_run(state,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_kb_repeat_event ON kb_repeat_result(current_event_id,created_at DESC);

INSERT OR IGNORE INTO kb_schema_version(version) VALUES(1);
