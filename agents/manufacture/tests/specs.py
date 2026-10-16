"""시험 자료: 고정 명세 → Detection이 만드는 것과 같은 동작 조각, 카탈로그 규칙."""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from pathlib import Path

import yaml

from a4w_core.openapi.operations import collect_operations

FIXTURES = Path(__file__).parent / "fixtures" / "workfluence"
SEED_SPEC = "openapi-75ops-4283c9bf.yaml"     # 저장소 seed가 맞춘 명세 (설계 25.2). 75ops는 그 전 판(8c83f9c6)


@cache
def _ops(name: str) -> dict:
    doc = yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))
    return collect_operations(doc)


def fragments(name: str = "openapi-75ops.yaml") -> dict[str, dict]:
    """operationId → 조각 (Detection 변경분의 `after`와 같은 모양)."""
    return {f["operationId"]: f for f in _ops(name).values()}


def fragment(operation_id: str, name: str = "openapi-75ops.yaml") -> dict:
    return fragments(name)[operation_id]


def catalog_dir():
    return files("a4w_target_workfluence") / "catalog"


def rules() -> dict:
    return yaml.safe_load((catalog_dir() / "rules.yaml").read_text(encoding="utf-8"))
