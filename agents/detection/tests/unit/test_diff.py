from a4w_detection.diff import OpenApiDiffer


def spec(paths, **extra):
    return {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": paths, **extra}


def op(op_id, **kw):
    return {"operationId": op_id, "responses": {"200": {"description": "ok"}}, **kw}


def diff(old, new):
    return OpenApiDiffer().diff(old, new)


def test_first_run_marks_everything_added():
    new = spec({"/a": {"get": op("a.get")}, "/b": {"post": op("b.post")}})
    ops, docs = diff(None, new)
    assert [(o.method, o.path, o.change) for o in ops] == [("GET", "/a", "added"), ("POST", "/b", "added")]
    assert ops[0].before is None and ops[0].after["operationId"] == "a.get"
    assert {d.section for d in docs} == {"openapi", "info"}
    assert all(d.change == "added" for d in docs)


def test_identical_specs_have_no_changes():
    s = spec({"/a": {"get": op("a.get")}})
    assert diff(s, s) == ((), ())


def test_added_and_removed():
    old = spec({"/a": {"get": op("a.get")}})
    new = spec({"/b": {"get": op("b.get")}})
    ops, _ = diff(old, new)
    assert [(o.path, o.change) for o in ops] == [("/a", "removed"), ("/b", "added")]
    removed = ops[0]
    assert removed.before["operationId"] == "a.get" and removed.after is None


def test_modified_lists_changed_fields_and_not_doc_only():
    old = spec({"/a": {"get": op("a.get", parameters=[{"name": "q", "in": "query"}])}})
    new = spec({"/a": {"get": op("a.get", parameters=[{"name": "q", "in": "query", "required": True}])}})
    (o,), _ = diff(old, new)
    assert o.change == "modified"
    assert o.changed_fields == ("parameters",)
    assert o.doc_only is False
    assert o.before and o.after


def test_description_only_change_is_doc_only():
    old = spec({"/a": {"get": op("a.get", description="old")}})
    new = spec({"/a": {"get": op("a.get", description="new")}})
    (o,), _ = diff(old, new)
    assert o.change == "modified" and o.doc_only is True and o.changed_fields == ("description",)


def test_nested_schema_description_change_is_doc_only():
    def s(desc):
        return spec({"/a": {"get": {"operationId": "a", "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object", "properties": {"x": {"type": "string", "description": desc}}}}}}}}}})
    (o,), _ = diff(s("old"), s("new"))
    assert o.doc_only is True and o.changed_fields == ("responses",)


def test_shared_schema_change_marks_all_users_modified():
    def s(code_type):
        return spec(
            {
                "/a": {"get": {"operationId": "a", "responses": {"400": {"description": "e", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}}}},
                "/b": {"get": {"operationId": "b", "responses": {"400": {"description": "e", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}}}},
                "/c": {"get": op("c")},
            },
            components={"schemas": {"Error": {"type": "object", "properties": {"code": {"type": code_type}}}}},
        )
    ops, docs = diff(s("string"), s("integer"))
    assert [(o.path, o.change, o.doc_only) for o in ops] == [("/a", "modified", False), ("/b", "modified", False)]
    assert docs == ()


def test_security_change_is_modified():
    old = spec({"/a": {"get": op("a", security=[{"bearerAuth": ["read"]}])}})
    new = spec({"/a": {"get": op("a", security=[{"bearerAuth": ["write"]}])}})
    (o,), _ = diff(old, new)
    assert o.changed_fields == ("security",) and o.doc_only is False


def test_operation_id_change_is_modified():
    (o,), _ = diff(spec({"/a": {"get": op("x")}}), spec({"/a": {"get": op("y")}}))
    assert o.changed_fields == ("operationId",) and o.operation_id == "y"


def test_array_order_change_is_modified():
    old = spec({"/a": {"get": op("a", tags=["x", "y"])}})
    new = spec({"/a": {"get": op("a", tags=["y", "x"])}})
    (o,), _ = diff(old, new)
    assert o.changed_fields == ("tags",)


def test_document_level_changes():
    old = spec({}, servers=[{"url": "/v1"}], tags=[{"name": "a"}], components={"securitySchemes": {"b": {"type": "http"}}})
    new = spec({}, servers=[{"url": "/v2"}], security=[{"b": []}], components={"securitySchemes": {"b": {"type": "apiKey"}}})
    new["info"] = {"title": "t2", "version": "1"}
    _, docs = diff(old, new)
    assert [(d.section, d.change) for d in docs] == [
        ("info", "modified"),
        ("servers", "modified"),
        ("tags", "removed"),
        ("security", "added"),
        ("components.securitySchemes", "modified"),
    ]


def test_order_is_path_then_method():
    new = spec({"/b": {"post": op("b2"), "get": op("b1")}, "/a": {"put": op("a")}})
    ops, _ = diff(None, new)
    assert [(o.path, o.method) for o in ops] == [("/a", "PUT"), ("/b", "GET"), ("/b", "POST")]


def test_diff_is_deterministic():
    old = spec({"/a": {"get": op("a")}, "/b": {"get": op("b")}})
    new = spec({"/b": {"get": op("b", description="x")}, "/c": {"get": op("c")}})
    assert diff(old, new) == diff(old, new)
