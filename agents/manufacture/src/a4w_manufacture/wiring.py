"""조립 지점 (composition root). 설정을 읽어 구현을 끼운다. 다른 곳은 구현을 모른다.

타겟 쪽은 Detection 클라이언트 하나뿐이다. 타겟 주소·token·TLS 설정을 읽지 않는다 (설계 1절, AC-14).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import httpx

from a4w_clients.llm import placeholders as llm_providers
from a4w_clients.rag import aipro_plus
from a4w_clients.rag.base import RagSpace
from a4w_core.builtin import register_builtins
from a4w_core.config import Settings, load_target_configs
from a4w_core.errors import ConfigError
from a4w_core.locking import FileTargetLock
from a4w_core.registry import Registry, load_target_plugins

from .app import AppContext
from .assist.assistant import LlmBudget, ToolAssistant
from .assist.prompts import PromptSet, parse_versions
from .catalog import FileCatalog
from .checks import DefinitionChecker, TextLimits
from .detection_client import DetectionClient
from .errors import ApplyInProgress
from .index.sync import IndexSync
from .reviews import ReviewService
from .runner import ApplyRunner
from .service import ApplyService, TargetRuntime
from .storage.bundles import FileToolStore
from .storage.sqlite_db import SqliteManufactureDb
from .toolsets import ToolsetQuery
from .transform.openapi3 import OpenApi3Transformer

AUTH_MODES = ("none", "bearer")
NO_SCENARIO = ("pending", "exclude")
RAG_PROVIDERS = {"aipro_plus": aipro_plus.from_settings}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_context(environ: Mapping[str, str]) -> AppContext:
    env = Settings(environ, origin="environment")
    logging.basicConfig(level=env.require("LOG_LEVEL").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")

    auth_mode = env.require("MANUFACTURE_AUTH_MODE")
    if auth_mode not in AUTH_MODES:
        raise ConfigError(f"MANUFACTURE_AUTH_MODE는 {AUTH_MODES} 중 하나: {auth_mode}")
    api_token = env.require("MANUFACTURE_API_TOKEN") if auth_mode == "bearer" else None

    store_root = Path(env.require("MANUFACTURE_TOOL_STORE_PATH"))
    target_ids = env.require_list("TARGETS")
    configs = load_target_configs(env.require("MANUFACTURE_TARGETS_DIR"), target_ids)

    registry = Registry()
    register_builtins(registry)
    load_target_plugins(registry)
    llm_providers.register(registry)

    detection = DetectionClient(
        base_url=env.require("DETECTION_BASE_URL"), token=env.optional("DETECTION_CLIENT_TOKEN"),
        http=httpx.Client(timeout=env.require_int("DETECTION_TIMEOUT_SECONDS"), trust_env=True),
        poll_seconds=env.require_int("DETECTION_VERIFY_POLL_SECONDS"),
        verify_timeout_seconds=env.require_int("DETECTION_VERIFY_TIMEOUT_SECONDS"),
    )
    # LLM 주소·key는 비밀 파일(config/secrets/manufacture-llm.env)이 env_file로 넣는다
    llm = registry.create("llm_client", env.require("LLM_PROVIDER"), (env, env))
    rag_provider = env.require("RAG_PROVIDER")
    if rag_provider not in RAG_PROVIDERS:
        raise ConfigError(f"알 수 없는 RAG_PROVIDER: {rag_provider}")
    rag = RAG_PROVIDERS[rag_provider](env, env)

    db = SqliteManufactureDb(store_root / "manufacture.db")
    store = FileToolStore(store_root)
    checker = DefinitionChecker(TextLimits.parse(env.require("TOOL_TEXT_LIMITS")),
                                max_visible_inputs=env.require_int("TOOL_MAX_VISIBLE_INPUTS"))
    effort = env.optional("LLM_REASONING_EFFORT")
    assistant = ToolAssistant(
        llm=llm, cache=db, prompts=PromptSet(env.require("MANUFACTURE_PROMPTS_DIR"),
                                             parse_versions(env.require("PROMPT_VERSIONS"))),
        checker=checker, model=env.require("LLM_MODEL"), timeout_seconds=env.require_int("LLM_TIMEOUT_SECONDS"),
        max_output_tokens=env.require_int("LLM_MAX_OUTPUT_TOKENS"), temperature=_float(env, "LLM_TEMPERATURE"),
        reasoning_effort=effort, max_retries=env.require_int("LLM_MAX_RETRIES"),
        budget=LlmBudget(env.require_int("LLM_MAX_CALLS_PER_APPLY")), clock=now_iso,
    )
    cprefix, dprefix = env.require("RAG_COLLECTION_PREFIX"), env.require("RAG_DOMAIN_PREFIX")
    runtimes = {tid: _runtime(registry, tid, cfg, cprefix, dprefix) for tid, cfg in configs.items()}
    service = ApplyService(
        targets=runtimes, detection=detection, assistant=assistant, checker=checker, store=store, db=db,
        index=IndexSync(rag, db, content_max_chars=env.require_int("RAG_CONTENT_MAX_CHARS"),
                        list_limit=env.require_int("RAG_LIST_LIMIT"), clock=now_iso),
        clock=now_iso, llm_max_calls=env.require_int("LLM_MAX_CALLS_PER_APPLY"),
        example_count=env.require_int("LLM_EXAMPLE_COUNT"),
    )
    runner = ApplyRunner(
        service=service, changesets=detection, db=db,
        lock=FileTargetLock(store_root / "locks", busy=lambda key: ApplyInProgress(f"반영이 진행 중이다: {key}")),
        clock=now_iso,
    )
    runner.recover()
    logging.getLogger(__name__).info("manufacture ready: targets=%s auth=%s model=%s", ",".join(target_ids),
                                     auth_mode, env.require("LLM_MODEL"))
    return AppContext(runner=runner, reviews=ReviewService(db, clock=now_iso), service=service, store=store, db=db,
                      toolsets=ToolsetQuery(targets=target_ids, store=store), auth_mode=auth_mode, api_token=api_token)


def _runtime(registry: Registry, tid: str, cfg, cprefix: str, dprefix: str) -> TargetRuntime:
    no_scenario = cfg.require("VERIFY_NO_SCENARIO")
    if no_scenario not in NO_SCENARIO:
        raise ConfigError(f"VERIFY_NO_SCENARIO는 {NO_SCENARIO} 중 하나: {no_scenario}")
    return TargetRuntime(
        target_id=tid,
        catalog=FileCatalog(registry.create("tool_catalog", cfg.require("TOOL_CATALOG"), cfg)),
        enabled_groups=tuple(cfg.require_list("TOOL_GROUPS_ENABLED")),
        transformer=OpenApi3Transformer(content_types=cfg.require_list("TOOL_REQUEST_CONTENT_TYPES")),
        max_chars=cfg.require_int("RESPONSE_MAX_CHARS"),
        no_scenario=no_scenario,
        rag_space=RagSpace(collection=cprefix + tid, domain=dprefix + tid, description=f"{tid} tool 색인 (aiagent4workfluence)"),
    )


def _float(env: Settings, key: str) -> float:
    try:
        return float(env.require(key))
    except ValueError:
        raise ConfigError(f"실수가 아니다: {key}") from None
