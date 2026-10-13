"""검증 접수·실행·조회 (Phase 2 설계 11.3절). 접수는 바로 답하고 실행은 작업 스레드에서 한다."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from a4w_core.errors import (
    A4WError, TargetNotFound, UnsupportedSandbox, VerificationNotFound, VerifyRequestInvalid,
)
from a4w_core.interfaces import ScopeProvider
from a4w_core.locking import FileTargetLock, HeldLock
from a4w_core.openapi.canonical import canonical_json

from ..ports import Clock
from .guard import check_request
from .runner import VerificationRunner
from .store import VerificationStore

log = logging.getLogger(__name__)

Starter = Callable[[Callable[[], None]], None]
ISO = "%Y-%m-%dT%H:%M:%SZ"


def thread_starter(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="a4w-verify", daemon=True).start()


@dataclass(frozen=True)
class VerifyRuntime:
    """타겟 하나의 검증 구현 묶음 (조립 지점이 타겟 설정·비밀을 보고 만든다)."""

    runner: VerificationRunner
    scope_provider: ScopeProvider
    token: str | None = field(repr=False)  # 로그·repr에 남기지 않는다
    sandbox_name: str
    context_keys: list[str]
    deny_scopes: frozenset[str]


class VerificationService:
    def __init__(
        self,
        *,
        known_targets: Collection[str],
        runtimes: Mapping[str, VerifyRuntime],
        store: VerificationStore,
        lock: FileTargetLock,
        spec_operations: Callable[[str], Collection[tuple[str, str]]],
        clock: Clock,
        max_tools: int,
        keep_days: int,
        starter: Starter = thread_starter,
    ):
        self._known = set(known_targets)
        self.runtimes = dict(runtimes)
        self._store = store
        self._lock = lock
        self._spec_operations = spec_operations
        self._clock = clock
        self._max_tools = max_tools
        self._keep_days = keep_days
        self._start = starter

    def submit(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """(기록, 새로 시작했는지). 같은 verificationId면 새로 실행하지 않는다."""
        tid, vid = payload["targetId"], payload["verificationId"]
        if tid not in self._known:
            raise TargetNotFound(f"등록되지 않은 타겟: {tid}")
        rt = self.runtimes.get(tid)
        if rt is None:
            raise UnsupportedSandbox(f"검증 환경이 설정되지 않은 타겟: {tid}")
        tools, refs = list(payload["tools"]), list(payload.get("references") or [])
        scenarios = dict(payload.get("scenarios") or {})
        fingerprint = _fingerprint(tools, refs, scenarios)
        existing = self._store.get(vid)
        if existing is not None:
            if existing["targetId"] != tid:
                raise VerifyRequestInvalid("다른 타겟이 쓴 verificationId다")
            if existing["request"].get("fingerprint") != fingerprint:
                # 같은 id에 다른 요청이면 옛 결과를 돌려주지 않는다 (조용히 낡은 결과를 쓰지 않게)
                raise VerifyRequestInvalid("같은 verificationId로 다른 요청이 왔다. 새 verificationId를 쓴다")
            return existing, False

        problems = check_request(
            tools=tools, references=refs, scenarios=scenarios, spec_operations=self._spec_operations(tid),
            deny_scopes=rt.deny_scopes, max_tools=self._max_tools, context_keys=set(rt.context_keys),
        )
        if problems:
            raise VerifyRequestInvalid("검증 요청을 받을 수 없다", details=problems)

        held = self._lock.acquire(f"verify-{tid}")  # 진행 중이면 VerifyInProgress
        try:
            now = self._clock.now_iso()
            self._store.purge_finished_before(_days_before(now, self._keep_days))
            record = {
                "verificationId": vid, "targetId": tid, "status": "running", "startedAt": now, "finishedAt": None,
                "request": {"tools": _names(tools), "references": _names(refs), "scenarios": sorted(scenarios),
                            "fingerprint": fingerprint},
                "results": [], "sandbox": None, "error": None,
            }
            self._store.create(record)
        except BaseException:
            held.release()
            raise
        self._start(lambda: self._execute(rt, vid, tools, refs, scenarios, held))
        return record, True

    def _execute(self, rt: VerifyRuntime, vid: str, tools, refs, scenarios, held: HeldLock) -> None:
        status, results, sandbox, error = "failed", [], None, None
        try:
            out = rt.runner.run(vid, tools=tools, references=refs, scenarios=scenarios)
            status, results, sandbox = "done", out["results"], out["sandbox"]
        except A4WError as e:
            error = {"code": e.code, "message": e.message}
            sandbox = {"prepared": False, "cleanup": None, "cleanupFailures": []}
        except Exception:
            log.exception("verification crashed: id=%s", vid)
            error = {"code": "INTERNAL_ERROR", "message": "검증 도중 내부 오류"}
        finally:
            try:
                self._store.finish(vid, status=status, results=results, sandbox=sandbox, error=error,
                                   finished_at=self._clock.now_iso())
            finally:
                held.release()
        log.info("verification finished: id=%s status=%s tools=%d", vid, status, len(results))

    def get(self, target_id: str, verification_id: str) -> dict[str, Any]:
        record = self._store.get(verification_id)
        if record is None or record["targetId"] != target_id:
            raise VerificationNotFound(f"검증 기록이 없다: {verification_id}")
        return record

    def recover(self) -> int:
        """시작할 때 `running`으로 남은 기록을 INTERRUPTED로 바꾼다. 다시 실행하지 않는다."""
        return self._store.interrupt_running(self._clock.now_iso())


def _names(defs: list[Any]) -> list[dict[str, Any]]:
    return [{"name": d.get("name"), "revision": d.get("revision")} for d in defs if isinstance(d, Mapping)]


def _days_before(iso: str, days: int) -> str:
    return (datetime.strptime(iso, ISO) - timedelta(days=days)).strftime(ISO)


def _fingerprint(tools, refs, scenarios) -> str:
    """요청 내용의 지문: tool·참조의 이름·revision과 시나리오 전문."""
    body = {"tools": _names(tools), "references": _names(refs), "scenarios": scenarios}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
