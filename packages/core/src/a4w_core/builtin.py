"""코어가 가진 구현을 등록한다."""

from __future__ import annotations

import ssl

import httpx

from .config import TargetConfig
from .normalizers import CanonicalNormalizer, IdentityNormalizer
from .registry import Registry
from .spec_parsers import JsonSpecParser, YamlSpecParser
from .spec_sources import HashUrlSpecSource, NoHashSpecSource


def _http_client(cfg: TargetConfig) -> httpx.Client:
    verify: bool | ssl.SSLContext = cfg.require_bool("TLS_VERIFY")
    ca_file = cfg.optional("TLS_CA_FILE")
    if verify and ca_file:
        verify = ssl.create_default_context(cafile=ca_file)
    # proxy는 환경변수(HTTP_PROXY·HTTPS_PROXY·NO_PROXY)를 따른다
    return httpx.Client(timeout=cfg.require_int("HTTP_TIMEOUT_SECONDS"), verify=verify, trust_env=True)


def _hash_url_source(cfg: TargetConfig) -> HashUrlSpecSource:
    return HashUrlSpecSource(
        spec_url=cfg.require("SPEC_URL"),
        hash_url=cfg.require("SPEC_HASH_URL"),
        max_bytes=cfg.require_int("SPEC_MAX_BYTES"),
        client=_http_client(cfg),
    )


def register_builtins(registry: Registry) -> None:
    registry.register("spec_source", "hash_url", _hash_url_source)
    registry.register("spec_source", "no_hash", lambda cfg: NoHashSpecSource())
    registry.register("spec_parser", "yaml", lambda cfg: YamlSpecParser())
    registry.register("spec_parser", "json", lambda cfg: JsonSpecParser())
    registry.register("spec_normalizer", "identity", lambda cfg: IdentityNormalizer())
    registry.register("spec_normalizer", "canonical", lambda cfg: CanonicalNormalizer())
