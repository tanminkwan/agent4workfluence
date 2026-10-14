"""stdio 진입점 (Phase 3 설계 D2·9절): 실제 자식 프로세스로 띄운다. stdout은 MCP 통로, 로그는 stderr.

Manufacture에 닿지 않는 주소를 주면 tool 없이 뜨고 상태에 실패가 드러나야 한다(FR-T16).
"""

from __future__ import annotations

import json
import os
import sys

import anyio
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from ..unit.test_wiring import files

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_stdio_starts_without_manufacture(tmp_path):
    env = files(tmp_path)
    # 시험용 타겟 어댑터 없이도 뜨도록 진짜 Workfluence 어댑터 이름을 쓴다 (설치된 entry point)
    (tmp_path / "t" / "wf.env").write_text(
        (tmp_path / "t" / "wf.env").read_text().replace("generic", "workfluence").replace("jwtish", "config")
        + "TARGET_TOKEN_SCOPES=read\n", encoding="utf-8")
    env.update(MCP_MANUFACTURE_URL="http://127.0.0.1:9", MCP_REFRESH_RETRY_SECONDS="1")
    params = StdioServerParameters(command=sys.executable, args=["-m", "a4w_mcp"],
                                   env={**{k: v for k, v in os.environ.items() if k in ("PATH", "PYTHONPATH")}, **env})
    async with Client(params, mode="legacy") as c:
        assert (await c.list_tools()).tools == []  # 받기 전에도 바로 응답한다
        with anyio.fail_after(10):
            while True:  # 첫 확인이 끝날 때까지 (갱신은 server와 따로 돈다)
                status = json.loads((await c.read_resource("a4w://status")).contents[0].text)
                if status["targets"]["wf"]["lastCheckedAt"]:
                    break
                await anyio.sleep(0.1)
    assert status["targets"]["wf"]["lastError"] == "TOOLSET_UNAVAILABLE"
    assert status["scopes"] == {"wf": ["read"]}
