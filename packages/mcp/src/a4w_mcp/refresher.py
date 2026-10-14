"""정의 받기·검사·교체·알림 (Phase 3 설계 6.4절).

조회 HTTP는 동기 client라 worker thread에서 부른다. 교체는 event loop에서 한다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

import anyio
import anyio.to_thread

from .catalog import ServingCatalog
from .snapshot import Exposure, ToolsetRejected, build_snapshot, validate_bundle
from .source import ToolsetMissing, ToolsetSource, ToolsetUnavailable

log = logging.getLogger(__name__)

MISSING = "TOOLSET_MISSING"
UNAVAILABLE = "TOOLSET_UNAVAILABLE"


class Notifier(Protocol):
    async def tools_changed(self) -> None: ...


class Refresher:
    def __init__(self, *, source: ToolsetSource, exposures: Mapping[str, Exposure], catalog: ServingCatalog,
                 notifier: Notifier, refresh_seconds: float, retry_seconds: float):
        self._source = source
        self._exposures = dict(exposures)
        self._catalog = catalog
        self._notifier = notifier
        self._refresh = refresh_seconds
        self._retry = retry_seconds
        self._accepted: dict[str, tuple[str, dict[str, Any]]] = {}  # targetId → (toolVersion, 정의)
        self._etags: dict[str, str] = {}
        self._lock = anyio.Lock()

    async def run_forever(self, sleep: Callable[[float], Awaitable[None]] = anyio.sleep) -> None:
        while True:
            ok = await self.refresh_once()
            await sleep(self._refresh if ok else self._retry)

    async def refresh_now(self) -> bool:
        """revision 불일치 때 바로 확인한다 (8.3). 도는 갱신이 있으면 끝나기를 기다린 뒤 한 번 더."""
        return await self.refresh_once()

    async def refresh_once(self) -> bool:
        """모든 타겟이 버전을 가졌고 이번 확인에 오류가 없으면 True."""
        async with self._lock:
            changed: list[str] = []
            ok = True
            for target_id in self._exposures:
                error = await self._check(target_id, changed)
                self._catalog.checked(target_id, error)
                ok = ok and error is None
            if changed:
                snapshot = build_snapshot(self._accepted, self._exposures)
                self._catalog.replace(snapshot, changed)
                log.info("toolset replaced: %s served=%d hidden=%d",
                         ",".join(f"{t}={snapshot.tool_versions[t]}" for t in changed), len(snapshot.served),
                         len(snapshot.hidden))
                await self._notifier.tools_changed()
            return ok and len(self._accepted) == len(self._exposures)

    async def _check(self, target_id: str, changed: list[str]) -> str | None:
        try:
            answer = await anyio.to_thread.run_sync(self._source.current_version, target_id,
                                                    self._etags.get(target_id))
            version = answer.tool_version
            if version is None or version == self._accepted.get(target_id, (None,))[0]:
                return None
            bundle = await anyio.to_thread.run_sync(self._source.fetch, target_id, version)
            tools = validate_bundle(target_id, version, bundle)
        except ToolsetMissing:
            return MISSING
        except ToolsetUnavailable as e:
            log.warning("toolset unavailable: target=%s %s", target_id, e)
            return UNAVAILABLE
        except ToolsetRejected as e:
            # 받은 묶음을 버리고 이전 것을 쓴다. ETag를 남기지 않아 다음 확인에서 다시 받는다
            log.error("toolset rejected: target=%s version=%s code=%s %s", target_id, version, e.code, e)
            return e.code
        self._accepted[target_id] = (version, tools)
        if answer.etag:
            self._etags[target_id] = answer.etag
        changed.append(target_id)
        return None
