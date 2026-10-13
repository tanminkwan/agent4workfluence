"""Detection 클라이언트 (Phase 2 설계 16.6절). manufacture가 타겟 쪽으로 닿는 유일한 길이다.

- 변경분 조회, 타겟 정보(scope) 조회: 재시도하지 않는다 (Phase 1 10절과 같은 원칙)
- 검증: 접수(202) → 결과 폴링. 진행 중(409)이면 기다렸다 다시 접수. 제한 시간을 넘으면 VerifyTimeout
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from a4w_core.errors import ChangeSetNotFound, ScopeUnknown, TargetNotFound
from a4w_core.models import ChangeSet, DocumentChange, OperationChange, ReviewResult
from a4w_tools.call_policy import Clock, SystemClock

from .errors import DetectionUnavailable, VerifyFailedAll, VerifyTimeout


class DetectionClient:
    def __init__(self, *, base_url: str, token: str | None, http: httpx.Client, poll_seconds: float,
                 verify_timeout_seconds: float, clock: Clock | None = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = http
        self._poll = poll_seconds
        self._timeout = verify_timeout_seconds
        self._clock = clock or SystemClock()

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            return self._http.request(method, self._base + path, headers=self._headers, **kw)
        except httpx.TransportError as e:
            raise DetectionUnavailable(f"Detection에 닿지 못했다: {type(e).__name__}") from None

    @staticmethod
    def _error_code(resp: httpx.Response) -> str | None:
        try:
            return resp.json()["error"]["code"]
        except (ValueError, KeyError, TypeError):
            return None

    # ── 변경분
    def changeset(self, target_id: str, change_set_id: str) -> ChangeSet:
        resp = self._request("GET", f"/v1/changesets/{change_set_id}", params={"targetId": target_id})
        if resp.status_code == 404:
            if self._error_code(resp) == "TARGET_NOT_FOUND":
                raise TargetNotFound(f"Detection에 없는 타겟: {target_id}")
            raise ChangeSetNotFound(f"없는 변경분: {change_set_id}")
        if resp.status_code != 200:
            raise DetectionUnavailable(f"Detection 변경분 조회 실패: {resp.status_code}")
        return _changeset(resp.json())

    # ── 타겟 정보
    def profile(self, target_id: str) -> dict[str, Any]:
        resp = self._request("GET", f"/v1/targets/{target_id}/profile")
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 502 and self._error_code(resp) == "SCOPE_UNKNOWN":
            raise ScopeUnknown("Detection이 token scope를 알지 못했다")
        raise ScopeUnknown(f"타겟 정보를 받지 못했다: {resp.status_code}")

    def scopes(self, target_id: str) -> frozenset[str]:
        return frozenset(self.profile(target_id)["tokenScopes"])

    # ── 실호출 검증
    def verify(self, target_id: str, verification_id: str, *, tools: list[Mapping], references: list[Mapping],
               scenarios: Mapping[str, Any]) -> dict[str, Any]:
        """{"results": {tool이름: 결과}, "sandbox": {...}}. 검증 전체 실패면 VerifyFailedAll."""
        body = {"targetId": target_id, "verificationId": verification_id, "tools": list(tools),
                "references": list(references), "scenarios": dict(scenarios)}
        deadline = self._clock.monotonic() + self._timeout
        while True:
            resp = self._request("POST", "/v1/verifications", json=body)
            if resp.status_code == 400 and self._error_code(resp) == "VERIFY_REQUEST_INVALID":
                return _rejected(tools, resp.json()["error"].get("details") or [])
            if resp.status_code == 409 and self._error_code(resp) == "VERIFY_IN_PROGRESS":
                if self._clock.monotonic() >= deadline:
                    raise VerifyTimeout("다른 검증이 끝나지 않았다")
                self._clock.sleep(self._poll)
                continue
            if resp.status_code not in (200, 202):
                raise DetectionUnavailable(f"검증 접수 실패: {resp.status_code} {self._error_code(resp) or ''}")
            record = resp.json()
            break
        while record.get("status") == "running":
            if self._clock.monotonic() >= deadline:
                raise VerifyTimeout(f"검증이 제한 시간 안에 끝나지 않았다: {verification_id}")
            self._clock.sleep(self._poll)
            resp = self._request("GET", f"/v1/verifications/{verification_id}", params={"targetId": target_id})
            if resp.status_code != 200:
                raise DetectionUnavailable(f"검증 조회 실패: {resp.status_code}")
            record = resp.json()
        if record["status"] == "failed":
            raise VerifyFailedAll("검증 전체가 실패했다", details=record.get("error"))
        return {"results": {r["tool"]: r for r in record.get("results", [])}, "sandbox": record.get("sandbox")}


def _rejected(tools: list[Mapping], details: list[Mapping]) -> dict[str, Any]:
    by_tool: dict[str, dict] = {}
    for d in details:
        by_tool.setdefault(d.get("tool"), {"code": d.get("reason"), "message": d.get("message")})
    general = by_tool.get("*")
    results = {}
    for t in tools:
        name = t.get("name")
        error = by_tool.get(name) or general or {"code": "REJECTED_WITH_OTHERS", "message": "같은 요청의 다른 tool 때문에 거절됐다"}
        results[name] = {"tool": name, "result": "failed", "reason": "VERIFY_REJECTED", "error": error}
    return {"results": results, "sandbox": None}


def _changeset(doc: Mapping[str, Any]) -> ChangeSet:
    ops = tuple(
        OperationChange(
            method=o["method"], path=o["path"], operation_id=o.get("operationId"), change=o["change"],
            before=o.get("before"), after=o.get("after"),
            changed_fields=tuple(o["changedFields"]) if o.get("changedFields") is not None else None,
            doc_only=o.get("docOnly"),
        )
        for o in doc["operations"]
    )
    return ChangeSet(
        change_set_id=doc["changeSetId"], target_id=doc["targetId"], from_version=doc.get("fromVersion"),
        to_version=doc["toVersion"], created_at=doc.get("createdAt", ""), operations=ops,
        document_changes=tuple(DocumentChange(d["section"], d["change"]) for d in doc.get("documentChanges", [])),
        review=ReviewResult(doc.get("needsReview", False), tuple(doc.get("reviewReasons", []))),
        text_diff=doc.get("textDiff") or "",
    )
