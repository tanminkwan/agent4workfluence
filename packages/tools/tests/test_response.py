"""응답 가공: 필드 고르기와 크기 상한."""

from __future__ import annotations

import json

from a4w_tools.response import shape


def test_no_fields_keeps_everything():
    data = {"a": 1, "b": {"c": 2}}
    out = shape(data, fields=None, max_chars=1000)
    assert out.data == data
    assert not out.truncated


def test_select_top_level_and_nested():
    data = {"id": "x", "createdAt": "t", "noise": 1, "author": {"id": "u", "name": "n"}}
    out = shape(data, fields=["id", "author.name"], max_chars=1000)
    assert out.data == {"id": "x", "author": {"name": "n"}}


def test_select_in_array_items():
    data = {"items": [{"id": 1, "title": "a", "x": 0}, {"id": 2, "title": "b", "x": 0}], "count": 2}
    out = shape(data, fields=["items[].id", "items[].title"], max_chars=1000)
    assert out.data == {"items": [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]}


def test_select_on_root_array():
    data = [{"id": 1, "x": 0}, {"id": 2, "x": 0}]
    out = shape(data, fields=["[].id"], max_chars=1000)
    assert out.data == [{"id": 1}, {"id": 2}]


def test_missing_path_is_ignored():
    out = shape({"id": 1}, fields=["id", "nope.deep"], max_chars=1000)
    assert out.data == {"id": 1}


def test_truncate_root_array_from_the_end():
    data = [{"id": i, "t": "x" * 50} for i in range(100)]
    out = shape(data, fields=None, max_chars=500)
    assert out.truncated
    assert out.original_count == 100
    assert len(json.dumps(out.data, ensure_ascii=False)) <= 500
    assert out.data[0]["id"] == 0


def test_truncate_largest_list_in_object():
    data = {"total": 100, "items": [{"id": i, "t": "y" * 40} for i in range(100)]}
    out = shape(data, fields=None, max_chars=600)
    assert out.truncated
    assert out.data["total"] == 100
    assert out.original_count == 100
    assert len(json.dumps(out.data, ensure_ascii=False)) <= 600


def test_truncate_without_list_falls_back_to_text():
    data = {"body": "가" * 1000}
    out = shape(data, fields=None, max_chars=100)
    assert out.truncated
    assert isinstance(out.data, str)
    assert len(out.data) <= 100
