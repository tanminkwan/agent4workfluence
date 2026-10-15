"""AgentEngine: 요청 한 건을 수행한다 (Phase 4 설계 6절). 채널·HTTP·DB·MCP 구현을 모른다.

흐름: 이력 읽기 → tool 검색 → 후보만 모델에 싣기 → (모델 호출 → tool 실행)을 한도 안에서 되풀이 → 최종 답.
정보가 모자라면 모델이 tool 없이 되묻는 text로 답하고 그 요청은 정상으로 끝난다(6.5). 이어 가기는 이력으로 한다(8절).
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import anyio

from a4w_clients.llm.base import LlmError
from a4w_clients.llm.chat import ChatLlmClient, ChatMessage, ChatRequest, ChatResult, ToolCall, ToolSpec

from .marks import AuthorshipMarker, MarkNotApplicable, MarkNotConfigured
from .models import (ActionSummary, Candidate, HistoryTurn, McpUnavailable, RunFailed, SearchUnavailable, ToolDef, ToolOutcome,
                     TurnRequest, TurnResult, Usage)
from .ports import ConversationStore, ToolGateway, ToolSearch, TurnEngine
from .prompts import SystemPrompt

log = logging.getLogger(__name__)
ALLOWED_RISKS = ("read", "write")          # danger·없음·모르는 값은 모델에 싣지 않는다 (닫는 쪽)
LLM_ERROR_CODES = {"auth": "LLM_AUTH", "timeout": "LLM_TIMEOUT", "rate_limit": "LLM_RATE_LIMIT",
                   "unavailable": "LLM_UNAVAILABLE", "output_format": "LLM_BAD_RESPONSE"}
MESSAGE_OVERHEAD_CHARS = 32               # 역할·구분자 등 직렬화 여분 (어림)
EXEC_NOTE = re.compile(r"(?:\s*\[실행:[^\]\n]*\])+\s*$")   # 이력에 붙이는 시스템 기록 줄. 모델이 흉내 내 쓰면 지운다


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def now(self): ...


@dataclass(frozen=True)
class EngineSettings:
    model: str
    timeout_seconds: float
    max_output_tokens: int
    temperature: float | None
    reasoning_effort: str | None
    max_steps: int
    max_tool_calls: int
    max_write_calls: int
    max_seconds: float
    max_same_error: int
    retry_search_max: int
    max_input_tokens: int
    safety_ratio: float
    chars_per_token: float
    tool_result_max_chars: int
    trim_keep_chars: int
    search_history_turns: int
    search_query_max_chars: int
    history_max_turns: int
    record_arg_max_chars: int


@dataclass(frozen=True)
class AgentProfile:
    targets: tuple[str, ...]
    prompt: SystemPrompt


class _Stop(Exception):
    """한도·입력 크기 때문에 끝낸다 (실패가 아니다)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Msg:
    role: str
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    body: str | None = None          # tool 결과 본문 (표시로 감싸기 전)
    trimmed: bool = False
    history: bool = False            # 앞선 대화 이력에서 온 메시지
    turn: int = -1


@dataclass
class _Run:
    request: TurnRequest
    profile: AgentProfile
    query: str = ""
    open_mark: str = ""
    close_mark: str = ""
    system: str = ""
    history: list[HistoryTurn] = field(default_factory=list)
    msgs: list[_Msg] = field(default_factory=list)
    functions: dict[str, tuple[ToolDef, Candidate]] = field(default_factory=dict)
    gen: int = 0
    research_left: int = 0
    started: float = 0.0
    actions: list[ActionSummary] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    error_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    cpt: float = 1.5
    measured_tokens: int = 0
    chars_at_measure: int = 0
    context_retried: bool = False


class AgentEngine(TurnEngine):
    def __init__(self, *, search: ToolSearch, gateway: ToolGateway, llm: ChatLlmClient, store: ConversationStore,
                 marker: AuthorshipMarker, clock: Clock, settings: EngineSettings, nonce: Callable[[], str],
                 agents: Mapping[str, AgentProfile]):
        self._search, self._gateway, self._llm, self._store = search, gateway, llm, store
        self._marker, self._clock, self._s, self._nonce, self._agents = marker, clock, settings, nonce, agents

    # ------------------------------------------------------------------ 진입

    async def run(self, request: TurnRequest) -> TurnResult:
        state = _Run(request=request, profile=self._agents[request.agent_id])
        try:
            return await self._run(state)
        except _Stop as stop:
            return self._result(state, None, stop.reason)
        except RunFailed:
            raise
        except McpUnavailable:
            raise self._failed(state, "MCP_UNAVAILABLE", "agent의 MCP server를 쓸 수 없다") from None
        except SearchUnavailable:
            raise self._failed(state, "SEARCH_UNAVAILABLE", "tool 검색을 쓸 수 없다") from None
        except Exception as e:                      # 요청 text·token이 들어 있을 수 있어 내용은 남기지 않는다
            log.error("engine failed: %s", type(e).__name__)
            raise self._failed(state, "INTERNAL", "예상하지 못한 오류가 났다") from None

    @staticmethod
    def _failed(state: _Run, code: str, message: str) -> RunFailed:
        return RunFailed(code, message, actions=tuple(state.actions), usage=state.usage)

    @staticmethod
    def _result(state: _Run, answer: str | None, stop: str | None) -> TurnResult:
        return TurnResult(answer=answer, actions=tuple(state.actions), usage=state.usage, complete=stop is None, stop_reason=stop)

    # ------------------------------------------------------------------ 본체

    async def _run(self, state: _Run) -> TurnResult:
        s, req = self._s, state.request
        state.started = self._clock.monotonic()
        state.research_left = s.retry_search_max
        nonce = self._nonce()
        state.open_mark, state.close_mark = f"<<a4w-data:{nonce}>>", f"<<a4w-data-end:{nonce}>>"
        if req.conversation_id:
            state.history = list(await self._store.history(req.agent_id, req.conversation_id, limit=s.history_max_turns))
        state.query = self._query(state)
        stored = await self._store.last_chars_per_token(req.agent_id)
        state.cpt = min(s.chars_per_token, stored) if stored else s.chars_per_token
        await self._build_functions(state)
        state.system = state.profile.prompt.render(data_open=state.open_mark, data_close=state.close_mark,
                                                   now=self._clock.now(), agent_id=req.agent_id, targets=state.profile.targets)
        state.msgs = self._base_messages(state)
        while True:
            self._check_limits(state)
            if not self._fit(state):
                raise _Stop("CONTEXT_LIMIT")
            result = await self._ask(state)
            msg = result.message
            if result.finish_reason == "length":
                raise _Stop("LLM_LENGTH")
            if msg.tool_calls:
                state.msgs.append(_Msg("assistant", msg.content, tool_calls=msg.tool_calls))
                for call in msg.tool_calls:
                    await self._execute(state, call)
                continue
            answer = EXEC_NOTE.sub("", msg.content or "").strip()
            if not answer:
                raise _Stop("EMPTY_ANSWER")
            return self._result(state, answer, None)

    # ------------------------------------------------------------------ 검색·후보

    def _query(self, state: _Run) -> str:
        s = self._s
        previous = [t.text for t in state.history][-s.search_history_turns:] if s.search_history_turns > 0 else []
        query = "\n".join([*previous, state.request.text])
        return query[-s.search_query_max_chars:]

    async def _build_functions(self, state: _Run) -> None:
        agent = state.request.agent_id
        candidates = await self._search.search(agent, state.query)
        defs = await self._gateway.definitions(agent) if candidates else []
        state.gen = self._gateway.generation(agent)
        best: dict[tuple[str, str], Candidate] = {}
        for c in candidates:
            key = (c.target_id, c.name)
            if key not in best or c.score > best[key].score:
                best[key] = c
        by_key = {(d.target_id, d.name): d for d in defs}
        functions: dict[str, tuple[ToolDef, Candidate]] = {}
        for cand in sorted(best.values(), key=lambda c: -c.score):
            tool = by_key.get((cand.target_id, cand.name))
            if tool is None or tool.risk not in ALLOWED_RISKS:
                log.info("candidate dropped: %s/%s", cand.target_id, cand.name)
                continue
            functions[tool.mcp_name] = (tool, cand)
        state.functions = functions

    async def _research(self, state: _Run) -> None:
        state.research_left -= 1
        await self._gateway.refresh(state.request.agent_id)
        await self._build_functions(state)

    # ------------------------------------------------------------------ 메시지·크기

    def _base_messages(self, state: _Run) -> list[_Msg]:
        msgs: list[_Msg] = []
        for i, turn in enumerate(state.history):
            msgs.append(_Msg("user", turn.text, history=True, turn=i))
            note = ""
            if turn.actions:
                note = "\n[실행: " + ", ".join(f"{a.tool} {'ok' if a.ok else a.error_code}" for a in turn.actions) + "]"
            msgs.append(_Msg("assistant", turn.answer + note, history=True, turn=i))
        msgs.append(_Msg("user", state.request.text))
        return msgs

    def _chat_messages(self, state: _Run) -> tuple[ChatMessage, ...]:
        out = [ChatMessage("system", state.system)]
        for m in state.msgs:
            if m.role == "tool":
                out.append(ChatMessage("tool", f"{state.open_mark}{m.body}{state.close_mark}", tool_call_id=m.tool_call_id))
            else:
                out.append(ChatMessage(m.role, m.content, tool_calls=m.tool_calls))  # type: ignore[arg-type]
        return tuple(out)

    def _tool_specs(self, state: _Run) -> tuple[ToolSpec, ...]:
        return tuple(ToolSpec(t.mcp_name, t.description, t.input_schema) for t, _ in state.functions.values())

    def _chars(self, state: _Run) -> int:
        total = len(state.system) + MESSAGE_OVERHEAD_CHARS
        for m in self._chat_messages(state)[1:]:
            total += len(m.content or "") + MESSAGE_OVERHEAD_CHARS
            total += sum(len(c.name) + len(c.arguments) + MESSAGE_OVERHEAD_CHARS for c in m.tool_calls)
        for spec in self._tool_specs(state):
            total += len(spec.name) + len(spec.description) + len(json.dumps(spec.parameters, ensure_ascii=False)) + MESSAGE_OVERHEAD_CHARS
        return total

    def _estimate(self, state: _Run) -> float:
        """실제 입력 전체의 토큰 추정: 직전 실측 + 그 뒤 바뀐 글자 수의 어림 (설계 6.7)."""
        return state.measured_tokens + (self._chars(state) - state.chars_at_measure) / state.cpt

    def _fit(self, state: _Run) -> bool:
        limit = self._s.max_input_tokens * self._s.safety_ratio
        while self._estimate(state) > limit:
            if state.history:
                self._drop_history(state, 1)
            elif not self._trim_oldest_result(state):
                return False
        return True

    def _drop_history(self, state: _Run, turns: int) -> None:
        turns = min(turns, len(state.history))
        state.history = state.history[turns:]
        state.msgs = [m for m in state.msgs if not (m.history and m.turn < turns)]
        for m in state.msgs:
            if m.history:
                m.turn -= turns
        state.usage.history_dropped_turns += turns

    def _trim_oldest_result(self, state: _Run) -> bool:
        keep = self._s.trim_keep_chars
        for m in state.msgs:
            if m.role == "tool" and not m.trimmed and m.body is not None and len(m.body) > keep:
                original = len(m.body)
                m.body = f"{m.body[:keep]}…(줄임: {original}자 중 {keep}자)"
                m.trimmed = True
                state.usage.trimmed_results += 1
                return True
        return False

    # ------------------------------------------------------------------ 모델 호출

    def _check_limits(self, state: _Run) -> None:
        s = self._s
        if self._remaining(state) <= 0:
            raise _Stop("LIMIT_TIME")
        if state.usage.steps >= s.max_steps:
            raise _Stop("LIMIT_STEPS")

    def _remaining(self, state: _Run) -> float:
        return self._s.max_seconds - (self._clock.monotonic() - state.started)

    async def _ask(self, state: _Run) -> ChatResult:
        s = self._s
        while True:
            request = ChatRequest(messages=self._chat_messages(state), tools=self._tool_specs(state), model=s.model,
                                  timeout_seconds=min(s.timeout_seconds, self._remaining(state)), max_output_tokens=s.max_output_tokens,
                                  temperature=s.temperature, reasoning_effort=s.reasoning_effort)
            chars = self._chars(state)
            try:
                result = await anyio.to_thread.run_sync(self._llm.chat, request)
            except LlmError as e:
                if e.kind == "context_length":
                    if state.context_retried or not state.history:
                        raise _Stop("CONTEXT_LIMIT") from None
                    state.context_retried = True
                    self._drop_history(state, math.ceil(len(state.history) / 2))
                    continue
                raise self._failed(state, LLM_ERROR_CODES.get(e.kind, "LLM_UNAVAILABLE"), e.message) from None
            u, used = state.usage, result.usage
            u.steps += 1
            u.prompt_tokens += used.prompt_tokens
            u.completion_tokens += used.completion_tokens
            u.reasoning_tokens += used.reasoning_tokens
            if used.prompt_tokens > 0:
                state.measured_tokens, state.chars_at_measure = used.prompt_tokens, chars
                u.last_input_tokens, u.last_input_chars = used.prompt_tokens, chars
            return result

    # ------------------------------------------------------------------ tool 실행

    async def _execute(self, state: _Run, call: ToolCall) -> None:
        s, u = self._s, state.usage
        if u.tool_calls >= s.max_tool_calls:
            raise _Stop("LIMIT_TOOL_CALLS")
        if self._remaining(state) <= 0:
            raise _Stop("LIMIT_TIME")
        u.tool_calls += 1
        if state.research_left > 0 and self._gateway.generation(state.request.agent_id) != state.gen:
            await self._research(state)               # 모델이 생각하는 사이 tool 목록이 바뀌었다
        entry = state.functions.get(call.name)
        args: dict[str, Any] = {}
        sent: dict[str, Any] = {}
        write = bool(entry and entry[0].risk == "write")
        outcome: ToolOutcome
        if entry is None:
            outcome = self._engine_error("NOT_A_CANDIDATE", "이번에 주어진 tool 목록에 없는 이름이다", "목록에 있는 tool 이름만 부를 수 있다")
        else:
            parsed = _parse_args(call.arguments)
            if parsed is None:
                outcome = self._engine_error("ARGUMENTS_NOT_JSON", "arguments가 JSON 객체가 아니다", "JSON 객체 하나로 다시 부르라")
            else:
                args = parsed
                outcome, sent = await self._run_tool(state, call, entry, args, write)
        tool, cand = (entry or (None, None))
        code = None if outcome.ok else str((outcome.error or {}).get("code", "UNKNOWN"))
        state.actions.append(ActionSummary(tool=call.name, revision=cand.revision if cand else None, ok=outcome.ok, error_code=code,
                                           target_request_id=outcome.request_id, write=write,
                                           args=self._summarize(sent or args)))
        log.info("tool=%s ok=%s code=%s attempts=%s", call.name, outcome.ok, code, outcome.attempts)
        state.msgs.append(_Msg("tool", tool_call_id=call.id, body=self._body(state, outcome)))
        if code is not None:
            key = (call.name, code)
            state.error_counts[key] = state.error_counts.get(key, 0) + 1
            if state.error_counts[key] >= s.max_same_error:
                raise _Stop("LIMIT_REPEATED_ERROR")

    async def _run_tool(self, state: _Run, call: ToolCall, entry: tuple[ToolDef, Candidate], args: dict[str, Any],
                        write: bool) -> tuple[ToolOutcome, dict[str, Any]]:
        s, u = self._s, state.usage
        agent = state.request.agent_id
        tool, cand = entry
        sent = args
        if write:
            if u.write_calls >= s.max_write_calls:
                raise _Stop("LIMIT_WRITES")
            try:
                sent = self._marker.apply(tool.target_id, tool.name, args)
            except MarkNotConfigured:
                return self._engine_error("WRITE_MARK_NOT_CONFIGURED", "이 쓰기 tool의 작성 표시 칸이 설정에 없어 실행하지 않았다",
                                          "작성 표시 설정이 있는 tool만 쓸 수 있다"), args
            except MarkNotApplicable as e:
                return self._engine_error("WRITE_MARK_NOT_APPLICABLE", f"작성 표시를 붙일 칸 {e}의 값이 문자열이 아니다",
                                          "그 칸에는 문자열을 넣어 다시 부르라"), args
            u.write_calls += 1
        outcome = await self._gateway.call(agent, call.name, sent, expected_revision=cand.revision,
                                           timeout_seconds=max(self._remaining(state), 0.001))
        if not outcome.ok and (outcome.error or {}).get("code") in ("REVISION_MISMATCH", "TOOL_NOT_FOUND") and state.research_left > 0:
            await self._research(state)
            entry = state.functions.get(call.name)
            if entry is None:
                return self._engine_error("TOOL_SET_REFRESHED", "tool 목록이 갱신되어 그 tool은 더 이상 후보가 아니다",
                                          "새 tool 목록에서 다시 고르라"), sent
            outcome = await self._gateway.call(agent, call.name, sent, expected_revision=entry[1].revision,
                                               timeout_seconds=max(self._remaining(state), 0.001))
        return outcome, sent

    @staticmethod
    def _engine_error(code: str, message: str, hint: str) -> ToolOutcome:
        return ToolOutcome(ok=False, error={"code": code, "message": message, "hint": hint, "retryable": False})

    def _body(self, state: _Run, outcome: ToolOutcome) -> str:
        payload = outcome.data if outcome.ok else {"error": outcome.error}
        body = json.dumps(payload, ensure_ascii=False)
        nonce_part = state.open_mark[len("<<a4w-data:"):-2]
        body = body.replace(nonce_part, "")             # 표시를 흉내 낼 수 없게 한다 (6.6)
        cap = self._s.tool_result_max_chars
        if len(body) > cap:
            body = f"{body[:cap]}…(줄임: {len(body)}자 중 {cap}자)"
        return body

    def _summarize(self, args: Mapping[str, Any]) -> dict[str, str]:
        cap = self._s.record_arg_max_chars
        out: dict[str, str] = {}
        for k, v in args.items():
            text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            out[k] = text if len(text) <= cap else f"{text[:cap]}…({len(text)}자)"
        return out


def _parse_args(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None
