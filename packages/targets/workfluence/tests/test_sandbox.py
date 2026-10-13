"""시험 스페이스 준비·정리 (Phase 2 설계 11.6절)."""

from __future__ import annotations

import itertools
import json
import re

import httpx
import pytest

from a4w_core.errors import SandboxUnavailable
from a4w_target_workfluence.sandbox import WorkfluenceSpaceSandbox
from a4w_tools.call_policy import ConfigCallPolicy
from a4w_tools.sandbox import RawCaller, VerifySandbox
from a4w_tools.target_http import TargetHttpClient

PREFIX = "aiagent-manufacture-"


class FakeWorkfluence:
    """필요한 경로만 흉내 낸다. fail에 (METHOD, 경로 정규식)을 넣으면 그 호출은 500."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.spaces: dict[str, dict] = {}
        self.pages: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail: set[tuple[str, str]] = set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        method, path = req.method, req.url.path.removeprefix("/api/v1")
        self.calls.append((method, path))
        for m, pattern in self.fail:
            if m == method and re.fullmatch(pattern, path):
                return httpx.Response(500, json={"error": {"code": "INTERNAL", "message": "x", "requestId": "r"}})
        body = json.loads(req.content) if req.content else {}
        if (method, path) == ("GET", "/spaces"):
            q = req.url.params.get("q", "")
            items = [s for s in self.spaces.values() if q in s["name"]]
            return httpx.Response(200, json={"items": items, "truncated": False})
        if (method, path) == ("POST", "/spaces"):
            sid = f"s{next(self.ids)}"
            self.spaces[sid] = {"id": sid, "name": body["name"], "kind": "team", "status": "active"}
            return httpx.Response(201, json=self.spaces[sid])
        if method == "DELETE" and path.startswith("/spaces/"):
            self.spaces.pop(path.split("/")[2], None)
            return httpx.Response(200, json={"ok": True})
        if (method, path) == ("POST", "/pages"):
            pid = f"p{next(self.ids)}"
            self.pages[pid] = {"id": pid, "spaceId": body["space"], "title": body["title"], "currentVersionNo": 1}
            return httpx.Response(201, json=self.pages[pid])
        m = re.fullmatch(r"/pages/([^/]+)", path)
        if method == "GET" and m and m.group(1) in self.pages:
            return httpx.Response(200, json={**self.pages[m.group(1)], "currentVersionNo": 3})
        m = re.fullmatch(r"/pages/([^/]+)/labels", path)
        if method == "POST" and m:
            return httpx.Response(201, json={"id": f"l{next(self.ids)}", "name": body["name"]})
        m = re.fullmatch(r"/pages/([^/]+)/comments", path)
        if method == "POST" and m:
            return httpx.Response(201, json={"id": f"c{next(self.ids)}", "pageId": m.group(1)})
        m = re.fullmatch(r"/pages/([^/]+)/attachments", path)
        if method == "POST" and m:
            assert body["encoding"] == "utf8" and body["filename"].endswith(".txt")
            return httpx.Response(201, json={"id": f"a{next(self.ids)}", "pageId": m.group(1), "filename": body["filename"]})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "x", "requestId": "r"}})


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


@pytest.fixture
def wf():
    return FakeWorkfluence()


def make_caller(wf):
    client = httpx.Client(transport=httpx.MockTransport(wf))
    http = TargetHttpClient(base_url="https://wf.test/api/v1", token="t", client=client)
    policy = ConfigCallPolicy(read_per_minute=100, write_per_minute=100, max_attempts=1, max_retry_wait_seconds=1, clock=Clock())
    return RawCaller(http, policy)


def test_prepare_creates_space_and_base_data(wf):
    sb = WorkfluenceSpaceSandbox(space_prefix=PREFIX)
    assert isinstance(sb, VerifySandbox)
    ctx = sb.prepare(make_caller(wf), "vf-1")
    v = ctx.values
    assert set(sb.context_keys()) <= set(v)
    assert v["spaceName"] == PREFIX + "vf-1"
    assert v["spaceId"] in wf.spaces
    assert v["pageId"] in wf.pages
    assert v["currentVersionNo"] == 3  # 바탕 자료를 다 만든 뒤 다시 읽은 값
    assert v["labelName"] and v["commentId"] and v["attachmentId"]


def test_prepare_removes_leftover_spaces_first(wf):
    wf.spaces["old"] = {"id": "old", "name": PREFIX + "vf-0", "kind": "team", "status": "active"}
    wf.spaces["mine"] = {"id": "mine", "name": "사람의 스페이스", "kind": "team", "status": "active"}
    WorkfluenceSpaceSandbox(space_prefix=PREFIX).prepare(make_caller(wf), "vf-1")
    assert "old" not in wf.spaces
    assert "mine" in wf.spaces  # 앞머리가 다른 스페이스는 건드리지 않는다


def test_cleanup_deletes_the_space(wf):
    sb = WorkfluenceSpaceSandbox(space_prefix=PREFIX)
    caller = make_caller(wf)
    ctx = sb.prepare(caller, "vf-1")
    report = sb.cleanup(caller, ctx)
    assert report.ok
    assert ctx.values["spaceId"] not in wf.spaces


def test_cleanup_failure_is_reported_not_raised(wf):
    sb = WorkfluenceSpaceSandbox(space_prefix=PREFIX)
    caller = make_caller(wf)
    ctx = sb.prepare(caller, "vf-1")
    wf.fail.add(("DELETE", r"/spaces/.*"))
    report = sb.cleanup(caller, ctx)
    assert not report.ok
    assert report.failures


def test_prepare_failure_cleans_partial_space_and_raises(wf):
    wf.fail.add(("POST", r"/pages"))
    with pytest.raises(SandboxUnavailable):
        WorkfluenceSpaceSandbox(space_prefix=PREFIX).prepare(make_caller(wf), "vf-1")
    assert wf.spaces == {}


def test_prefix_is_required():
    with pytest.raises(ValueError):
        WorkfluenceSpaceSandbox(space_prefix="")


def test_registered_kinds():
    from a4w_core.registry import Registry
    from a4w_target_workfluence import register

    reg = Registry()
    register(reg)
    assert "workfluence_jwt" in reg.names("scope_provider")
    assert "workfluence" in reg.names("error_interpreter")
    assert "workfluence_space" in reg.names("verify_sandbox")


def test_home_space_context_is_the_test_space(wf):
    ctx = WorkfluenceSpaceSandbox(space_prefix=PREFIX).prepare(make_caller(wf), "vf-1")
    assert ctx.values["homeSpace"] == ctx.values["spaceId"]
