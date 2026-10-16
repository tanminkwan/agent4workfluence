"""대상 선정 (Phase 2 설계 5절)."""

from __future__ import annotations

from collections import Counter

import pytest

from a4w_manufacture.selection import Selector

from ..specs import fragment, fragments, rules

ALL_GROUPS = ["read", "history", "communication", "authoring", "danger"]
DEFAULT_GROUPS = ["read", "history", "communication", "authoring"]
ALL_SCOPES = frozenset({"read", "write", "admin"})


def select_all(groups=ALL_GROUPS, scopes=ALL_SCOPES):
    sel = Selector(rules(), enabled_groups=groups, token_scopes=scopes)
    return {op: sel.select(f) for op, f in fragments().items()}


def test_workfluence_counts_match_design_table():
    out = select_all()
    tools = Counter(s.group for s in out.values() if s.kind == "tool")
    excluded = Counter(s.reason for s in out.values() if s.kind == "excluded")
    assert tools == {"read": 13, "history": 3, "communication": 8, "authoring": 9, "danger": 9}   # 스페이스 8개 포함 (설계 23절)
    # 채팅 9개는 tool 대상이 아니다(채팅 API는 Detection·Manufacture 대상이 아니고 어댑터 코드가 부른다). 변경을 따로 알리지도 않는다
    assert excluded == {"ADMIN": 12, "STRUCTURE_CHANGE": 6, "NOT_NEEDED": 12, "SPEC_META": 3}
    assert not [s for s in out.values() if s.kind == "codeOwned"]


def test_danger_group_disabled_by_default():
    out = select_all(groups=DEFAULT_GROUPS)
    disabled = sorted(op for op, s in out.items() if s.reason == "GROUP_DISABLED")
    assert disabled == sorted(["pages.delete", "pages.move", "pages.restoreVersion", "attachments.upload",
                               "attachments.delete", "trash.pages", "trash.restorePage", "spaces.delete",
                               "spaces.setStatus"])
    assert sum(1 for s in out.values() if s.kind == "tool") == 33


def test_missing_scope_excludes_write_tools():
    out = select_all(groups=DEFAULT_GROUPS, scopes=frozenset({"read"}))
    assert out["comments.create"].reason == "SCOPE_MISSING"
    assert out["comments.list"].kind == "tool"


def test_write_scope_implies_read():
    out = select_all(groups=DEFAULT_GROUPS, scopes=frozenset({"write"}))
    assert out["pages.get"].kind == "tool"


@pytest.mark.parametrize("op,risk", [
    ("pages.get", "read"), ("pages.versions", "read"), ("comments.list", "read"), ("comments.create", "write"),
    ("pages.update", "write"), ("pages.delete", "danger"), ("trash.pages", "danger"),
    ("spaces.create", "write"), ("spaces.update", "write"), ("spaces.members", "read"), ("spaces.addMember", "write"),
    ("spaces.removeMember", "write"), ("spaces.setMemberRole", "write"), ("spaces.delete", "danger"),
    ("spaces.setStatus", "danger"),
])
def test_risk(op, risk):
    assert select_all()[op].risk == risk


def test_scopes_come_from_security():
    s = select_all()["comments.create"]
    assert s.scopes == ("write",)


def new_op(method, path="/new/thing", op_id="new.thing"):
    return {"operationId": op_id, "method": method, "path": path, "security": [{"bearerAuth": ["read"]}]}


@pytest.mark.parametrize("method,group,fallback", [
    ("GET", "read", False),       # 마지막 규칙(method: GET)에 걸린다
    ("POST", "authoring", True),
    ("DELETE", "danger", True),
])
def test_unknown_operation_uses_rules_then_fallback(method, group, fallback):
    sel = Selector(rules(), enabled_groups=ALL_GROUPS, token_scopes=ALL_SCOPES)
    s = sel.select(new_op(method))
    assert (s.kind, s.group, s.fallback) == ("tool", group, fallback)


def test_chat_operations_are_never_tools_whatever_the_scope_or_groups():
    for scopes in (frozenset(), ALL_SCOPES):
        s = Selector(rules(), enabled_groups=ALL_GROUPS, token_scopes=scopes).select(fragment("chat.send"))
        assert (s.kind, s.reason) == ("excluded", "NOT_NEEDED")


def test_transform_fixed_values():
    sel = Selector(rules(), enabled_groups=ALL_GROUPS, token_scopes=ALL_SCOPES)
    assert sel.fixed_values(fragment("attachments.download"))["query"]["format"] == "json"
    assert "format" not in sel.fixed_values(fragment("pages.get")).get("query", {})


def test_unknown_group_in_settings_fails():
    with pytest.raises(ValueError):
        Selector(rules(), enabled_groups=["nope"], token_scopes=ALL_SCOPES)


def test_context_binds_become_fixed_context_values():
    """agent가 늘 쓰는 스페이스는 맥락 키다 (Phase 2 설계 6.3.1). Workfluence 카탈로그는 바꿀 수 있게 둔다 (설계 26절)."""
    sel = Selector(rules(), enabled_groups=ALL_GROUPS, token_scopes=ALL_SCOPES)
    ctx = {"$context": "homeSpace", "$overridable": True}
    assert sel.fixed_values(fragment("search.find")) == {"query": {"space": ctx}, "body": {"space": ctx}}
    assert sel.fixed_values(fragment("attachments.download"))["query"]["format"] == "json"


def test_space_operations_are_tools_but_category_and_template_changes_stay_excluded():
    """2026-10-16 정정(설계 23절, FR-217): 스페이스 동작 8개는 tool, 분류·템플릿 변경 6개는 계속 STRUCTURE_CHANGE."""
    out = select_all()
    spaces = {op: s for op, s in out.items() if op.startswith("spaces.")}
    assert all(s.kind == "tool" for s in spaces.values()), {op: s.reason for op, s in spaces.items() if s.kind != "tool"}
    structure = sorted(op for op, s in out.items() if s.reason == "STRUCTURE_CHANGE")
    assert structure == sorted(["categories.create", "categories.rename", "categories.delete",
                                "templates.create", "templates.update", "templates.delete"])


def test_context_marked_overridable_is_passed_as_an_overridable_value():
    r = rules()
    r["context"] = {"homeSpace": {"overridable": True, "bind": [{"in": "query", "name": "space"}]},
                    "fixedOne": {"bind": [{"in": "query", "name": "lang"}]}}
    out = Selector(r, enabled_groups=ALL_GROUPS, token_scopes=ALL_SCOPES).fixed_values(fragment("search.find"))
    assert out["query"]["space"] == {"$context": "homeSpace", "$overridable": True}
    assert out["query"]["lang"] == {"$context": "fixedOne"}
