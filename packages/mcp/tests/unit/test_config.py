"""설정 읽기 (Phase 3 설계 3절). 기본값은 코드에 없다. 비밀 값은 오류 메시지에 나오지 않는다."""

from __future__ import annotations

import pytest

from a4w_core.config import Settings
from a4w_core.errors import ConfigError
from a4w_mcp.config import McpSettings, load_agent_target, parse_context

ENV = {
    "MCP_AGENT_ID": "aiagent", "MCP_TARGETS": "wf", "MCP_TARGETS_DIR": "/t", "MCP_AGENTS_DIR": "/a",
    "MCP_SECRETS_DIR": "/s", "MCP_MANUFACTURE_URL": "http://m:1", "MCP_MANUFACTURE_TOKEN": "",
    "MCP_MANUFACTURE_TIMEOUT_SECONDS": "10", "MCP_REFRESH_SECONDS": "60", "MCP_REFRESH_RETRY_SECONDS": "10",
    "MCP_TRANSPORT": "stdio", "MCP_TOOL_NAME_SEPARATOR": "__", "MCP_TOOL_NAME_MAX": "64",
    "MCP_MAX_CONCURRENT_CALLS": "4", "MCP_SCOPE_UNKNOWN": "fail",
}


def settings(**over):
    return McpSettings.from_env(Settings({**ENV, **over}, origin="environment"))


def test_reads_all_values():
    s = settings()
    assert s.agent_id == "aiagent" and s.targets == ["wf"] and s.manufacture_token is None
    assert s.refresh_seconds == 60 and s.separator == "__" and s.name_max == 64 and s.scope_unknown == "fail"


@pytest.mark.parametrize("over", [
    {"MCP_TRANSPORT": "streamable_http"},   # 자리만
    {"MCP_TRANSPORT": "sse"},
    {"MCP_SCOPE_UNKNOWN": "maybe"},
    {"MCP_AGENT_ID": "Bad Id"},
    {"MCP_TOOL_NAME_SEPARATOR": "."},        # OpenAI 호환 function 이름에 못 쓴다
    {"MCP_REFRESH_SECONDS": "0"},
    {"MCP_MANUFACTURE_URL": ""},
])
def test_rejects(over):
    with pytest.raises(ConfigError):
        settings(**over)


def test_context_parsing():
    assert parse_context("homeSpace=s1, other=x") == {"homeSpace": "s1", "other": "x"}
    assert parse_context("") == {}
    for bad in ("homeSpace", "=x", "a=1,a=2"):
        with pytest.raises(ConfigError):
            parse_context(bad)


def write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def test_agent_target_files(tmp_path):
    write(tmp_path / "a" / "aiagent" / "wf.env",
          "AGENT_CONTEXT=homeSpace=s1\nAGENT_TOOL_MAX_RISK=write\nAGENT_TOOL_GROUPS=read, communication\n")
    write(tmp_path / "s" / "aiagent" / "wf.env", "TARGET_API_BASE_URL=https://t\nTARGET_API_TOKEN=tok-secret\n")
    at = load_agent_target(tmp_path / "a", tmp_path / "s", "aiagent", "wf")
    assert at.context == {"homeSpace": "s1"} and at.max_risk == "write"
    assert at.groups == frozenset({"read", "communication"})
    assert at.secrets.require("TARGET_API_TOKEN") == "tok-secret"
    assert "tok-secret" not in repr(at)


def test_all_groups_and_errors(tmp_path):
    write(tmp_path / "a" / "aiagent" / "wf.env", "AGENT_CONTEXT=\nAGENT_TOOL_MAX_RISK=danger\nAGENT_TOOL_GROUPS=*\n")
    write(tmp_path / "s" / "aiagent" / "wf.env", "TARGET_API_BASE_URL=https://t\nTARGET_API_TOKEN=tok-secret\n")
    at = load_agent_target(tmp_path / "a", tmp_path / "s", "aiagent", "wf")
    assert at.groups is None and at.context == {}
    with pytest.raises(ConfigError, match="없다"):
        load_agent_target(tmp_path / "a", tmp_path / "s", "aiagent", "other")
    write(tmp_path / "a" / "aiagent" / "wf.env", "AGENT_CONTEXT=\nAGENT_TOOL_MAX_RISK=all\nAGENT_TOOL_GROUPS=*\n")
    with pytest.raises(ConfigError):
        load_agent_target(tmp_path / "a", tmp_path / "s", "aiagent", "wf")


def test_secret_value_never_in_error(tmp_path):
    write(tmp_path / "a" / "aiagent" / "wf.env", "AGENT_CONTEXT=\nAGENT_TOOL_MAX_RISK=read\nAGENT_TOOL_GROUPS=*\n")
    write(tmp_path / "s" / "aiagent" / "wf.env", "TARGET_API_TOKEN=tok-secret\n")  # 주소 없음
    at = load_agent_target(tmp_path / "a", tmp_path / "s", "aiagent", "wf")
    with pytest.raises(ConfigError) as e:
        at.secrets.require("TARGET_API_BASE_URL")
    assert "tok-secret" not in str(e.value)
