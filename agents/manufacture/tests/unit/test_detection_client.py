"""Detection 클라이언트 (Phase 2 설계 16.6절). manufacture가 타겟 쪽으로 나가는 유일한 길이다."""

from __future__ import annotations

import json

import httpx
import pytest

from a4w_core.errors import ChangeSetNotFound, ScopeUnknown
from a4w_manufacture.detection_client import DetectionClient
from a4w_manufacture.errors import DetectionUnavailable, VerifyFailedAll, VerifyTimeout

CS = {
    "changeSetId": "cs-1", "targetId": "wf", "fromVersion": None, "toVersion": "v2", "createdAt": "t",
    "needsReview": True, "reviewReasons": ["OPERATION_CHANGED"],
    "summary": {"added": 1, "removed": 1, "modified": 1, "docOnly": 0, "documentChanges": 0},
    "operations": [
        {"method": "GET", "path": "/a", "operationId": "a.get", "change": "added", "after": {"operationId": "a.get"}},
        {"method": "GET", "path": "/b", "operationId": "b.get", "change": "removed", "before": {"operationId": "b.get"}},
        {"method": "POST", "path": "/c", "operationId": "c.post", "change": "modified", "before": {}, "after": {"x": 1},
         "changedFields": ["parameters"], "docOnly": False},
    ],
    "documentChanges": [{"section": "info", "change": "modified"}],
}


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


def client(handler, token=None, clock=None):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return DetectionClient(base_url="http://detection:18001", token=token, http=http, poll_seconds=5,
                           verify_timeout_seconds=60, clock=clock or Clock())


def test_changeset_is_parsed():
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json=CS)

    cs = client(h, token="tk").changeset("wf", "cs-1")
    assert seen[0].url.path == "/v1/changesets/cs-1" and seen[0].url.params["targetId"] == "wf"
    assert seen[0].headers["Authorization"] == "Bearer tk"
    assert cs.from_version is None and cs.to_version == "v2"
    assert [(o.change, o.operation_id) for o in cs.operations] == [("added", "a.get"), ("removed", "b.get"),
                                                                     ("modified", "c.post")]
    assert cs.operations[2].doc_only is False and cs.review.needs_review


def test_changeset_not_found_and_unavailable():
    with pytest.raises(ChangeSetNotFound):
        client(lambda r: httpx.Response(404, json={"error": {"code": "CHANGESET_NOT_FOUND", "message": "x"}})
               ).changeset("wf", "cs-x")
    with pytest.raises(DetectionUnavailable):
        client(lambda r: httpx.Response(503)).changeset("wf", "cs-x")

    def down(req):
        raise httpx.ConnectError("x")

    with pytest.raises(DetectionUnavailable):
        client(down).changeset("wf", "cs-x")


def test_scopes():
    body = {"targetId": "wf", "tokenScopes": ["read", "write"], "verification": {"available": True, "sandbox": "s",
                                                                               "contextKeys": ["pageId"]}}
    c = client(lambda r: httpx.Response(200, json=body))
    assert c.scopes("wf") == frozenset({"read", "write"})
    assert c.profile("wf")["verification"]["contextKeys"] == ["pageId"]
    with pytest.raises(ScopeUnknown):
        client(lambda r: httpx.Response(502, json={"error": {"code": "SCOPE_UNKNOWN", "message": "x"}})).scopes("wf")


def test_verify_submits_then_polls_until_done():
    calls = []
    states = iter(["running", "running", "done"])

    def h(req):
        calls.append((req.method, req.url.path))
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["verificationId"] == "vf-1" and body["tools"][0]["name"] == "t1"
            return httpx.Response(202, json={"verificationId": "vf-1", "targetId": "wf", "status": "running"})
        st = next(states)
        return httpx.Response(200, json={"verificationId": "vf-1", "targetId": "wf", "status": st,
                                         "results": [{"tool": "t1", "revision": "r", "result": "passed", "calls": 1,
                                                      "elapsedMs": 1}],
                                         "sandbox": {"prepared": True, "cleanup": "ok", "cleanupFailures": []},
                                         "error": None})

    clock = Clock()
    out = client(h, clock=clock).verify("wf", "vf-1", tools=[{"name": "t1"}], references=[], scenarios={})
    assert out["results"]["t1"]["result"] == "passed"
    assert out["sandbox"]["cleanup"] == "ok"
    assert calls.count(("GET", "/v1/verifications/vf-1")) == 3
    assert clock.now == 15


def test_verify_rejected_marks_tools_failed():
    err = {"error": {"code": "VERIFY_REQUEST_INVALID", "message": "x",
                     "details": [{"tool": "t1", "reason": "DENIED_SCOPE", "message": "admin"}]}}
    out = client(lambda r: httpx.Response(400, json=err)).verify("wf", "vf-1", tools=[{"name": "t1"}, {"name": "t2"}],
                                                                 references=[], scenarios={})
    assert out["results"]["t1"] == {"tool": "t1", "result": "failed", "reason": "VERIFY_REJECTED",
                                    "error": {"code": "DENIED_SCOPE", "message": "admin"}}
    assert out["results"]["t2"]["result"] == "failed"


def test_verify_failed_all_and_timeout():
    def failed(req):
        if req.method == "POST":
            return httpx.Response(202, json={"verificationId": "vf-1", "targetId": "wf", "status": "running"})
        return httpx.Response(200, json={"verificationId": "vf-1", "targetId": "wf", "status": "failed", "results": [],
                                         "sandbox": None, "error": {"code": "SANDBOX_UNAVAILABLE", "message": "x"}})

    with pytest.raises(VerifyFailedAll) as e:
        client(failed).verify("wf", "vf-1", tools=[{"name": "t1"}], references=[], scenarios={})
    assert e.value.details["code"] == "SANDBOX_UNAVAILABLE"

    def running(req):
        if req.method == "POST":
            return httpx.Response(202, json={"verificationId": "vf-1", "targetId": "wf", "status": "running"})
        return httpx.Response(200, json={"verificationId": "vf-1", "targetId": "wf", "status": "running", "results": []})

    with pytest.raises(VerifyTimeout):
        client(running).verify("wf", "vf-1", tools=[{"name": "t1"}], references=[], scenarios={})


def test_verify_conflict_waits_and_retries_submit():
    answers = iter([httpx.Response(409, json={"error": {"code": "VERIFY_IN_PROGRESS", "message": "x"}}),
                    httpx.Response(200, json={"verificationId": "vf-1", "targetId": "wf", "status": "done",
                                              "results": [], "sandbox": None, "error": None})])
    out = client(lambda r: next(answers)).verify("wf", "vf-1", tools=[], references=[], scenarios={})
    assert out["results"] == {}
