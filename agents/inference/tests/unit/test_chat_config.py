"""ChatSettings: 채팅 채널 설정 (Phase 5 설계 7절, NFR-C01, AC-C12).

기본값을 코드에 두지 않는다. CHAT_ENABLED가 없으면 기동하지 않고, false면 다른 CHAT_* 없이도 된다(채팅을 쓰지 않는 환경).
켜면 모든 값이 필요하고, 서로 맞지 않는 값(짧은 답 ≥ 서버 본문 상한, 보내기 속도 > 서버 한도 등)은 기동 때 거절한다.
"""

from __future__ import annotations

import pytest

from a4w_core.errors import ConfigError
from a4w_inference.channels.chat.config import ChatSettings

GOOD = {
    "CHAT_ENABLED": "true", "CHAT_AGENT_ID": "aiagent", "CHAT_TARGET_ID": "workfluence", "CHAT_API_ADAPTER": "workfluence",
    "CHAT_API_BASE_URL": "https://wf.test:8444/api/v1", "CHAT_TLS_CA_FILE": "/config/targets/ca.pem",
    "CHAT_MENTION_NAME": "AI Agent", "CHAT_WRITE_ALLOWED_USERS": "uid-alice, uid-bob",
    "CHAT_ANSWER_MAX_CHARS": "600", "CHAT_MAX_MESSAGE_CHARS": "4000", "CHAT_SERVER_SEND_PER_MINUTE": "60", "CHAT_SEND_PER_MINUTE": "50",
    "CHAT_SEND_RETRY_MAX": "3", "CHAT_SEND_RETRY_SECONDS": "5", "CHAT_SEND_RETRY_WAIT_MAX_SECONDS": "60",
    "CHAT_RECONNECT_INITIAL_SECONDS": "1", "CHAT_RECONNECT_MAX_SECONDS": "60", "CHAT_CONFLICT_LIMIT": "3",
    "CHAT_PING_INTERVAL_SECONDS": "20", "CHAT_PING_TIMEOUT_SECONDS": "20", "CHAT_OPEN_TIMEOUT_SECONDS": "10",
    "CHAT_HTTP_TIMEOUT_SECONDS": "15", "CHAT_CATCHUP_PAGE": "200", "CHAT_CATCHUP_MAX_AGE_SECONDS": "86400",
    "CHAT_POLL_INTERVAL_SECONDS": "0.5", "CHAT_WAIT_MAX_SECONDS": "420", "CHAT_ROOM_QUEUE_MAX": "5",
    "CHAT_BUSY_RETRY_SECONDS": "2", "CHAT_BUSY_RETRY_MAX_SECONDS": "120", "CHAT_REPLIES_FILE": "/config/prompts/chat_replies.yaml",
    "CHAT_NOTES_FILE": "/config/prompts/chat_notes.md", "CHAT_STATE_PATH": "/var/lib/a4w/inference/chat.db", "CHAT_RETENTION_DAYS": "30",
    "CHAT_SECRETS_DIR": "/config/secrets/chat",
}
REQUIRED = [k for k in GOOD if k not in ("CHAT_TLS_CA_FILE", "CHAT_WRITE_ALLOWED_USERS", "CHAT_NOTES_FILE")]


def env(**over):
    d = dict(GOOD)
    d.update(over)
    return d


def test_C1_a_full_environment_is_read_into_typed_settings():
    s = ChatSettings.from_environ(GOOD)
    assert s.enabled is True and s.agent_id == "aiagent" and s.target_id == "workfluence" and s.api_adapter == "workfluence"
    assert s.api_base_url == "https://wf.test:8444/api/v1" and s.tls_ca_file == "/config/targets/ca.pem"
    assert s.mention_name == "AI Agent" and s.write_allowed_users == ("uid-alice", "uid-bob")
    assert (s.answer_max_chars, s.max_message_chars, s.server_send_per_minute, s.send_per_minute) == (600, 4000, 60, 50)
    assert (s.send_retry_max, s.send_retry_seconds, s.send_retry_wait_max_seconds) == (3, 5.0, 60.0)
    assert (s.reconnect_initial_seconds, s.reconnect_max_seconds, s.conflict_limit) == (1.0, 60.0, 3)
    assert (s.ping_interval_seconds, s.ping_timeout_seconds, s.open_timeout_seconds, s.http_timeout_seconds) == (20.0, 20.0, 10.0, 15.0)
    assert (s.catchup_page, s.catchup_max_age_seconds, s.poll_interval_seconds, s.wait_max_seconds) == (200, 86400.0, 0.5, 420.0)
    assert (s.room_queue_max, s.busy_retry_seconds, s.busy_retry_max_seconds) == (5, 2.0, 120.0)
    assert (s.replies_file, s.notes_file, s.state_path, s.retention_days, s.secrets_dir) == (
        "/config/prompts/chat_replies.yaml", "/config/prompts/chat_notes.md", "/var/lib/a4w/inference/chat.db", 30, "/config/secrets/chat")


def test_C2_chat_enabled_has_no_default():
    with pytest.raises(ConfigError) as e:
        ChatSettings.from_environ({})
    assert "CHAT_ENABLED" in str(e.value)


@pytest.mark.parametrize("raw", ["yes", "1", "TRUE ", "", "on", "enabled"])
def test_C3_chat_enabled_accepts_only_true_or_false(raw):
    if raw.strip().lower() in ("true", "false") and raw == raw.strip():
        return
    with pytest.raises(ConfigError):
        ChatSettings.from_environ({"CHAT_ENABLED": raw})


def test_C4_disabled_needs_nothing_else_and_ignores_broken_values():
    s = ChatSettings.from_environ({"CHAT_ENABLED": "false"})
    assert s.enabled is False
    s2 = ChatSettings.from_environ({"CHAT_ENABLED": "false", "CHAT_SEND_PER_MINUTE": "많이", "CHAT_API_BASE_URL": "???"})
    assert s2.enabled is False


@pytest.mark.parametrize("key", REQUIRED)
def test_C5_every_required_value_is_checked_and_named_in_the_error(key):
    d = env()
    del d[key]
    with pytest.raises(ConfigError) as e:
        ChatSettings.from_environ(d)
    assert key in str(e.value)


@pytest.mark.parametrize("key", [k for k in REQUIRED if k not in ("CHAT_AGENT_ID", "CHAT_TARGET_ID", "CHAT_API_ADAPTER", "CHAT_MENTION_NAME",
                                                                  "CHAT_API_BASE_URL", "CHAT_REPLIES_FILE", "CHAT_STATE_PATH", "CHAT_SECRETS_DIR")])
def test_C6_numeric_values_reject_text(key):
    with pytest.raises(ConfigError) as e:
        ChatSettings.from_environ(env(**{key: "많이"}))
    assert key in str(e.value)


def test_C7_optional_values_may_be_absent():
    d = env()
    for key in ("CHAT_TLS_CA_FILE", "CHAT_WRITE_ALLOWED_USERS", "CHAT_NOTES_FILE"):
        del d[key]
    s = ChatSettings.from_environ(d)
    assert s.tls_ca_file is None and s.write_allowed_users == () and s.notes_file is None


@pytest.mark.parametrize("raw,want", [("", ()), ("  ", ()), ("a", ("a",)), ("a,b", ("a", "b")), (" a , b ,, c ", ("a", "b", "c")), ("a,a", ("a",))])
def test_C8_write_allowed_users_are_parsed_as_a_deduplicated_list(raw, want):
    assert ChatSettings.from_environ(env(CHAT_WRITE_ALLOWED_USERS=raw)).write_allowed_users == want


@pytest.mark.parametrize("over", [
    {"CHAT_ANSWER_MAX_CHARS": "4000"}, {"CHAT_ANSWER_MAX_CHARS": "5000"}, {"CHAT_ANSWER_MAX_CHARS": "0"},       # 짧은 답 < 서버 본문 상한
    {"CHAT_SEND_PER_MINUTE": "61"}, {"CHAT_SEND_PER_MINUTE": "0"},                                              # 서버 한도 이하
    {"CHAT_SERVER_SEND_PER_MINUTE": "0"},
    {"CHAT_RECONNECT_INITIAL_SECONDS": "90"},                                                                    # 처음 ≤ 최대
    {"CHAT_RECONNECT_INITIAL_SECONDS": "0"}, {"CHAT_CONFLICT_LIMIT": "0"}, {"CHAT_CATCHUP_PAGE": "0"}, {"CHAT_CATCHUP_PAGE": "201"},
    {"CHAT_ROOM_QUEUE_MAX": "0"}, {"CHAT_POLL_INTERVAL_SECONDS": "0"}, {"CHAT_WAIT_MAX_SECONDS": "0"},
    {"CHAT_BUSY_RETRY_SECONDS": "0"}, {"CHAT_BUSY_RETRY_MAX_SECONDS": "1"},                                       # 간격 ≤ 최대
    {"CHAT_PING_INTERVAL_SECONDS": "0"}, {"CHAT_PING_TIMEOUT_SECONDS": "0"}, {"CHAT_OPEN_TIMEOUT_SECONDS": "0"},
    {"CHAT_HTTP_TIMEOUT_SECONDS": "0"}, {"CHAT_SEND_RETRY_MAX": "-1"}, {"CHAT_SEND_RETRY_WAIT_MAX_SECONDS": "0"},
    {"CHAT_RETENTION_DAYS": "0"}, {"CHAT_CATCHUP_MAX_AGE_SECONDS": "0"}, {"CHAT_MAX_MESSAGE_CHARS": "0"},
])
def test_C9_values_that_do_not_fit_together_are_refused_at_startup(over):
    with pytest.raises(ConfigError) as e:
        ChatSettings.from_environ(env(**over))
    assert any(k in str(e.value) for k in over)


@pytest.mark.parametrize("raw", ["   ", ""])
def test_C10_a_blank_mention_name_is_refused(raw):
    with pytest.raises(ConfigError):
        ChatSettings.from_environ(env(CHAT_MENTION_NAME=raw))


@pytest.mark.parametrize("raw", ["wf.internal/api/v1", "ftp://wf/api/v1", "ws://wf/api/v1"])
def test_C11_the_api_base_url_must_be_http_or_https(raw):
    with pytest.raises(ConfigError) as e:
        ChatSettings.from_environ(env(CHAT_API_BASE_URL=raw))
    assert "CHAT_API_BASE_URL" in str(e.value)


def test_C12_the_wait_for_a_result_must_cover_the_engine_time_limit():
    """수행 상한(INFERENCE_MAX_SECONDS)보다 짧게 기다리면 정상 결과를 시간 초과로 버린다. 조립 때 함께 검사한다."""
    s = ChatSettings.from_environ(env(CHAT_WAIT_MAX_SECONDS="100"))
    with pytest.raises(ConfigError) as e:
        s.check_against_engine(engine_max_seconds=300.0)
    assert "CHAT_WAIT_MAX_SECONDS" in str(e.value)
    ChatSettings.from_environ(env(CHAT_WAIT_MAX_SECONDS="420")).check_against_engine(engine_max_seconds=300.0)


def test_C13_settings_are_immutable():
    s = ChatSettings.from_environ(GOOD)
    with pytest.raises(Exception):
        s.agent_id = "x"  # type: ignore[misc]
