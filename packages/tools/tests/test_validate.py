"""tool 정의·manifest 스키마 검사와 참조 무결성."""

from __future__ import annotations

from a4w_tools.validate import check_references, validate_tool, validate_toolset


def test_valid_operation_tool_has_no_errors(op_tool):
    assert validate_tool(op_tool) == []


def test_valid_composite_tool_has_no_errors(composite_tool):
    assert validate_tool(composite_tool) == []


def test_name_format_is_enforced(op_tool):
    op_tool["name"] = "Add-Comment"
    assert validate_tool(op_tool)


def test_unknown_top_level_field_is_rejected(op_tool):
    op_tool["extra"] = 1
    assert validate_tool(op_tool)


def test_binding_location_must_be_known(op_tool):
    op_tool["http"]["bindings"][0]["in"] = "cookie"
    assert validate_tool(op_tool)


def test_operation_requires_http(op_tool):
    del op_tool["http"]
    assert validate_tool(op_tool)


def test_composite_requires_steps(composite_tool):
    del composite_tool["steps"]
    assert validate_tool(composite_tool)


def test_handler_kind_needs_handler_name(op_tool):
    tool = {k: v for k, v in op_tool.items() if k not in ("http", "response", "source")}
    tool["kind"] = "handler"
    assert validate_tool(tool)
    tool["handler"] = "custom_thing"
    assert validate_tool(tool) == []


def test_risk_value_is_enumerated(op_tool):
    op_tool["risk"] = "maybe"
    assert validate_tool(op_tool)


def test_references_ok(op_tool, search_tool, composite_tool):
    tools = {t["name"]: t for t in (op_tool, search_tool, composite_tool)}
    assert check_references(tools) == []


def test_reference_to_missing_tool(composite_tool, search_tool):
    tools = {t["name"]: t for t in (search_tool, composite_tool)}
    errors = check_references(tools)
    assert any("add_comment" in e for e in errors)


def test_reference_to_composite_is_rejected(op_tool, search_tool, composite_tool):
    other = dict(composite_tool, name="nested")
    other["steps"] = [{"id": "x", "tool": "comment_on_page_by_title", "args": {}}]
    tools = {t["name"]: t for t in (op_tool, search_tool, composite_tool, other)}
    errors = check_references(tools)
    assert any("nested" in e for e in errors)


def test_step_reference_must_point_backwards(op_tool, search_tool, composite_tool):
    composite_tool["steps"][0]["args"]["q"] = {"$step": "comment", "pointer": "/id"}
    tools = {t["name"]: t for t in (op_tool, search_tool, composite_tool)}
    assert check_references(tools)


def test_step_input_must_exist(op_tool, search_tool, composite_tool):
    composite_tool["steps"][1]["args"]["body"] = {"$input": "nope"}
    tools = {t["name"]: t for t in (op_tool, search_tool, composite_tool)}
    assert check_references(tools)


def test_step_arg_must_be_input_of_target_tool(op_tool, search_tool, composite_tool):
    composite_tool["steps"][1]["args"]["unknownArg"] = {"$const": 1}
    tools = {t["name"]: t for t in (op_tool, search_tool, composite_tool)}
    assert check_references(tools)


def test_step_must_bind_required_inputs(op_tool, search_tool, composite_tool):
    del composite_tool["steps"][1]["args"]["body"]
    tools = {t["name"]: t for t in (op_tool, search_tool, composite_tool)}
    assert check_references(tools)


def test_toolset_manifest():
    manifest = {
        "schema": "a4w.toolset/v1",
        "targetId": "workfluence",
        "toolVersion": "tv-000001-1a2b3c4d",
        "parent": None,
        "specVersion": "s" * 64,
        "changeSetId": "cs-abc",
        "createdAt": "2026-10-13T06:00:00Z",
        "tools": [
            {
                "name": "add_comment",
                "kind": "operation",
                "file": "tools/add_comment.json",
                "revision": "r-000000000000",
                "risk": "write",
                "scopes": ["write"],
                "operationId": "comments.create",
                "verified": "passed",
            }
        ],
    }
    assert validate_toolset(manifest) == []
    manifest["toolVersion"] = "v1"
    assert validate_toolset(manifest)


def test_fixed_entry_may_name_a_context_key(op_tool):
    op_tool["http"]["fixed"].append({"in": "query", "name": "space", "context": "homeSpace"})
    assert validate_tool(op_tool) == []


def test_fixed_entry_needs_value_or_context_not_both(op_tool):
    op_tool["http"]["fixed"].append({"in": "query", "name": "space"})
    assert validate_tool(op_tool)
    op_tool["http"]["fixed"][-1] = {"in": "query", "name": "space", "value": 1, "context": "homeSpace"}
    assert validate_tool(op_tool)


def test_omitted_inputs_are_listed(op_tool):
    op_tool["http"]["omitted"] = ["parentId"]
    assert validate_tool(op_tool) == []
