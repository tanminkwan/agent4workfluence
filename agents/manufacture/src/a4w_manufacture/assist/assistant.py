"""LLM 보조 (Phase 2 설계 9절): create · update · redescribe.

- update: 이름은 이전 이름으로 덮어쓴다. 구조가 같은 칸은 이전 글(설명·숨김·기본값)을 코드가 옮긴다.
- redescribe: 이름·숨김·기본값·responseFields는 이전 그대로, 글만 LLM.
- 검사(DefinitionChecker)에 걸리면 오류 목록을 붙여 다시 요청한다. 반영 한 번의 호출 수에 상한이 있다.
- 검사를 통과한 출력만 재사용 지문으로 저장한다.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from threading import Lock
from typing import Any, Protocol

from a4w_clients.llm.base import LlmClient, LlmError, LlmRequest
from a4w_core.openapi.canonical import canonical_json, strip_doc_keys

from ..assembly import NO_DEFAULT, authoring_to_raw, extract_authoring
from ..checks import AUTHORING_SCHEMA, DefinitionChecker
from ..errors import LlmAuthFailed
from ..transform.base import Skeleton
from .prompts import PromptSet

TASKS = {"create": "tool_create", "update": "tool_update", "redescribe": "tool_redescribe"}
ORIGIN = {"create": "llm", "update": "llm", "redescribe": "llm-doc"}
ERROR_CODES = {"timeout": "LLM_TIMEOUT", "unavailable": "LLM_UNAVAILABLE", "rate_limit": "LLM_UNAVAILABLE",
               "output_format": "LLM_OUTPUT_INVALID"}


class Cache(Protocol):
    def llm_cached(self, key: str) -> Any: ...
    def llm_store(self, key: str, *, task: str, model: str, prompt: str, output: Any, usage: Any, at: str) -> None: ...


class AssistFailed(Exception):
    def __init__(self, code: str, message: str, problems: list[dict[str, str]] | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.problems = problems or []


class LlmBudget:
    """반영 한 번의 LLM 호출 상한 (재시도 포함, FR-245)."""

    def __init__(self, max_calls: int):
        self._left = max_calls
        self._lock = Lock()
        self.used = 0

    def take(self) -> None:
        with self._lock:
            if self._left <= 0:
                raise AssistFailed("LLM_BUDGET_EXCEEDED", "반영 한 번의 LLM 호출 상한에 닿았다")
            self._left -= 1
            self.used += 1

    def refund(self, n: int) -> None:
        with self._lock:
            self._left += n
            self.used -= n


@dataclass(frozen=True)
class Authored:
    raw: dict[str, Any]
    origin: str        # llm | llm-doc | cache
    calls: int
    usage: dict[str, int]


class ToolAssistant:
    def __init__(
        self,
        *,
        llm: LlmClient,
        cache: Cache,
        prompts: PromptSet,
        checker: DefinitionChecker,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        temperature: float | None,
        reasoning_effort: str | None,
        max_retries: int,
        budget: LlmBudget,
        clock: Callable[[], str],
    ):
        self._llm = llm
        self._cache = cache
        self._prompts = prompts
        self._checker = checker
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_output_tokens
        self._temperature = temperature
        self._effort = reasoning_effort
        self._retries = max_retries
        self.budget = budget
        self._clock = clock

    def with_budget(self, budget: LlmBudget) -> "ToolAssistant":
        clone = copy.copy(self)
        clone.budget = budget
        return clone

    def author(
        self,
        task: str,
        skeleton: Skeleton,
        fragment: Mapping[str, Any],
        *,
        previous: Mapping[str, Any] | None,
        taken_names: Collection[str],
        examples: list[Mapping[str, Any]],
    ) -> Authored:
        system, prompt_id = self._prompts.get(TASKS[task])
        payload = _payload(skeleton, fragment, previous, taken_names, examples)
        key = _cache_key(task, fragment, prompt_id, self._model, previous, examples, taken_names)
        cached = self._cache.llm_cached(key)
        if cached is not None:
            return Authored(raw=cached, origin="cache", calls=0, usage={})

        user = json.dumps(payload, ensure_ascii=False, indent=1)
        feedback = ""
        calls = 0
        usage = {"promptTokens": 0, "completionTokens": 0, "reasoningTokens": 0}
        problems: list[dict[str, str]] = []
        for _ in range(1 + self._retries):
            self.budget.take()
            try:
                result = self._llm.complete(LlmRequest(
                    system=system, user=user + feedback, output_schema=AUTHORING_SCHEMA, model=self._model,
                    timeout_seconds=self._timeout, max_output_tokens=self._max_tokens, temperature=self._temperature,
                    reasoning_effort=self._effort,
                ))
            except LlmError as e:
                if e.kind == "auth":
                    raise LlmAuthFailed(f"LLM 인증 실패: {e.message}") from None
                raise AssistFailed(ERROR_CODES.get(e.kind, "LLM_UNAVAILABLE"), e.message) from None
            # 클라이언트 안의 재시도도 호출 수에 넣는다
            extra = max(0, result.attempts - 1)
            for _ in range(extra):
                try:
                    self.budget.take()
                except AssistFailed:
                    break
            calls += result.attempts
            for k, v in result.usage.to_dict().items():
                usage[k] += v
            raw = self._merge(task, result.value, skeleton, previous)
            taken = set(taken_names) - ({previous["name"]} if previous else set())
            found = self._checker.check_authoring(skeleton, raw, taken_names=taken)
            if not found:
                self._cache.llm_store(key, task=task, model=result.model or self._model, prompt=prompt_id, output=raw,
                                      usage=usage, at=self._clock())
                return Authored(raw=raw, origin=ORIGIN[task], calls=calls, usage=usage)
            problems = [p.to_dict() for p in found]
            feedback = ("\n\n앞선 출력이 검사에 걸렸다. 고쳐서 다시 출력하라:\n"
                        + "\n".join(f"- {p['code']} {p['field']}: {p['message']}" for p in problems))
        raise AssistFailed("LLM_OUTPUT_INVALID", "검사를 통과한 출력을 받지 못했다", problems)

    @staticmethod
    def _merge(task: str, out: Mapping[str, Any], skeleton: Skeleton, previous: Mapping[str, Any] | None) -> dict:
        raw = copy.deepcopy(dict(out))
        if previous is None or task == "create":
            return raw
        prev = authoring_to_raw(extract_authoring(previous, skeleton))
        raw["name"] = prev["name"]  # 이름은 지킨다 (FR-226)
        if task == "redescribe":
            if "responseFields" in prev:
                raw["responseFields"] = prev["responseFields"]
            else:
                raw.pop("responseFields", None)
            for name, item in prev["inputs"].items():
                text = (raw.get("inputs", {}).get(name) or {}).get("description") or item["description"]
                raw.setdefault("inputs", {})[name] = {**{k: v for k, v in item.items() if k != "description"},
                                                      "description": text}
            return raw
        # update: 구조가 같은 칸은 이전 글을 그대로 옮긴다
        for name in _unchanged_inputs(previous, skeleton):
            if name in prev["inputs"]:
                raw.setdefault("inputs", {})[name] = prev["inputs"][name]
        return raw


def _unchanged_inputs(previous: Mapping[str, Any], skeleton: Skeleton) -> list[str]:
    props = previous.get("inputSchema", {}).get("properties", {})
    prev_bindings = {b["input"]: b for b in previous.get("http", {}).get("bindings", [])}
    prev_fixed = previous.get("http", {}).get("fixed", [])
    out = []
    for inp in skeleton.inputs:
        if inp.name in props:
            if prev_bindings.get(inp.name) != inp.binding():
                continue
            if _shape(props[inp.name]) == _shape(inp.schema):
                out.append(inp.name)
        elif any(f.get("in") == inp.location and (f.get("name") == inp.wire if inp.location != "body"
                                                    else f.get("pointer") == inp.pointer) for f in prev_fixed):
            out.append(inp.name)
    return out


def _shape(schema: Mapping[str, Any]) -> str:
    return canonical_json(strip_doc_keys({k: v for k, v in schema.items() if k != "default"}))


def _payload(skeleton, fragment, previous, taken_names, examples) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "operation": {"operationId": skeleton.operation_id, "method": skeleton.method, "path": skeleton.path,
                      "summary": fragment.get("summary", ""), "description": fragment.get("description", "")},
        "inputs": [{"name": i.name, "location": i.location, "required": i.required, "schema": i.schema,
                    "machineHidden": i.machine_hidden} for i in skeleton.inputs],
        "fixedByContext": [f for f in skeleton.http.get("fixed", []) if "context" in f],
        "responseFieldPaths": list(skeleton.response_paths),
        "existingToolNames": sorted(taken_names),
    }
    if previous is not None:
        payload["previous"] = _brief(previous)
    if examples:
        payload["examples"] = [_brief(e) for e in examples]
    return payload


def _brief(d: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("name", "title", "description", "inputSchema", "response", "index")
    out = {k: d[k] for k in keys if k in d}
    if "http" in d:
        out["fixed"] = d["http"].get("fixed", [])
    return out


def _cache_key(task, fragment, prompt_id, model, previous, examples, taken_names) -> str:
    body = {
        "task": task, "fragment": hashlib.sha256(canonical_json(fragment).encode()).hexdigest(), "prompt": prompt_id,
        "model": model, "previous": (previous or {}).get("revision"),
        "examples": [e.get("revision") for e in examples],
        "names": hashlib.sha256(canonical_json(sorted(taken_names)).encode()).hexdigest(),
    }
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


__all__ = ["AssistFailed", "Authored", "LlmBudget", "ToolAssistant", "NO_DEFAULT"]
