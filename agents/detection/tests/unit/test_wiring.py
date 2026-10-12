import pytest

from a4w_core.errors import ConfigError
from a4w_detection.wiring import build_context

TARGET = """TARGET_ADAPTER=workfluence
SPEC_SOURCE=hash_url
SPEC_FORMAT=yaml
SPEC_NORMALIZER=identity
SPEC_URL=https://t/openapi.yaml
SPEC_HASH_URL=https://t/openapi.sha256
SPEC_MAX_BYTES=1000
HTTP_TIMEOUT_SECONDS=5
TLS_VERIFY=true
REVIEW_POLICY=workfluence_v1_additive
"""


@pytest.fixture
def env(tmp_path):
    (tmp_path / "targets").mkdir()
    (tmp_path / "targets" / "workfluence.env").write_text(TARGET)
    return {
        "LOG_LEVEL": "INFO",
        "DETECTION_AUTH_MODE": "none",
        "DETECTION_SPEC_STORE_PATH": str(tmp_path / "store"),
        "DETECTION_TARGETS_DIR": str(tmp_path / "targets"),
        "DETECTION_TEXT_DIFF_CONTEXT_LINES": "3",
        "TARGETS": "workfluence",
    }


def test_builds_from_environment(env):
    ctx = build_context(env)
    assert ctx.auth_mode == "none"
    ctx.store.check()


@pytest.mark.parametrize("key", ["LOG_LEVEL", "DETECTION_AUTH_MODE", "DETECTION_SPEC_STORE_PATH",
                                 "DETECTION_TARGETS_DIR", "DETECTION_TEXT_DIFF_CONTEXT_LINES", "TARGETS"])
def test_missing_setting_fails_fast(env, key):
    del env[key]
    with pytest.raises(ConfigError):
        build_context(env)


def test_bearer_requires_token(env):
    env["DETECTION_AUTH_MODE"] = "bearer"
    with pytest.raises(ConfigError):
        build_context(env)
    env["DETECTION_API_TOKEN"] = "x"
    assert build_context(env).api_token == "x"


def test_bad_auth_mode(env):
    env["DETECTION_AUTH_MODE"] = "basic"
    with pytest.raises(ConfigError):
        build_context(env)


def test_unknown_adapter_component_fails(env, tmp_path):
    (tmp_path / "targets" / "workfluence.env").write_text(TARGET.replace("REVIEW_POLICY=workfluence_v1_additive", "REVIEW_POLICY=nope"))
    with pytest.raises(ConfigError):
        build_context(env)


def test_missing_target_value_fails(env, tmp_path):
    (tmp_path / "targets" / "workfluence.env").write_text(TARGET.replace("SPEC_URL=https://t/openapi.yaml\n", ""))
    with pytest.raises(ConfigError):
        build_context(env)


def test_placeholder_source_builds_but_fails_on_use(env, tmp_path):
    from a4w_core.errors import UnsupportedSpecSource
    (tmp_path / "targets" / "workfluence.env").write_text(TARGET.replace("SPEC_SOURCE=hash_url", "SPEC_SOURCE=no_hash"))
    ctx = build_context(env)
    with pytest.raises(UnsupportedSpecSource):
        ctx.detect.detect("workfluence")
