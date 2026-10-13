"""반영 접수와 비동기 실행 (Phase 2 설계 4.1·4.2절).

접수 단계(동기): 타겟 등록, 변경분 존재, 순서(fromVersion), 멱등(이미 반영한 changeSetId), 타겟 잠금.
실행 단계(작업 스레드): ApplyService.run → 결과 기록 → 잠금 풀기.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from a4w_core.errors import A4WError, TargetNotFound
from a4w_core.locking import FileTargetLock, HeldLock

from .errors import ApplyInProgress, ApplyNotFound, ChangeSetOutOfOrder
from .ports import ChangeSetSource
from .service import ApplyService
from .storage.sqlite_db import SqliteManufactureDb

log = logging.getLogger(__name__)
Starter = Callable[[Callable[[], None]], None]


def thread_starter(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="a4w-apply", daemon=True).start()


def new_apply_id() -> str:
    return f"ap-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


class ApplyRunner:
    def __init__(self, *, service: ApplyService, changesets: ChangeSetSource, db: SqliteManufactureDb,
                 lock: FileTargetLock, clock: Callable[[], str], new_id: Callable[[], str] = new_apply_id,
                 starter: Starter = thread_starter):
        self._service = service
        self._changesets = changesets
        self._db = db
        self._lock = lock
        self._clock = clock
        self._new_id = new_id
        self._start = starter

    def start(self, target_id: str, change_set_id: str | None) -> tuple[dict[str, Any], bool]:
        """(응답 본문, 새로 시작했는지)."""
        if target_id not in self._service.targets:
            raise TargetNotFound(f"등록되지 않은 타겟: {target_id}")
        cs = None
        if change_set_id is not None:
            done = self._db.find_done_apply(target_id, change_set_id)
            if done is not None:
                return self.view(done), False
            cs = self._changesets.changeset(target_id, change_set_id)
            state = self._db.state(target_id)
            last = state.last_applied_spec_version if state else None
            if cs.from_version != last:
                raise ChangeSetOutOfOrder(
                    "변경분 순서가 맞지 않다. 마지막 반영 버전에서 시작하는 변경분을 먼저 반영하라",
                    details={"lastAppliedSpecVersion": last, "changeSetFromVersion": cs.from_version},
                )
        try:
            held = self._lock.acquire(target_id)
        except ApplyInProgress:
            raise ApplyInProgress("같은 타겟의 반영이 진행 중이다",
                                  details={"applyId": self._db.running_apply(target_id)}) from None
        try:
            apply_id = self._new_id()
            self._db.create_apply({"applyId": apply_id, "targetId": target_id, "changeSetId": change_set_id,
                                   "status": "running", "startedAt": self._clock()})
        except BaseException:
            held.release()
            raise
        self._start(lambda: self._execute(target_id, apply_id, cs, held))
        return {"status": "running", "applyId": apply_id, "targetId": target_id, "changeSetId": change_set_id}, True

    def _execute(self, target_id: str, apply_id: str, cs, held: HeldLock) -> None:
        try:
            try:
                result = self._service.run(target_id, apply_id=apply_id, changeset=cs)
                status, code, version = result["status"], None, result.get("toolVersion")
            except A4WError as e:
                log.warning("apply failed: target=%s apply=%s code=%s", target_id, apply_id, e.code)
                result = _failed(apply_id, target_id, cs, e.code, e.message, getattr(e, "details", None))
                status, code, version = "failed", e.code, None
            except Exception:
                log.exception("apply crashed: target=%s apply=%s", target_id, apply_id)
                result = _failed(apply_id, target_id, cs, "INTERNAL_ERROR", "반영 도중 내부 오류", None)
                status, code, version = "failed", "INTERNAL_ERROR", None
            self._db.finish_apply(apply_id, status=status, error_code=code, tool_version=version, result=result,
                                  finished_at=self._clock())
        finally:
            held.release()

    def get(self, apply_id: str) -> dict[str, Any]:
        rec = self._db.get_apply(apply_id)
        if rec is None:
            raise ApplyNotFound(f"반영 기록이 없다: {apply_id}")
        return self.view(rec)

    @staticmethod
    def view(rec: dict[str, Any]) -> dict[str, Any]:
        if rec["status"] == "running" or rec["result"] is None:
            return {"status": "running", "applyId": rec["applyId"], "targetId": rec["targetId"],
                    "changeSetId": rec["changeSetId"], "startedAt": rec["startedAt"]}
        out = dict(rec["result"])
        out.update(applyId=rec["applyId"], startedAt=rec["startedAt"], finishedAt=rec["finishedAt"])
        out.setdefault("error", None)
        return out

    def rollback(self, target_id: str, tool_version: str) -> dict[str, Any]:
        """되돌리기도 타겟 잠금을 잡는다 (반영과 동시에 돌지 않음). 동기로 끝난다."""
        if target_id not in self._service.targets:
            raise TargetNotFound(f"등록되지 않은 타겟: {target_id}")
        try:
            held = self._lock.acquire(target_id)
        except ApplyInProgress:
            raise ApplyInProgress("같은 타겟의 반영이 진행 중이다",
                                  details={"applyId": self._db.running_apply(target_id)}) from None
        try:
            return self._service.rollback(target_id, tool_version)
        finally:
            held.release()

    def recover(self) -> int:
        """시작할 때 running으로 남은 반영을 INTERRUPTED로 바꾸고, 저장소를 정리한다. 다시 실행하지 않는다."""
        count = self._db.interrupt_running(self._clock())
        for tid in self._service.targets:
            self._service.recover(tid)
        return count


def _failed(apply_id, target_id, cs, code, message, details) -> dict[str, Any]:
    return {"applyId": apply_id, "status": "failed", "targetId": target_id,
            "changeSetId": cs.change_set_id if cs else None, "toolVersion": None,
            "error": {"code": code, "message": message, **({"details": details} if details is not None else {})}}
