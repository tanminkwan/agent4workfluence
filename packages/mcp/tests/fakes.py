"""a4w_mcp 시험 공용 자료: 작은 tool 묶음, 가짜 Manufacture, 가짜 타겟."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable

import httpx

TARGET = "wf"
SPACE = "home-1"


def _op(name, method, path, *, inputs, required=(), bindings=(), fixed=(), risk, scopes, group, revision,
        fields=None):
    return {
        "schema": "a4w.tool/v1", "kind": "operation", "name": name, "title": f"{name} 제목",
        "description": f"{name} 설명",
        "inputSchema": {"type": "object", "properties": inputs, "required": list(required),
                        "additionalProperties": False},
        "http": {"method": method, "path": path, "bindings": list(bindings), "fixed": list(fixed),
                 "success": [200, 201]},
        "response": {"fields": fields or [], "maxChars": 2000},
        "risk": risk, "scopes": scopes, "group": group,
        "index": {"summary": f"{name} 요약", "examples": ["예시 하나", "예시 둘", "예시 셋"]},
        "source": {"operationId": f"op.{name}", "method": method, "path": path, "specVersion": "v1",
                   "fragmentSha256": "a" * 64, "structureSha256": "b" * 64},
        "origin": "seed", "revision": revision,
    }


def search_pages(revision="r-000000000001"):
    return _op("search_pages", "GET", "/search", inputs={"q": {"type": "string", "description": "검색어"}},
               required=["q"], bindings=[{"input": "q", "in": "query", "name": "q"}],
               fixed=[{"in": "query", "name": "space", "context": "homeSpace"}],
               risk="read", scopes=["read"], group="read", revision=revision,
               fields=["items[].pageId", "items[].title"])


def add_comment(revision="r-000000000002"):
    return _op("add_comment", "POST", "/pages/{pageId}/comments",
               inputs={"pageId": {"type": "string"}, "body": {"type": "string"}}, required=["pageId", "body"],
               bindings=[{"input": "pageId", "in": "path", "name": "pageId"},
                         {"input": "body", "in": "body", "pointer": "/body"}],
               risk="write", scopes=["write"], group="communication", revision=revision, fields=["id"])


def delete_page(revision="r-000000000003"):
    return _op("delete_page", "DELETE", "/pages/{id}", inputs={"id": {"type": "string"}}, required=["id"],
               bindings=[{"input": "id", "in": "path", "name": "id"}],
               risk="danger", scopes=["write"], group="danger", revision=revision)


def comment_by_title(revision="r-000000000004"):
    return {
        "schema": "a4w.tool/v1", "kind": "composite", "name": "comment_on_page_by_title",
        "title": "제목으로 찾아 댓글 달기", "description": "제목으로 페이지를 하나 찾아 댓글을 단다.",
        "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                        "required": ["title", "body"], "additionalProperties": False},
        "steps": [
            {"id": "find", "tool": "search_pages", "args": {"q": {"$input": "title"}},
             "expect": {"pointer": "/items", "count": "one"}},
            {"id": "comment", "tool": "add_comment",
             "args": {"pageId": {"$step": "find", "pointer": "/items/0/pageId"}, "body": {"$input": "body"}}},
        ],
        "output": {"$step": "comment"},
        "risk": "write", "scopes": ["read", "write"], "group": "communication",
        "index": {"summary": "제목으로 찾아 댓글", "examples": ["회의록에 댓글", "공지에 답글", "보고서에 의견"]},
        "origin": "seed", "revision": revision,
    }


def default_tools():
    return [search_pages(), add_comment(), delete_page(), comment_by_title()]


def bundle(tools=None, *, version="tv-000001-aaaaaaaa", target=TARGET):
    tools = copy.deepcopy(tools if tools is not None else default_tools())
    manifest = {
        "schema": "a4w.toolset/v1", "targetId": target, "toolVersion": version, "parent": None,
        "specVersion": "v1", "changeSetId": "cs-1", "createdAt": "2026-10-14T00:00:00Z",
        "tools": [{"name": t["name"], "kind": t["kind"], "file": f"tools/{t['name']}.json",
                   "revision": t["revision"], "risk": t["risk"], "scopes": t["scopes"],
                   "operationId": t.get("source", {}).get("operationId"), "verified": "passed"} for t in tools],
    }
    return {"targetId": target, "toolVersion": version, "manifest": manifest, "tools": {t["name"]: t for t in tools}}


class FakeManufacture:
    """조회 API 3개만 아는 가짜 manufacture (Phase 3 설계 5절)."""

    def __init__(self, *bundles):
        self.bundles = {b["toolVersion"]: b for b in bundles}
        self.current = bundles[-1]["toolVersion"] if bundles else None
        self.requests: list[httpx.Request] = []
        self.fail: int | None = None  # 이 상태 코드로 모두 실패

    def publish(self, b):
        self.bundles[b["toolVersion"]] = b
        self.current = b["toolVersion"]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.fail:
            return httpx.Response(self.fail, json={"error": {"code": "X", "message": "x"}})
        parts = req.url.path.strip("/").split("/")  # v1 targets <id> toolset current | toolsets <v>
        if parts[2] != TARGET:
            return httpx.Response(404, json={"error": {"code": "TARGET_NOT_FOUND", "message": "x"}})
        if parts[3] == "toolset" and parts[-1] == "current":
            if self.current is None:
                return httpx.Response(404, json={"error": {"code": "TOOLSET_NOT_FOUND", "message": "x"}})
            etag = f'"{self.current}"'
            if req.headers.get("if-none-match") == etag:
                return httpx.Response(304, headers={"ETag": etag})
            return httpx.Response(200, headers={"ETag": etag},
                                  json={"targetId": TARGET, "toolVersion": self.current, "updatedAt": "t"})
        if parts[3] == "toolsets" and parts[4] in self.bundles:
            return httpx.Response(200, json=self.bundles[parts[4]])
        return httpx.Response(404, json={"error": {"code": "TOOL_VERSION_NOT_FOUND", "message": "x"}})


class FakeTarget:
    """가짜 타겟: 경로별 응답 함수. 받은 요청을 남긴다."""

    def __init__(self, routes: dict[tuple[str, str], Callable[[httpx.Request], httpx.Response]] | None = None):
        self.routes = routes if routes is not None else default_routes()
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        key = (req.method, req.url.path.removeprefix("/api/v1"))
        handler = self.routes.get(key)
        if handler is None:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "없다", "requestId": "rq-404"}})
        return handler(req)


def default_routes():
    return {
        ("GET", "/search"): lambda req: httpx.Response(
            200, json={"items": [{"pageId": "p1", "title": "회의록", "excerpt": "길다"}]}),
        ("POST", "/pages/p1/comments"): lambda req: httpx.Response(
            201, json={"id": "c1", "body": json.loads(req.content)["body"]}),
    }


TOKEN = "eyJhbGciOiJub25lIn0.eyJzY29wZSI6InJlYWQgd3JpdGUifQ.sig"  # scope "read write" (서명 없음, 시험용)
