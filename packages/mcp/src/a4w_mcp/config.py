"""설정 (Phase 3 설계 3절). 기본값을 코드에 두지 않는다. 비밀은 Settings로만 들고 값을 드러내지 않는다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from a4w_core.config import Settings, parse_env_text
from a4w_core.errors import ConfigError

from .snapshot import RISK_ORDER

TRANSPORTS = ("stdio",)                # streamable_http는 자리만 (설계 3.2)
SCOPE_UNKNOWN = ("fail", "all")
_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_SEPARATOR = re.compile(r"^[A-Za-z0-9_-]+$")  # OpenAI 호환 function 이름에 쓸 수 있는 글자


@dataclass(frozen=True)
class McpSettings:
    agent_id: str
    targets: list[str]
    targets_dir: Path
    agents_dir: Path
    secrets_dir: Path
    manufacture_url: str
    manufacture_token: str | None = field(repr=False)
    manufacture_timeout: float
    refresh_seconds: float
    retry_seconds: float
    transport: str
    separator: str
    name_max: int
    max_concurrent: int
    scope_unknown: str

    @classmethod
    def from_env(cls, env: Settings) -> "McpSettings":
        agent_id = env.require("MCP_AGENT_ID")
        if not _AGENT_ID.match(agent_id):
            raise ConfigError(f"MCP_AGENT_ID 형식이 아니다: {agent_id!r}")
        transport = env.require("MCP_TRANSPORT")
        if transport not in TRANSPORTS:
            raise ConfigError(f"MCP_TRANSPORT는 {TRANSPORTS} 중 하나 (streamable_http는 자리만): {transport}")
        scope_unknown = env.require("MCP_SCOPE_UNKNOWN")
        if scope_unknown not in SCOPE_UNKNOWN:
            raise ConfigError(f"MCP_SCOPE_UNKNOWN은 {SCOPE_UNKNOWN} 중 하나: {scope_unknown}")
        separator = env.require("MCP_TOOL_NAME_SEPARATOR")
        if not _SEPARATOR.match(separator):
            raise ConfigError(f"MCP_TOOL_NAME_SEPARATOR에 쓸 수 없는 글자: {separator!r}")
        values = {k: env.require_int(k) for k in ("MCP_MANUFACTURE_TIMEOUT_SECONDS", "MCP_REFRESH_SECONDS",
                                                   "MCP_REFRESH_RETRY_SECONDS", "MCP_TOOL_NAME_MAX",
                                                   "MCP_MAX_CONCURRENT_CALLS")}
        bad = [k for k, v in values.items() if v < 1]
        if bad:
            raise ConfigError(f"1 이상이어야 한다: {', '.join(bad)}")
        return cls(
            agent_id=agent_id, targets=env.require_list("MCP_TARGETS"),
            targets_dir=Path(env.require("MCP_TARGETS_DIR")), agents_dir=Path(env.require("MCP_AGENTS_DIR")),
            secrets_dir=Path(env.require("MCP_SECRETS_DIR")), manufacture_url=env.require("MCP_MANUFACTURE_URL"),
            manufacture_token=env.optional("MCP_MANUFACTURE_TOKEN"),
            manufacture_timeout=values["MCP_MANUFACTURE_TIMEOUT_SECONDS"],
            refresh_seconds=values["MCP_REFRESH_SECONDS"], retry_seconds=values["MCP_REFRESH_RETRY_SECONDS"],
            transport=transport, separator=separator, name_max=values["MCP_TOOL_NAME_MAX"],
            max_concurrent=values["MCP_MAX_CONCURRENT_CALLS"], scope_unknown=scope_unknown,
        )


@dataclass(frozen=True)
class AgentTarget:
    """agent 하나가 타겟 하나에서 쓰는 설정(3.3)과 비밀(주소·token)."""

    target_id: str
    context: dict[str, str]
    max_risk: str
    groups: frozenset[str] | None   # None = 모든 묶음
    secrets: Settings = field(repr=False)


def load_agent_target(agents_dir: Path | str, secrets_dir: Path | str, agent_id: str, target_id: str) -> AgentTarget:
    cfg = _read(Path(agents_dir) / agent_id / f"{target_id}.env", f"agent {agent_id}/{target_id}")
    secrets = _read(Path(secrets_dir) / agent_id / f"{target_id}.env", f"agent secrets {agent_id}/{target_id}")
    max_risk = cfg.require("AGENT_TOOL_MAX_RISK")
    if max_risk not in RISK_ORDER:
        raise ConfigError(f"AGENT_TOOL_MAX_RISK는 {tuple(RISK_ORDER)} 중 하나: {max_risk}")
    groups = cfg.require_list("AGENT_TOOL_GROUPS")
    return AgentTarget(
        target_id=target_id, context=parse_context(cfg.optional("AGENT_CONTEXT") or ""), max_risk=max_risk,
        groups=None if groups == ["*"] else frozenset(groups), secrets=secrets,
    )


def parse_context(raw: str) -> dict[str, str]:
    """`키=값,키=값` (Phase 2 설계 6.3.1)."""
    out: dict[str, str] = {}
    for item in (s.strip() for s in raw.split(",")):
        if not item:
            continue
        key, sep, value = (p.strip() for p in item.partition("="))
        if not sep or not key or not value:
            raise ConfigError(f"AGENT_CONTEXT 항목은 키=값이어야 한다 (항목 {len(out) + 1})")
        if key in out:
            raise ConfigError(f"AGENT_CONTEXT 키가 겹친다: {key}")
        out[key] = value
    return out


def _read(path: Path, origin: str) -> Settings:
    if not path.is_file():
        raise ConfigError(f"설정 파일이 없다: {path}")
    return Settings(parse_env_text(path.read_text(encoding="utf-8")), origin=origin)
