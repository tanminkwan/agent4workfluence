"""manufacture SQLite (Phase 2 설계 13.3절): 타겟 상태, 반영 기록, 동작 상태, 보류, LLM 결과 재사용, 색인 상태.

manufacture-mcp는 이 파일을 읽지 않는다 (시스템아키텍처 4.1절).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from a4w_core.errors import StorageError

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class TargetState:
    target_id: str
    last_applied_spec_version: str | None
    last_change_set_id: str | None
    head_tool_version: str | None
    current_tool_version: str | None
    updated_at: str


@dataclass(frozen=True)
class OperationRecord:
    target_id: str
    method: str
    path: str
    operation_id: str | None
    fragment: dict[str, Any] | None
    fragment_sha256: str | None
    structure_sha256: str | None
    spec_version: str
    selection: str
    reason: str | None
    tool_name: str | None
    state: str
    last_error: Any
    updated_at: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)


_REVIEW_COLS = ("review_id", "target_id", "apply_id", "kind", "method", "path", "operation_id", "tool_name", "reasons",
                "definition", "previous_revision", "verify", "fragment_sha256", "status", "decided_by", "decided_at",
                "note", "applied_in")
_REVIEW_KEYS = ("reviewId", "targetId", "applyId", "kind", "method", "path", "operationId", "toolName", "reasons",
                "definition", "previousRevision", "verify", "fragmentSha256", "status", "decidedBy", "decidedAt",
                "note", "appliedIn")
_REVIEW_JSON = {"reasons", "definition", "verify"}


class _Tx:
    def __init__(self, con: sqlite3.Connection):
        self._con = con

    def save_state(self, st: TargetState) -> None:
        self._con.execute(
            "INSERT INTO target_state VALUES (?,?,?,?,?,?) ON CONFLICT(target_id) DO UPDATE SET "
            "last_applied_spec_version=excluded.last_applied_spec_version, last_change_set_id=excluded.last_change_set_id, "
            "head_tool_version=excluded.head_tool_version, current_tool_version=excluded.current_tool_version, "
            "updated_at=excluded.updated_at",
            (st.target_id, st.last_applied_spec_version, st.last_change_set_id, st.head_tool_version,
             st.current_tool_version, st.updated_at),
        )

    def save_operations(self, target_id: str, records: Iterable[OperationRecord]) -> None:
        """그 타겟의 동작 상태를 통째로 바꾼다."""
        self._con.execute("DELETE FROM operations WHERE target_id = ?", (target_id,))
        self._con.executemany(
            "INSERT INTO operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r.target_id, r.method, r.path, r.operation_id, _dump(r.fragment), r.fragment_sha256, r.structure_sha256,
              r.spec_version, r.selection, r.reason, r.tool_name, r.state, _dump(r.last_error), r.updated_at)
             for r in records],
        )

    def add_items(self, apply_id: str, items: Iterable[dict[str, Any]]) -> None:
        self._con.executemany("INSERT INTO apply_items VALUES (?,?,?)",
                              [(apply_id, i, _dump(item)) for i, item in enumerate(items)])

    def add_review(self, review: dict[str, Any]) -> None:
        values = [_dump(review.get(k)) if k in _REVIEW_JSON else review.get(k) for k in _REVIEW_KEYS]
        self._con.execute(f"INSERT INTO reviews ({','.join(_REVIEW_COLS)}) VALUES ({','.join('?' * len(_REVIEW_COLS))})",
                          values)

    def set_review_status(self, review_id: str, status: str, *, applied_in: str | None = None) -> None:
        self._con.execute("UPDATE reviews SET status=?, applied_in=COALESCE(?, applied_in) WHERE review_id=?",
                          (status, applied_in, review_id))

    def supersede_reviews(self, target_id: str, method: str, path: str) -> None:
        self._con.execute(
            "UPDATE reviews SET status='superseded' WHERE target_id=? AND method=? AND path=? "
            "AND status IN ('pending','approved')", (target_id, method, path))


class SqliteManufactureDb:
    def __init__(self, db_path: Path | str):
        self._path = Path(db_path)
        self._lock = threading.Lock()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as con, con:
                if con.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                    con.executescript(files("a4w_manufacture.storage").joinpath("schema.sql").read_text())
                    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except (OSError, sqlite3.Error) as e:
            raise StorageError(f"SQLite를 열 수 없다: {type(e).__name__}") from None

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=30, check_same_thread=False)
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        return con

    @contextmanager
    def transaction(self) -> Iterator[_Tx]:
        try:
            with self._lock, closing(self._connect()) as con, con:
                yield _Tx(con)
        except sqlite3.Error as e:
            raise StorageError(f"SQLite 쓰기 실패: {type(e).__name__}") from None

    def _query(self, sql: str, args: tuple = ()) -> list[tuple]:
        try:
            with closing(self._connect()) as con:
                return con.execute(sql, args).fetchall()
        except sqlite3.Error as e:
            raise StorageError(f"SQLite 읽기 실패: {type(e).__name__}") from None

    def _write(self, sql: str, args: tuple = ()) -> int:
        with self.transaction() as tx:
            return tx._con.execute(sql, args).rowcount

    def check(self) -> None:
        self._query("SELECT 1")

    # ── 타겟 상태
    def state(self, target_id: str) -> TargetState | None:
        rows = self._query("SELECT * FROM target_state WHERE target_id=?", (target_id,))
        return TargetState(*rows[0]) if rows else None

    # ── 동작 상태
    def operations(self, target_id: str) -> dict[tuple[str, str], OperationRecord]:
        rows = self._query("SELECT * FROM operations WHERE target_id=? ORDER BY path, method", (target_id,))
        out = {}
        for r in rows:
            rec = OperationRecord(r[0], r[1], r[2], r[3], _load(r[4]), r[5], r[6], r[7], r[8], r[9], r[10], r[11],
                                  _load(r[12]), r[13])
            out[rec.key] = rec
        return out

    # ── 반영 기록
    def create_apply(self, rec: dict[str, Any]) -> None:
        self._write("INSERT INTO applies (apply_id, target_id, change_set_id, status, started_at) VALUES (?,?,?,?,?)",
                    (rec["applyId"], rec["targetId"], rec.get("changeSetId"), rec["status"], rec["startedAt"]))

    def finish_apply(self, apply_id: str, *, status: str, error_code: str | None, tool_version: str | None,
                     result: dict[str, Any], finished_at: str) -> None:
        self._write("UPDATE applies SET status=?, error_code=?, tool_version=?, result=?, finished_at=? WHERE apply_id=?",
                    (status, error_code, tool_version, _dump(result), finished_at, apply_id))

    def get_apply(self, apply_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT apply_id, target_id, change_set_id, status, error_code, tool_version, result, "
                           "started_at, finished_at FROM applies WHERE apply_id=?", (apply_id,))
        if not rows:
            return None
        a = rows[0]
        return {"applyId": a[0], "targetId": a[1], "changeSetId": a[2], "status": a[3], "errorCode": a[4],
                "toolVersion": a[5], "result": _load(a[6]), "startedAt": a[7], "finishedAt": a[8]}

    def find_done_apply(self, target_id: str, change_set_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT apply_id FROM applies WHERE target_id=? AND change_set_id=? "
                           "AND status IN ('applied','partial','noop') ORDER BY started_at DESC LIMIT 1",
                           (target_id, change_set_id))
        return self.get_apply(rows[0][0]) if rows else None

    def running_apply(self, target_id: str) -> str | None:
        rows = self._query("SELECT apply_id FROM applies WHERE target_id=? AND status='running' "
                           "ORDER BY started_at DESC LIMIT 1", (target_id,))
        return rows[0][0] if rows else None

    def interrupt_running(self, finished_at: str) -> int:
        result = _dump({"status": "failed", "error": {"code": "INTERRUPTED", "message": "반영 도중 서버가 다시 시작됐다"}})
        return self._write("UPDATE applies SET status='failed', error_code='INTERRUPTED', result=?, finished_at=? "
                           "WHERE status='running'", (result, finished_at))

    def items(self, apply_id: str) -> list[dict[str, Any]]:
        return [_load(r[0]) for r in self._query("SELECT item FROM apply_items WHERE apply_id=? ORDER BY seq",
                                                  (apply_id,))]

    # ── 보류
    def reviews(self, target_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        sql = f"SELECT {','.join(_REVIEW_COLS)} FROM reviews WHERE target_id=?"
        args: tuple = (target_id,)
        if status:
            sql += " AND status=?"
            args += (status,)
        return [_review(r) for r in self._query(sql + " ORDER BY review_id", args)]

    def review(self, review_id: str) -> dict[str, Any] | None:
        rows = self._query(f"SELECT {','.join(_REVIEW_COLS)} FROM reviews WHERE review_id=?", (review_id,))
        return _review(rows[0]) if rows else None

    def decide_review(self, review_id: str, *, decision: str, by: str | None, note: str | None, at: str) -> int:
        return self._write("UPDATE reviews SET status=?, decided_by=?, note=?, decided_at=? "
                           "WHERE review_id=? AND status='pending'", (decision, by, note, at, review_id))

    # ── LLM 결과 재사용
    def llm_cached(self, key: str) -> dict[str, Any] | None:
        rows = self._query("SELECT output FROM llm_cache WHERE cache_key=?", (key,))
        return _load(rows[0][0]) if rows else None

    def llm_store(self, key: str, *, task: str, model: str, prompt: str, output: Any, usage: Any, at: str) -> None:
        self._write("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?,?,?)",
                    (key, task, model, prompt, _dump(output), _dump(usage), at))

    # ── 색인 상태
    def index_state(self, target_id: str) -> dict[str, dict[str, str]]:
        rows = self._query("SELECT tool_name, point_id, revision, content_sha256 FROM index_state WHERE target_id=?",
                           (target_id,))
        return {r[0]: {"pointId": r[1], "revision": r[2], "contentSha256": r[3]} for r in rows}

    def index_upsert(self, target_id: str, tool_name: str, *, point_id: str, revision: str, content_sha256: str,
                     at: str) -> None:
        self._write("INSERT OR REPLACE INTO index_state VALUES (?,?,?,?,?,?)",
                    (target_id, tool_name, point_id, revision, content_sha256, at))

    def index_delete(self, target_id: str, tool_name: str) -> None:
        self._write("DELETE FROM index_state WHERE target_id=? AND tool_name=?", (target_id, tool_name))


def _review(row: tuple) -> dict[str, Any]:
    return {k: (_load(v) if k in _REVIEW_JSON else v) for k, v in zip(_REVIEW_KEYS, row)}


def _dump(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, sort_keys=True)


def _load(s: str | None) -> Any:
    return None if s is None else json.loads(s)
