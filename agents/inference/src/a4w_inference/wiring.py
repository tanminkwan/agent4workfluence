"""조립 지점 (composition root). 설정을 읽어 구현을 끼운다. 다른 곳은 구현을 모른다 (Phase 4 설계 13절).

agent token 파일은 여기서도 열지 않는다. MCP 자식(a4w_mcp)이 MCP_SECRETS_DIR로 읽는다.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
import time
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from a4w_clients.llm import placeholders as llm_providers
from a4w_clients.rag import aipro_plus
from a4w_clients.rag.base import RagSpace
from a4w_core.config import Settings, load_target_configs
from a4w_core.errors import ConfigError
from a4w_core.registry import Registry

from .adapters.gateway_mcp import McpToolGateway
from .adapters.mcp_pool import AgentMcpConnection, McpAgentPool, McpChildSpec, child_env
from .adapters.search_rag import RagToolSearch
from .adapters.store_async import ThreadedConversationStore
from .channels.rest.app import RestContext
from .config import InferenceSettings, load_agent_settings, load_system_prompt, load_write_marks
from .engine.engine import AgentEngine, AgentProfile
from .engine.prompts import SystemPrompt
from .engine.router import EngineRouter
from .runner import RequestRunner
from .storage.sqlite_db import SqliteInferenceDb

log = logging.getLogger(__name__)
RAG_PROVIDERS = {"aipro_plus": aipro_plus.from_settings}


class SystemClock:
    def __init__(self, tz):
        self._tz = tz

    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(self._tz)


def utc_iso(moment: datetime | None = None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_rest_context(environ: Mapping[str, str]) -> RestContext:
    env = Settings(environ, origin="environment")
    logging.basicConfig(level=env.require("LOG_LEVEL").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = InferenceSettings.from_environ(environ)

    registry = Registry()
    llm_providers.register(registry)
    llm_env = Settings({"LLM_BASE_URL": env.optional("LLM_BASE_URL") or "", "LLM_API_KEY": env.optional("LLM_API_KEY") or "",
                        "LLM_MAX_RETRIES": str(s.llm_max_retries)}, origin="LLM 설정(config/secrets/inference-llm.env)")
    llm = registry.create("chat_llm_client", env.require("INFERENCE_LLM_PROVIDER"), (llm_env, llm_env))
    rag_provider = env.require("RAG_PROVIDER")
    if rag_provider not in RAG_PROVIDERS:
        raise ConfigError(f"알 수 없는 RAG_PROVIDER: {rag_provider}")
    rag = RAG_PROVIDERS[rag_provider](env, env)
    cprefix, dprefix = env.require("RAG_COLLECTION_PREFIX"), env.require("RAG_DOMAIN_PREFIX")

    db = SqliteInferenceDb(Path(s.store_path) / "inference.db")
    store = ThreadedConversationStore(db)
    clock = SystemClock(s.timezone)

    prefixes = tuple(x.strip() for x in env.require("INFERENCE_MCP_CHILD_ENV_PREFIXES").split(",") if x.strip())
    names = tuple(x.strip() for x in env.require("INFERENCE_MCP_CHILD_ENV_NAMES").split(",") if x.strip())
    connections: dict[str, AgentMcpConnection] = {}
    profiles: list[tuple[str, AgentProfile, object]] = []
    limits: dict[str, int] = {}
    spaces: dict[str, RagSpace] = {}
    agent_targets: dict[str, tuple[str, ...]] = {}
    pool_args = dict(start_timeout=float(env.require_int("INFERENCE_MCP_START_TIMEOUT_SECONDS")),
                     backoff_initial=float(env.require_int("INFERENCE_MCP_RESTART_BACKOFF_SECONDS")),
                     backoff_max=float(env.require_int("INFERENCE_MCP_RESTART_BACKOFF_MAX_SECONDS")),
                     tools_wait=float(env.require_int("INFERENCE_MCP_TOOLS_WAIT_SECONDS")),
                     ping_interval=float(env.require_int("INFERENCE_MCP_PING_SECONDS")))

    for agent_id in s.agents:
        agent = load_agent_settings(s.agents_dir, agent_id)
        load_target_configs(s.targets_dir, agent.targets)                       # 타겟 설정 파일이 있는지 일찍 확인한다
        prompt = SystemPrompt.parse(load_system_prompt(environ, agent.values))
        marker = load_write_marks(s.agents_dir, agent_id, agent.targets, suffix=s.ai_mark_suffix)
        limits[agent_id] = agent.max_concurrent or s.max_concurrent
        agent_targets[agent_id] = agent.targets
        for target in agent.targets:
            spaces.setdefault(target, RagSpace(collection=cprefix + target, domain=dprefix + target,
                                               description=f"{target} tool 색인 (aiagent4workfluence)"))
        connections[agent_id] = AgentMcpConnection(
            McpChildSpec(agent_id=agent_id, command=(sys.executable, "-m", "a4w_mcp"),
                         env=child_env(environ, agent_id=agent_id, targets=agent.targets, prefixes=prefixes, names=names)),
            **pool_args)
        profiles.append((agent_id, AgentProfile(targets=agent.targets, prompt=prompt), marker))
        log.info("agent ready: id=%s targets=%s concurrent=%d", agent_id, ",".join(agent.targets), limits[agent_id])

    pool = McpAgentPool(connections)
    gateway = McpToolGateway(pool)
    search = RagToolSearch(rag, spaces=spaces, agent_targets=agent_targets, top_k=s.search_top_k, min_score=s.search_min_score)
    router = EngineRouter({
        agent_id: AgentEngine(search=search, gateway=gateway, llm=llm, store=store, marker=marker, clock=clock, settings=s.engine,
                              nonce=lambda: secrets.token_hex(8), agents={agent_id: profile})
        for agent_id, profile, marker in profiles
    })
    runner = RequestRunner(engine=router, db=db, agent_limits=limits, text_max_chars=s.text_max_chars,
                           requester_max_chars=s.requester_max_chars, now_iso=utc_iso, new_id=lambda: "r-" + uuid.uuid4().hex)

    async def purge_loop() -> None:
        while True:
            removed = await asyncio.to_thread(db.purge, before=utc_iso(datetime.now(UTC) - timedelta(days=s.retention_days)))
            if removed:
                log.info("expired records purged: %d", removed)
            await asyncio.sleep(s.purge_interval_seconds)

    @asynccontextmanager
    async def lifecycle():
        runner.recover()
        async with pool:
            purge = asyncio.create_task(purge_loop())
            try:
                yield
            finally:
                purge.cancel()
                await asyncio.gather(purge, return_exceptions=True)

    log.info("inference ready: agents=%s auth=%s model=%s", ",".join(s.agents), s.auth_mode, s.engine.model)
    return RestContext(runner=runner, auth_mode=s.auth_mode, api_token=s.api_token, health=pool.health, lifecycle=lifecycle)


def build_search_for_evaluation(environ: Mapping[str, str], *, agent_id: str):
    """평가 도구용: 상위 K를 바꿔 가며 쓸 수 있는 검색 함수 공장. RAG 설정은 서비스와 같다."""
    env = Settings(environ, origin="environment")
    s = InferenceSettings.from_environ(environ)
    rag_provider = env.require("RAG_PROVIDER")
    if rag_provider not in RAG_PROVIDERS:
        raise ConfigError(f"알 수 없는 RAG_PROVIDER: {rag_provider}")
    rag = RAG_PROVIDERS[rag_provider](env, env)
    cprefix, dprefix = env.require("RAG_COLLECTION_PREFIX"), env.require("RAG_DOMAIN_PREFIX")
    targets = load_agent_settings(s.agents_dir, agent_id).targets
    spaces = {t: RagSpace(collection=cprefix + t, domain=dprefix + t, description=f"{t} tool 색인 (aiagent4workfluence)") for t in targets}

    def for_k(k: int):
        return RagToolSearch(rag, spaces=spaces, agent_targets={agent_id: targets}, top_k=k, min_score=s.search_min_score).search

    return for_k
