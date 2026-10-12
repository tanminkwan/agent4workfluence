"""명세 해석기. YAML만 구현하고 JSON은 자리만 둔다."""

from __future__ import annotations

from typing import Any

import yaml

from .errors import SpecInvalid, UnsupportedSpecSource
from .interfaces import SpecParser


class _StrictLoader(yaml.SafeLoader):
    """날짜·시각을 문자열로 둔다 (JSON으로 그대로 옮기기 위해)."""


_StrictLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _stringify_keys(n: Any) -> Any:
    if isinstance(n, dict):
        return {str(k): _stringify_keys(v) for k, v in n.items()}
    if isinstance(n, list):
        return [_stringify_keys(v) for v in n]
    return n


def check_openapi3(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise SpecInvalid("명세 최상위가 객체가 아니다")
    version = doc.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise SpecInvalid("OpenAPI 3.x 명세가 아니다")
    if not isinstance(doc.get("paths"), dict):
        raise SpecInvalid("paths가 없거나 객체가 아니다")
    return doc


class YamlSpecParser(SpecParser):
    def parse(self, raw: bytes) -> dict[str, Any]:
        try:
            text = raw.decode("utf-8")
            doc = yaml.load(text, Loader=_StrictLoader)  # noqa: S506 (SafeLoader 기반)
        except (UnicodeDecodeError, yaml.YAMLError) as e:
            raise SpecInvalid(f"YAML로 해석되지 않는다: {type(e).__name__}") from None
        return check_openapi3(_stringify_keys(doc))


class JsonSpecParser(SpecParser):
    """자리만 둔다 (Workfluence는 YAML을 쓴다)."""

    def parse(self, raw: bytes) -> dict[str, Any]:
        raise UnsupportedSpecSource("JSON 명세 해석은 아직 구현하지 않았다")
