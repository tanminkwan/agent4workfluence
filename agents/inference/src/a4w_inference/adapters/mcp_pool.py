"""agent마다 공용 MCP server(a4w_mcp)를 자식 프로세스로 띄우고 관리한다 (Phase 4 설계 10절, FR-I42~I44).

연결은 supervisor task가 소유한다(SDK Client의 비동기 context는 연 task에서 닫아야 한다). 자식이 죽으면 백오프로 다시 띄운다.
agent token 파일은 자식(a4w_mcp)만 읽는다. 자식에는 허용 목록의 환경변수만 준다(LLM key·REST token이 가지 않는다).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import anyio
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.client.subscriptions import ListenNotSupportedError
from mcp.shared.exceptions import MCPError

from a4w_core.errors import ConfigError

from ..engine.models import McpUnavailable, ToolDef, ToolOutcome

log = logging.getLogger(__name__)
META = "a4w/"                                            # a4w_mcp의 _meta 키 앞머리 (식별 규칙)
EXPECTED_REVISION = META + "expectedRevision"
INVALID_PARAMS = -32602
FORBIDDEN_PREFIXES = ("INFERENCE_", "LLM_", "RAG_", "CHAT_")      # 서비스 비밀(채팅 token 포함): 자식에게 주지 않는다
PING_TIMEOUT_SECONDS = 5.0


def child_env(environ: Mapping[str, str], *, agent_id: str, targets: Sequence[str], prefixes: Sequence[str],
              names: Sequence[str]) -> dict[str, str]:
    for p in (*prefixes, *names):
        if any(f.startswith(p) or p.startswith(f) for f in FORBIDDEN_PREFIXES):
            raise ConfigError(f"MCP 자식 환경변수 허용 목록에 서비스 비밀 이름이 들어 있다: {p!r}")
    env = {k: v for k, v in environ.items() if k in names or any(k.startswith(p) for p in prefixes)}
    env["MCP_AGENT_ID"] = agent_id
    env["MCP_TARGETS"] = ",".join(targets)
    return env


@dataclass(frozen=True)
class McpChildSpec:
    agent_id: str
    command: tuple[str, ...]
    env: Mapping[str, str]
    client_mode: str = "auto"


class AgentMcpConnection:
    def __init__(self, spec: McpChildSpec, *, start_timeout: float, backoff_initial: float, backoff_max: float, tools_wait: float,
                 ping_interval: float):
        self._spec = spec
        self._start_timeout, self._backoff_initial, self._backoff_max = start_timeout, backoff_initial, backoff_max
        self._tools_wait, self._ping_interval = tools_wait, ping_interval
        self.state = "starting"                          # starting | up | restarting | down
        self.generation = 0
        self._client: Client | None = None
        self._tools: list[ToolDef] | None = None
        self._dirty = False

    # ------------------------------------------------------------------ supervisor

    async def run(self) -> None:
        backoff = self._backoff_initial
        try:
            while True:
                reached_up = False
                try:
                    reached_up = await self._session()
                except Exception as e:                    # 다시 띄운다. 내용(비밀 가능성)은 남기지 않는다
                    log.warning("mcp child ended: agent=%s %s", self._spec.agent_id, type(e).__name__)
                self.state, self._client = "restarting", None
                backoff = self._backoff_initial if reached_up else backoff
                await anyio.sleep(backoff)
                backoff = min(backoff * 2, self._backoff_max)
        finally:
            self.state, self._client = "down", None

    async def _session(self) -> bool:
        """자식을 띄워 붙고, 연결이 끝날 때까지 지킨다. 기동 제한 시간은 감시 task가 취소 범위를 취소해 건다
        (Client의 task group이 제한 시간 범위 안에서 열리면 범위가 어긋나므로 범위를 연결 전체에 씌운다)."""
        spec = self._spec
        params = StdioServerParameters(command=spec.command[0], args=list(spec.command[1:]), env=dict(spec.env))
        client = Client(params, mode=spec.client_mode, message_handler=self._on_message)  # type: ignore[arg-type]
        scope = anyio.CancelScope()
        up = {"value": False}
        watchdog = asyncio.create_task(self._watchdog(scope, up))
        try:
            with scope:
                async with AsyncExitStack() as stack:
                    await stack.enter_async_context(client)
                    await self._load(client, bump=True)
                    self._client, self.state, up["value"] = client, "up", True
                    log.info("mcp child up: agent=%s tools=%d", spec.agent_id, len(self._tools or []))
                    await self._watch(client)
        finally:
            watchdog.cancel()
        if scope.cancelled_caught:
            raise TimeoutError("MCP 자식이 제한 시간 안에 준비되지 않았다")
        return up["value"]

    async def _watchdog(self, scope: anyio.CancelScope, up: dict[str, bool]) -> None:
        await asyncio.sleep(self._start_timeout)
        if not up["value"]:
            scope.cancel()

    async def _watch(self, client: Client) -> None:
        """연결이 끝날 때까지 지킨다. 2026-07-28 판은 변경 구독(listen)이 곧 연결 감시다(구독이 끝나면 연결이 끊긴 것).
        handshake 판은 listen이 없어 ping으로 감시하고, 알림은 message_handler가 받는다."""
        try:
            async with client.listen(tools_list_changed=True) as sub:
                async for _ in sub:
                    self._mark_changed()
            raise ConnectionError("변경 구독이 끝났다")
        except ListenNotSupportedError:
            pass
        while True:
            await anyio.sleep(self._ping_interval)
            with anyio.fail_after(PING_TIMEOUT_SECONDS):
                await client.send_ping()

    async def _on_message(self, message: Any) -> None:
        if type(getattr(message, "root", message)).__name__ == "ToolListChangedNotification":
            self._mark_changed()

    def _mark_changed(self) -> None:
        self._dirty = True
        self.generation += 1

    # ------------------------------------------------------------------ tool 목록

    async def _load(self, client: Client, *, bump: bool) -> None:
        result = await client.list_tools()
        tools: list[ToolDef] = []
        for t in result.tools:
            meta = t.meta or {}
            target, name, revision = meta.get(META + "targetId"), meta.get(META + "name"), meta.get(META + "revision")
            if not (target and name and revision):
                log.info("tool ignored (no a4w meta): %s", t.name)
                continue
            tools.append(ToolDef(mcp_name=t.name, description=t.description or "", input_schema=dict(t.input_schema), target_id=target,
                                 name=name, revision=revision, risk=str(meta.get(META + "risk") or ""),
                                 group=str(meta.get(META + "group") or "")))
        self._tools, self._dirty = tools, False
        if bump:
            self.generation += 1

    def _live(self) -> Client:
        if self.state != "up" or self._client is None:
            raise McpUnavailable(f"agent {self._spec.agent_id}의 MCP server가 {self.state} 상태다")
        return self._client

    async def definitions(self) -> list[ToolDef]:
        client = self._live()
        try:
            if self._dirty or self._tools is None:
                await self._load(client, bump=False)
            deadline = anyio.current_time() + self._tools_wait
            while not self._tools and anyio.current_time() < deadline:   # 시작 직후: 정의를 아직 못 받았을 수 있다
                await anyio.sleep(0.05)
                await self._load(client, bump=False)
        except McpUnavailable:
            raise
        except Exception as e:
            raise McpUnavailable(f"tool 목록을 읽지 못했다: {type(e).__name__}") from None
        return list(self._tools or [])

    async def refresh(self) -> None:
        client = self._live()
        try:
            await self._load(client, bump=True)
        except Exception as e:
            raise McpUnavailable(f"tool 목록을 읽지 못했다: {type(e).__name__}") from None

    def tool_count(self) -> int:
        return len(self._tools or []) if self.state == "up" else 0

    # ------------------------------------------------------------------ 실행

    async def call(self, mcp_name: str, arguments: Mapping[str, Any], *, expected_revision: str | None,
                   timeout_seconds: float) -> ToolOutcome:
        client = self._live()
        meta = {EXPECTED_REVISION: expected_revision} if expected_revision else None
        try:
            with anyio.fail_after(timeout_seconds):
                result = await client.call_tool(mcp_name, dict(arguments), meta=meta, read_timeout_seconds=timeout_seconds)
        except MCPError as e:
            if e.code == INVALID_PARAMS:
                return _error("TOOL_NOT_FOUND", "MCP server에 그 tool이 없다")
            return _error("MCP_ERROR", e.message)
        except TimeoutError:
            return _error("MCP_TIMEOUT", "tool 실행이 제한 시간을 넘었다")
        except Exception as e:                            # 연결이 끊긴 경우 등
            raise McpUnavailable(f"MCP 호출에 실패했다: {type(e).__name__}") from None
        return _outcome(result)


def _error(code: str, message: str) -> ToolOutcome:
    return ToolOutcome(ok=False, error={"code": code, "message": message, "hint": None, "retryable": False})


def _outcome(result: Any) -> ToolOutcome:
    body = result.structured_content
    if body is None:
        try:
            body = json.loads(result.content[0].text)
        except Exception:
            body = {}
    meta = result.meta or {}
    if isinstance(meta, Mapping):
        meta = dict(meta)
    else:
        meta = meta.model_dump(by_alias=True, exclude_none=True)
    if result.is_error:
        error = body.get("error") or {"code": "TOOL_ERROR", "message": "tool이 오류를 돌려줬다"}
        return ToolOutcome(ok=False, error=error, request_id=error.get("requestId") or meta.get(META + "requestId"))
    return ToolOutcome(ok=True, data=body, request_id=meta.get(META + "requestId"), attempts=int(meta.get(META + "attempts", 1)))


class McpAgentPool:
    def __init__(self, connections: Mapping[str, AgentMcpConnection]):
        self._conns = dict(connections)
        self._tasks: list[asyncio.Task] = []

    async def __aenter__(self) -> McpAgentPool:
        self._tasks = [asyncio.create_task(c.run(), name=f"mcp-{a}") for a, c in self._conns.items()]
        return self

    async def __aexit__(self, *exc: object) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except BaseException:                         # noqa: BLE001 - 정리 중이다
                pass

    def connection(self, agent_id: str) -> AgentMcpConnection:
        try:
            return self._conns[agent_id]
        except KeyError:
            raise McpUnavailable(f"등록되지 않은 agent다: {agent_id}") from None

    def health(self) -> dict[str, dict[str, Any]]:
        return {a: {"mcp": c.state, "tools": c.tool_count()} for a, c in self._conns.items()}
