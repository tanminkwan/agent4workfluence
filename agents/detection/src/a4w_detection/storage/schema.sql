-- Detection SQLite 스키마 (설계 8.2절). 버전은 PRAGMA user_version.
CREATE TABLE IF NOT EXISTS spec_versions (
  target_id       TEXT NOT NULL,
  version         TEXT NOT NULL,
  git_commit      TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  operation_count INTEGER NOT NULL,
  first_seen_at   TEXT NOT NULL,
  PRIMARY KEY (target_id, version)
);

CREATE TABLE IF NOT EXISTS target_state (
  target_id       TEXT PRIMARY KEY,
  current_version TEXT NOT NULL,
  current_commit  TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS changesets (
  change_set_id    TEXT PRIMARY KEY,
  target_id        TEXT NOT NULL,
  from_version     TEXT,
  to_version       TEXT NOT NULL,
  needs_review     INTEGER NOT NULL,
  review_reasons   TEXT NOT NULL,
  document_changes TEXT NOT NULL,
  text_diff        TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  UNIQUE (target_id, from_version, to_version)
);

CREATE TABLE IF NOT EXISTS changeset_operations (
  change_set_id   TEXT NOT NULL REFERENCES changesets(change_set_id),
  seq             INTEGER NOT NULL,
  method          TEXT NOT NULL,
  path            TEXT NOT NULL,
  operation_id    TEXT,
  change          TEXT NOT NULL,
  changed_fields  TEXT,
  doc_only        INTEGER,
  before_fragment TEXT,
  after_fragment  TEXT,
  PRIMARY KEY (change_set_id, seq)
);
