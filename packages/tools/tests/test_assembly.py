"""타겟 호출 부품 조립 (Phase 3 설계 8.2절): Detection 검증과 MCP server가 같은 조립을 쓴다."""

from __future__ import annotations

import pytest

from a4w_core.config import Settings, TargetConfig
from a4w_core.errors import ConfigError
from a4w_core.registry import Registry
from a4w_tools.assembly import build_target_caller
from a4w_tools.call_policy import ConfigCallPolicy
from a4w_tools.executor import ToolExecutor
from a4w_tools.interfaces import GenericErrorInterpreter

TARGET = {"TARGET_ADAPTER": "x", "TLS_VERIFY": "true", "CALL_TIMEOUT_SECONDS": "5", "TARGET_API_PATH_PREFIX": "/api/v1",
          "CALL_READ_PER_MINUTE": "10", "CALL_WRITE_PER_MINUTE": "5", "CALL_MAX_ATTEMPTS": "2",
          "CALL_MAX_RETRY_WAIT_SECONDS": "3", "CALL_ERROR_INTERPRETER": "generic"}
SECRETS = {"TARGET_API_BASE_URL": "https://t.example", "TARGET_API_TOKEN": "secret-token"}


def registry():
    r = Registry()
    r.register("error_interpreter", "generic", lambda cfg: GenericErrorInterpreter())
    return r


def test_parts_share_http_and_policy():
    parts = build_target_caller(registry(), TargetConfig("t", TARGET), Settings(SECRETS, origin="s"))
    assert isinstance(parts.executor, ToolExecutor)
    assert isinstance(parts.policy, ConfigCallPolicy)
    assert parts.http.base_url == "https://t.example/api/v1"
    assert "secret-token" not in repr(parts)


def test_unknown_interpreter_is_config_error():
    cfg = TargetConfig("t", {**TARGET, "CALL_ERROR_INTERPRETER": "nope"})
    with pytest.raises(ConfigError):
        build_target_caller(registry(), cfg, Settings(SECRETS, origin="s"))


def test_transport_reaches_target_with_token():
    import httpx

    seen = []
    parts = build_target_caller(registry(), TargetConfig("t", TARGET), Settings(SECRETS, origin="s"),
                                transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200, json={})))
    tool = {"schema": "a4w.tool/v1", "kind": "operation", "name": "ping_tool",
            "inputSchema": {"type": "object", "properties": {}}, "http": {"method": "GET", "path": "/ping"}}
    assert parts.executor.execute(tool, {}).ok
    assert str(seen[0].url) == "https://t.example/api/v1/ping"
    assert seen[0].headers["authorization"] == "Bearer secret-token"
