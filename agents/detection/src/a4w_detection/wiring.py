"""조립 지점 (composition root). 설정을 읽어 구현을 끼운다. 다른 곳은 구현을 모른다."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from a4w_core.builtin import register_builtins
from a4w_core.config import Settings, load_target_configs
from a4w_core.errors import ConfigError
from a4w_core.registry import Registry, load_target_plugins

from .app import AppContext
from .clock import SystemClock
from .diff import OpenApiDiffer
from .service import ChangeSetQuery, DetectService, TargetRuntime
from .storage.file_lock import FileTargetLock
from .storage.git_archive import GitSpecArchive
from .storage.sqlite_store import SqliteDetectionStore
from .textdiff import TextDiffer

AUTH_MODES = ("none", "bearer")
# SPEC_FORMAT → 보관 파일 이름
SPEC_FILE_NAMES = {"yaml": "openapi.yaml", "json": "openapi.json"}


def build_context(environ: Mapping[str, str]) -> AppContext:
    env = Settings(environ, origin="environment")
    logging.basicConfig(level=env.require("LOG_LEVEL").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")

    auth_mode = env.require("DETECTION_AUTH_MODE")
    if auth_mode not in AUTH_MODES:
        raise ConfigError(f"DETECTION_AUTH_MODE는 {AUTH_MODES} 중 하나: {auth_mode}")
    api_token = env.require("DETECTION_API_TOKEN") if auth_mode == "bearer" else None

    store_root = Path(env.require("DETECTION_SPEC_STORE_PATH"))
    target_ids = env.require_list("TARGETS")
    configs = load_target_configs(env.require("DETECTION_TARGETS_DIR"), target_ids)

    registry = Registry()
    register_builtins(registry)
    load_target_plugins(registry)

    formats = {cfg.require("SPEC_FORMAT") for cfg in configs.values()}
    if len(formats) != 1:
        raise ConfigError("이번 구현은 모든 타겟이 같은 SPEC_FORMAT을 써야 한다")
    fmt = formats.pop()
    if fmt not in SPEC_FILE_NAMES:
        raise ConfigError(f"알 수 없는 SPEC_FORMAT: {fmt}")

    runtimes = {
        tid: TargetRuntime(
            source=registry.create("spec_source", cfg.require("SPEC_SOURCE"), cfg),
            parser=registry.create("spec_parser", fmt, cfg),
            normalizer=registry.create("spec_normalizer", cfg.require("SPEC_NORMALIZER"), cfg),
            policy=registry.create("review_policy", cfg.require("REVIEW_POLICY"), cfg),
        )
        for tid, cfg in configs.items()
    }
    store = SqliteDetectionStore(store_root / "detection.db")
    service = DetectService(
        targets=runtimes,
        archive=GitSpecArchive(store_root, file_name=SPEC_FILE_NAMES[fmt]),
        store=store,
        lock=FileTargetLock(store_root / "locks"),
        differ=OpenApiDiffer(),
        text_differ=TextDiffer(context_lines=env.require_int("DETECTION_TEXT_DIFF_CONTEXT_LINES")),
        clock=SystemClock(),
    )
    logging.getLogger(__name__).info("detection ready: targets=%s auth=%s", ",".join(target_ids), auth_mode)
    return AppContext(
        detect=service, query=ChangeSetQuery(targets=runtimes.keys(), store=store),
        store=store, auth_mode=auth_mode, api_token=api_token,
    )
