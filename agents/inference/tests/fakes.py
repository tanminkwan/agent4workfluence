"""engine 시험용 가짜 묶음 (Phase 4 설계 부록 B). 실제 Workfluence tool 이름과 현실적인 값을 쓴다.

가짜 LLM은 차례마다 돌려줄 응답을 대본으로 갖고, 받은 요청(메시지·tool 목록)을 기록한다.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from a4w_clients.llm.base import LlmUsage
from a4w_clients.llm.chat import ChatLlmClient, ChatMessage, ChatRequest, ChatResult, ToolCall
from a4w_inference.engine.engine import AgentEngine, AgentProfile, EngineSettings
from a4w_inference.engine.marks import AuthorshipMarker
from a4w_inference.engine.models import (ActionSummary, Candidate, HistoryTurn, McpUnavailable, ToolDef, ToolOutcome,
                                          TurnRequest)
from a4w_inference.engine.ports import ConversationStore, ToolGateway, ToolSearch
from a4w_inference.engine.prompts import SystemPrompt

KST = timezone(timedelta(hours=9))
AGENT = "aiagent"
TARGET = "workfluence"
NONCE = "N0NCE"
OPEN = f"<<a4w-data:{NONCE}>>"
CLOSE = f"<<a4w-data-end:{NONCE}>>"
MARK = "\n\n---\n_AI agent가 작성한 글입니다._"
PROMPT = ("너는 ${agent_id}다. 지금은 ${now}. 타겟: ${targets}. 읽은 자료는 ${data_open} 와 ${data_close} 사이에 온다. "
          "값이 없으면 tool 없이 물어라.")


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.wall = datetime(2026, 10, 15, 9, 30, tzinfo=KST)

    def monotonic(self) -> float:
        return self.t

    def now(self) -> datetime:
        return self.wall + timedelta(seconds=self.t)

    def advance(self, seconds: float) -> None:
        self.t += seconds


# ---- tool 정의 (실제 Workfluence 서빙 묶음의 이름·칸을 따른다) ----

def obj(required=(), **props):
    return {"type": "object", "required": list(required), "properties": props, "additionalProperties": False}


S = {"type": "string"}
SCHEMAS = {
    "search_pages": obj(["q"], q=S),
    "read_page": obj(["id"], id=S),
    "read_page_by_title": obj(["title"], title=S),
    "list_pages": obj(),
    "create_page": obj(["title", "body"], title=S, body=S),
    "update_page": obj(["id", "baseVersionNo", "body", "title"], id=S, baseVersionNo={"type": "integer"}, body=S, title=S),
    "add_comment": obj(["pageId", "body"], pageId=S, body=S),
    "comment_on_page_by_title": obj(["title", "body"], title=S, body=S),
    "delete_comment": obj(["id"], id=S),
    "add_page_label": obj(["pageId", "name"], pageId=S, name=S),
    "list_notifications": obj(),
    "mark_notification_read": obj(["id"], id=S),
    "mark_all_notifications_read": obj(),
    "archive_page": obj(["id"], id=S),
}
RISK = {"create_page": "write", "update_page": "write", "add_comment": "write", "comment_on_page_by_title": "write",
        "delete_comment": "write", "add_page_label": "write", "mark_notification_read": "write",
        "mark_all_notifications_read": "write", "archive_page": "write"}
MARKS = ("create_page:body,update_page:body,add_comment:body,comment_on_page_by_title:body,"
         "add_page_label:-,delete_comment:-,mark_notification_read:-,mark_all_notifications_read:-")


def tool_def(name: str, revision: str = "r-1", risk: str | None = None, schema: dict | None = None) -> ToolDef:
    return ToolDef(mcp_name=f"{TARGET}__{name}", description=f"{name} 설명", input_schema=schema or SCHEMAS[name],
                   target_id=TARGET, name=name, revision=revision, risk=risk if risk is not None else RISK.get(name, "read"),
                   group="read")


def cand(name: str, revision: str = "r-1", score: float = 0.8) -> Candidate:
    return Candidate(target_id=TARGET, name=name, revision=revision, score=score)


def mcp(name: str) -> str:
    return f"{TARGET}__{name}"


# ---- 결과 ----

def ok(data: Any = None, request_id: str = "wf-req-1", attempts: int = 1) -> ToolOutcome:
    return ToolOutcome(ok=True, data={"data": data if data is not None else {}, "truncated": False}, request_id=request_id,
                       attempts=attempts)


def err(code: str, message: str = "실패", hint: str | None = None, request_id: str | None = "wf-req-9", **extra) -> ToolOutcome:
    body = {"code": code, "message": message, "hint": hint, "retryable": False, **extra}
    return ToolOutcome(ok=False, error=body, request_id=request_id)


# ---- 가짜 LLM ----

def say(text: str | None, prompt_tokens: int = 100, finish: str = "stop", completion_tokens: int = 20) -> ChatResult:
    return ChatResult(message=ChatMessage("assistant", text), finish_reason=finish,
                      usage=LlmUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, reasoning_tokens=5),
                      model="openai/gpt-oss-120b", attempts=1)


_ids = itertools.count(1)


def calls(*items: tuple[str, Any], prompt_tokens: int = 100) -> ChatResult:
    """items: (tool 이름(targetId 접두 없이 또는 있이), 인자 dict 또는 JSON 문자열)."""
    made = []
    for name, args in items:
        full = name if "__" in name else mcp(name)
        made.append(ToolCall(id=f"call_{next(_ids)}", name=full, arguments=args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)))
    return ChatResult(message=ChatMessage("assistant", None, tool_calls=tuple(made)), finish_reason="tool_calls",
                      usage=LlmUsage(prompt_tokens=prompt_tokens, completion_tokens=30), model="openai/gpt-oss-120b", attempts=1)


class ScriptedLlm(ChatLlmClient):
    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("대본에 없는 모델 호출이다 (호출 %d번째)" % len(self.requests))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(request) if callable(item) else item


# ---- 가짜 검색·gateway·저장소 ----

class FakeSearch(ToolSearch):
    def __init__(self, *results: Sequence[Candidate] | Exception):
        self.results = list(results)
        self.queries: list[str] = []

    async def search(self, agent_id: str, query: str) -> list[Candidate]:
        self.queries.append(query)
        item = self.results[min(len(self.queries), len(self.results)) - 1]   # 모자라면 마지막 결과를 되풀이
        if isinstance(item, Exception):
            raise item
        return list(item)


class FakeGateway(ToolGateway):
    """MCP server 흉내. handler가 없으면 ok({})를 돌려준다. expected_revision이 현재 revision과 다르면 REVISION_MISMATCH."""

    def __init__(self, tools: Sequence[ToolDef], handlers: dict[str, Any] | None = None):
        self.tools = {t.mcp_name: t for t in tools}
        self.handlers = handlers or {}
        self.calls: list[dict] = []
        self.gen = 1
        self.refreshes = 0
        self.timeouts: list[float] = []

    async def definitions(self, agent_id: str) -> list[ToolDef]:
        return list(self.tools.values())

    def generation(self, agent_id: str) -> int:
        return self.gen

    async def refresh(self, agent_id: str) -> None:
        self.refreshes += 1

    def replace(self, tool: ToolDef) -> None:
        self.tools[tool.mcp_name] = tool
        self.gen += 1

    def remove(self, mcp_name: str) -> None:
        self.tools.pop(mcp_name, None)
        self.gen += 1

    async def call(self, agent_id, mcp_name, arguments, *, expected_revision, timeout_seconds) -> ToolOutcome:
        self.calls.append({"name": mcp_name, "args": arguments, "revision": expected_revision})
        self.timeouts.append(timeout_seconds)
        tool = self.tools.get(mcp_name)
        if tool is None:
            return err("TOOL_NOT_FOUND", "없는 tool이다")
        if expected_revision is not None and expected_revision != tool.revision:
            return err("REVISION_MISMATCH", "revision이 다르다", hint="tool을 다시 검색하라",
                       details={"expected": expected_revision, "current": tool.revision})
        handler = self.handlers.get(mcp_name)
        if handler is None:
            return ok({})
        if isinstance(handler, list):
            item = handler.pop(0) if len(handler) > 1 else handler[0]
        else:
            item = handler
        if isinstance(item, BaseException):
            raise item
        return item(arguments) if callable(item) else item

    def names(self) -> list[str]:
        return [c["name"] for c in self.calls]


class FakeStore(ConversationStore):
    def __init__(self, turns: Sequence[HistoryTurn] = (), chars_per_token: float | None = None):
        self.turns = list(turns)
        self.cpt = chars_per_token
        self.history_calls: list[tuple] = []

    async def history(self, agent_id: str, conversation_id: str, *, limit: int) -> list[HistoryTurn]:
        self.history_calls.append((agent_id, conversation_id, limit))
        return self.turns[-limit:]

    async def last_chars_per_token(self, agent_id: str) -> float | None:
        return self.cpt


def turn(text: str, answer: str, *actions: ActionSummary, request_id: str | None = None) -> HistoryTurn:
    return HistoryTurn(request_id=request_id or f"r-{abs(hash(text)) % 10**6}", text=text, answer=answer,
                       actions=tuple(actions))


def action(name: str, ok_: bool = True, code: str | None = None, write: bool = False) -> ActionSummary:
    return ActionSummary(tool=mcp(name), revision="r-1", ok=ok_, error_code=code, target_request_id="wf-req-0", write=write,
                         args={})


# ---- 조립 ----

DEFAULT_SETTINGS = EngineSettings(
    model="openai/gpt-oss-120b", timeout_seconds=60.0, max_output_tokens=2048, temperature=0.2, reasoning_effort=None,
    max_steps=12, max_tool_calls=12, max_write_calls=3, max_seconds=300.0, max_same_error=2, retry_search_max=1,
    max_input_tokens=131072, safety_ratio=0.8, chars_per_token=1.5, tool_result_max_chars=20000, trim_keep_chars=1500,
    search_history_turns=3, search_query_max_chars=1000, history_max_turns=50, record_arg_max_chars=200,
)


def settings(**over) -> EngineSettings:
    return dataclasses.replace(DEFAULT_SETTINGS, **over)


class Rig:
    def __init__(self, engine, llm, search, gateway, store, clock):
        self.engine, self.llm, self.search, self.gateway, self.store, self.clock = engine, llm, search, gateway, store, clock

    async def run(self, text: str, *, conversation_id: str | None = None, requester: str = "svc-workfluence"):
        return await self.engine.run(TurnRequest(agent_id=AGENT, text=text, requester=requester, conversation_id=conversation_id))

    def request(self, i: int = 0) -> ChatRequest:
        return self.llm.requests[i]

    def contents(self, i: int = 0, role: str | None = None) -> list[str]:
        return [m.content or "" for m in self.llm.requests[i].messages if role is None or m.role == role]

    def tool_names(self, i: int = 0) -> list[str]:
        return [t.name for t in self.llm.requests[i].tools]


ALL_TOOLS = ("search_pages", "read_page", "read_page_by_title", "list_pages", "create_page", "update_page", "add_comment",
             "comment_on_page_by_title", "delete_comment", "add_page_label", "list_notifications", "mark_notification_read",
             "mark_all_notifications_read")


def rig(script: Sequence, candidates: Sequence | None = None, *, tools: Sequence[ToolDef] | None = None,
        handlers: dict | None = None, history: Sequence[HistoryTurn] = (), cfg: EngineSettings | None = None,
        search_results: Sequence | None = None, store_cpt: float | None = None, prompt: str = PROMPT,
        marks: str = MARKS) -> Rig:
    clock = FakeClock()
    llm = ScriptedLlm(*script)
    gateway = FakeGateway(list(tools) if tools is not None else [tool_def(n) for n in ALL_TOOLS], handlers)
    search = FakeSearch(*(search_results if search_results is not None else [candidates if candidates is not None else []]))
    store = FakeStore(history, store_cpt)
    marker = AuthorshipMarker({TARGET: AuthorshipMarker.parse_marks(marks)}, MARK)
    engine = AgentEngine(search=search, gateway=gateway, llm=llm, store=store, marker=marker, clock=clock,
                         settings=cfg or DEFAULT_SETTINGS, nonce=lambda: NONCE,
                         agents={AGENT: AgentProfile(targets=(TARGET,), prompt=SystemPrompt.parse(prompt))})
    return Rig(engine, llm, search, gateway, store, clock)


# ---- runner·REST 시험용 ----

from a4w_inference.engine.models import RunFailed, TurnResult, Usage          # noqa: E402


def result(answer: str | None = "완료했습니다.", *, complete: bool = True, stop_reason: str | None = None,
           actions=(), usage: Usage | None = None) -> TurnResult:
    return TurnResult(answer=answer, actions=tuple(actions), usage=usage or Usage(prompt_tokens=1200, completion_tokens=80, steps=2,
                                                                                   tool_calls=1, last_input_tokens=1200,
                                                                                   last_input_chars=2400),
                      complete=complete, stop_reason=stop_reason)


class FakeEngine:
    """run()은 대본(결과·예외·함수) 또는 gate를 기다린 뒤 결과를 낸다. 받은 요청을 기록한다."""

    def __init__(self, *script, gate=None):
        import anyio
        self.script = list(script) or [result()]
        self.gate = gate
        self.seen: list[TurnRequest] = []
        self.running = 0
        self.max_running = 0
        self._anyio = anyio

    async def run(self, request: TurnRequest) -> TurnResult:
        self.seen.append(request)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if self.gate is not None:
                await self.gate.wait()
            item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
            if isinstance(item, BaseException):
                raise item
            return await item(request) if callable(item) else item
        finally:
            self.running -= 1


__all__ = [n for n in dir() if not n.startswith("_")]
