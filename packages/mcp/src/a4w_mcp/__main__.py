"""진입점: `python -m a4w_mcp` (또는 a4w-mcp). agent가 자식 프로세스로 띄운다 (Phase 3 설계 D2).

stdout은 MCP stdio 통로다. 로그는 stderr로만 쓴다. 설정은 환경변수와 설정 파일에서 읽는다(3절).
"""

from __future__ import annotations

import logging
import os
import sys

import anyio
from mcp.server.stdio import stdio_server

from a4w_core.config import Settings
from a4w_core.errors import A4WError

from .wiring import McpApp, build


def main() -> None:
    try:
        level = Settings(os.environ, origin="environment").require("LOG_LEVEL").upper()
    except A4WError as e:
        print(f"start failed: code={e.code} {e}", file=sys.stderr)
        sys.exit(1)
    logging.basicConfig(stream=sys.stderr, level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        app = build(os.environ)
    except A4WError as e:
        # 값(비밀)이 아니라 이유만 남긴다. 설정 오류 메시지는 변수 이름만 담는다
        logging.getLogger("a4w_mcp").error("start failed: code=%s %s", e.code, e)
        sys.exit(1)
    anyio.run(serve, app)


async def serve(app: McpApp) -> None:
    async with stdio_server() as (read, write), anyio.create_task_group() as tg:
        tg.start_soon(app.refresher.run_forever)
        await app.server.run(read, write, app.server.create_initialization_options())
        tg.cancel_scope.cancel()


if __name__ == "__main__":
    main()
