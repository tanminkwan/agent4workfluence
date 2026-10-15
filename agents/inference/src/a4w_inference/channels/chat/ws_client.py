"""WebSocket 연결 한 번의 수명 (Phase 5 설계 4.1절, FR-C01·C02).

재연결은 supervisor 몫이다. 이 모듈은 연결해서 프레임을 내다가, 끝나면 ConnectionEnded로 끝난 이유만 알린다.
token은 Authorization 헤더로만 보낸다(주소의 질의 문자열로 보내면 서버가 401). token은 repr·오류에 남기지 않는다.
"""

from __future__ import annotations

import logging
import ssl as ssl_module
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .frames import parse_frame
from .models import Frame

log = logging.getLogger(__name__)


class ConnectionEnded(Exception):
    """연결이 끝났다. kind: closed(닫힘 코드) | rejected(연결 단계 HTTP 거절) | network(연결 못 함·시간 초과)."""

    def __init__(self, kind: str, *, code: int | None = None, http_status: int | None = None):
        super().__init__(kind)
        self.kind = kind
        self.code = code
        self.http_status = http_status

    @property
    def label(self) -> str:
        if self.kind == "closed":
            return f"closed:{self.code}"
        if self.kind == "rejected":
            return f"rejected:{self.http_status}"
        return self.kind


_WS_SCHEMES = {"https": "wss", "http": "ws"}


def chat_ws_url(rest_base_url: str) -> str:
    """REST 기준 주소(…/api/v1)에서 채팅 WebSocket 주소를 만든다."""
    parts = urlsplit((rest_base_url or "").strip())
    scheme = _WS_SCHEMES.get(parts.scheme)
    if scheme is None or not parts.netloc:
        raise ValueError("채팅 API 주소는 http 또는 https 주소여야 한다")
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") + "/chat/ws", "", ""))


class WsConnection:
    def __init__(self, url: str, token: str, *, ssl_context: ssl_module.SSLContext | None = None, open_timeout: float = 10.0,
                 ping_interval: float | None = 20.0, ping_timeout: float | None = 20.0, max_frame_bytes: int = 1 << 20,
                 connect: Callable[..., Any] | None = None):
        self._url, self._token, self._ssl = url, token, ssl_context
        self._open_timeout, self._ping_interval, self._ping_timeout = open_timeout, ping_interval, ping_timeout
        self._max_size = max_frame_bytes
        self._connect = connect

    def __repr__(self) -> str:
        return f"WsConnection(url={self._url!r})"

    async def frames(self) -> AsyncIterator[Frame]:
        from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

        connect = self._connect
        if connect is None:
            from websockets.asyncio.client import connect
        kwargs: dict[str, Any] = dict(additional_headers={"Authorization": f"Bearer {self._token}"}, open_timeout=self._open_timeout,
                                      ping_interval=self._ping_interval, ping_timeout=self._ping_timeout, max_size=self._max_size)
        if self._ssl is not None:
            kwargs["ssl"] = self._ssl
        code: int | None = None
        try:
            async with connect(self._url, **kwargs) as ws:
                async for raw in ws:
                    yield parse_frame(raw)
                code = ws.close_code
        except InvalidStatus as e:
            raise ConnectionEnded("rejected", http_status=e.response.status_code) from None
        except ConnectionClosed as e:
            raise ConnectionEnded("closed", code=e.rcvd.code if e.rcvd else 1006) from None
        except (OSError, TimeoutError, InvalidHandshake, ValueError) as e:
            log.info("chat websocket connect failed: %s", type(e).__name__)
            raise ConnectionEnded("network") from None
        raise ConnectionEnded("closed", code=code if code is not None else 1000)
