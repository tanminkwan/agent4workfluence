"""SQLite 기록 (설계 8.2절). 요약은 저장하지 않고 변경 목록에서 계산한다."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from importlib.resources import files
from pathlib import Path

from a4w_core.errors import StorageError
from a4w_core.models import ChangeSet, DocumentChange, OperationChange, ReviewResult, SpecVersion

from ..ports import DetectionStore

SCHEMA_VERSION = 1


def _dump(v):
    return None if v is None else json.dumps(v, ensure_ascii=False, sort_keys=True)


def _load(s):
    return None if s is None else json.loads(s)


class SqliteDetectionStore(DetectionStore):
    def __init__(self, db_path: Path | str):
        self._path = Path(db_path)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as con, con:
                if con.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
                    con.executescript(files("a4w_detection.storage").joinpath("schema.sql").read_text())
                    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except (OSError, sqlite3.Error) as e:
            raise StorageError(f"SQLite를 열 수 없다: {type(e).__name__}") from None

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=30)
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        return con

    def check(self) -> None:
        try:
            with closing(self._connect()) as con:
                con.execute("SELECT 1").fetchone()
        except sqlite3.Error as e:
            raise StorageError(f"SQLite 접근 실패: {type(e).__name__}") from None

    def current(self, target_id: str) -> SpecVersion | None:
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT v.version, s.current_commit, v.size_bytes, v.operation_count, v.first_seen_at "
                "FROM target_state s JOIN spec_versions v "
                "ON v.target_id = s.target_id AND v.version = s.current_version WHERE s.target_id = ?",
                (target_id,),
            ).fetchone()
        return SpecVersion(target_id, *row) if row else None

    def _changeset(self, con, row) -> ChangeSet:
        cs_id, target_id, frm, to, needs, reasons, docs, text, created = row
        ops = tuple(
            OperationChange(
                method=m, path=p, operation_id=oid, change=ch,
                before=_load(bf), after=_load(af),
                changed_fields=tuple(_load(cf)) if cf is not None else None,
                doc_only=None if do is None else bool(do),
            )
            for m, p, oid, ch, cf, do, bf, af in con.execute(
                "SELECT method, path, operation_id, change, changed_fields, doc_only, before_fragment, after_fragment "
                "FROM changeset_operations WHERE change_set_id = ? ORDER BY seq",
                (cs_id,),
            )
        )
        return ChangeSet(
            change_set_id=cs_id, target_id=target_id, from_version=frm, to_version=to, created_at=created,
            operations=ops,
            document_changes=tuple(DocumentChange(**d) for d in _load(docs)),
            review=ReviewResult(bool(needs), tuple(_load(reasons))),
            text_diff=text,
        )

    _CS_COLS = "change_set_id, target_id, from_version, to_version, needs_review, review_reasons, document_changes, text_diff, created_at"

    def find_changeset(self, target_id, from_version, to_version):
        with closing(self._connect()) as con:
            row = con.execute(
                f"SELECT {self._CS_COLS} FROM changesets WHERE target_id = ? AND from_version IS ? AND to_version = ?",
                (target_id, from_version, to_version),
            ).fetchone()
            return self._changeset(con, row) if row else None

    def get_changeset(self, target_id, change_set_id):
        with closing(self._connect()) as con:
            row = con.execute(
                f"SELECT {self._CS_COLS} FROM changesets WHERE target_id = ? AND change_set_id = ?",
                (target_id, change_set_id),
            ).fetchone()
            return self._changeset(con, row) if row else None

    def record(self, version: SpecVersion, commit: str, changeset: ChangeSet, updated_at: str) -> None:
        cs = changeset
        try:
            with closing(self._connect()) as con, con:
                con.execute("BEGIN IMMEDIATE")
                # SQLite UNIQUE는 NULL을 서로 다르게 보므로 첫 변경분 중복은 직접 막는다 (설계 8.2절)
                if con.execute(
                    "SELECT 1 FROM changesets WHERE target_id = ? AND from_version IS ? AND to_version = ?",
                    (cs.target_id, cs.from_version, cs.to_version),
                ).fetchone():
                    raise StorageError("같은 변경분이 이미 있다")
                con.execute(
                    "INSERT OR IGNORE INTO spec_versions VALUES (?, ?, ?, ?, ?, ?)",
                    (version.target_id, version.version, version.git_commit, version.size_bytes,
                     version.operation_count, version.first_seen_at),
                )
                con.execute(
                    f"INSERT INTO changesets ({self._CS_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cs.change_set_id, cs.target_id, cs.from_version, cs.to_version, int(cs.review.needs_review),
                     _dump(list(cs.review.reasons)),
                     _dump([{"section": d.section, "change": d.change} for d in cs.document_changes]),
                     cs.text_diff, cs.created_at),
                )
                con.executemany(
                    "INSERT INTO changeset_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (cs.change_set_id, i, o.method, o.path, o.operation_id, o.change,
                         _dump(list(o.changed_fields)) if o.changed_fields is not None else None,
                         None if o.doc_only is None else int(o.doc_only),
                         _dump(o.before), _dump(o.after))
                        for i, o in enumerate(cs.operations)
                    ],
                )
                con.execute(
                    "INSERT INTO target_state VALUES (?, ?, ?, ?) ON CONFLICT(target_id) DO UPDATE SET "
                    "current_version = excluded.current_version, current_commit = excluded.current_commit, "
                    "updated_at = excluded.updated_at",
                    (version.target_id, version.version, commit, updated_at),
                )
        except sqlite3.Error as e:
            raise StorageError(f"기록 실패: {type(e).__name__}") from None
