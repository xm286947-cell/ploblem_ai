CREATE TABLE IF NOT EXISTS repeat_query_trace(
  query_id TEXT PRIMARY KEY,
  subject_ref TEXT NOT NULL,
  itr_version TEXT NOT NULL DEFAULT '',
  itr_snapshot_json TEXT NOT NULL,
  include_missed_test INTEGER NOT NULL DEFAULT 0 CHECK(include_missed_test IN (0,1)),
  missed_test_ref TEXT,
  optional_context_json TEXT,
  query_time TEXT NOT NULL,
  algorithm_version TEXT NOT NULL,
  correlation_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repeat_query_subject
ON repeat_query_trace(subject_ref, query_time DESC);

CREATE INDEX IF NOT EXISTS idx_repeat_query_correlation
ON repeat_query_trace(correlation_id);


CREATE TABLE IF NOT EXISTS repeat_result_snapshot(
  query_id TEXT PRIMARY KEY REFERENCES repeat_query_trace(query_id),
  result_version TEXT NOT NULL,
  result_status TEXT NOT NULL,
  search_status TEXT NOT NULL,
  result_json TEXT NOT NULL,
  human_decision TEXT NOT NULL DEFAULT 'PENDING'
    CHECK(human_decision IN ('PENDING','REPEAT','SIMILAR','NOT_REPEAT','INSUFFICIENT_EVIDENCE')),
  decided_by TEXT,
  decision_reason TEXT,
  decided_at TEXT,
  generated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repeat_result_status
ON repeat_result_snapshot(result_status, generated_at DESC);
