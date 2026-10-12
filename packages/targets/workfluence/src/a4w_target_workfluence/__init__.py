"""Workfluence 타겟 어댑터. entry point `a4w.targets`로 등록된다."""

from a4w_core.registry import Registry

from .review_policy import WorkfluenceV1AdditivePolicy


def register(registry: Registry) -> None:
    registry.register("review_policy", "workfluence_v1_additive", lambda cfg: WorkfluenceV1AdditivePolicy())
