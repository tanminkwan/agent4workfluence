"""tool 목록 바뀜 알림 (Phase 3 설계 7.4절). 프로토콜 판마다 길이 다르다.

- 2026-07-28: subscriptions/listen 흐름 (bus에 publish)
- handshake 판(2025-11-25 이하): session에 notifications/tools/list_changed.
  SDK 2.x의 ServerSession은 요청마다 새로 만드는 대리 객체이고, 알림은 연결 단위 통로로 나간다. 연결 객체는
  공개되지 않았으므로 마지막으로 본 session 하나를 잡아 둔다. stdio는 연결이 하나다(Phase 3 설계 7.4, I2).
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

log = logging.getLogger(__name__)


class ChangeNotifier:
    def __init__(self) -> None:
        self.bus = InMemorySubscriptionBus()
        self.listen_handler = ListenHandler(self.bus)
        self._session: Any = None

    def remember(self, ctx: Any) -> None:
        if getattr(ctx, "protocol_version", None) in HANDSHAKE_PROTOCOL_VERSIONS and ctx.session is not None:
            self._session = ctx.session

    async def tools_changed(self) -> None:
        await self.bus.publish(ToolsListChanged())
        session = self._session
        if session is None:
            return
        try:
            await session.send_tool_list_changed()
        except Exception as e:  # 끊긴 연결이다. 다음 요청이 새 session을 알려 준다
            log.info("list_changed not delivered: %s", type(e).__name__)
            self._session = None
