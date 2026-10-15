"""MCP 풀: agent마다 자식 프로세스를 띄우고 관리한다 (설계 10절, 부록 B.9 M1~M5). 가짜 stdio server로 진짜 자식 프로세스를 쓴다.

TDD red: a4w_inference.adapters.mcp_pool이 아직 없다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import pytest

from a4w_inference.adapters.gateway_mcp import McpToolGateway
from a4w_inference.adapters.mcp_pool import AgentMcpConnection, McpAgentPool, McpChildSpec, child_env
from a4w_inference.engine.models import McpUnavailable
from a4w_inference.engine.ports import ToolGateway

pytestmark = pytest.mark.anyio
SERVER = Path(__file__).resolve().parents[1] / "fake_mcp_server.py"
READ = {"name": "search_pages", "revision": "r-aaa", "schema": {"type": "object", "required": ["q"], "properties": {"q": {"type": "string"}}}}
WRITE = {"name": "create_page", "revision": "r-bbb", "risk": "write"}


def make_state(tmp_path, name="a", tools=(READ,)):
    d = tmp_path / name
    d.mkdir()
    (d / "tools.json").write_text(json.dumps(list(tools)), encoding="utf-8")
    return d


def set_tools(state, tools):
    """server가 읽는 도중에 반쯤 쓴 파일을 보지 않게 원자적으로 바꾼다."""
    tmp = state / "tools.json.tmp"
    tmp.write_text(json.dumps(list(tools)), encoding="utf-8")
    os.replace(tmp, state / "tools.json")


def spec(state, agent="aiagent", **over):
    env = {"FAKE_STATE_DIR": str(state), "PATH": "/usr/local/bin:/usr/bin"}
    kw = dict(agent_id=agent, command=(sys.executable, str(SERVER)), env=env, client_mode="auto")
    kw.update(over)
    return McpChildSpec(**kw)


def conn(state, **over):
    mode = over.pop("client_mode", "auto")
    return AgentMcpConnection(spec(state, client_mode=mode), start_timeout=20, backoff_initial=0.05, backoff_max=0.2, tools_wait=1.0,
                              ping_interval=0.05, **over)


async def until(predicate, timeout=10):
    with anyio.fail_after(timeout):
        while not predicate():
            await anyio.sleep(0.02)


def calls(state):
    p = state / "calls.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


class Running:
    """연결을 asyncio task로 돌리고 끝나면 정리한다."""

    def __init__(self, *conns):
        self.conns, self.tasks = conns, []

    async def __aenter__(self):
        import asyncio
        self.tasks = [asyncio.create_task(c.run()) for c in self.conns]
        return self.conns[0] if len(self.conns) == 1 else self.conns

    async def __aexit__(self, *exc):
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except BaseException:
                pass


# ---- M1 자식 환경변수는 허용 목록뿐 ----

def test_M1_child_env_is_an_allow_list_and_never_carries_service_secrets():
    environ = {"LLM_API_KEY": "sk-LLM", "LLM_BASE_URL": "https://llm", "INFERENCE_API_TOKEN": "tok-REST", "RAG_API_KEY": "rag-key",
               "MCP_MANUFACTURE_URL": "http://manufacture:18002", "MCP_REFRESH_SECONDS": "60", "MCP_AGENT_ID": "wrong",
               "HTTP_PROXY": "http://proxy:3128", "NO_PROXY": "manufacture", "PATH": "/usr/bin", "HOME": "/root", "LOG_LEVEL": "INFO",
               "AWS_SECRET_ACCESS_KEY": "aws"}
    env = child_env(environ, agent_id="aiagent", targets=("workfluence",), prefixes=("MCP_", "LOG_LEVEL"),
                    names=("PATH", "HTTP_PROXY", "NO_PROXY"))
    assert env["MCP_MANUFACTURE_URL"] == "http://manufacture:18002" and env["MCP_REFRESH_SECONDS"] == "60"
    assert env["HTTP_PROXY"] == "http://proxy:3128" and env["PATH"] == "/usr/bin" and env["LOG_LEVEL"] == "INFO"
    assert env["MCP_AGENT_ID"] == "aiagent" and env["MCP_TARGETS"] == "workfluence"        # agent 설정으로 덮어쓴다
    assert not ({"LLM_API_KEY", "LLM_BASE_URL", "INFERENCE_API_TOKEN", "RAG_API_KEY", "HOME", "AWS_SECRET_ACCESS_KEY"} & set(env))
    assert "sk-LLM" not in json.dumps(env) and "tok-REST" not in json.dumps(env)


@pytest.mark.parametrize("prefixes", [("INFERENCE_",), ("LLM_",), ("RAG_",), ("MCP_", "LLM_")])
def test_M1_allow_list_that_would_leak_service_secrets_is_refused(prefixes):
    from a4w_core.errors import ConfigError
    with pytest.raises(ConfigError):
        child_env({}, agent_id="a", targets=("t",), prefixes=prefixes, names=())


async def test_M1_the_real_child_only_sees_the_allowed_variables(tmp_path):
    state = make_state(tmp_path)
    environ = {"FAKE_STATE_DIR": str(state), "PATH": "/usr/local/bin:/usr/bin", "MCP_X": "1", "LLM_API_KEY": "sk-LLM", "INFERENCE_API_TOKEN": "t"}
    env = child_env(environ, agent_id="aiagent", targets=("workfluence",), prefixes=("MCP_",), names=("PATH", "FAKE_STATE_DIR"))
    c = AgentMcpConnection(McpChildSpec("aiagent", (sys.executable, str(SERVER)), env, "auto"), start_timeout=20, backoff_initial=0.05,
                           backoff_max=0.2, tools_wait=1.0, ping_interval=0.05)
    async with Running(c):
        await until(lambda: c.state == "up")
    seen = set(json.loads((state / "env.json").read_text()))
    assert {"MCP_X", "MCP_AGENT_ID", "MCP_TARGETS"} <= seen
    assert "LLM_API_KEY" not in seen and "INFERENCE_API_TOKEN" not in seen


# ---- 기본 동작 ----

async def test_definitions_are_read_from_tool_meta_not_from_name_rules(tmp_path):
    state = make_state(tmp_path, tools=(READ, WRITE))
    async with Running(conn(state)) as c:
        await until(lambda: c.state == "up")
        defs = await c.definitions()
    by = {d.name: d for d in defs}
    assert set(by) == {"search_pages", "create_page"}
    d = by["search_pages"]
    assert (d.mcp_name, d.target_id, d.revision, d.risk) == ("workfluence__search_pages", "workfluence", "r-aaa", "read")
    assert d.input_schema["required"] == ["q"] and by["create_page"].risk == "write"


async def test_tools_without_the_a4w_meta_are_ignored(tmp_path):
    state = make_state(tmp_path, tools=(READ, {"name": "weird", "revision": "r-x", "meta": {"a4w/targetId": None, "a4w/name": None}}))
    async with Running(conn(state)) as c:
        await until(lambda: c.state == "up")
        assert [d.name for d in await c.definitions()] == ["search_pages"]


async def test_call_success_maps_data_request_id_and_sends_expected_revision(tmp_path):
    state = make_state(tmp_path)
    async with Running(conn(state)) as c:
        await until(lambda: c.state == "up")
        out = await c.call("workfluence__search_pages", {"q": "회의록"}, expected_revision="r-aaa", timeout_seconds=10)
    assert out.ok and out.data == {"data": {"echo": {"q": "회의록"}, "tool": "search_pages"}, "truncated": False}
    assert out.request_id == "wf-req-1" and out.attempts == 1
    [rec] = calls(state)
    assert rec["arguments"] == {"q": "회의록"} and rec["meta"]["a4w/expectedRevision"] == "r-aaa"


async def test_call_error_result_is_an_outcome_not_an_exception(tmp_path):
    state = make_state(tmp_path, tools=({**READ, "fail": "TARGET_ERROR"},))
    async with Running(conn(state)) as c:
        await until(lambda: c.state == "up")
        out = await c.call("workfluence__search_pages", {"q": "x"}, expected_revision="r-aaa", timeout_seconds=10)
    assert out.ok is False and out.error["code"] == "TARGET_ERROR" and out.error["hint"] == "고쳐서 다시" and out.request_id == "wf-req-err"


async def test_revision_mismatch_comes_back_as_an_error_outcome(tmp_path):
    state = make_state(tmp_path)
    async with Running(conn(state)) as c:
        await until(lambda: c.state == "up")
        out = await c.call("workfluence__search_pages", {"q": "x"}, expected_revision="r-OLD", timeout_seconds=10)
    assert out.ok is False and out.error["code"] == "REVISION_MISMATCH" and out.error["details"]["current"] == "r-aaa"


async def test_M5_unknown_tool_is_tool_not_found_outcome(tmp_path):
    state = make_state(tmp_path)
    async with Running(conn(state)) as c:
        await until(lambda: c.state == "up")
        out = await c.call("workfluence__ghost", {}, expected_revision=None, timeout_seconds=10)
    assert out.ok is False and out.error["code"] == "TOOL_NOT_FOUND"


# ---- M2 자식이 죽으면 ----

async def test_M2_child_crash_is_survived_with_unavailable_in_between_and_a_new_generation(tmp_path):
    state = make_state(tmp_path)
    async with Running(conn(state)) as c:
        await until(lambda: c.state == "up")
        gen = c.generation
        (state / "crash").write_text("x")
        await until(lambda: c.state != "up")
        with pytest.raises(McpUnavailable):
            await c.call("workfluence__search_pages", {"q": "x"}, expected_revision=None, timeout_seconds=5)
        (state / "crash").unlink()                       # 다시 뜬 자식은 죽지 않는다
        await until(lambda: c.state == "up", timeout=15)
        assert c.generation > gen
        out = await c.call("workfluence__search_pages", {"q": "x"}, expected_revision="r-aaa", timeout_seconds=10)
        assert out.ok


async def test_M2_a_child_that_cannot_start_leaves_the_pool_alive_and_calls_unavailable(tmp_path):
    state = make_state(tmp_path)
    bad = McpChildSpec("aiagent", (sys.executable, "/no/such/server.py"), {"PATH": "/usr/bin"}, "auto")
    c = AgentMcpConnection(bad, start_timeout=5, backoff_initial=0.05, backoff_max=0.2, tools_wait=0.1, ping_interval=0.05)
    async with Running(c):
        await anyio.sleep(0.5)
        assert c.state in ("restarting", "starting")
        with pytest.raises(McpUnavailable):
            await c.call("workfluence__search_pages", {}, expected_revision=None, timeout_seconds=1)
        with pytest.raises(McpUnavailable):
            await c.definitions()
    assert state


async def test_M2_one_agent_crashing_does_not_touch_the_other(tmp_path):
    s1, s2 = make_state(tmp_path, "a1"), make_state(tmp_path, "a2")
    c1, c2 = conn(s1), AgentMcpConnection(spec(s2, agent="other"), start_timeout=20, backoff_initial=0.05, backoff_max=0.2,
                                          tools_wait=1.0, ping_interval=0.05)
    async with Running(c1, c2):
        await until(lambda: c1.state == "up" and c2.state == "up")
        (s1 / "crash").write_text("x")
        await until(lambda: c1.state != "up")
        assert c2.state == "up"
        out = await c2.call("workfluence__search_pages", {"q": "x"}, expected_revision="r-aaa", timeout_seconds=10)
        assert out.ok


# ---- M3 목록 바뀜 ----

@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_M3_list_changed_bumps_generation_and_refreshes_the_cache(tmp_path, mode):
    state = make_state(tmp_path)
    async with Running(conn(state, client_mode=mode)) as c:
        await until(lambda: c.state == "up")
        assert [d.revision for d in await c.definitions()] == ["r-aaa"]
        await anyio.sleep(0.3)                           # 알림 통로가 자리 잡을 때까지
        gen = c.generation
        set_tools(state, [{**READ, "revision": "r-NEW"}])
        await until(lambda: c.generation > gen)
        assert [d.revision for d in await c.definitions()] == ["r-NEW"]


async def test_refresh_reloads_even_without_a_notification(tmp_path):
    state = make_state(tmp_path)
    async with Running(conn(state)) as c:
        await until(lambda: c.state == "up")
        assert [d.revision for d in await c.definitions()] == ["r-aaa"]
        set_tools(state, [{**READ, "revision": "r-2"}])
        gen = c.generation
        await c.refresh()
        assert c.generation > gen and [d.revision for d in await c.definitions()] == ["r-2"]


# ---- M4 시작 직후 빈 목록 ----

async def test_M4_empty_list_at_startup_waits_for_tools_to_arrive(tmp_path):
    state = make_state(tmp_path, tools=())
    async with Running(conn(state)) as c:
        await until(lambda: c.state == "up")

        async def fill():
            await anyio.sleep(0.3)
            set_tools(state, [READ])
        async with anyio.create_task_group() as tg:
            tg.start_soon(fill)
            defs = await c.definitions()
        assert [d.name for d in defs] == ["search_pages"]


async def test_M4_still_empty_after_the_wait_returns_an_empty_list(tmp_path):
    state = make_state(tmp_path, tools=())
    c = AgentMcpConnection(spec(state), start_timeout=20, backoff_initial=0.05, backoff_max=0.2, tools_wait=0.3, ping_interval=0.05)
    async with Running(c):
        await until(lambda: c.state == "up")
        assert await c.definitions() == []


# ---- pool · gateway ----

async def test_pool_health_and_gateway_routes_by_agent(tmp_path):
    s1, s2 = make_state(tmp_path, "a1"), make_state(tmp_path, "a2", tools=(WRITE,))
    c1 = conn(s1)
    c2 = AgentMcpConnection(spec(s2, agent="other"), start_timeout=20, backoff_initial=0.05, backoff_max=0.2, tools_wait=1.0, ping_interval=0.05)
    pool = McpAgentPool({"aiagent": c1, "other": c2})
    async with pool:
        await until(lambda: pool.health()["aiagent"]["mcp"] == "up" and pool.health()["other"]["mcp"] == "up")
        assert pool.health() == {"aiagent": {"mcp": "up", "tools": 1}, "other": {"mcp": "up", "tools": 1}}
        gw = McpToolGateway(pool)
        assert isinstance(gw, ToolGateway)
        assert [d.name for d in await gw.definitions("aiagent")] == ["search_pages"]
        assert [d.name for d in await gw.definitions("other")] == ["create_page"]
        out = await gw.call("aiagent", "workfluence__search_pages", {"q": "x"}, expected_revision="r-aaa", timeout_seconds=10)
        assert out.ok and isinstance(gw.generation("aiagent"), int)
        await gw.refresh("aiagent")
        with pytest.raises(McpUnavailable):
            await gw.definitions("ghost")


async def test_pool_stop_terminates_the_child_processes(tmp_path):
    state = make_state(tmp_path)
    c = conn(state)
    pool = McpAgentPool({"aiagent": c})
    async with pool:
        await until(lambda: c.state == "up")
    assert c.state == "down"
