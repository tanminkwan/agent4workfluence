"""조립 지점 (Phase 3 설계 8.2절). 설정을 읽어 구현을 끼운다. 다른 곳은 구현을 모른다.

타겟마다: 타겟 설정(호출 규약·TLS) + agent 비밀(주소·token) → 실행기, token scope, 노출 규칙.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from a4w_core.builtin import register_builtins
from a4w_core.config import Settings, load_target_configs
from a4w_core.errors import ScopeUnknown
from a4w_core.registry import Registry, load_target_plugins
from a4w_tools.assembly import build_target_caller

from .catalog import ServingCatalog
from .config import McpSettings, load_agent_target
from .notifier import ChangeNotifier
from .refresher import Refresher
from .runner import DirectToolRunner
from .server import build_server
from .snapshot import Exposure
from .source import ManufactureToolsetSource

log = logging.getLogger(__name__)
SERVER_NAME = "a4w-mcp"


@dataclass(frozen=True)
class McpApp:
    settings: McpSettings = field(repr=False)
    catalog: ServingCatalog
    refresher: Refresher
    runner: DirectToolRunner
    notifier: ChangeNotifier
    scopes: dict[str, list[str] | None]
    server: object = field(repr=False)


def build(environ: Mapping[str, str], *, registry: Registry | None = None,
          manufacture_transport: httpx.BaseTransport | None = None,
          target_transport: httpx.BaseTransport | None = None) -> McpApp:
    """transport 인자는 시험용이다(가짜 Manufacture·타겟)."""
    settings = McpSettings.from_env(Settings(environ, origin="environment"))
    if registry is None:
        registry = Registry()
        register_builtins(registry)
        load_target_plugins(registry)
    target_cfgs = load_target_configs(settings.targets_dir, settings.targets)

    executors, contexts, exposures, scopes = {}, {}, {}, {}
    for tid, cfg in target_cfgs.items():
        agent = load_agent_target(settings.agents_dir, settings.secrets_dir, settings.agent_id, tid)
        caller = build_target_caller(registry, cfg, agent.secrets, transport=target_transport)
        token_scopes = _scopes(registry, cfg, agent.secrets.require("TARGET_API_TOKEN"), settings.scope_unknown, tid)
        executors[tid], contexts[tid] = caller.executor, agent.context
        scopes[tid] = sorted(token_scopes) if token_scopes is not None else None
        exposures[tid] = Exposure(scopes=token_scopes, max_risk=agent.max_risk, groups=agent.groups,
                                  separator=settings.separator, name_max=settings.name_max, context=agent.context)

    catalog = ServingCatalog(targets=settings.targets, clock=_now)
    notifier = ChangeNotifier()
    source = ManufactureToolsetSource(base_url=settings.manufacture_url, token=settings.manufacture_token,
                                      timeout=settings.manufacture_timeout, transport=manufacture_transport)
    refresher = Refresher(source=source, exposures=exposures, catalog=catalog, notifier=notifier,
                          refresh_seconds=settings.refresh_seconds, retry_seconds=settings.retry_seconds)
    runner = DirectToolRunner(executors=executors, contexts=contexts)
    server = build_server(name=SERVER_NAME, version=_version(), agent_id=settings.agent_id, catalog=catalog,
                          runner=runner, refresher=refresher, notifier=notifier,
                          max_concurrent=settings.max_concurrent, scopes=scopes)
    log.info("a4w-mcp ready: agent=%s targets=%s scopes=%s", settings.agent_id, ",".join(settings.targets),
             {t: s if s is not None else "*" for t, s in scopes.items()})
    return McpApp(settings=settings, catalog=catalog, refresher=refresher, runner=runner, notifier=notifier,
                  scopes=scopes, server=server)


def _scopes(registry: Registry, cfg, token: str, on_unknown: str, target_id: str) -> frozenset[str] | None:
    provider = registry.create("scope_provider", cfg.require("TARGET_SCOPE_PROVIDER"), cfg)
    try:
        return frozenset(provider.scopes(token))
    except ScopeUnknown:
        if on_unknown == "fail":
            raise
        log.warning("token scope unknown, not filtering: target=%s", target_id)
        return None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("a4w-mcp")
    except PackageNotFoundError:
        return "0"
