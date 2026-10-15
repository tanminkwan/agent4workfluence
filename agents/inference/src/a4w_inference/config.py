"""설정 읽기 (Phase 4 설계 4절). 기본값을 코드에 두지 않는다. 없으면 기동하지 않는다(ConfigError)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from a4w_core.config import Settings, parse_env_text
from a4w_core.errors import ConfigError

from .engine.engine import EngineSettings
from .engine.marks import AuthorshipMarker

AUTH_MODES = ("none", "bearer")


def _positive_int(env: Settings, key: str, *, minimum: int = 1) -> int:
    value = env.require_int(key)
    if value < minimum:
        raise ConfigError(f"{minimum} 이상이어야 한다: {key}")
    return value


def _float(env: Settings, key: str) -> float:
    try:
        return float(env.require(key))
    except ValueError:
        raise ConfigError(f"실수가 아니다: {key}") from None


def _optional_float(env: Settings, key: str) -> float | None:
    raw = env.optional(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"실수가 아니다: {key}") from None


@dataclass(frozen=True)
class InferenceSettings:
    port: int
    auth_mode: str
    store_path: str
    agents: tuple[str, ...]
    agents_dir: str
    targets_dir: str
    timezone: ZoneInfo
    text_max_chars: int
    requester_max_chars: int
    max_concurrent: int
    retention_days: int
    purge_interval_seconds: int
    search_top_k: int
    search_min_score: float
    llm_max_retries: int
    ai_mark_suffix: str
    engine: EngineSettings
    api_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> InferenceSettings:
        env = Settings(environ, origin="environment")
        auth = env.require("INFERENCE_AUTH_MODE")
        if auth not in AUTH_MODES:
            raise ConfigError(f"INFERENCE_AUTH_MODE는 {AUTH_MODES} 중 하나: {auth}")
        token = env.require("INFERENCE_API_TOKEN") if auth == "bearer" else None
        try:
            tz = ZoneInfo(env.require("INFERENCE_TIMEZONE"))
        except ZoneInfoNotFoundError:
            raise ConfigError("알 수 없는 시간대: INFERENCE_TIMEZONE") from None
        ratio = _float(env, "INFERENCE_INPUT_SAFETY_RATIO")
        if not 0 < ratio <= 1:
            raise ConfigError("INFERENCE_INPUT_SAFETY_RATIO는 0 초과 1 이하여야 한다")
        cpt = _float(env, "INFERENCE_CHARS_PER_TOKEN")
        if cpt <= 0:
            raise ConfigError("INFERENCE_CHARS_PER_TOKEN은 0보다 커야 한다")
        min_score = _float(env, "INFERENCE_SEARCH_MIN_SCORE")
        if min_score < 0:
            raise ConfigError("INFERENCE_SEARCH_MIN_SCORE는 0 이상이어야 한다 (0 = 끔)")
        engine = EngineSettings(
            model=env.require("INFERENCE_LLM_MODEL"), timeout_seconds=float(_positive_int(env, "INFERENCE_LLM_TIMEOUT_SECONDS")),
            max_output_tokens=_positive_int(env, "INFERENCE_LLM_MAX_OUTPUT_TOKENS"),
            temperature=_optional_float(env, "INFERENCE_LLM_TEMPERATURE"), reasoning_effort=env.optional("INFERENCE_LLM_REASONING_EFFORT"),
            max_steps=_positive_int(env, "INFERENCE_MAX_STEPS"), max_tool_calls=_positive_int(env, "INFERENCE_MAX_TOOL_CALLS"),
            max_write_calls=_positive_int(env, "INFERENCE_MAX_WRITE_CALLS", minimum=0),
            max_seconds=float(_positive_int(env, "INFERENCE_MAX_SECONDS")), max_same_error=_positive_int(env, "INFERENCE_MAX_SAME_ERROR"),
            retry_search_max=_positive_int(env, "INFERENCE_RETRY_SEARCH_MAX", minimum=0),
            max_input_tokens=_positive_int(env, "INFERENCE_MAX_INPUT_TOKENS"), safety_ratio=ratio, chars_per_token=cpt,
            tool_result_max_chars=_positive_int(env, "INFERENCE_TOOL_RESULT_MAX_CHARS"),
            trim_keep_chars=_positive_int(env, "INFERENCE_TRIM_KEEP_CHARS"),
            search_history_turns=_positive_int(env, "INFERENCE_SEARCH_HISTORY_TURNS", minimum=0),
            search_query_max_chars=_positive_int(env, "INFERENCE_SEARCH_QUERY_MAX_CHARS"),
            history_max_turns=_positive_int(env, "INFERENCE_HISTORY_MAX_TURNS"),
            record_arg_max_chars=_positive_int(env, "INFERENCE_RECORD_ARG_MAX_CHARS"),
        )
        return cls(
            port=_positive_int(env, "INFERENCE_PORT"), auth_mode=auth, store_path=env.require("INFERENCE_STORE_PATH"),
            agents=tuple(env.require_list("INFERENCE_AGENTS")), agents_dir=env.require("INFERENCE_AGENTS_DIR"),
            targets_dir=env.require("INFERENCE_TARGETS_DIR"), timezone=tz,
            text_max_chars=_positive_int(env, "INFERENCE_TEXT_MAX_CHARS"),
            requester_max_chars=_positive_int(env, "INFERENCE_REQUESTER_MAX_CHARS"),
            max_concurrent=_positive_int(env, "INFERENCE_MAX_CONCURRENT_PER_AGENT"),
            retention_days=_positive_int(env, "INFERENCE_RETENTION_DAYS"),
            purge_interval_seconds=_positive_int(env, "INFERENCE_PURGE_INTERVAL_SECONDS"),
            search_top_k=_positive_int(env, "INFERENCE_SEARCH_TOP_K"), search_min_score=min_score,
            llm_max_retries=_positive_int(env, "INFERENCE_LLM_MAX_RETRIES", minimum=0),
            ai_mark_suffix=AuthorshipMarker.decode_suffix(env.require("INFERENCE_AI_MARK_SUFFIX")), engine=engine, api_token=token,
        )


@dataclass(frozen=True)
class AgentSettings:
    agent_id: str
    targets: tuple[str, ...]
    max_concurrent: int | None
    values: Mapping[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_values(cls, agent_id: str, values: Mapping[str, str]) -> AgentSettings:
        env = Settings(values, origin=f"agent {agent_id}")
        targets = tuple(env.require_list("AGENT_TARGETS"))
        raw = env.optional("AGENT_MAX_CONCURRENT_REQUESTS")
        concurrent = None
        if raw is not None:
            concurrent = env.require_int("AGENT_MAX_CONCURRENT_REQUESTS")
            if concurrent < 1:
                raise ConfigError("AGENT_MAX_CONCURRENT_REQUESTS는 1 이상이어야 한다")
        return cls(agent_id=agent_id, targets=targets, max_concurrent=concurrent, values=dict(values))


def load_system_prompt(env: Mapping[str, str], agent_env: Mapping[str, str]) -> str:
    """agent 설정 → 서비스 설정 순. 같은 곳에 text와 파일이 둘 다 있으면 파일이 이긴다."""
    for prefix, values in (("AGENT", agent_env), ("INFERENCE", env)):
        path = values.get(f"{prefix}_SYSTEM_PROMPT_FILE")
        if path:
            p = Path(path)
            if not p.is_file():
                raise ConfigError(f"시스템 프롬프트 파일이 없다: {prefix}_SYSTEM_PROMPT_FILE")
            text = p.read_text(encoding="utf-8")
            if not text.strip():
                raise ConfigError(f"시스템 프롬프트 파일이 비었다: {prefix}_SYSTEM_PROMPT_FILE")
            return text
        text = values.get(f"{prefix}_SYSTEM_PROMPT")
        if text:
            return text
    raise ConfigError("시스템 프롬프트가 없다: INFERENCE_SYSTEM_PROMPT 또는 INFERENCE_SYSTEM_PROMPT_FILE (agent는 AGENT_*)")


def _read_env_file(path: Path, what: str) -> dict[str, str]:
    if not path.is_file():
        raise ConfigError(f"{what} 설정 파일이 없다: {path}")
    return parse_env_text(path.read_text(encoding="utf-8"))


def load_agent_settings(agents_dir: str | Path, agent_id: str) -> AgentSettings:
    """config/agents/<agentId>/_agent.env (targetId는 `_`로 시작할 수 없으므로 타겟 파일과 겹치지 않는다)."""
    return AgentSettings.from_values(agent_id, _read_env_file(Path(agents_dir) / agent_id / "_agent.env", f"agent {agent_id}"))


def load_write_marks(agents_dir: str | Path, agent_id: str, targets: tuple[str, ...], *, suffix: str) -> AuthorshipMarker:
    """agent×타겟 파일의 AGENT_WRITE_MARKS. 없으면 그 타겟의 쓰기 tool은 하나도 실행되지 않는다(표시 없는 쓰기를 막는 쪽이 기본)."""
    marks: dict[str, dict[str, tuple[str, ...]]] = {}
    for target in targets:
        values = _read_env_file(Path(agents_dir) / agent_id / f"{target}.env", f"agent {agent_id} × 타겟 {target}")
        raw = values.get("AGENT_WRITE_MARKS")
        if not raw:
            marks[target] = {}
            continue
        try:
            marks[target] = AuthorshipMarker.parse_marks(raw)
        except ConfigError as e:
            raise ConfigError(f"{e} ({agent_id}/{target}.env)") from None
    return AuthorshipMarker(marks, suffix)
