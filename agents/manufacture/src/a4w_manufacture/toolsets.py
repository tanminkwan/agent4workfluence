"""tool 정의 조회 (Phase 3 설계 5절). tool-store를 읽기만 하고 잠금을 잡지 않는다.

포인터(current.json)는 원자적으로 바뀌고 묶음은 한 번 쓰면 바뀌지 않으므로(Phase 2 설계 13.1절) 잠금이 필요 없다.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from a4w_core.errors import StorageError, TargetNotFound
from a4w_tools.reader import ToolsetNotFound as ReaderNotFound

from .errors import ToolsetNotFound, ToolVersionNotFound
from .storage.bundles import FileToolStore


class ToolsetQuery:
    def __init__(self, *, targets: Iterable[str], store: FileToolStore):
        self._targets = frozenset(targets)
        self._store = store

    def current(self, target_id: str) -> dict[str, str]:
        """{"targetId", "toolVersion", "updatedAt"}"""
        self._known(target_id)
        pointer = self._store.pointer(target_id)
        if pointer is None:
            raise ToolsetNotFound(f"아직 tool 버전이 없다: {target_id}")
        return {"targetId": target_id, **pointer}

    def bundle(self, target_id: str, tool_version: str | None = None) -> dict[str, Any]:
        """{"targetId", "toolVersion", "manifest", "tools"}. tool_version이 없으면 현재 버전."""
        if tool_version is None:
            tool_version = self.current(target_id)["toolVersion"]
        else:
            self._known(target_id)
            if not self._store.has_version(target_id, tool_version):
                raise ToolVersionNotFound(f"없는 tool 버전: {target_id}/{tool_version}")
        try:
            toolset = self._store.read(target_id, tool_version)
        except ReaderNotFound as e:
            # 디렉터리는 있는데 읽을 수 없다 → 저장소가 망가졌다
            raise StorageError(str(e)) from None
        return {"targetId": target_id, "toolVersion": tool_version, "manifest": toolset.manifest,
                "tools": toolset.tools}

    def _known(self, target_id: str) -> None:
        if target_id not in self._targets:
            raise TargetNotFound(f"등록되지 않은 타겟: {target_id}")
