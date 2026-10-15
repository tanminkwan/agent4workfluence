"""SQLite 기록·이력 저장소 (Phase 4 설계 11절). Inference 전용 볼륨에만 둔다.

요청 text·답이 들어 있다. tool 인자·결과 본문은 넣지 않는다(길이 제한된 인자 요약만).
동기 sqlite3를 쓰고 연결은 호출마다 연다(worker thread에서 불러도 안전).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from ..engine.models import ActionSummary, HistoryTurn, TurnResult, Usage
from ..records import RequestRecord, RequestStore

INTERRUPTED_MESSAGE = "서비스가 다시 시작되어 수행이 끊겼다. 다시 수행하지 않는다"


class SqliteInferenceDb(RequestStore):
    def __init__(self, path: Path | str):
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript((Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8"))

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ---- 쓰기 ----

    def create_running(self, *, request_id, agent_id, requester, text, conversation_id, idempotency_key, created_at) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO requests(request_id, agent_id, requester, conversation_id, idempotency_key, request_text, status,"
                      " created_at) VALUES (?,?,?,?,?,?, 'running', ?)",
                      (request_id, agent_id, requester, conversation_id, idempotency_key, text, created_at))

    def finish_done(self, request_id: str, result: TurnResult, *, finished_at: str, elapsed_ms: int) -> None:
        u = result.usage
        with self._conn() as c:
            c.execute("UPDATE requests SET status='done', complete=?, stop_reason=?, answer=?, actions_json=?, usage_json=?,"
                      " input_chars=?, prompt_tokens=?, finished_at=?, elapsed_ms=? WHERE request_id=?",
                      (1 if result.complete else 0, result.stop_reason, result.answer,
                       json.dumps([a.to_dict() for a in result.actions], ensure_ascii=False), json.dumps(u.to_dict()),
                       u.last_input_chars or None, u.last_input_tokens or None, finished_at, elapsed_ms, request_id))

    def finish_failed(self, request_id: str, *, code: str, message: str, actions: Sequence[ActionSummary], usage: Usage,
                      finished_at: str, elapsed_ms: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE requests SET status='failed', error_code=?, error_message=?, actions_json=?, usage_json=?,"
                      " finished_at=?, elapsed_ms=? WHERE request_id=?",
                      (code, message, json.dumps([a.to_dict() for a in actions], ensure_ascii=False), json.dumps(usage.to_dict()),
                       finished_at, elapsed_ms, request_id))

    def mark_interrupted(self, *, finished_at: str) -> int:
        with self._conn() as c:
            cur = c.execute("UPDATE requests SET status='failed', error_code='INTERRUPTED', error_message=?, finished_at=?"
                            " WHERE status='running'", (INTERRUPTED_MESSAGE, finished_at))
            return cur.rowcount

    def purge(self, *, before: str) -> int:
        with self._conn() as c:
            return c.execute("DELETE FROM requests WHERE status IN ('done','failed') AND created_at < ?", (before,)).rowcount

    # ---- 읽기 ----

    def get(self, request_id: str) -> RequestRecord | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        return _record(row) if row else None

    def find_by_key(self, agent_id: str, key: str) -> RequestRecord | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM requests WHERE agent_id=? AND idempotency_key=?", (agent_id, key)).fetchone()
        return _record(row) if row else None

    def running_in_conversation(self, agent_id: str, conversation_id: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM requests WHERE agent_id=? AND conversation_id=? AND status='running' LIMIT 1",
                             (agent_id, conversation_id)).fetchone() is not None

    def history(self, agent_id: str, conversation_id: str, *, limit: int) -> list[HistoryTurn]:
        with self._conn() as c:
            rows = c.execute("SELECT request_id, request_text, answer, actions_json FROM requests WHERE agent_id=? AND conversation_id=?"
                             " AND status='done' AND answer IS NOT NULL ORDER BY created_at DESC, rowid DESC LIMIT ?",
                             (agent_id, conversation_id, limit)).fetchall()
        return [HistoryTurn(request_id=r["request_id"], text=r["request_text"], answer=r["answer"],
                            actions=tuple(ActionSummary.from_dict(a) for a in json.loads(r["actions_json"]))) for r in reversed(rows)]

    def last_chars_per_token(self, agent_id: str) -> float | None:
        with self._conn() as c:
            row = c.execute("SELECT input_chars, prompt_tokens FROM requests WHERE agent_id=? AND input_chars > 0 AND prompt_tokens > 0"
                            " ORDER BY finished_at DESC, rowid DESC LIMIT 1", (agent_id,)).fetchone()
        return row["input_chars"] / row["prompt_tokens"] if row else None


def _record(r: sqlite3.Row) -> RequestRecord:
    return RequestRecord(
        request_id=r["request_id"], agent_id=r["agent_id"], requester=r["requester"], conversation_id=r["conversation_id"],
        idempotency_key=r["idempotency_key"], text=r["request_text"], status=r["status"],
        complete=None if r["complete"] is None else bool(r["complete"]), stop_reason=r["stop_reason"], answer=r["answer"],
        error_code=r["error_code"], error_message=r["error_message"],
        actions=tuple(ActionSummary.from_dict(a) for a in json.loads(r["actions_json"])),
        usage=Usage.from_dict(json.loads(r["usage_json"])), created_at=r["created_at"], finished_at=r["finished_at"],
        elapsed_ms=r["elapsed_ms"])
