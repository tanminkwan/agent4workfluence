"""조립 (Phase 3 설계 8.2절): 설정 파일·비밀 → 부품. 타겟 어댑터는 registry 이름으로 고른다."""

from __future__ import annotations

import logging

import httpx
import pytest

from a4w_core.errors import ConfigError, ScopeUnknown
from a4w_core.registry import Registry
from a4w_mcp.wiring import build
from a4w_tools.interfaces import GenericErrorInterpreter

from ..fakes import TOKEN, FakeManufacture, FakeTarget, bundle

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


TARGET_ENV = """TARGET_ADAPTER=fake
TLS_VERIFY=true
CALL_TIMEOUT_SECONDS=5
TARGET_API_PATH_PREFIX=/api/v1
CALL_READ_PER_MINUTE=100
CALL_WRITE_PER_MINUTE=100
CALL_MAX_ATTEMPTS=1
CALL_MAX_RETRY_WAIT_SECONDS=1
CALL_ERROR_INTERPRETER=generic
TARGET_SCOPE_PROVIDER=jwtish
"""


class JwtishScopes:
    def scopes(self, token):
        if token != TOKEN:
            raise ScopeUnknown("모름")
        return frozenset({"read", "write"})


def registry():
    r = Registry()
    r.register("error_interpreter", "generic", lambda cfg: GenericErrorInterpreter())
    r.register("scope_provider", "jwtish", lambda cfg: JwtishScopes())
    return r


def files(tmp_path, token=TOKEN, context="homeSpace=home-1"):
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "wf.env").write_text(TARGET_ENV, encoding="utf-8")
    (tmp_path / "a" / "aiagent").mkdir(parents=True)
    (tmp_path / "a" / "aiagent" / "wf.env").write_text(
        f"AGENT_CONTEXT={context}\nAGENT_TOOL_MAX_RISK=write\nAGENT_TOOL_GROUPS=*\n", encoding="utf-8")
    (tmp_path / "s" / "aiagent").mkdir(parents=True)
    (tmp_path / "s" / "aiagent" / "wf.env").write_text(
        f"TARGET_API_BASE_URL=https://wf.test\nTARGET_API_TOKEN={token}\n", encoding="utf-8")
    return {
        "MCP_AGENT_ID": "aiagent", "MCP_TARGETS": "wf", "MCP_TARGETS_DIR": str(tmp_path / "t"),
        "MCP_AGENTS_DIR": str(tmp_path / "a"), "MCP_SECRETS_DIR": str(tmp_path / "s"),
        "MCP_MANUFACTURE_URL": "http://m:1", "MCP_MANUFACTURE_TOKEN": "", "MCP_MANUFACTURE_TIMEOUT_SECONDS": "5",
        "MCP_REFRESH_SECONDS": "60", "MCP_REFRESH_RETRY_SECONDS": "10", "MCP_TRANSPORT": "stdio",
        "MCP_TOOL_NAME_SEPARATOR": "__", "MCP_TOOL_NAME_MAX": "64", "MCP_MAX_CONCURRENT_CALLS": "2",
        "MCP_SCOPE_UNKNOWN": "fail", "LOG_LEVEL": "INFO",
    }


async def test_build_serves_and_runs_with_agent_settings(tmp_path, caplog):
    manufacture, target = FakeManufacture(bundle()), FakeTarget()
    app = build(files(tmp_path), registry=registry(), manufacture_transport=httpx.MockTransport(manufacture),
                target_transport=httpx.MockTransport(target))
    with caplog.at_level(logging.DEBUG):
        await app.refresher.refresh_once()
    snap = app.catalog.snapshot()
    assert set(snap.served) == {"wf__search_pages", "wf__add_comment", "wf__comment_on_page_by_title"}  # danger 숨김
    assert snap.hidden == {"wf__delete_page": "RISK"}
    result = app.runner.run(snap.served["wf__search_pages"], {"q": "x"}, snap.toolsets["wf"])
    assert result.ok and target.requests[0].url.params["space"] == "home-1"
    assert str(target.requests[0].url).startswith("https://wf.test/api/v1/search")
    assert all("authorization" not in r.headers for r in manufacture.requests)  # 조회 API에는 타겟 token이 없다
    assert TOKEN not in caplog.text and TOKEN not in repr(app)
    assert app.scopes == {"wf": ["read", "write"]}


def test_scope_unknown_fails_or_disables_filter(tmp_path):
    env = files(tmp_path, token="not-a-jwt")
    with pytest.raises(ScopeUnknown):
        build(env, registry=registry())
    app = build({**env, "MCP_SCOPE_UNKNOWN": "all"}, registry=registry())
    assert app.scopes == {"wf": None}


def test_missing_agent_files_is_config_error(tmp_path):
    env = files(tmp_path)
    with pytest.raises(ConfigError):
        build({**env, "MCP_AGENT_ID": "other"}, registry=registry())
