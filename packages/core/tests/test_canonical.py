from a4w_core.openapi.canonical import canonical_json, strip_doc_keys


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": [1, {"d": 2, "c": 3}]}) == canonical_json({"a": [1, {"c": 3, "d": 2}], "b": 1})


def test_canonical_json_keeps_array_order():
    assert canonical_json([1, 2]) != canonical_json([2, 1])


def test_canonical_json_keeps_unicode():
    assert canonical_json({"설명": "한글"}) == '{"설명":"한글"}'


def test_strip_doc_keys_removes_keywords_at_any_depth():
    node = {"summary": "s", "description": "d", "schema": {"title": "t", "type": "string", "example": "e", "examples": ["x"]}}
    assert strip_doc_keys(node) == {"schema": {"type": "string"}}


def test_strip_doc_keys_keeps_property_named_like_keyword():
    node = {"type": "object", "properties": {"description": {"type": "string", "description": "doc"}, "title": {"type": "string"}}}
    assert strip_doc_keys(node) == {"type": "object", "properties": {"description": {"type": "string"}, "title": {"type": "string"}}}


def test_strip_doc_keys_does_not_mutate():
    node = {"description": "d", "type": "string"}
    strip_doc_keys(node)
    assert node == {"description": "d", "type": "string"}
