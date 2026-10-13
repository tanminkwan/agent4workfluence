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
        "DETECTION_SECRETS_DIR": str(tmp_path / "secrets"),
        "DETECTION_VERIFY_MAX_TOOLS": "50",
        "DETECTION_VERIFY_RESULT_DAYS": "30",
    }


def test_builds_from_environment(env):
    ctx = build_context(env)
    assert ctx.auth_mode == "none"
    ctx.store.check()


@pytest.mark.parametrize("key", ["LOG_LEVEL", "DETECTION_AUTH_MODE", "DETECTION_SPEC_STORE_PATH",
                                 "DETECTION_TARGETS_DIR", "DETECTION_TEXT_DIFF_CONTEXT_LINES", "TARGETS",
                                 "DETECTION_SECRETS_DIR", "DETECTION_VERIFY_MAX_TOOLS", "DETECTION_VERIFY_RESULT_DAYS"])
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


# ── Phase 2 보강: 타겟 비밀이 있으면 검증·타겟 정보가 켜진다

VERIFY = """TARGET_SCOPE_PROVIDER=workfluence_jwt
TARGET_API_PATH_PREFIX=/api/v1
CALL_READ_PER_MINUTE=120
CALL_WRITE_PER_MINUTE=30
CALL_MAX_ATTEMPTS=3
CALL_MAX_RETRY_WAIT_SECONDS=60
CALL_TIMEOUT_SECONDS=30
CALL_ERROR_INTERPRETER=workfluence
VERIFY_SANDBOX=workfluence_space
VERIFY_SPACE_PREFIX=aiagent-manufacture-
VERIFY_DENY_SCOPES=admin
"""
SECRETS = "TARGET_API_BASE_URL=https://wf.test/api/v1\nTARGET_API_TOKEN=SECRET.TOKEN.VALUE\n"


def with_secrets(env, tmp_path, target=TARGET + VERIFY, secrets=SECRETS):
    (tmp_path / "targets" / "workfluence.env").write_text(target)
    (tmp_path / "secrets").mkdir(exist_ok=True)
    (tmp_path / "secrets" / "workfluence.env").write_text(secrets)
    return build_context(env)


def test_without_secrets_verification_is_off(env):
    ctx = build_context(env)
    assert ctx.verify is not None and ctx.verify.runtimes == {}
    assert ctx.profile.profile("workfluence")["verification"]["available"] is False


def test_with_secrets_verification_is_on(env, tmp_path):
    ctx = with_secrets(env, tmp_path)
    rt = ctx.verify.runtimes["workfluence"]
    assert rt.sandbox_name == "workfluence_space"
    assert rt.deny_scopes == frozenset({"admin"})
    assert "pageId" in rt.context_keys
    assert "SECRET.TOKEN.VALUE" not in repr(rt.runner.__dict__)


@pytest.mark.parametrize("key", ["TARGET_SCOPE_PROVIDER", "CALL_WRITE_PER_MINUTE", "VERIFY_SANDBOX",
                                 "VERIFY_SPACE_PREFIX", "VERIFY_DENY_SCOPES", "CALL_ERROR_INTERPRETER"])
def test_with_secrets_missing_verify_setting_fails(env, tmp_path, key):
    target = "\n".join(line for line in (TARGET + VERIFY).splitlines() if not line.startswith(key + "="))
    with pytest.raises(ConfigError):
        with_secrets(env, tmp_path, target=target)


def test_with_secrets_missing_token_fails(env, tmp_path):
    with pytest.raises(ConfigError):
        with_secrets(env, tmp_path, secrets="TARGET_API_BASE_URL=https://wf.test/api/v1\n")


def test_spec_operations_empty_before_first_detect(env, tmp_path):
    ctx = with_secrets(env, tmp_path)
    assert ctx.verify._spec_operations("workfluence") == set()
