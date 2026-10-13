"""검증 기록 SQLite. 변경 식별 DB와 파일을 나눈다(스키마 버전을 따로 올리기 위해, Phase 2 설계 15절 구현 변경).

요청의 tool 정의 전문은 남기지 않는다. 이름·revision만 남긴다.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from contextlib import closing
from pathlib import Path
from typing import Any

from a4w_core.errors import StorageError

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS verifications (
  verification_id TEXT PRIMARY KEY,
  target_id       TEXT NOT NULL,
  status          TEXT NOT NULL,          -- running | done | failed
  request         TEXT NOT NULL,          -- JSON (이름·revision만)
  results         TEXT,                   -- JSON 배열
  sandbox         TEXT,                   -- JSON
  error           TEXT,                   -- JSON {code, message}
  started_at      TEXT NOT NULL,
  finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS verifications_status ON verifications(status);
"""


class VerificationStore(ABC):
    @abstractmethod
    def get(self, verification_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def create(self, record: dict[str, Any]) -> None: ...

    @abstractmethod
    def finish(self, verification_id: str, *, status: str, results, sandbox, error, finished_at: str) -> None: ...

    @abstractmethod
    def interrupt_running(self, finished_at: str) -> int: ...

    @abstractmethod
    def purge_finished_before(self, iso: str) -> int: ...


class SqliteVerificationStore(VerificationStore):
    def __init__(self, db_path: Path | str):
        self._path = Path(db_path)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as con, con:
                if con.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                    con.executescript(SCHEMA)
                    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except (OSError, sqlite3.Error) as e:
            raise StorageError(f"검증 기록 SQLite를 열 수 없다: {type(e).__name__}") from None

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=30, check_same_thread=False)
        con.execute("PRAGMA journal_mode = WAL")
        return con

    def get(self, verification_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT verification_id, target_id, status, request, results, sandbox, error, started_at, finished_at "
                "FROM verifications WHERE verification_id = ?", (verification_id,)
            ).fetchone()
        if row is None:
            return None
        vid, tid, status, request, results, sandbox, error, started, finished = row
        return {
            "verificationId": vid, "targetId": tid, "status": status, "startedAt": started, "finishedAt": finished,
            "request": json.loads(request), "results": _load(results) or [], "sandbox": _load(sandbox),
            "error": _load(error),
        }

    def create(self, record: dict[str, Any]) -> None:
        with closing(self._connect()) as con, con:
            con.execute(
                "INSERT INTO verifications (verification_id, target_id, status, request, started_at) VALUES (?,?,?,?,?)",
                (record["verificationId"], record["targetId"], record["status"], _dump(record["request"]),
                 record["startedAt"]),
            )

    def finish(self, verification_id: str, *, status: str, results, sandbox, error, finished_at: str) -> None:
        with closing(self._connect()) as con, con:
            con.execute(
                "UPDATE verifications SET status=?, results=?, sandbox=?, error=?, finished_at=? WHERE verification_id=?",
                (status, _dump(results), _dump(sandbox), _dump(error), finished_at, verification_id),
            )

    def interrupt_running(self, finished_at: str) -> int:
        error = _dump({"code": "INTERRUPTED", "message": "검증 도중 서버가 다시 시작됐다"})
        with closing(self._connect()) as con, con:
            return con.execute(
                "UPDATE verifications SET status='failed', error=?, finished_at=? WHERE status='running'",
                (error, finished_at),
            ).rowcount

    def purge_finished_before(self, iso: str) -> int:
        with closing(self._connect()) as con, con:
            return con.execute(
                "DELETE FROM verifications WHERE status != 'running' AND finished_at < ?", (iso,)
            ).rowcount


def _dump(v):
    return None if v is None else json.dumps(v, ensure_ascii=False, sort_keys=True)


def _load(s):
    return None if s is None else json.loads(s)
