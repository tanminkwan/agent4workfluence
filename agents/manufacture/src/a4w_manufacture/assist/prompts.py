"""프롬프트 파일 (Phase 2 설계 9.4절, FR-246): `<작업>.v<번호>.md`, 공통 부분은 `_common.v<번호>.md`의 {{common}}."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from a4w_core.errors import ConfigError


def parse_versions(text: str) -> dict[str, str]:
    try:
        return {k.strip(): v.strip() for k, v in (p.split("=", 1) for p in text.split(",") if p.strip())}
    except ValueError:
        raise ConfigError("PROMPT_VERSIONS는 작업=v번호,... 꼴이어야 한다") from None


class PromptSet:
    def __init__(self, root: Path | str, versions: Mapping[str, str]):
        self._root = Path(root)
        self._versions = dict(versions)

    def get(self, task: str) -> tuple[str, str]:
        """(system 글, 프롬프트 id). id는 LLM 결과 재사용 지문에 들어간다."""
        version = self._versions.get(task)
        if version is None:
            raise ConfigError(f"PROMPT_VERSIONS에 {task}가 없다")
        text = self._read(f"{task}.{version}.md")
        pid = f"{task}@{version}"
        if "{{common}}" in text:
            cv = self._versions.get("common")
            if cv is None:
                raise ConfigError("PROMPT_VERSIONS에 common이 없다")
            text = text.replace("{{common}}", self._read(f"_common.{cv}.md").strip())
            pid += f"+common@{cv}"
        return text, pid

    def _read(self, name: str) -> str:
        path = self._root / name
        if not path.is_file():
            raise ConfigError(f"프롬프트 파일이 없다: {path}")
        return path.read_text(encoding="utf-8")
