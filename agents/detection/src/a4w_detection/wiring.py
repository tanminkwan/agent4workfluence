"""조립 지점 (composition root). 설정을 읽어 구현을 끼운다. 다른 곳은 구현을 모른다."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from a4w_core.builtin import register_builtins
from a4w_core.config import Settings, TargetConfig, load_target_configs, parse_env_text
from a4w_core.errors import ConfigError, VerifyInProgress
from a4w_core.locking import FileTargetLock as CoreFileTargetLock
from a4w_core.openapi.operations import collect_operations
from a4w_core.registry import Registry, load_target_plugins
from a4w_tools.call_policy import ConfigCallPolicy
from a4w_tools.executor import ToolExecutor
from a4w_tools.sandbox import RawCaller
from a4w_tools.target_http import TargetHttpClient

from .app import AppContext
from .clock import SystemClock
from .diff import OpenApiDiffer
from .profile import TargetProfileService
from .service import ChangeSetQuery, DetectService, TargetRuntime
from .storage.file_lock import FileTargetLock
from .storage.git_archive import GitSpecArchive
from .storage.sqlite_store import SqliteDetectionStore
from .textdiff import TextDiffer
from .verify.runner import VerificationRunner
from .verify.service import VerificationService, VerifyRuntime
from .verify.store import SqliteVerificationStore

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
    archive = GitSpecArchive(store_root, file_name=SPEC_FILE_NAMES[fmt])
    clock = SystemClock()
    service = DetectService(
        targets=runtimes,
        archive=archive,
        store=store,
        lock=FileTargetLock(store_root / "locks"),
        differ=OpenApiDiffer(),
        text_differ=TextDiffer(context_lines=env.require_int("DETECTION_TEXT_DIFF_CONTEXT_LINES")),
        clock=clock,
    )

    # ── Phase 2 보강: 타겟 창구 (타겟 비밀이 있는 타겟만 검증·scope 확인을 켠다)
    secrets_dir = Path(env.require("DETECTION_SECRETS_DIR"))
    verify_runtimes = {
        tid: _verify_runtime(registry, cfg, secrets, secrets_dir)
        for tid, cfg in configs.items()
        if (secrets := _load_secrets(secrets_dir, tid)) is not None
    }

    def spec_operations(target_id: str) -> set[tuple[str, str]]:
        """검증 경로 검사용: Detection이 보관한 현재 명세의 (METHOD, path)."""
        current = store.current(target_id)
        if current is None:
            return set()
        doc = runtimes[target_id].parser.parse(archive.read(target_id, current.git_commit))
        return set(collect_operations(doc))

    verify = VerificationService(
        known_targets=target_ids, runtimes=verify_runtimes,
        store=SqliteVerificationStore(store_root / "verifications.db"),
        lock=CoreFileTargetLock(store_root / "locks", busy=lambda key: VerifyInProgress(f"검증이 진행 중이다: {key}")),
        spec_operations=spec_operations, clock=clock,
        max_tools=env.require_int("DETECTION_VERIFY_MAX_TOOLS"),
        keep_days=env.require_int("DETECTION_VERIFY_RESULT_DAYS"),
    )
    verify.recover()
    logging.getLogger(__name__).info(
        "detection ready: targets=%s auth=%s verify=%s", ",".join(target_ids), auth_mode,
        ",".join(sorted(verify_runtimes)) or "-",
    )
    return AppContext(
        detect=service, query=ChangeSetQuery(targets=runtimes.keys(), store=store),
        store=store, auth_mode=auth_mode, api_token=api_token,
        verify=verify, profile=TargetProfileService(known_targets=target_ids, runtimes=verify_runtimes),
    )


def _load_secrets(secrets_dir: Path, target_id: str) -> Settings | None:
    path = secrets_dir / f"{target_id}.env"
    if not path.is_file():
        return None
    return Settings(parse_env_text(path.read_text(encoding="utf-8")), origin=f"secrets {target_id}")


def _verify_runtime(registry: Registry, cfg: TargetConfig, secrets: Settings, secrets_dir: Path) -> VerifyRuntime:
    http = TargetHttpClient.from_settings(cfg, secrets)
    policy = ConfigCallPolicy.from_settings(cfg)
    executor = ToolExecutor(
        http=http, policy=policy,
        interpreter=registry.create("error_interpreter", cfg.require("CALL_ERROR_INTERPRETER"), cfg),
    )
    sandbox_name = cfg.require("VERIFY_SANDBOX")
    # 시험 환경은 타겟 비밀 디렉터리에서 검증 상대 계정(peer) 비밀을 찾을 수 있다 (설계 11.6.1)
    sandbox = registry.create("verify_sandbox", sandbox_name, (cfg, secrets_dir))
    return VerifyRuntime(
        runner=VerificationRunner(executor=executor, caller=RawCaller(http, policy), sandbox=sandbox),
        scope_provider=registry.create("scope_provider", cfg.require("TARGET_SCOPE_PROVIDER"), cfg),
        token=secrets.require("TARGET_API_TOKEN"),
        sandbox_name=sandbox_name,
        context_keys=sandbox.context_keys(),
        deny_scopes=frozenset(cfg.require_list("VERIFY_DENY_SCOPES")),
    )
