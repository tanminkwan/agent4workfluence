import pytest

from a4w_core.errors import SpecInvalid
from a4w_core.openapi.operations import collect_operations


def test_collects_operations_keyed_by_method_and_path():
    doc = {
        "openapi": "3.1.0",
        "paths": {
            "/pages": {
                "get": {"operationId": "pages.tree", "responses": {"200": {"description": "ok"}}},
                "post": {"operationId": "pages.create", "responses": {"201": {"description": "ok"}}},
            }
        },
    }
    ops = collect_operations(doc)
    assert set(ops) == {("GET", "/pages"), ("POST", "/pages")}
    assert ops[("GET", "/pages")]["operationId"] == "pages.tree"
    assert ops[("GET", "/pages")]["method"] == "GET"
    assert ops[("GET", "/pages")]["path"] == "/pages"


def test_non_method_keys_are_not_operations():
    doc = {"openapi": "3.1.0", "paths": {"/x": {"summary": "s", "servers": [], "get": {"responses": {}}}}}
    assert set(collect_operations(doc)) == {("GET", "/x")}


def test_path_level_parameters_are_merged_and_operation_wins():
    doc = {
        "openapi": "3.1.0",
        "paths": {
            "/pages/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "description": "path-level"},
                    {"name": "trace", "in": "header"},
                ],
                "get": {"parameters": [{"name": "id", "in": "path", "description": "op-level"}], "responses": {}},
            }
        },
    }
    params = collect_operations(doc)[("GET", "/pages/{id}")]["parameters"]
    assert {"name": "id", "in": "path", "description": "op-level"} in params
    assert {"name": "trace", "in": "header"} in params
    assert len(params) == 2


def test_effective_security_falls_back_to_top_level():
    doc = {
        "openapi": "3.1.0",
        "security": [{"bearerAuth": ["read"]}],
        "paths": {
            "/a": {"get": {"responses": {}}},
            "/b": {"get": {"security": [], "responses": {}}},
        },
    }
    ops = collect_operations(doc)
    assert ops[("GET", "/a")]["security"] == [{"bearerAuth": ["read"]}]
    assert ops[("GET", "/b")]["security"] == []


def test_refs_inside_operation_are_resolved():
    doc = {
        "openapi": "3.1.0",
        "components": {"schemas": {"E": {"type": "object"}}},
        "paths": {"/a": {"get": {"responses": {"400": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/E"}}}}}}}},
    }
    frag = collect_operations(doc)[("GET", "/a")]
    assert frag["responses"]["400"]["content"]["application/json"]["schema"] == {"type": "object"}


def test_absent_keys_are_omitted():
    doc = {"openapi": "3.1.0", "paths": {"/a": {"get": {"responses": {}}}}}
    assert collect_operations(doc)[("GET", "/a")] == {"method": "GET", "path": "/a", "responses": {}}


def test_paths_must_be_mapping():
    with pytest.raises(SpecInvalid):
        collect_operations({"openapi": "3.1.0", "paths": []})
