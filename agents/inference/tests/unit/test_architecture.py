"""구조 검사 (설계 2절·부록 B.7 G5·G6, AC-I11·I13·I16).

의존 방향(engine·runner는 채널·HTTP·DB 구현을 모른다)과 하드코딩 금지를 소스를 읽어 확인한다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "a4w_inference"
CORE_FILES = sorted((SRC / "engine").rglob("*.py")) + [SRC / "runner.py"]
ALL_FILES = sorted(SRC.rglob("*.py"))
FORBIDDEN_IN_CORE = ("a4w_inference.channels", "a4w_inference.adapters", "a4w_inference.storage", "fastapi", "starlette", "uvicorn",
                     "sqlite3", "httpx", "mcp", "a4w_mcp")


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = node.module if node.level == 0 else ".".join(["a4w_inference", *path.relative_to(SRC).with_suffix("").parts[:-node.level or None]] + [node.module])
            found.add(base)
            found |= {f"{base}.{a.name}" for a in node.names}
    return found


def string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):                                  # docstring은 건너뛴다
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                skip.add(id(first.value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]


def test_the_package_exists():
    assert CORE_FILES and (SRC / "engine" / "engine.py").exists() and (SRC / "runner.py").exists()


@pytest.mark.parametrize("path", CORE_FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_G6_engine_and_runner_do_not_import_channels_http_db_or_mcp(path):
    bad = [m for m in imports(path) if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_IN_CORE)]
    assert bad == [], f"{path.name}: {bad}"


def test_G6_channels_depend_on_the_runner_only_not_on_the_engine_internals():
    for path in (SRC / "channels").rglob("*.py"):
        engine_internals = [m for m in imports(path) if m.startswith("a4w_inference.engine.engine")]
        assert engine_internals == [], f"{path.name}가 AgentEngine을 직접 쓴다: {engine_internals}"


def test_phase5_chat_channel_slot_exists_and_is_empty():
    chat = SRC / "channels" / "chat"
    assert chat.is_dir() and [p.name for p in chat.glob("*.py") if p.name != "__init__.py"] == []


@pytest.mark.parametrize("path", [p for p in CORE_FILES], ids=lambda p: str(p.relative_to(SRC)))
def test_engine_and_runner_know_nothing_about_workfluence(path):
    text = " ".join(string_constants(path)).lower()
    assert "workfluence" not in text and "wf.internal" not in text


HARDCODE = [r"https?://", r"gpt-oss", r"openai/", r"zai-org", r"Bearer\s+\S{6,}", r"sk-[A-Za-z0-9]{8,}", r"eyJ[A-Za-z0-9_-]{10,}"]


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_G5_no_urls_model_names_or_tokens_in_source_strings(path):
    for s in string_constants(path):
        for pat in HARDCODE:
            assert not re.search(pat, s), f"{path.name}: 하드코딩 의심 {pat!r} in {s[:60]!r}"


PROMPT_FRAGMENTS = ["추측하지", "tool 없이", "읽은 자료", "요청자의 지시", "무엇인가요", "시스템이 붙인"]


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: str(p.relative_to(SRC)))
def test_G5_no_system_prompt_wording_in_source(path):
    blob = " ".join(string_constants(path))
    for frag in PROMPT_FRAGMENTS:
        assert frag not in blob, f"{path.name}: 프롬프트 문구가 코드에 있다: {frag}"


def test_the_ai_mark_text_and_marks_are_not_in_source():
    blob = " ".join(s for p in ALL_FILES for s in string_constants(p))
    assert "AI agent가 작성" not in blob and "create_page" not in blob
