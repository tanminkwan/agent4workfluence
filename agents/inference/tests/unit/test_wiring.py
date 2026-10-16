"""agent 설정 읽기와 조립 (설계 4절·13절). 네트워크 없이 조립만 확인한다. TDD red."""

from __future__ import annotations

import pytest

from a4w_core.errors import ConfigError
from a4w_inference.config import load_agent_settings, load_write_marks
from a4w_inference.engine.marks import MarkNotConfigured
from tests.unit.test_settings_and_marks import GOOD


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def agents_dir(tmp_path, marks="create_page:body,add_page_label:-", agent="aiagent"):
    d = tmp_path / "agents"
    write(d / agent / "_agent.env", "# agent\nAGENT_TARGETS=workfluence\nAGENT_MAX_CONCURRENT_REQUESTS=3\n")
    body = "AGENT_CONTEXT=homeSpace=f05f841a\nAGENT_TOOL_MAX_RISK=write\nAGENT_TOOL_GROUPS=*\n"
    if marks is not None:
        body += f"AGENT_WRITE_MARKS={marks}\n"
    write(d / agent / "workfluence.env", body)
    return d


def test_load_agent_settings_reads_the_underscore_file(tmp_path):
    a = load_agent_settings(agents_dir(tmp_path), "aiagent")
    assert a.agent_id == "aiagent" and a.targets == ("workfluence",) and a.max_concurrent == 3


def test_missing_agent_file_is_a_config_error_naming_the_path(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_agent_settings(agents_dir(tmp_path), "ghost")
    assert "ghost" in str(e.value)


def test_write_marks_come_from_the_agent_target_files(tmp_path):
    marker = load_write_marks(agents_dir(tmp_path), "aiagent", ("workfluence",), suffix="\n--AI")
    assert marker.apply("workfluence", "create_page", {"body": "본문"}) == {"body": "본문\n--AI"}
    assert marker.apply("workfluence", "add_page_label", {"name": "x"}) == {"name": "x"}
    with pytest.raises(MarkNotConfigured):
        marker.apply("workfluence", "archive_page", {})


def test_absent_write_marks_means_no_write_tool_can_run(tmp_path):
    marker = load_write_marks(agents_dir(tmp_path, marks=None), "aiagent", ("workfluence",), suffix="--AI")
    with pytest.raises(MarkNotConfigured):
        marker.apply("workfluence", "create_page", {"body": "x"})


def test_a_missing_agent_target_file_is_a_config_error(tmp_path):
    d = agents_dir(tmp_path)
    (d / "aiagent" / "workfluence.env").unlink()
    with pytest.raises(ConfigError):
        load_write_marks(d, "aiagent", ("workfluence",), suffix="--AI")


def test_bad_marks_are_reported_with_the_variable_name(tmp_path):
    with pytest.raises(ConfigError) as e:
        load_write_marks(agents_dir(tmp_path, marks="create_page"), "aiagent", ("workfluence",), suffix="--AI")
    assert "AGENT_WRITE_MARKS" in str(e.value)


def full_environ(tmp_path):
    d = agents_dir(tmp_path)
    targets = tmp_path / "targets"
    write(targets / "workfluence.env", "TARGET_ADAPTER=workfluence\n")
    env = dict(GOOD)
    env.update({"INFERENCE_AGENTS_DIR": str(d), "INFERENCE_TARGETS_DIR": str(targets), "INFERENCE_STORE_PATH": str(tmp_path / "store"),
                "LOG_LEVEL": "INFO", "INFERENCE_LLM_PROVIDER": "openai_compatible", "LLM_BASE_URL": "https://llm.test",
                "LLM_API_KEY": "sk-secret", "RAG_PROVIDER": "aipro_plus", "RAG_BASE_URL": "http://rag.test", "RAG_AUTH_MODE": "none",
                "RAG_VIRTUAL_USER": "u", "RAG_TIMEOUT_SECONDS": "5", "RAG_MAX_ATTEMPTS": "2", "RAG_COLLECTION_PREFIX": "a4w_tools_",
                "RAG_DOMAIN_PREFIX": "a4w-tools-", "INFERENCE_MCP_CHILD_ENV_PREFIXES": "MCP_,LOG_LEVEL",
                "INFERENCE_MCP_CHILD_ENV_NAMES": "PATH,HTTP_PROXY,NO_PROXY", "INFERENCE_MCP_RESTART_BACKOFF_SECONDS": "1",
                "INFERENCE_MCP_RESTART_BACKOFF_MAX_SECONDS": "30", "INFERENCE_MCP_START_TIMEOUT_SECONDS": "30",
                "INFERENCE_MCP_TOOLS_WAIT_SECONDS": "10", "INFERENCE_MCP_PING_SECONDS": "5",
                "INFERENCE_SYSTEM_PROMPT": "너는 ${agent_id}다. ${data_open} ${data_close}", "CHAT_ENABLED": "false"})
    return env


def test_build_rest_context_assembles_everything_without_touching_the_network(tmp_path):
    from a4w_inference.wiring import build_rest_context
    ctx = build_rest_context(full_environ(tmp_path))
    assert ctx.auth_mode == "none" and ctx.api_token is None
    assert ctx.health() == {"aiagent": {"mcp": "starting", "tools": 0}}          # 자식은 lifespan에서 뜬다
    assert ctx.lifecycle is not None


def test_build_refuses_to_start_without_a_system_prompt(tmp_path):
    from a4w_inference.wiring import build_rest_context
    env = full_environ(tmp_path)
    del env["INFERENCE_SYSTEM_PROMPT"]
    with pytest.raises(ConfigError):
        build_rest_context(env)


def test_build_refuses_a_prompt_with_an_unknown_placeholder(tmp_path):
    from a4w_inference.wiring import build_rest_context
    env = full_environ(tmp_path)
    env["INFERENCE_SYSTEM_PROMPT"] = "너는 ${agnt_id}다"
    with pytest.raises(ConfigError):
        build_rest_context(env)


def test_build_refuses_secret_names_in_the_mcp_child_allow_list(tmp_path):
    from a4w_inference.wiring import build_rest_context
    env = full_environ(tmp_path)
    env["INFERENCE_MCP_CHILD_ENV_PREFIXES"] = "MCP_,LLM_"
    with pytest.raises(ConfigError):
        build_rest_context(env)


def test_the_service_never_reads_the_agent_token_files(tmp_path):
    """본체는 agent token 파일을 열지 않는다(자식만 읽는다). 비밀 디렉터리가 환경에 있어도 조립이 건드리지 않는다."""
    from a4w_inference.wiring import build_rest_context
    env = full_environ(tmp_path)
    secrets = tmp_path / "secrets" / "aiagent"
    write(secrets / "workfluence.env", "TARGET_API_TOKEN=tok-AGENT-SECRET\n")
    env["MCP_SECRETS_DIR"] = str(tmp_path / "secrets")
    ctx = build_rest_context(env)
    assert "tok-AGENT-SECRET" not in repr(ctx) and "tok-AGENT-SECRET" not in repr(vars(ctx))
