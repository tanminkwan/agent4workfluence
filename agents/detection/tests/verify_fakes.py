"""검증 시험용 자료: tool 정의, 가짜 시험 환경, 가짜 타겟."""

from __future__ import annotations

import copy
import json
import re

import httpx

from a4w_core.errors import SandboxUnavailable
from a4w_tools.call_policy import ConfigCallPolicy
from a4w_tools.executor import ToolExecutor
from a4w_tools.interfaces import GenericErrorInterpreter
from a4w_tools.sandbox import CleanupReport, RawCaller, SandboxContext, VerifySandbox
from a4w_tools.target_http import TargetHttpClient

SHA = "a" * 64


def _base(name, kind, risk, scopes, **extra):
    tool = {
        "schema": "a4w.tool/v1", "kind": kind, "name": name, "title": name, "description": name,
        "risk": risk, "scopes": scopes, "group": "g",
        "index": {"summary": name, "examples": ["가", "나", "다"]}, "origin": "seed", "revision": "r-" + "0" * 12,
    }
    tool.update(extra)
    return tool


def op(name, method, path, *, inputs, required=(), bindings=(), risk=None, scopes=None, fields=None):
    risk = risk or ("read" if method == "GET" else "write")
    return _base(
        name, "operation", risk, scopes or (["read"] if method == "GET" else ["write"]),
        inputSchema={"type": "object", "properties": {k: {"type": t} for k, t in inputs.items()},
                     "required": list(required), "additionalProperties": False},
        http={"method": method, "path": path, "bindings": list(bindings), "success": [200, 201]},
        response={"fields": fields, "maxChars": 4000} if fields else {"maxChars": 4000},
        source={"operationId": name, "method": method, "path": path, "specVersion": "v", "fragmentSha256": SHA,
                "structureSha256": SHA},
    )


def read_page():
    return op("read_page", "GET", "/pages/{id}", inputs={"pageId": "string"}, required=["pageId"],
              bindings=[{"input": "pageId", "in": "path", "name": "id"}], fields=["id", "title"])


def search_pages():
    return op("search_pages", "GET", "/search", inputs={"q": "string"}, required=["q"],
              bindings=[{"input": "q", "in": "query", "name": "q"}], fields=["items[].pageId", "items[].title"])


def add_comment():
    return op("add_comment", "POST", "/pages/{pageId}/comments", inputs={"pageId": "string", "body": "string"},
              required=["pageId", "body"],
              bindings=[{"input": "pageId", "in": "path", "name": "pageId"},
                        {"input": "body", "in": "body", "pointer": "/body"}], fields=["id"])


def list_users():
    return op("list_users", "GET", "/users", inputs={}, scopes=["admin", "read"])


def comment_by_title():
    return _base(
        "comment_on_page_by_title", "composite", "write", ["read", "write"],
        inputSchema={"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                     "required": ["title", "body"], "additionalProperties": False},
        steps=[
            {"id": "find", "tool": "search_pages", "args": {"q": {"$input": "title"}},
             "expect": {"pointer": "/items", "count": "one"}},
            {"id": "comment", "tool": "add_comment",
             "args": {"pageId": {"$step": "find", "pointer": "/items/0/pageId"}, "body": {"$input": "body"}}},
        ],
    )


SPEC_OPS = {("GET", "/pages/{id}"), ("GET", "/search"), ("POST", "/pages/{pageId}/comments"), ("GET", "/users")}

CONTEXT = {"spaceId": "s1", "spaceName": "aiagent-manufacture-vf-1", "pageId": "p1", "pageTitle": "검증 바탕 페이지",
           "currentVersionNo": 1, "labelName": "a4w-verify", "commentId": "c0", "homeSpace": "s1"}


class FakeSandbox(VerifySandbox):
    def __init__(self, fail_prepare=False, fail_cleanup=False, unfilled_keys=()):
        self.fail_prepare = fail_prepare
        self.fail_cleanup = fail_cleanup
        self.unfilled_keys = tuple(unfilled_keys)  # 맥락 키로 알리지만 이번 환경에서 값이 없는 키 (예: peer 없는 notificationId)
        self.prepared: list[str] = []
        self.cleaned = 0

    def context_keys(self):
        return list(CONTEXT) + list(self.unfilled_keys)

    def prepare(self, caller, run_id):
        if self.fail_prepare:
            raise SandboxUnavailable("준비 실패")
        self.prepared.append(run_id)
        return SandboxContext(values=dict(CONTEXT), internal={"spaceId": "s1"})

    def cleanup(self, caller, context):
        self.cleaned += 1
        return CleanupReport(ok=not self.fail_cleanup, failures=("삭제 실패",) if self.fail_cleanup else ())


class FakeTarget:
    """시험 스페이스 안의 자료만 아는 가짜 타겟."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.search_items = [{"pageId": "p1", "title": "검증 바탕 페이지"}]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        method, path = req.method, req.url.path.removeprefix("/api/v1")
        self.calls.append((method, path))
        if method == "GET" and path == "/pages/p1":
            return httpx.Response(200, json={"id": "p1", "title": "검증 바탕 페이지", "body": "x"})
        if method == "GET" and path == "/search":
            return httpx.Response(200, json={"items": self.search_items})
        m = re.fullmatch(r"/pages/(p1)/comments", path)
        if method == "POST" and m:
            return httpx.Response(201, json={"id": "c9", "body": json.loads(req.content)["body"]})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "없음", "requestId": "rq"}})


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


def make_runtime_parts(target=None):
    target = target or FakeTarget()
    client = httpx.Client(transport=httpx.MockTransport(target))
    http = TargetHttpClient(base_url="https://wf.test/api/v1", token="t", client=client)
    policy = ConfigCallPolicy(read_per_minute=100, write_per_minute=100, max_attempts=1, max_retry_wait_seconds=1,
                              clock=Clock())
    executor = ToolExecutor(http=http, policy=policy, interpreter=GenericErrorInterpreter())
    return target, executor, RawCaller(http, policy)


def clone(x):
    return copy.deepcopy(x)
