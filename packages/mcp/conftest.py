"""MCP SDK가 없는 이미지(detection·manufacture)에서는 이 패키지의 시험을 모으지 않는다.

그 이미지들도 packages/* 시험을 돌리지만 MCP SDK는 넣지 않는다(Phase 3 설계 10.1절).
"""

import importlib.util

collect_ignore_glob = [] if importlib.util.find_spec("mcp") else ["tests/*"]
