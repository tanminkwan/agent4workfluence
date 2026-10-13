"""a4w_tools 시험 공용 자료."""

from __future__ import annotations

import copy

import pytest


def make_operation_tool(**over):
    tool = {
        "schema": "a4w.tool/v1",
        "kind": "operation",
        "name": "add_comment",
        "title": "페이지에 댓글 달기",
        "description": "페이지에 댓글을 단다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pageId": {"type": "string", "description": "페이지 id"},
                "body": {"type": "string", "description": "본문"},
                "parentId": {"type": ["string", "null"], "description": "상위 댓글 id"},
            },
            "required": ["pageId", "body"],
            "additionalProperties": False,
        },
        "http": {
            "method": "POST",
            "path": "/pages/{pageId}/comments",
            "contentType": "application/json",
            "bindings": [
                {"input": "pageId", "in": "path", "name": "pageId"},
                {"input": "body", "in": "body", "pointer": "/body"},
                {"input": "parentId", "in": "body", "pointer": "/parentId"},
            ],
            "fixed": [{"in": "body", "pointer": "/format", "value": "markdown"}],
            "success": [200, 201],
        },
        "response": {"fields": ["id", "createdAt"], "maxChars": 2000},
        "risk": "write",
        "scopes": ["write"],
        "group": "communication",
        "index": {"summary": "댓글을 남긴다", "examples": ["댓글 달아줘", "답글 남겨줘", "의견 적어줘"]},
        "source": {
            "operationId": "comments.create",
            "method": "POST",
            "path": "/pages/{pageId}/comments",
            "specVersion": "v1",
            "fragmentSha256": "a" * 64,
            "structureSha256": "b" * 64,
        },
        "origin": "seed",
        "revision": "r-000000000000",
    }
    tool = copy.deepcopy(tool)
    tool.update(over)
    return tool


def make_search_tool():
    return make_operation_tool(
        name="search_pages",
        title="페이지 검색",
        description="제목·본문으로 페이지를 찾는다.",
        inputSchema={
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "검색어"},
                "spaceKey": {"type": "string", "description": "스페이스"},
                "limit": {"type": "integer", "description": "최대 개수"},
            },
            "required": ["q"],
            "additionalProperties": False,
        },
        http={
            "method": "GET",
            "path": "/search",
            "bindings": [
                {"input": "q", "in": "query", "name": "q"},
                {"input": "spaceKey", "in": "query", "name": "space"},
                {"input": "limit", "in": "query", "name": "limit"},
            ],
            "fixed": [{"in": "query", "name": "format", "value": "json"}],
            "success": [200],
        },
        response={"fields": ["items[].id", "items[].title"], "maxChars": 4000},
        risk="read",
        scopes=["read"],
        group="read",
        source={
            "operationId": "search.find",
            "method": "GET",
            "path": "/search",
            "specVersion": "v1",
            "fragmentSha256": "c" * 64,
            "structureSha256": "d" * 64,
        },
    )


def make_composite_tool():
    return {
        "schema": "a4w.tool/v1",
        "kind": "composite",
        "name": "comment_on_page_by_title",
        "title": "제목으로 찾아 댓글 달기",
        "description": "제목으로 페이지를 하나 찾아 댓글을 단다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "spaceKey": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["title", "body"],
            "additionalProperties": False,
        },
        "steps": [
            {
                "id": "find",
                "tool": "search_pages",
                "args": {"q": {"$input": "title"}, "spaceKey": {"$input": "spaceKey"}},
                "expect": {"pointer": "/items", "count": "one"},
            },
            {
                "id": "comment",
                "tool": "add_comment",
                "args": {"pageId": {"$step": "find", "pointer": "/items/0/id"}, "body": {"$input": "body"}},
            },
        ],
        "output": {"$step": "comment"},
        "risk": "write",
        "scopes": ["read", "write"],
        "group": "communication",
        "index": {"summary": "제목으로 찾아 댓글", "examples": ["회의록에 댓글 달아줘", "공지에 답글", "보고서에 의견"]},
        "origin": "seed",
        "revision": "r-111111111111",
    }


@pytest.fixture
def op_tool():
    return make_operation_tool()


@pytest.fixture
def search_tool():
    return make_search_tool()


@pytest.fixture
def composite_tool():
    return make_composite_tool()
