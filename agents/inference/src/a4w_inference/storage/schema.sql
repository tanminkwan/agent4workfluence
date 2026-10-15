CREATE TABLE IF NOT EXISTS requests (
  request_id        TEXT PRIMARY KEY,
  agent_id          TEXT NOT NULL,
  requester         TEXT NOT NULL,
  conversation_id   TEXT,
  idempotency_key   TEXT,
  request_text      TEXT NOT NULL,
  status            TEXT NOT NULL,
  complete          INTEGER,
  stop_reason       TEXT,
  answer            TEXT,
  error_code        TEXT,
  error_message     TEXT,
  actions_json      TEXT NOT NULL DEFAULT '[]',
  usage_json        TEXT NOT NULL DEFAULT '{}',
  input_chars       INTEGER,
  prompt_tokens     INTEGER,
  created_at        TEXT NOT NULL,
  finished_at       TEXT,
  elapsed_ms        INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_requests_idem ON requests(agent_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_requests_conv ON requests(agent_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS ix_requests_created ON requests(created_at);
