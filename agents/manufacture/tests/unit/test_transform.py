"""기계 변환: 명세 조각 → tool 골격 (Phase 2 설계 7.1절)."""

from __future__ import annotations

import copy

from a4w_manufacture.transform.openapi3 import OpenApi3Transformer

from ..specs import fragment

T = OpenApi3Transformer(content_types=["application/json", "multipart/form-data"])


def names(sk):
    return [i.name for i in sk.inputs]


def test_comments_create_inputs_and_collision():
    sk = T.skeleton(fragment("comments.create"), fixed={})
    # path → query → body. 본문 format은 질의 format과 겹쳐 body_ 앞머리
    assert names(sk) == ["pageId", "format", "body", "body_format", "parentId"]
    by = {i.name: i for i in sk.inputs}
    assert by["pageId"].location == "path" and by["pageId"].required
    assert by["body"].location == "body" and by["body"].pointer == "/body" and by["body"].required
    assert by["body_format"].pointer == "/format"
    assert not by["parentId"].required
    assert sk.http["method"] == "POST" and sk.http["path"] == "/pages/{pageId}/comments"
    assert sk.http["contentType"] == "application/json"
    assert sk.http["success"] == [201]
    assert sk.scopes == ("write",)


def test_fixed_rule_removes_input():
    sk = T.skeleton(fragment("attachments.download"), fixed={"query": {"format": "json"}})
    assert names(sk) == ["id"]
    assert sk.http["fixed"] == [{"in": "query", "name": "format", "value": "json"}]


def test_json_preferred_over_multipart():
    sk = T.skeleton(fragment("attachments.upload"), fixed={})
    assert sk.http["contentType"] == "application/json"
    assert names(sk) == ["pageId", "content", "encoding", "filename"]


def test_response_paths():
    sk = T.skeleton(fragment("search.find"), fixed={})
    assert "items[].pageId" in sk.response_paths
    assert "items[].title" in sk.response_paths
    labels = T.skeleton(fragment("labels.forPage"), fixed={})
    assert "[].name" in labels.response_paths


def test_input_schema_keeps_constraints_and_description():
    sk = T.skeleton(fragment("search.find"), fixed={})
    q = next(i for i in sk.inputs if i.name == "q")
    assert q.schema["type"] == "string" and q.schema["minLength"] == 1 and q.schema["maxLength"] == 200


def test_deterministic():
    a = T.skeleton(fragment("pages.update"), fixed={})
    b = T.skeleton(copy.deepcopy(fragment("pages.update")), fixed={})
    assert a == b


def test_no_body_operation():
    sk = T.skeleton(fragment("notifications.readAll"), fixed={})
    assert sk.inputs == ()
    assert "contentType" not in sk.http or sk.http["contentType"] == "application/json"


def test_cycle_marker_becomes_object():
    frag = {"operationId": "x.y", "method": "POST", "path": "/x",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "properties": {"node": {"$ref": "#/components/schemas/N", "x-a4w-cycle": True}}}}}},
            "responses": {"200": {"description": "ok"}}}
    sk = T.skeleton(frag, fixed={})
    assert sk.inputs[0].schema == {"type": "object"}


def test_non_object_body_is_single_input():
    frag = {"operationId": "x.y", "method": "PUT", "path": "/x",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "array",
                                                                                          "items": {"type": "string"}}}}},
            "responses": {"204": {"description": "ok"}}}
    sk = T.skeleton(frag, fixed={})
    assert names(sk) == ["body"] and sk.inputs[0].pointer == ""
    assert sk.http["success"] == [204]


def test_context_fixed_value_removes_input_and_keeps_key():
    ctx = {"query": {"space": {"$context": "homeSpace"}}, "body": {"space": {"$context": "homeSpace"}}}
    sk = T.skeleton(fragment("search.find"), fixed=ctx)
    assert names(sk) == ["q", "limit"]
    assert {"in": "query", "name": "space", "context": "homeSpace"} in sk.http["fixed"]
    pc = T.skeleton(fragment("pages.create"), fixed=ctx)
    assert "space" not in names(pc)
    assert {"in": "body", "pointer": "/space", "context": "homeSpace"} in pc.http["fixed"]


# ── 바꿀 수 있는 맥락 (설계 26절): 입력 칸으로 남고, 비우면 실행하는 쪽이 맥락 값을 넣는다

OVER = {"query": {"space": {"$context": "homeSpace", "$overridable": True}},
        "body": {"space": {"$context": "homeSpace", "$overridable": True}}}


def test_overridable_context_keeps_an_optional_input_with_a_context_default_binding():
    pc = T.skeleton(fragment("pages.create"), fixed=OVER)
    space = pc.input("space")
    assert space is not None and space.required is False and space.context_default == "homeSpace"
    assert {"input": "space", "in": "body", "pointer": "/space", "contextDefault": "homeSpace"} in pc.http["bindings"]
    assert not [f for f in pc.http["fixed"] if f.get("pointer") == "/space"]
    sf = T.skeleton(fragment("search.find"), fixed=OVER)
    assert {"input": "space", "in": "query", "name": "space", "contextDefault": "homeSpace"} in sf.http["bindings"]
