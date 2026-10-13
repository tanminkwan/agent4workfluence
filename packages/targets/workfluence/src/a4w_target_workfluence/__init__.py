"""Workfluence 타겟 어댑터. entry point `a4w.targets`로 등록된다.

- Detection(타겟 창구)이 쓰는 것: scope 확인, 오류 해석, 시험 환경
- manufacture가 쓰는 것: 카탈로그(대상 선정 규칙, seed, 시나리오, 예시 질의) — 데이터 파일이다
"""

from importlib.resources import files

from a4w_core.registry import Registry

from .errors import WorkfluenceErrorInterpreter
from .review_policy import WorkfluenceV1AdditivePolicy
from .sandbox import WorkfluenceSpaceSandbox
from .scopes import WorkfluenceJwtScopeProvider


def register(registry: Registry) -> None:
    registry.register("review_policy", "workfluence_v1_additive", lambda cfg: WorkfluenceV1AdditivePolicy())
    registry.register("scope_provider", "workfluence_jwt", lambda cfg: WorkfluenceJwtScopeProvider())
    registry.register("error_interpreter", "workfluence", lambda cfg: WorkfluenceErrorInterpreter())
    registry.register("verify_sandbox", "workfluence_space", WorkfluenceSpaceSandbox.from_config)
    # 카탈로그는 위치만 알려 준다. 해석은 manufacture가 한다 (어댑터가 manufacture를 모르게)
    registry.register("tool_catalog", "workfluence_v1", lambda cfg: files("a4w_target_workfluence") / "catalog")
