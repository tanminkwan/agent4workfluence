"""시험 스택 실호출 (AC-1, AC-2). A4W_LIVE_TESTS=1 이고 타겟 설정이 있을 때만 돈다."""

import os
from pathlib import Path

import pytest

from a4w_core.builtin import register_builtins
from a4w_core.config import load_target_configs
from a4w_core.registry import Registry

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("A4W_LIVE_TESTS") != "1", reason="A4W_LIVE_TESTS=1 일 때만"),
]

TARGETS_DIR = Path(os.environ.get("DETECTION_TARGETS_DIR", ""))


@pytest.fixture
def context(tmp_path):
    from a4w_detection.wiring import build_context
    return build_context({
        "LOG_LEVEL": "INFO", "DETECTION_AUTH_MODE": "none",
        "DETECTION_SPEC_STORE_PATH": str(tmp_path / "store"),
        "DETECTION_TARGETS_DIR": str(TARGETS_DIR),
        "DETECTION_TEXT_DIFF_CONTEXT_LINES": "3", "TARGETS": "workfluence",
    })


def test_hash_url_matches_spec_body():
    reg = Registry(); register_builtins(reg)
    cfg = load_target_configs(TARGETS_DIR, ["workfluence"])["workfluence"]
    src = reg.create("spec_source", "hash_url", cfg)
    import hashlib
    assert src.fetch_hash() == hashlib.sha256(src.fetch_spec()).hexdigest()


def test_first_run_then_unchanged(context):
    first = context.detect.detect("workfluence")
    assert first.status == "changed" and first.from_version is None
    assert first.summary.added > 0 and first.summary.removed == 0
    cs = context.query.get("workfluence", first.change_set_id)
    assert all(o.change == "added" for o in cs.operations)
    second = context.detect.detect("workfluence")
    assert second.status == "unchanged" and second.current_version == first.to_version
