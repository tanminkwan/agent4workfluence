"""조립 지점 (composition root). 설정을 읽어 구현을 끼운다. 다른 곳은 구현을 모른다 (Phase 4 설계 13절).

agent token 파일은 여기서도 열지 않는다. MCP 자식(a4w_mcp)이 MCP_SECRETS_DIR로 읽는다.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import ssl
import sys
import time
import uuid
from collections.abc import Callable, Mapping
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
from .adapters.runner_submitter import RunnerSubmitter
from .adapters.search_rag import RagToolSearch
from .adapters.store_async import ThreadedConversationStore
from .channels.chat.config import ChatSettings, load_chat_notes, load_chat_replies, load_chat_token
from .channels.chat.dispatcher import ChatDispatcher
from .channels.chat.health import ChatHealth
from .channels.chat.policy import ReplyPolicy
from .channels.chat.ports import ChatApi
from .channels.chat.rate import RateLimiter
from .channels.chat.rest_api import WorkfluenceChatApi
from .channels.chat.sender import ChatSender
from .channels.chat.store import SqliteChatState
from .channels.chat.supervisor import ConnectionSupervisor
from .channels.chat.worker import ChatWorkerPool
from .channels.chat.ws_client import WsConnection, chat_ws_url
from .channels.rest.app import RestContext
from .config import InferenceSettings, load_agent_settings, load_system_prompt, load_write_marks
from .engine.engine import AgentEngine, AgentProfile
from .engine.prompts import SystemPrompt
from .engine.router import EngineRouter
from .runner import RequestRunner
from .storage.sqlite_db import SqliteInferenceDb

log = logging.getLogger(__name__)
RAG_PROVIDERS = {"aipro_plus": aipro_plus.from_settings}
# 채팅 API 구현 (CHAT_API_ADAPTER). 새 타겟의 채팅은 여기에 더한다 (Phase 5 설계 2절, OCP)
CHAT_API_ADAPTERS: dict[str, Callable[[ChatSettings, str], ChatApi]] = {
    "workfluence": lambda chat, token: WorkfluenceChatApi(chat.api_base_url, token, timeout=chat.http_timeout_seconds,
                                                          ca_file=chat.tls_ca_file),
}


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
    chat = build_chat_channel(environ, runner=runner, agent_ids=s.agents, engine_max_seconds=s.engine.max_seconds)

    async def purge_loop() -> None:
        while True:
            removed = await asyncio.to_thread(db.purge, before=utc_iso(datetime.now(UTC) - timedelta(days=s.retention_days)))
            if removed:
                log.info("expired records purged: %d", removed)
            if chat is not None:
                chat_removed = await asyncio.to_thread(chat.purge, before=utc_iso(datetime.now(UTC) - timedelta(days=chat.retention_days)))
                if chat_removed:
                    log.info("expired chat records purged: %d", chat_removed)
            await asyncio.sleep(s.purge_interval_seconds)

    @asynccontextmanager
    async def lifecycle():
        runner.recover()
        async with pool:
            purge = asyncio.create_task(purge_loop())
            try:
                if chat is None:
                    yield
                else:
                    async with chat.running():
                        yield
            finally:
                purge.cancel()
                await asyncio.gather(purge, return_exceptions=True)

    log.info("inference ready: agents=%s auth=%s model=%s", ",".join(s.agents), s.auth_mode, s.engine.model)
    return RestContext(runner=runner, auth_mode=s.auth_mode, api_token=s.api_token, health=pool.health, lifecycle=lifecycle,
                       chat_health=chat.health if chat is not None else None)


class ChatChannel:
    """채팅 채널 한 벌 (Phase 5). running() 동안 연결을 지키고, 끝나면 연결 task를 멈춘다. token은 담지 않는다."""

    def __init__(self, *, supervisor: ConnectionSupervisor, worker: ChatWorkerPool, state: SqliteChatState, health: ChatHealth,
                 retention_days: int):
        self._supervisor, self._worker, self._state, self._health = supervisor, worker, state, health
        self.retention_days = retention_days

    def health(self) -> dict:
        return self._health.snapshot()

    def purge(self, *, before: str) -> int:
        return self._state.purge(before=before)

    @asynccontextmanager
    async def running(self):
        await self._worker.recover()                   # 재시작으로 끊긴 요청을 닫고 방마다 한 번 알린다 (설계 6절)
        task = asyncio.create_task(self._supervisor.run())
        try:
            yield self
        finally:
            self._supervisor.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def build_chat_channel(environ: Mapping[str, str], *, runner: RequestRunner, agent_ids, engine_max_seconds: float,
                       api: ChatApi | None = None, connection_factory: Callable[[], object] | None = None) -> ChatChannel | None:
    """CHAT_ENABLED=false면 None (Phase 4와 같다). api·connection_factory는 시험이 가짜를 넣을 때만 준다."""
    chat = ChatSettings.from_environ(environ)
    if not chat.enabled:
        return None
    if chat.agent_id not in tuple(agent_ids):
        raise ConfigError("CHAT_AGENT_ID가 INFERENCE_AGENTS에 없다")
    if chat.api_adapter not in CHAT_API_ADAPTERS:
        raise ConfigError(f"알 수 없는 CHAT_API_ADAPTER (허용: {', '.join(sorted(CHAT_API_ADAPTERS))})")
    chat.check_against_engine(engine_max_seconds=engine_max_seconds)
    token = load_chat_token(chat.secrets_dir, chat.agent_id, chat.target_id)
    replies = load_chat_replies(chat.replies_file)
    notes = load_chat_notes(chat.notes_file)
    api = api or CHAT_API_ADAPTERS[chat.api_adapter](chat, token)
    state = SqliteChatState(chat.state_path)
    sender = ChatSender(api, limiter=RateLimiter(max_per_window=chat.send_per_minute), answer_max_chars=chat.answer_max_chars,
                        hard_max_chars=chat.max_message_chars, truncate_notice=replies.truncate_notice, retry_max=chat.send_retry_max,
                        retry_seconds=chat.send_retry_seconds, retry_wait_max_seconds=chat.send_retry_wait_max_seconds)
    worker = ChatWorkerPool(submitter=RunnerSubmitter(runner), sender=sender, state=state, replies=replies.texts, now_iso=utc_iso,
                            agent_id=chat.agent_id, poll_interval=chat.poll_interval_seconds, wait_max_seconds=chat.wait_max_seconds,
                            busy_retry_seconds=chat.busy_retry_seconds, busy_retry_max_seconds=chat.busy_retry_max_seconds,
                            queue_max=chat.room_queue_max, notes=notes, notes_members_max=chat.notes_members_max,
                            notes_name_max_chars=chat.notes_name_max_chars)
    dispatcher = ChatDispatcher(api=api, state=state, policy=ReplyPolicy(mention_name=chat.mention_name,
                                                                         write_allowed_users=chat.write_allowed_users),
                                sink=worker, clock=lambda: datetime.now(UTC), now_iso=utc_iso, catchup_page=chat.catchup_page,
                                catchup_max_age_seconds=chat.catchup_max_age_seconds)
    if connection_factory is None:
        url = chat_ws_url(chat.api_base_url)
        context = ssl.create_default_context(cafile=chat.tls_ca_file) if chat.tls_ca_file else None

        def connection_factory():
            return WsConnection(url, token, ssl_context=context, open_timeout=chat.open_timeout_seconds,
                                ping_interval=chat.ping_interval_seconds, ping_timeout=chat.ping_timeout_seconds)

    tracked = None                                     # 상태 표시(health)가 감독(supervisor)을 알아야 해서 처리기는 뒤에 채운다

    async def handle(frame) -> None:
        await tracked(frame)

    supervisor = ConnectionSupervisor(connection_factory, handle, backoff_initial=chat.reconnect_initial_seconds,
                                      backoff_max=chat.reconnect_max_seconds, conflict_limit=chat.conflict_limit)
    health = ChatHealth(supervisor=supervisor, dispatcher=dispatcher, worker=worker, state=state, now_iso=utc_iso)
    tracked = health.track(dispatcher.handle)
    log.info("chat channel ready: agent=%s target=%s adapter=%s writers=%d", chat.agent_id, chat.target_id, chat.api_adapter,
             len(chat.write_allowed_users))
    return ChatChannel(supervisor=supervisor, worker=worker, state=state, health=health, retention_days=chat.retention_days)


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
