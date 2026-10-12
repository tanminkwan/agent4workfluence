"""설정 읽기. 기본값을 코드에 두지 않는다. 필수 값이 없으면 ConfigError."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from .errors import ConfigError

_TARGET_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


def parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key.strip()] = value
    return out


class Settings:
    """이름 → 문자열 값 묶음에 형 변환·필수 검사를 더한다."""

    def __init__(self, values: Mapping[str, str], origin: str):
        self._values = dict(values)
        self._origin = origin

    def optional(self, key: str) -> str | None:
        value = self._values.get(key)
        return value if value else None

    def require(self, key: str) -> str:
        value = self.optional(key)
        if value is None:
            raise ConfigError(f"설정 없음: {key} ({self._origin})")
        return value

    def require_int(self, key: str) -> int:
        raw = self.require(key)
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"정수가 아니다: {key} ({self._origin})") from None

    def require_bool(self, key: str) -> bool:
        raw = self.require(key).lower()
        if raw in _TRUE:
            return True
        if raw in _FALSE:
            return False
        raise ConfigError(f"참·거짓이 아니다: {key} ({self._origin})")

    def require_list(self, key: str) -> list[str]:
        items = [s.strip() for s in self.require(key).split(",") if s.strip()]
        if not items:
            raise ConfigError(f"목록이 비었다: {key} ({self._origin})")
        return items


class TargetConfig(Settings):
    def __init__(self, target_id: str, values: Mapping[str, str]):
        super().__init__(values, origin=f"target {target_id}")
        self.target_id = target_id
        self.adapter = self.require("TARGET_ADAPTER")


def load_target_configs(directory: Path | str, target_ids: Iterable[str]) -> dict[str, TargetConfig]:
    directory = Path(directory)
    out: dict[str, TargetConfig] = {}
    for tid in target_ids:
        if not _TARGET_ID.match(tid):
            raise ConfigError(f"targetId 형식이 아니다: {tid!r}")
        path = directory / f"{tid}.env"
        if not path.is_file():
            raise ConfigError(f"타겟 설정 파일이 없다: {path}")
        out[tid] = TargetConfig(tid, parse_env_text(path.read_text(encoding="utf-8")))
    return out
