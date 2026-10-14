"""타겟 호출 부품 조립: 타겟 설정 + 비밀(주소·token) → HTTP client, 호출 규약, 실행기.

실행하는 쪽(Detection 실호출 검증, 공용 MCP server)이 같은 조립을 쓴다(Phase 3 설계 8.2절).
오류 해석기는 타겟 설정의 이름(CALL_ERROR_INTERPRETER)으로 registry에서 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from a4w_core.registry import Registry

from .call_policy import ConfigCallPolicy
from .executor import ToolExecutor
from .target_http import TargetHttpClient


@dataclass(frozen=True)
class TargetCaller:
    http: TargetHttpClient
    policy: ConfigCallPolicy
    executor: ToolExecutor


def build_target_caller(registry: Registry, target_cfg, secrets, *,
                        transport: httpx.BaseTransport | None = None) -> TargetCaller:
    """transport는 시험용(가짜 타겟)이다."""
    http = TargetHttpClient.from_settings(target_cfg, secrets, transport=transport)
    policy = ConfigCallPolicy.from_settings(target_cfg)
    interpreter = registry.create("error_interpreter", target_cfg.require("CALL_ERROR_INTERPRETER"), target_cfg)
    return TargetCaller(http=http, policy=policy, executor=ToolExecutor(http=http, policy=policy, interpreter=interpreter))
