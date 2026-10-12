"""실제 Workfluence 명세 두 벌로 본 변경 (요구사항 AC-3)."""

from pathlib import Path

import pytest

from a4w_core.openapi.operations import collect_operations
from a4w_core.spec_parsers import YamlSpecParser
from a4w_detection.diff import OpenApiDiffer
from a4w_target_workfluence.review_policy import WorkfluenceV1AdditivePolicy

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "workfluence"
CHAT_OPS = {
    ("GET", "/chat/rooms"), ("POST", "/chat/rooms"), ("GET", "/chat/rooms/{id}"),
    ("POST", "/chat/rooms/{id}/members"), ("DELETE", "/chat/rooms/{id}/members/me"),
    ("GET", "/chat/rooms/{id}/messages"), ("POST", "/chat/rooms/{id}/messages"),
    ("PUT", "/chat/rooms/{id}/read"), ("GET", "/chat/users"),
}


@pytest.fixture(scope="module")
def docs():
    p = YamlSpecParser()
    return p.parse((FIX / "openapi-66ops.yaml").read_bytes()), p.parse((FIX / "openapi-75ops.yaml").read_bytes())


def test_fixture_sizes(docs):
    old, new = docs
    assert len(collect_operations(old)) == 66 and len(collect_operations(new)) == 75


def test_chat_operations_are_added_and_nothing_else_changed(docs):
    old, new = docs
    ops, documents = OpenApiDiffer().diff(old, new)
    assert {(o.method, o.path) for o in ops if o.change == "added"} == CHAT_OPS
    assert [o for o in ops if o.change != "added"] == []
    # 독립 확인: 동작 키 집합의 차이와 같다
    assert set(collect_operations(new)) - set(collect_operations(old)) == CHAT_OPS
    assert [(d.section, d.change) for d in documents] == [("info", "modified"), ("tags", "modified")]


def test_additive_change_needs_no_review(docs):
    old, new = docs
    ops, documents = OpenApiDiffer().diff(old, new)
    assert WorkfluenceV1AdditivePolicy().evaluate(ops, documents).needs_review is False


def test_added_fragments_have_no_refs(docs):
    old, new = docs
    ops, _ = OpenApiDiffer().diff(old, new)
    for o in ops:
        assert "'$ref'" not in repr(o.after)
        assert o.after["operationId"].startswith("chat.")


def test_first_run_on_real_spec(docs):
    _, new = docs
    ops, _ = OpenApiDiffer().diff(None, new)
    assert len(ops) == 75 and all(o.change == "added" for o in ops)
