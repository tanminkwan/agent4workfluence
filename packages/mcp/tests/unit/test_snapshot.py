"""묶음 검사와 노출 규칙 (Phase 3 설계 6.2·6.3절)."""

from __future__ import annotations

import pytest

from a4w_mcp.snapshot import Exposure, ToolsetRejected, build_snapshot, validate_bundle

from ..fakes import TARGET, add_comment, bundle, comment_by_title, default_tools, search_pages


def exposure(**over):
    base = dict(scopes=frozenset({"read", "write"}), max_risk="danger", groups=None, separator="__", name_max=64,
                context={"homeSpace": "home-1"})
    base.update(over)
    return Exposure(**base)


def snap(b=None, **over):
    b = b or bundle()
    tools = validate_bundle(TARGET, b["toolVersion"], b)
    return build_snapshot({TARGET: (b["toolVersion"], tools)}, {TARGET: exposure(**over)})


def test_all_tools_served_with_namespaced_names():
    s = snap()
    assert set(s.served) == {"wf__search_pages", "wf__add_comment", "wf__delete_page", "wf__comment_on_page_by_title"}
    t = s.served["wf__add_comment"]
    assert t.target_id == TARGET and t.tool_version == "tv-000001-aaaaaaaa" and t.definition["name"] == "add_comment"
    assert s.tool_versions == {TARGET: "tv-000001-aaaaaaaa"}


def test_definitions_are_read_only():
    s = snap()
    with pytest.raises(TypeError):
        s.served["wf__add_comment"].definition["name"] = "x"


@pytest.mark.parametrize("over, hidden, reason", [
    ({"scopes": frozenset({"read"})}, {"wf__add_comment", "wf__delete_page", "wf__comment_on_page_by_title"}, "SCOPE"),
    ({"max_risk": "write"}, {"wf__delete_page"}, "RISK"),
    ({"max_risk": "read"}, {"wf__add_comment", "wf__delete_page", "wf__comment_on_page_by_title"}, "RISK"),
    ({"groups": frozenset({"read"})}, {"wf__add_comment", "wf__delete_page", "wf__comment_on_page_by_title"}, "GROUP"),
    ({"name_max": 16}, {"wf__comment_on_page_by_title"}, "NAME"),
])
def test_exposure_rules(over, hidden, reason):
    s = snap(**over)
    assert set(s.hidden) == hidden and set(s.hidden.values()) == {reason}
    assert not hidden & set(s.served)


def test_scopes_none_means_no_filter():
    assert len(snap(scopes=None).served) == 4


def test_hidden_operation_stays_for_composite_steps():
    s = snap(groups=frozenset({"communication"}))  # search_pages(read 묶음)는 숨김
    assert "wf__search_pages" in s.hidden and "wf__comment_on_page_by_title" in s.served
    assert "search_pages" in s.toolsets[TARGET]


def test_missing_context_is_reported_not_hidden():
    s = snap(context={})
    assert "wf__search_pages" in s.served
    assert s.context_missing == {TARGET: ["homeSpace"]}


@pytest.mark.parametrize("mutate, code", [
    (lambda b: b["tools"]["add_comment"].pop("http"), "TOOLSET_INVALID"),
    (lambda b: b["manifest"].pop("createdAt"), "TOOLSET_INVALID"),
    (lambda b: b.update(toolVersion="tv-000009-bbbbbbbb"), "TOOLSET_MISMATCH"),
    (lambda b: b["manifest"].update(targetId="other"), "TOOLSET_MISMATCH"),
    (lambda b: b["tools"]["add_comment"].update(revision="r-999999999999"), "TOOLSET_MISMATCH"),
    (lambda b: b["tools"].pop("add_comment"), "TOOLSET_MISMATCH"),
    (lambda b: b["tools"].update(extra=search_pages() | {"name": "extra_tool"}), "TOOLSET_MISMATCH"),
])
def test_rejected_bundles(mutate, code):
    b = bundle()
    mutate(b)
    with pytest.raises(ToolsetRejected) as e:
        validate_bundle(TARGET, "tv-000001-aaaaaaaa", b)
    assert e.value.code == code


def test_composite_with_missing_step_is_rejected():
    b = bundle([search_pages(), comment_by_title()])  # add_comment 없음
    with pytest.raises(ToolsetRejected) as e:
        validate_bundle(TARGET, "tv-000001-aaaaaaaa", b)
    assert e.value.code == "TOOLSET_INVALID"


def test_validate_returns_tools_by_name():
    tools = validate_bundle(TARGET, "tv-000001-aaaaaaaa", bundle())
    assert set(tools) == {t["name"] for t in default_tools()}
    assert tools["add_comment"] == add_comment()
