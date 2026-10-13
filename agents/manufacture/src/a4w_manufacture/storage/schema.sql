-- manufacture SQLite 스키마 (Phase 2 설계 13.3절). 버전은 PRAGMA user_version.
CREATE TABLE IF NOT EXISTS target_state (
  target_id                 TEXT PRIMARY KEY,
  last_applied_spec_version TEXT,
  last_change_set_id        TEXT,
  head_tool_version         TEXT,
  current_tool_version      TEXT,
  updated_at                TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applies (
  apply_id      TEXT PRIMARY KEY,
  target_id     TEXT NOT NULL,
  change_set_id TEXT,
  status        TEXT NOT NULL,          -- running | applied | partial | noop | failed
  error_code    TEXT,
  tool_version  TEXT,
  result        TEXT,                   -- JSON
  started_at    TEXT NOT NULL,
  finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS applies_by_changeset ON applies(target_id, change_set_id, status);

CREATE TABLE IF NOT EXISTS operations (
  target_id        TEXT NOT NULL,
  method           TEXT NOT NULL,
  path             TEXT NOT NULL,
  operation_id     TEXT,
  fragment         TEXT,                -- JSON (removed면 NULL)
  fragment_sha256  TEXT,
  structure_sha256 TEXT,
  spec_version     TEXT NOT NULL,
  selection        TEXT NOT NULL,       -- tool | excluded | codeOwned | removed
  reason           TEXT,
  tool_name        TEXT,
  state            TEXT NOT NULL,       -- active | pending | failed | excluded | codeOwned | rejected | removed
  last_error       TEXT,                -- JSON
  updated_at       TEXT NOT NULL,
  PRIMARY KEY (target_id, method, path)
);

CREATE TABLE IF NOT EXISTS apply_items (
  apply_id     TEXT NOT NULL REFERENCES applies(apply_id),
  seq          INTEGER NOT NULL,
  item         TEXT NOT NULL,           -- JSON (응답 items 한 줄)
  PRIMARY KEY (apply_id, seq)
);

CREATE TABLE IF NOT EXISTS reviews (
  review_id         TEXT PRIMARY KEY,
  target_id         TEXT NOT NULL,
  apply_id          TEXT NOT NULL,
  kind              TEXT NOT NULL,      -- tool | notice
  method            TEXT,
  path              TEXT,
  operation_id      TEXT,
  tool_name         TEXT,
  reasons           TEXT NOT NULL,      -- JSON
  definition        TEXT,               -- JSON
  previous_revision TEXT,
  verify            TEXT,               -- JSON
  fragment_sha256   TEXT,
  status            TEXT NOT NULL,      -- pending | approved | rejected | applied | superseded
  decided_by        TEXT,
  decided_at        TEXT,
  note              TEXT,
  applied_in        TEXT
);
CREATE INDEX IF NOT EXISTS reviews_by_target ON reviews(target_id, status);

CREATE TABLE IF NOT EXISTS llm_cache (
  cache_key  TEXT PRIMARY KEY,
  task       TEXT NOT NULL,
  model      TEXT NOT NULL,
  prompt     TEXT NOT NULL,
  output     TEXT NOT NULL,
  usage      TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS index_state (
  target_id      TEXT NOT NULL,
  tool_name      TEXT NOT NULL,
  point_id       TEXT NOT NULL,
  revision       TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  indexed_at     TEXT NOT NULL,
  PRIMARY KEY (target_id, tool_name)
);
