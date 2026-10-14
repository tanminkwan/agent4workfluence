"""공용 MCP server (Phase 3 설계). Manufacture tool 정의 조회 API에서 정의(JSON)를 받아 tools/list로 내주고,
tools/call은 agent 자기 token으로 공통 실행기(a4w_tools)를 써서 타겟을 직접 부른다. token은 밖으로 내보내지 않는다.
"""
