"""조립 지점 (Phase 2 설계 3절). 기본값을 코드에 두지 않는다."""

from __future__ import annotations

from pathlib import Path

import pytest

from a4w_core.errors import ConfigError
from a4w_manufacture.wiring import build_context

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
TARGET = """TARGET_ADAPTER=workfluence
TOOL_CATALOG=workfluence_v1
TOOL_GROUPS_ENABLED=read,history,communication,authoring
TOOL_REQUEST_CONTENT_TYPES=application/json,multipart/form-data
RESPONSE_MAX_CHARS=8000
VERIFY_NO_SCENARIO=pending
"""


@pytest.fixture
def env(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "workfluence.env").write_text(TARGET)
    return {
        "LOG_LEVEL": "INFO", "TARGETS": "workfluence", "MANUFACTURE_AUTH_MODE": "none",
        "MANUFACTURE_TOOL_STORE_PATH": str(tmp_path / "store"), "MANUFACTURE_TARGETS_DIR": str(tmp_path / "targets"),
        "MANUFACTURE_PROMPTS_DIR": str(PROMPTS),
        "DETECTION_BASE_URL": "http://detection:18001", "DETECTION_TIMEOUT_SECONDS": "30",
        "DETECTION_VERIFY_POLL_SECONDS": "5", "DETECTION_VERIFY_TIMEOUT_SECONDS": "1800",
        "LLM_PROVIDER": "openai_compatible", "LLM_BASE_URL": "https://llm.test", "LLM_API_KEY": "k",
        "LLM_MODEL": "m", "LLM_RESPONSE_FORMAT": "json_object", "LLM_TEMPERATURE": "0.2",
        "LLM_MAX_OUTPUT_TOKENS": "32000", "LLM_TIMEOUT_SECONDS": "300", "LLM_MAX_RETRIES": "2",
        "LLM_MAX_CALLS_PER_APPLY": "60", "LLM_EXAMPLE_COUNT": "3",
        "PROMPT_VERSIONS": "common=v1,tool_create=v1,tool_update=v1,tool_redescribe=v1",
        "TOOL_TEXT_LIMITS": "description=600,summary=120,example=60", "TOOL_MAX_VISIBLE_INPUTS": "4",
        "RAG_PROVIDER": "aipro_plus", "RAG_BASE_URL": "http://aipro:28000", "RAG_AUTH_MODE": "none",
        "RAG_VIRTUAL_USER": "a4w", "RAG_TIMEOUT_SECONDS": "30", "RAG_MAX_ATTEMPTS": "3",
        "RAG_COLLECTION_PREFIX": "a4w_tools_", "RAG_DOMAIN_PREFIX": "a4w-tools-", "RAG_CONTENT_MAX_CHARS": "600",
        "RAG_LIST_LIMIT": "1000",
    }


def test_builds(env):
    ctx = build_context(env)
    rt = ctx.service.targets["workfluence"]
    assert rt.rag_space.collection == "a4w_tools_workfluence"
    assert rt.enabled_groups == ("read", "history", "communication", "authoring")
    assert "groups" in rt.catalog.rules()
    ctx.db.check()


@pytest.mark.parametrize("key", ["LLM_API_KEY", "LLM_MODEL", "DETECTION_BASE_URL", "RAG_BASE_URL", "PROMPT_VERSIONS",
                                 "TOOL_MAX_VISIBLE_INPUTS", "MANUFACTURE_TOOL_STORE_PATH", "LLM_MAX_CALLS_PER_APPLY"])
def test_missing_setting_fails_fast(env, key):
    del env[key]
    with pytest.raises(ConfigError):
        build_context(env)


def test_placeholder_llm_provider_fails(env):
    env["LLM_PROVIDER"] = "anthropic"
    with pytest.raises(ConfigError):
        build_context(env)


def test_bad_no_scenario_policy(env, tmp_path):
    (tmp_path / "targets" / "workfluence.env").write_text(TARGET.replace("VERIFY_NO_SCENARIO=pending",
                                                                         "VERIFY_NO_SCENARIO=maybe"))
    with pytest.raises(ConfigError):
        build_context(env)


def test_bearer_requires_token(env):
    env["MANUFACTURE_AUTH_MODE"] = "bearer"
    with pytest.raises(ConfigError):
        build_context(env)
