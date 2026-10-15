"""채팅 상태 저장소 (Phase 5 설계 4.3·6·11.1절). Inference 전용 볼륨의 별도 파일(chat.db)이다.

방마다 처리한 마지막 seq와 메시지별 처리 상태만 둔다. 본문·표시 이름은 저장하지 않는다(NFR-C03).
동기 sqlite3를 쓰고 연결은 호출마다 연다(worker thread에서 불러도 안전). 쓰기는 BEGIN IMMEDIATE로 직렬화해
같은 (roomId, seq)를 동시에 넣어도 한 번만 받는다(NFR-C04).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
  room_id   TEXT PRIMARY KEY,
  kind      TEXT NOT NULL,
  last_seq  INTEGER NOT NULL,
  joined_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE TABLE IF NOT EXISTS handled (
  room_id      TEXT NOT NULL,
  seq          INTEGER NOT NULL,
  state        TEXT NOT NULL,
  request_id   TEXT,
  reply_msg_id TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  PRIMARY KEY (room_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_handled_open ON handled(state) WHERE state IN ('queued','submitted');
"""

BEGIN_STATES = ("queued", "skipped")
OPEN_STATES = ("queued", "submitted")
ALL_STATES = ("queued", "submitted", "replied", "abandoned", "skipped")


@dataclass(frozen=True)
class RoomState:
    room_id: str
    kind: str
    last_seq: int
    joined_at: str
    closed_at: str | None


@dataclass(frozen=True)
class HandledRow:
    room_id: str
    seq: int
    state: str
    request_id: str | None
    reply_msg_id: str | None
    created_at: str
    updated_at: str


class SqliteChatState:
    def __init__(self, path: Path | str):
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    # ---- 방 ----

    def register_room(self, room_id: str, kind: str, *, start_seq: int, now: str) -> bool:
        """처음 보는 방(또는 나갔다 다시 든 방)이면 start_seq에서 시작하고 True. 아는 방이면 종류만 고치고 False."""
        with self._write() as c:
            row = c.execute("SELECT closed_at, kind FROM rooms WHERE room_id=?", (room_id,)).fetchone()
            if row is None:
                c.execute("INSERT INTO rooms(room_id, kind, last_seq, joined_at) VALUES (?,?,?,?)", (room_id, kind, start_seq, now))
                return True
            if row["closed_at"] is not None:
                c.execute("UPDATE rooms SET kind=?, last_seq=?, joined_at=?, closed_at=NULL WHERE room_id=?",
                          (kind, start_seq, now, room_id))
                return True
            if row["kind"] != kind:
                c.execute("UPDATE rooms SET kind=? WHERE room_id=?", (kind, room_id))
            return False

    def close_room(self, room_id: str, *, now: str) -> None:
        with self._write() as c:
            c.execute("UPDATE rooms SET closed_at=? WHERE room_id=? AND closed_at IS NULL", (now, room_id))

    def get_room(self, room_id: str) -> RoomState | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM rooms WHERE room_id=?", (room_id,)).fetchone()
        return _room(row) if row else None

    def last_seq(self, room_id: str) -> int | None:
        room = self.get_room(room_id)
        return room.last_seq if room else None

    def open_rooms(self) -> list[RoomState]:
        with self._conn() as c:
            return [_room(r) for r in c.execute("SELECT * FROM rooms WHERE closed_at IS NULL ORDER BY joined_at, room_id")]

    # ---- 메시지 처리 ----

    def begin(self, room_id: str, seq: int, state: str, *, now: str) -> bool:
        """(roomId, seq)를 처음 받으면 기록하고 last_seq를 올린 뒤 True. 이미 처리했거나 닫힌 방이면 False."""
        if state not in BEGIN_STATES:
            raise ValueError(f"begin은 {BEGIN_STATES}만 받는다: {state}")
        with self._write() as c:
            row = c.execute("SELECT last_seq, closed_at FROM rooms WHERE room_id=?", (room_id,)).fetchone()
            if row is None:
                raise KeyError(room_id)
            if row["closed_at"] is not None or seq <= row["last_seq"]:
                return False
            c.execute("INSERT INTO handled(room_id, seq, state, created_at, updated_at) VALUES (?,?,?,?,?)",
                      (room_id, seq, state, now, now))
            c.execute("UPDATE rooms SET last_seq=? WHERE room_id=?", (seq, room_id))
            return True

    def set_state(self, room_id: str, seq: int, state: str, *, now: str, request_id: str | None = None,
                  reply_msg_id: str | None = None) -> None:
        if state not in ALL_STATES:
            raise ValueError(f"알 수 없는 상태: {state}")
        with self._write() as c:
            cur = c.execute("UPDATE handled SET state=?, request_id=COALESCE(?, request_id), reply_msg_id=COALESCE(?, reply_msg_id),"
                            " updated_at=? WHERE room_id=? AND seq=?", (state, request_id, reply_msg_id, now, room_id, seq))
            if cur.rowcount == 0:
                raise KeyError((room_id, seq))

    def get_handled(self, room_id: str, seq: int) -> HandledRow | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM handled WHERE room_id=? AND seq=?", (room_id, seq)).fetchone()
        return _handled(row) if row else None

    def open_requests(self) -> list[HandledRow]:
        """큐에 넣었으나 답을 못 보낸 요청(재시작 알림용). 방·seq 순서."""
        marks = ",".join("?" * len(OPEN_STATES))
        with self._conn() as c:
            rows = c.execute(f"SELECT * FROM handled WHERE state IN ({marks}) ORDER BY room_id, seq", OPEN_STATES).fetchall()
        return [_handled(r) for r in rows]

    def purge(self, *, before: str) -> int:
        """before보다 오래된 처리 기록을 지운다. 열린 요청과 방(last_seq)은 남긴다."""
        marks = ",".join("?" * len(OPEN_STATES))
        with self._write() as c:
            return c.execute(f"DELETE FROM handled WHERE state NOT IN ({marks}) AND created_at < ?", (*OPEN_STATES, before)).rowcount


def _room(row: sqlite3.Row) -> RoomState:
    return RoomState(room_id=row["room_id"], kind=row["kind"], last_seq=row["last_seq"], joined_at=row["joined_at"],
                     closed_at=row["closed_at"])


def _handled(row: sqlite3.Row) -> HandledRow:
    return HandledRow(room_id=row["room_id"], seq=row["seq"], state=row["state"], request_id=row["request_id"],
                      reply_msg_id=row["reply_msg_id"], created_at=row["created_at"], updated_at=row["updated_at"])
