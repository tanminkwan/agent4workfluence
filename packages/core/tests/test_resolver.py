import pytest

from a4w_core.errors import SpecInvalid
from a4w_core.openapi.resolver import resolve_refs


def doc_with(schemas):
    return {"openapi": "3.1.0", "paths": {}, "components": {"schemas": schemas}}


def test_internal_ref_is_inlined():
    doc = doc_with({"Error": {"type": "object", "properties": {"code": {"type": "string"}}}})
    node = {"schema": {"$ref": "#/components/schemas/Error"}}
    assert resolve_refs(node, doc) == {
        "schema": {"type": "object", "properties": {"code": {"type": "string"}}}
    }


def test_nested_refs_are_inlined():
    doc = doc_with({
        "A": {"type": "object", "properties": {"b": {"$ref": "#/components/schemas/B"}}},
        "B": {"type": "string"},
    })
    assert resolve_refs({"$ref": "#/components/schemas/A"}, doc) == {
        "type": "object", "properties": {"b": {"type": "string"}}
    }


def test_cycle_is_cut_and_marked():
    doc = doc_with({"Node": {"type": "object", "properties": {"next": {"$ref": "#/components/schemas/Node"}}}})
    out = resolve_refs({"$ref": "#/components/schemas/Node"}, doc)
    assert out["properties"]["next"] == {"$ref": "#/components/schemas/Node", "x-a4w-cycle": True}


def test_same_ref_twice_in_siblings_is_not_a_cycle():
    doc = doc_with({"S": {"type": "string"}})
    out = resolve_refs({"a": {"$ref": "#/components/schemas/S"}, "b": {"$ref": "#/components/schemas/S"}}, doc)
    assert out == {"a": {"type": "string"}, "b": {"type": "string"}}


def test_external_ref_is_invalid():
    with pytest.raises(SpecInvalid):
        resolve_refs({"$ref": "other.yaml#/X"}, doc_with({}))


def test_missing_ref_target_is_invalid():
    with pytest.raises(SpecInvalid):
        resolve_refs({"$ref": "#/components/schemas/Nope"}, doc_with({}))


def test_json_pointer_escapes():
    doc = {"openapi": "3.1.0", "paths": {}, "components": {"schemas": {"a/b~c": {"type": "integer"}}}}
    assert resolve_refs({"$ref": "#/components/schemas/a~1b~0c"}, doc) == {"type": "integer"}


def test_input_is_not_mutated():
    doc = doc_with({"S": {"type": "string"}})
    node = {"x": {"$ref": "#/components/schemas/S"}}
    resolve_refs(node, doc)
    assert node == {"x": {"$ref": "#/components/schemas/S"}}
