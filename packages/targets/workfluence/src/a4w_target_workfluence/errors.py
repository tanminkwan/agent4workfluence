"""Workfluence 오류 해석: `{"error":{"code","message","requestId"}}`에 코드별 안내문을 붙인다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from functools import cache
from importlib.resources import files

import yaml

from a4w_tools.errors import ToolError
from a4w_tools.interfaces import ErrorInterpreter, GenericErrorInterpreter

# 사람이 풀어야 하는 오류는 다시 해도 같다
NOT_RETRYABLE = frozenset({
    "TOKEN_MISSING", "TOKEN_INVALID", "TOKEN_UNKNOWN", "TOKEN_EXPIRED", "TOKEN_REVOKED",
    "ACCOUNT_INACTIVE", "PASSWORD_CHANGE_REQUIRED", "API_DISABLED",
})


@cache
def _hints() -> dict:
    return yaml.safe_load(files("a4w_target_workfluence").joinpath("catalog", "hints.yaml").read_text(encoding="utf-8"))


class WorkfluenceErrorInterpreter(ErrorInterpreter):
    def __init__(self) -> None:
        self._generic = GenericErrorInterpreter()

    def interpret(self, status: int, headers: Mapping[str, str], body: bytes) -> ToolError:
        err = self._generic.interpret(status, headers, body)
        hints = _hints()
        hint = hints["codes"].get(err.target_code or "") or hints["status"].get(str(status))
        retryable = err.retryable and err.target_code not in NOT_RETRYABLE
        return replace(err, hint=hint, retryable=retryable)
