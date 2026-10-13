"""공통 tool 실행기 (설계 15절). tool마다 코드가 없다. 정의를 읽어 실행한다."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator

from .call_policy import CallPolicy
from .composite import run_composite
from .errors import INPUT_INVALID, UNSUPPORTED, ToolError, ToolFailure, ToolResult
from .interfaces import ErrorInterpreter, Handler
from .request import build_request
from .response import shape
from .target_http import TargetHttpClient

DEFAULT_MAX_CHARS = 8000


class ToolExecutor:
    def __init__(
        self,
        *,
        http: TargetHttpClient,
        policy: CallPolicy,
        interpreter: ErrorInterpreter,
        handlers: Mapping[str, Handler] | None = None,
        default_max_chars: int = DEFAULT_MAX_CHARS,
    ):
        self._http = http
        self._policy = policy
        self._interpreter = interpreter
        self._handlers = dict(handlers or {})
        self._default_max_chars = default_max_chars

    def execute(
        self,
        definition: Mapping[str, Any],
        args: Mapping[str, Any],
        *,
        toolset: Mapping[str, Mapping[str, Any]] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """context: agent의 고정 맥락 값 (예: homeSpace). 실행하는 쪽 설정에서 온다."""
        problems = input_errors(definition, args)
        if problems:
            return ToolResult.failure(
                ToolError(INPUT_INVALID, "입력이 tool의 입력 스키마에 맞지 않는다", details=problems,
                          hint="details의 칸을 고쳐 다시 하라")
            )
        kind = definition.get("kind")
        if kind == "operation":
            return self._operation(definition, args, context)
        if kind == "composite":
            if toolset is None:
                return ToolResult.failure(ToolError(UNSUPPORTED, "상위 tool은 tool 묶음과 함께 실행해야 한다"))
            return run_composite(self, definition, args, toolset, context)
        if kind == "handler":
            handler = self._handlers.get(definition.get("handler", ""))
            if handler is None:
                return ToolResult.failure(ToolError(UNSUPPORTED, f"등록되지 않은 handler: {definition.get('handler')}"))
            return handler.run(args, self)
        return ToolResult.failure(ToolError(UNSUPPORTED, f"모르는 tool 종류: {kind}"))

    def _operation(self, definition: Mapping[str, Any], args: Mapping[str, Any], context) -> ToolResult:
        started = time.monotonic()
        try:
            req = build_request(definition, args, context=context)
        except ToolFailure as f:
            return ToolResult.failure(f.error)
        outcome = self._policy.run(lambda: self._http.send(req), write=req.is_write)
        meta: dict[str, Any] = {"attempts": outcome.attempts}
        resp = outcome.response
        if resp is not None:
            meta["status"] = resp.status_code
        meta["elapsedMs"] = int((time.monotonic() - started) * 1000)
        if outcome.error is not None:
            return ToolResult(ok=False, error=outcome.error, meta=meta)
        if not _is_success(resp.status_code, definition["http"].get("success")):
            error = self._interpreter.interpret(resp.status_code, resp.headers, resp.content)
            if error.request_id:
                meta["requestId"] = error.request_id
            return ToolResult(ok=False, error=error, meta=meta)
        rule = definition.get("response", {})
        shaped = shape(_body(resp), rule.get("fields"), rule.get("maxChars", self._default_max_chars))
        if shaped.original_count is not None:
            meta["originalCount"] = shaped.original_count
        return ToolResult(ok=True, data=shaped.data, truncated=shaped.truncated, meta=meta)


def input_errors(definition: Mapping[str, Any], args: Mapping[str, Any]) -> list[dict[str, str]]:
    validator = Draft202012Validator(definition.get("inputSchema", {"type": "object"}))
    return [
        {"field": "/".join(str(p) for p in e.absolute_path) or "(root)", "message": e.message}
        for e in sorted(validator.iter_errors(dict(args)), key=lambda e: list(e.absolute_path))
    ]


def _is_success(status: int, success: list[int] | None) -> bool:
    return status in success if success else 200 <= status < 300


def _body(resp) -> Any:
    if not resp.content:
        return None
    try:
        return json.loads(resp.content)
    except (ValueError, UnicodeDecodeError):
        return resp.text
