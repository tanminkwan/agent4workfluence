"""채팅 채널 설정 읽기 (Phase 5 설계 7절, NFR-C01). 기본값을 코드에 두지 않는다. 없거나 맞지 않으면 기동하지 않는다(ConfigError).

CHAT_ENABLED=false면 다른 CHAT_* 없이도 읽힌다(채팅을 쓰지 않는 환경은 Phase 4와 같게 동작한다).
오류 메시지에는 변수 이름과 파일 경로만 쓰고 값은 쓰지 않는다(token 보호). 채팅 token은 MCP 자식이 읽는 agent token과 다른 파일이다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from a4w_core.config import Settings, parse_env_text
from a4w_core.errors import ConfigError

from .worker import REQUIRED_TEXTS, ReplyTexts

SERVER_MESSAGES_PAGE_MAX = 200          # 채팅 API 계약: messages는 한 번에 200개까지만 준다(openapi.yaml chat.messages)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _int(env: Settings, key: str, *, minimum: int = 1, maximum: int | None = None) -> int:
    value = env.require_int(key)
    if value < minimum or (maximum is not None and value > maximum):
        raise ConfigError(f"{minimum} 이상{f' {maximum} 이하' if maximum is not None else ''}이어야 한다: {key}")
    return value


def _float(env: Settings, key: str, *, positive: bool = True) -> float:
    try:
        value = float(env.require(key))
    except ValueError:
        raise ConfigError(f"실수가 아니다: {key}") from None
    if (positive and value <= 0) or (not positive and value < 0):
        raise ConfigError(f"{'0보다 커야' if positive else '0 이상이어야'} 한다: {key}")
    return value


@dataclass(frozen=True)
class ChatSettings:
    enabled: bool
    agent_id: str = ""
    target_id: str = ""
    api_adapter: str = ""
    api_base_url: str = ""
    tls_ca_file: str | None = None
    mention_name: str = ""
    write_allowed_users: tuple[str, ...] = ()
    answer_max_chars: int = 0
    max_message_chars: int = 0
    server_send_per_minute: int = 0
    send_per_minute: int = 0
    send_retry_max: int = 0
    send_retry_seconds: float = 0.0
    send_retry_wait_max_seconds: float = 0.0
    reconnect_initial_seconds: float = 0.0
    reconnect_max_seconds: float = 0.0
    conflict_limit: int = 0
    ping_interval_seconds: float = 0.0
    ping_timeout_seconds: float = 0.0
    open_timeout_seconds: float = 0.0
    http_timeout_seconds: float = 0.0
    catchup_page: int = 0
    catchup_max_age_seconds: float = 0.0
    poll_interval_seconds: float = 0.0
    wait_max_seconds: float = 0.0
    room_queue_max: int = 0
    busy_retry_seconds: float = 0.0
    busy_retry_max_seconds: float = 0.0
    replies_file: str = ""
    notes_file: str | None = None
    state_path: str = ""
    retention_days: int = 0
    secrets_dir: str = ""

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> ChatSettings:
        env = Settings(environ, origin="environment")
        flag = env.require("CHAT_ENABLED")
        if flag not in ("true", "false"):
            raise ConfigError("CHAT_ENABLED는 true 또는 false여야 한다")
        if flag == "false":
            return cls(enabled=False)
        mention = env.require("CHAT_MENTION_NAME")
        if not mention.strip():
            raise ConfigError("CHAT_MENTION_NAME이 비었다")
        base = env.require("CHAT_API_BASE_URL")
        parts = urlsplit(base)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ConfigError("CHAT_API_BASE_URL은 http 또는 https 주소여야 한다")
        answer = _int(env, "CHAT_ANSWER_MAX_CHARS")
        max_message = _int(env, "CHAT_MAX_MESSAGE_CHARS")
        if answer >= max_message:
            raise ConfigError("CHAT_ANSWER_MAX_CHARS는 CHAT_MAX_MESSAGE_CHARS보다 작아야 한다(안내 줄이 들어갈 자리)")
        server_rate = _int(env, "CHAT_SERVER_SEND_PER_MINUTE")
        rate = _int(env, "CHAT_SEND_PER_MINUTE")
        if rate > server_rate:
            raise ConfigError("CHAT_SEND_PER_MINUTE는 CHAT_SERVER_SEND_PER_MINUTE 이하여야 한다")
        initial, longest = _float(env, "CHAT_RECONNECT_INITIAL_SECONDS"), _float(env, "CHAT_RECONNECT_MAX_SECONDS")
        if initial > longest:
            raise ConfigError("CHAT_RECONNECT_INITIAL_SECONDS는 CHAT_RECONNECT_MAX_SECONDS 이하여야 한다")
        busy, busy_max = _float(env, "CHAT_BUSY_RETRY_SECONDS"), _float(env, "CHAT_BUSY_RETRY_MAX_SECONDS")
        if busy > busy_max:
            raise ConfigError("CHAT_BUSY_RETRY_SECONDS는 CHAT_BUSY_RETRY_MAX_SECONDS 이하여야 한다")
        users = tuple(dict.fromkeys(u.strip() for u in (env.optional("CHAT_WRITE_ALLOWED_USERS") or "").split(",") if u.strip()))
        return cls(
            enabled=True, agent_id=env.require("CHAT_AGENT_ID"), target_id=env.require("CHAT_TARGET_ID"),
            api_adapter=env.require("CHAT_API_ADAPTER"), api_base_url=base, tls_ca_file=env.optional("CHAT_TLS_CA_FILE"),
            mention_name=mention, write_allowed_users=users, answer_max_chars=answer, max_message_chars=max_message,
            server_send_per_minute=server_rate, send_per_minute=rate, send_retry_max=_int(env, "CHAT_SEND_RETRY_MAX", minimum=0),
            send_retry_seconds=_float(env, "CHAT_SEND_RETRY_SECONDS", positive=False),
            send_retry_wait_max_seconds=_float(env, "CHAT_SEND_RETRY_WAIT_MAX_SECONDS"),
            reconnect_initial_seconds=initial, reconnect_max_seconds=longest, conflict_limit=_int(env, "CHAT_CONFLICT_LIMIT"),
            ping_interval_seconds=_float(env, "CHAT_PING_INTERVAL_SECONDS"), ping_timeout_seconds=_float(env, "CHAT_PING_TIMEOUT_SECONDS"),
            open_timeout_seconds=_float(env, "CHAT_OPEN_TIMEOUT_SECONDS"), http_timeout_seconds=_float(env, "CHAT_HTTP_TIMEOUT_SECONDS"),
            catchup_page=_int(env, "CHAT_CATCHUP_PAGE", maximum=SERVER_MESSAGES_PAGE_MAX),
            catchup_max_age_seconds=_float(env, "CHAT_CATCHUP_MAX_AGE_SECONDS"),
            poll_interval_seconds=_float(env, "CHAT_POLL_INTERVAL_SECONDS"), wait_max_seconds=_float(env, "CHAT_WAIT_MAX_SECONDS"),
            room_queue_max=_int(env, "CHAT_ROOM_QUEUE_MAX"), busy_retry_seconds=busy, busy_retry_max_seconds=busy_max,
            replies_file=env.require("CHAT_REPLIES_FILE"), notes_file=env.optional("CHAT_NOTES_FILE"),
            state_path=env.require("CHAT_STATE_PATH"), retention_days=_int(env, "CHAT_RETENTION_DAYS"),
            secrets_dir=env.require("CHAT_SECRETS_DIR"),
        )

    def check_against_engine(self, *, engine_max_seconds: float) -> None:
        """수행 상한보다 짧게 기다리면 정상 결과를 시간 초과로 버린다."""
        if self.wait_max_seconds < engine_max_seconds:
            raise ConfigError("CHAT_WAIT_MAX_SECONDS는 수행 시간 상한(INFERENCE_MAX_SECONDS) 이상이어야 한다")


# ── 파일 ──────────────────────────────────────────────────────────────────────

def load_chat_token(secrets_dir: Path | str, agent_id: str, target_id: str) -> str:
    """<secrets_dir>/<agent_id>/<target_id>.env의 CHAT_API_TOKEN. agent token 파일로 대신하지 않는다."""
    for name, value in (("agentId", agent_id), ("targetId", target_id)):
        if not _ID.match(value or ""):
            raise ConfigError(f"채팅 token 파일 경로에 쓸 수 없는 {name}다")
    path = Path(secrets_dir) / agent_id / f"{target_id}.env"
    try:
        values = parse_env_text(path.read_text(encoding="utf-8"))
    except OSError:
        raise ConfigError(f"채팅 token 파일을 읽을 수 없다: {path}") from None
    token = values.get("CHAT_API_TOKEN", "").strip()
    if not token:
        raise ConfigError(f"CHAT_API_TOKEN이 없다: {path}")
    return token


@dataclass(frozen=True)
class ChatReplies:
    texts: ReplyTexts
    truncate_notice: str


def load_chat_replies(path: Path | str) -> ChatReplies:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        raise ConfigError(f"채팅 응답 문구 파일을 읽을 수 없다: {path}") from None
    except yaml.YAMLError:
        raise ConfigError(f"채팅 응답 문구 파일이 YAML이 아니다: {path}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"채팅 응답 문구 파일은 texts·stop_labels·truncate_notice를 가진 목록이어야 한다: {path}")
    texts, labels, notice = data.get("texts"), data.get("stop_labels") or {}, data.get("truncate_notice")
    if not isinstance(texts, dict) or not isinstance(labels, dict):
        raise ConfigError(f"texts와 stop_labels는 이름: 문구 목록이어야 한다: {path}")
    if not isinstance(notice, str) or not notice.strip():
        raise ConfigError(f"truncate_notice가 없다: {path}")
    for key in REQUIRED_TEXTS:
        if not isinstance(texts.get(key), str) or not texts[key].strip():
            raise ConfigError(f"채팅 응답 문구가 없다: {key} ({path})")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
        raise ConfigError(f"stop_labels의 이름과 문구는 글이어야 한다: {path}")
    return ChatReplies(texts=ReplyTexts(texts, stop_labels=labels), truncate_notice=notice.strip())


def load_chat_notes(path: Path | str | None) -> str | None:
    if path is None:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        raise ConfigError(f"채팅 프롬프트 덧붙임 파일을 읽을 수 없다: {path}") from None
    return text or None
