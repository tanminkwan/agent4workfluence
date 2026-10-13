"""OpenAI 호환 /v1/chat/completions (Phase 2 설계 10.1절, FR-253~255).

기본은 json_object 모드 + 스키마를 system에 넣고 우리 쪽에서 검사한다. 이 엔드포인트의 json_schema(strict)는
일부 입력에서 출력이 한도까지 이어졌다(부록_LLM모델선정 4.1). 사고 내용(reasoning_content)은 읽지 않는다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from .base import LlmClient, LlmError, LlmRequest, LlmResult, LlmUsage

log = logging.getLogger(__name__)
MODES = ("json_object", "json_schema")
FEEDBACK_MAX = 1500


class OpenAiCompatibleClient(LlmClient):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        response_format: str,
        max_retries: int,
        http: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if response_format not in MODES:
            raise ValueError(f"LLM_RESPONSE_FORMAT는 {MODES} 중 하나: {response_format}")
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._key = api_key
        self._mode = response_format
        self._attempts = 1 + max_retries
        self._http = http
        self._sleep = sleep

    def __repr__(self) -> str:  # key를 드러내지 않는다
        return f"OpenAiCompatibleClient(url={self._url!r}, mode={self._mode!r})"

    def complete(self, request: LlmRequest) -> LlmResult:
        validator = Draft202012Validator(request.output_schema)
        messages = [{"role": "system", "content": self._system(request)}, {"role": "user", "content": request.user}]
        last: LlmError | None = None
        started = time.monotonic()
        for attempt in range(1, self._attempts + 1):
            try:
                resp = self._http.post(self._url, json=self._body(request, messages),
                                       headers={"Authorization": f"Bearer {self._key}"}, timeout=request.timeout_seconds)
            except httpx.TimeoutException:
                last = LlmError("timeout", "LLM 응답 시간 초과", attempt)
                continue
            except httpx.TransportError as e:
                last = LlmError("unavailable", f"LLM 접속 실패: {type(e).__name__}", attempt)
                continue
            if resp.status_code in (401, 403):
                raise LlmError("auth", f"LLM 인증 실패 ({resp.status_code})", attempt)
            if resp.status_code == 429:
                last = LlmError("rate_limit", "LLM 호출 한도", attempt)
                wait = _retry_after(resp)
                if wait is not None and attempt < self._attempts:
                    self._sleep(wait)
                continue
            if resp.status_code >= 500 or resp.status_code != 200:
                last = LlmError("unavailable", f"LLM이 {resp.status_code}로 답했다", attempt)
                continue
            try:
                value, model, usage = self._parse(resp, validator)
            except _FormatProblem as p:
                last = LlmError("output_format", p.message, attempt)
                messages = messages[:2] + [{"role": "user", "content": request.user + _feedback(p.message)}]
                continue
            log.info("llm call ok: model=%s attempts=%d elapsed_ms=%d prompt=%d completion=%d", model, attempt,
                     int((time.monotonic() - started) * 1000), usage.prompt_tokens, usage.completion_tokens)
            return LlmResult(value=value, model=model, usage=usage, attempts=attempt)
        assert last is not None
        log.info("llm call failed: kind=%s attempts=%d", last.kind, last.attempts)
        raise last

    def _system(self, request: LlmRequest) -> str:
        if self._mode == "json_object":
            schema = json.dumps(request.output_schema, ensure_ascii=False)
            return f"{request.system}\n\n출력 JSON Schema (이 스키마를 따르는 JSON 객체 하나만 출력한다):\n{schema}"
        return request.system

    def _body(self, request: LlmRequest, messages: list[dict[str, str]]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": request.model, "messages": messages, "max_tokens": request.max_output_tokens}
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.reasoning_effort:
            body["reasoning_effort"] = request.reasoning_effort
        if self._mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        else:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "output", "schema": request.output_schema, "strict": True}}
        return body

    @staticmethod
    def _parse(resp: httpx.Response, validator: Draft202012Validator) -> tuple[dict[str, Any], str, LlmUsage]:
        try:
            doc = resp.json()
            choice = doc["choices"][0]
        except (ValueError, KeyError, IndexError):
            raise _FormatProblem("응답이 chat completion 모양이 아니다") from None
        if choice.get("finish_reason") != "stop":
            raise _FormatProblem(f"출력이 끝나지 않았다 (finish_reason={choice.get('finish_reason')}). 더 짧게 출력하라")
        content = (choice.get("message") or {}).get("content") or ""
        try:
            value = json.loads(content)
        except ValueError:
            raise _FormatProblem("출력이 JSON이 아니다. JSON 객체 하나만 출력하라") from None
        if not isinstance(value, dict):
            raise _FormatProblem("출력이 JSON 객체가 아니다")
        errors = sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path))
        if errors:
            raise _FormatProblem("; ".join(f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
                                           for e in errors[:5]))
        u = doc.get("usage") or {}
        usage = LlmUsage(prompt_tokens=u.get("prompt_tokens", 0), completion_tokens=u.get("completion_tokens", 0),
                         reasoning_tokens=(u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0))
        return value, doc.get("model", ""), usage


class _FormatProblem(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _feedback(message: str) -> str:
    return f"\n\n앞선 출력의 오류: {message[:FEEDBACK_MAX]}\n오류를 고쳐 스키마에 맞는 JSON 객체 하나만 다시 출력하라."


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return max(0.0, float(resp.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None


def from_settings(env, secrets) -> OpenAiCompatibleClient:
    http = httpx.Client(trust_env=True)  # proxy는 환경변수. LLM 주소는 NO_PROXY 밖
    return OpenAiCompatibleClient(
        base_url=secrets.require("LLM_BASE_URL"), api_key=secrets.require("LLM_API_KEY"),
        response_format=env.require("LLM_RESPONSE_FORMAT"), max_retries=env.require_int("LLM_MAX_RETRIES"), http=http,
    )
