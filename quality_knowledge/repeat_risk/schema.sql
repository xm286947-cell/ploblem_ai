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
