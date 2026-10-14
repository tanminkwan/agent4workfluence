"""단절 (AC-T11): a4w_tools는 MCP를 모르고, a4w_mcp에는 타겟 고유 값이 없다."""

from __future__ import annotations

import ast
from pathlib import Path

import a4w_mcp
import a4w_tools

FORBIDDEN_IN_MCP = ("workfluence", "wf.internal", "/api/v1", "homeSpace")


def sources(package) -> list[Path]:
    return sorted(Path(package.__file__).parent.rglob("*.py"))


def imported(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_tools_does_not_import_mcp():
    for path in sources(a4w_tools):
        assert not imported(path) & {"mcp", "mcp_types", "a4w_mcp"}, path


def test_mcp_has_no_target_specific_values():
    for path in sources(a4w_mcp):
        text = path.read_text(encoding="utf-8")
        for word in FORBIDDEN_IN_MCP:
            assert word not in text, f"{path.name}: {word}"
