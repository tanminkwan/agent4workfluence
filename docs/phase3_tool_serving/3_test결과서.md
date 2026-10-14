# Phase 3 test 결과서 — tool 서빙 (정의 조회 API + 공용 MCP server)

- 작성일: 2026-10-14
- 상태: 사용자 검토 대기 (미커밋)
- 요구사항: [`1_요구사항정의.md`](1_요구사항정의.md), 설계: [`2_설계.md`](2_설계.md) (구현 중 바뀐 것은 16절 I1~I7)

## 1. 실행한 명령과 결과

모든 시험은 container 안에서 돌렸다. "이미지"는 이미지를 다시 빌드한 뒤 이미지 안 코드로 돌린 것이다. "--src"는 작업 트리를 붙여 돌린 것이다.

| # | 명령 | 결과 |
|---|---|---|
| 1 | `scripts/build.sh detection test`, `… detection runtime`, `… manufacture test`, `… manufacture runtime`, `… mcp test` | 모두 성공. `mcp` 이미지 빌드의 `pip check` 통과(`mcp==2.3.0`과 고정 판 충돌 없음) |
| 2 | `scripts/test.sh detection` (이미지) | **374 passed**, 9 skipped |
| 3 | `scripts/test.sh manufacture` (이미지) | **383 passed**, 9 skipped |
| 4 | `scripts/test.sh mcp` (이미지) | **287 passed**, 6 skipped |
| 5 | `docker compose up -d --force-recreate manufacture` 뒤 `curl …/v1/targets/workfluence/toolset/current` | `200`, `ETag: "tv-000002-8a2b35b1"`. `…/toolset`은 tool 30개 |
| 6 | `docker compose up -d --force-recreate detection` 뒤 `A4W_LIVE_TESTS=1 scripts/test.sh detection` | **379 passed**, 4 skipped (검증 API 실호출 포함) |
| 7 | `A4W_LIVE_TESTS=1 scripts/test.sh mcp` | **289 passed**, 4 skipped (MCP live 2개 포함) |
| 8 | `A4W_LIVE_TESTS=1 scripts/test.sh manufacture` | **392 passed** (185.1초, 모든 연결 반영 포함) |

- skipped는 `A4W_LIVE_TESTS` 없이 도는 live 시험과, 그 이미지에 비밀이 없는 LLM 녹화 시험이다.
- Detection·Manufacture 이미지는 MCP SDK를 넣지 않으므로 `packages/mcp` 시험을 모으지 않는다(설계 I6). 숫자에 MCP 시험은 들어가지 않는다.
- 시험 수 변화(Phase 2 끝 대비): Detection +3(`build_target_caller` 시험), Manufacture +12(조회 API 9 + 조립 3), MCP 새로 74(`packages/mcp/tests`, live 2개는 skipped).

## 2. TDD 기록

| 단위 | red 확인 | green |
|---|---|---|
| 조회 API (`test_toolset_api.py`) | 9 failed (라우트 없음) | 9 passed. 시험 하나는 고쳤다: cs-2는 삭제라 보류로 끝나 새 버전이 생기지 않는다 → 승인 뒤 정리 반영으로 두 번째 버전을 만든다 |
| `build_target_caller` | 수집 오류 (모듈 없음) | 통과. 그 뒤 Detection `wiring`을 이것으로 바꿨고 Detection 시험이 그대로 통과 |
| `snapshot` | 수집 오류 (모듈 없음) | 통과 |
| `config` | **확인하지 않았다** — 시험과 구현을 같이 썼다 | 통과 |
| `source` | 수집 오류 (모듈 없음) | 통과 |
| `refresher`·`catalog` | 수집 오류 (모듈 없음) | 통과 |
| `server`·`mapping`·`runner`·`notifier` | 수집 오류 (모듈 없음) | 처음 1 failed: handshake 판 알림이 오지 않음 → 원인은 요청마다 새로 만드는 `ServerSession`을 약한 참조로 잡은 것(설계 I2). 고친 뒤 통과 |
| `wiring`·stdio 진입점 | 수집 오류 (모듈 없음) | 통과. stdio 시험이 가끔 실패했다: 첫 갱신이 끝나기 전에 상태를 읽는 경쟁 → 시험이 첫 확인(`lastCheckedAt`)을 기다리게 고침. 그 뒤 3회 연속 통과 |

## 3. 인수 기준

| # | 기준 | 결과 | 근거 |
|---|---|---|---|
| AC-T1 | 조회 API 3개, 파일 내용과 같음, 거절 코드 | **충족** | `test_toolset_api.py`(현재 버전·`304`, 묶음 = 파일, 지정 버전 `immutable`, `TARGET_NOT_FOUND`·`TOOLSET_NOT_FOUND`·`TOOL_VERSION_NOT_FOUND`·`VALIDATION_ERROR`, 망가진 묶음 `STORAGE_ERROR`, bearer). compose 실행 확인(명령 5) |
| AC-T2 | 반쯤 쓴 버전이 나오지 않음, 되돌리기 뒤 되돌린 버전 | **충족** | `test_staging_is_never_served`, `test_rollback_moves_current` |
| AC-T3 | `tools/list` 모양, 숨긴 칸 없음, `_meta` | **충족** | `test_list_tools_shape`(두 판), live 1단계 |
| AC-T4 | scope에 없는 tool은 목록에 없고 실행 안 됨 | **충족** | `test_unknown_and_hidden_tools_are_protocol_errors`(두 판), `test_exposure_rules` |
| AC-T5 | 버전이 바뀌면 교체·알림, 진행 중 호출은 옛 정의 | **충족** | `test_new_version_replaces_and_notifies`, `test_modern_listen_gets_change_event`, `test_legacy_session_gets_list_changed_notification`, `test_call_in_flight_keeps_its_snapshot` |
| AC-T6 | revision 불일치 → 다시 받기 → 거절, 타겟 호출 없음 | **충족** | `test_revision_mismatch_*`, live 5단계 |
| AC-T7 | 조회 API가 멈춰도 계속, 상태에 실패, 검사 실패 묶음은 안 씀 | **충족** | `test_unavailable_keeps_previous`, `test_rejected_bundle_keeps_previous`, `test_stdio_starts_without_manufacture` |
| AC-T8 | 실행 결과·오류가 Phase 2 실행기와 같음, 맥락 값은 agent 설정에서 | **충족(일부는 Phase 2 시험에 기댐)** | MCP를 거쳐 본 것: 단순·상위 tool, `INPUT_INVALID`, `CONTEXT_MISSING`, 타겟 404. 409 안내문·429 대기는 MCP를 거쳐 따로 보지 않았다. 같은 `ToolExecutor`를 그대로 부르므로 Phase 2 실행기 시험(`packages/tools/tests`)에 기댄다 |
| AC-T9 | 시험 스택 실호출: 읽기·상위·쓰기(달기→지우기), 자료 정리 | **충족** | 4절 |
| AC-T10 | token이 로그·결과·오류·조회 요청에 없음, Manufacture에 타겟 token 없음 | **충족** | `test_build_serves_and_runs_with_agent_settings`(조회 요청에 `Authorization` 없음, 로그·`repr`에 token 없음), `test_call_log_has_no_token_or_body`, live 로그 전체에서 agent token 문자열 0건(값은 출력하지 않고 셈). compose의 manufacture 설정은 바꾸지 않았다(Phase 2 AC-14 그대로) |
| AC-T11 | `a4w_mcp`에 타겟 고유 값·하드코딩 없음, `a4w_tools`는 MCP를 import하지 않음 | **충족** | `test_isolation.py`(소스 검사). 코드에 남은 상수: `_meta` 앞머리 `a4w/`, 상태 resource URI `a4w://status`, server 이름 `a4w-mcp` — 식별 규칙이라 설정에서 뺐다(설계 7.1) |
| AC-T12 | Phase 1·2 시험 그대로 통과 | **충족** | 명령 2·3·6·8 |

## 4. 실호출 (AC-T9)

- 대상: 시험 스택 `https://wf.internal:8444`, agent 계정 `aiagent`(token은 `config/secrets/agents/aiagent/workfluence.env`), `homeSpace` "AI Agent 작업공간"(`f05f841a-…`).
- 정의: compose의 manufacture(`http://host.docker.internal:18002`), 묶음 `tv-000002-8a2b35b1`(tool 30개, `read`·`write`).
- MCP server: 같은 시험 container 안에서 `python -m a4w_mcp`를 stdio 자식 프로세스로 띄웠다. 프로토콜은 2026-07-28과 handshake 판(2025-11-25) 둘 다로 붙었다.

server의 stderr 로그(두 번째 실행, `requestId`는 생략):

```
a4w-mcp ready: agent=aiagent targets=workfluence scopes={'workfluence': ['read', 'write']}
toolset replaced: workfluence=tv-000002-8a2b35b1 served=30 hidden=0
call tool=workfluence__search_pages revision=r-feb86a5c02ec ok=true code=- status=200 attempts=1
call tool=workfluence__read_page_by_title revision=r-91591a9069a1 ok=true code=- status=- attempts=-
call tool=workfluence__add_comment revision=r-26de2be39ca7 ok=true code=- status=201 attempts=1
call tool=workfluence__delete_comment revision=r-8da5580e101f ok=true code=- status=200 attempts=1
call tool=workfluence__search_pages revision=r-feb86a5c02ec ok=false code=REVISION_MISMATCH expected=r-000000000000
call tool=workfluence__search_pages revision=r-feb86a5c02ec ok=true code=- status=200 attempts=1
```

- 바탕 페이지 `a4w 시험 바탕 페이지`(`MCP_LIVE_PAGE_TITLE`)는 첫 실행에서 `create_page`로 만들었다(`createdAt` 2026-10-14T03:55:44Z, 스페이스 `f05f841a-…`). 뒤 실행은 검색으로 찾아 썼다. 지우지 않고 남긴다(사용자 결정, 설계 15절 3번).
- 시험이 단 댓글은 매번 지웠다. 시험 뒤 그 페이지의 댓글 목록은 0건이다.
- 상위 tool 줄의 `status`·`attempts`가 `-`인 것은, 상위 tool 결과의 `meta`가 step별이라 한 줄에 상태를 싣지 않기 때문이다.

## 5. 구현 중 바뀐 설정·파일

| 바뀐 것 | 내용 |
|---|---|
| agent token | `config/secrets/targets/workfluence.agent.env` → `config/secrets/agents/aiagent/workfluence.env`(600, 디렉터리 700) |
| agent 설정 | `config/agents/aiagent/workfluence.env`(새 파일): `AGENT_CONTEXT`(타겟 설정에서 옮김), `AGENT_TOOL_MAX_RISK=danger`, `AGENT_TOOL_GROUPS=*`. `.gitignore`에 `config/agents/**/*.env` |
| 타겟 설정 | `config/targets/workfluence.env(.example)`에서 `AGENT_CONTEXT` 뺌 |
| `.env`·`.env.example` | `MCP_*` 변수(설계 3.2), `NO_PROXY`에 `manufacture` |
| 스크립트 | `build.sh`: `packages/<s>/Dockerfile`도 찾는다. `test.sh`: `agents/<s>/src`는 있을 때만, live 시험에 `<SERVICE>_AGENTS_DIR`이면 `config/agents`를 붙인다 |
| compose | 바꾸지 않았다(공용 MCP server의 compose 자리는 Phase 4 `inference`) |
| 문서 | Phase 2 요구사항 FR-282·307·335, NFR-204, 설계 6.3.1·13.1·15·15.2, 시스템아키텍처 5.4·6절, CLAUDE.md 설정·명령 줄, Manufacture Dockerfile 주석 (설계 14절) |

## 6. 확인하지 못한 것

- **Streamable HTTP 전송**: 자리만 두었다(`MCP_TRANSPORT=streamable_http`는 설정 오류). 시험하지 않았다.
- **409 안내문·429 대기를 MCP를 거쳐**: 따로 시험하지 않았다(AC-T8). 같은 실행기라 Phase 2 시험에 기댄다.
- **조회 API가 실제로 바뀌는 중의 교체**: 단위 시험(가짜 Manufacture)으로만 봤다. 실호출에서는 묶음이 한 번도 바뀌지 않았다(`tv-000002` 그대로). live에서 `list_changed` 알림은 보지 않았다.
- **bus 알림이 handshake 판 session에도 가는지**: 확인하지 않았다. 두 길로 다 보내고, 시험은 판별로 한 길씩만 본다.
- **여러 client가 한 server에 붙는 경우**: stdio라 하나뿐이다. handshake 판 알림은 마지막 session에만 간다(설계 I2).
- **`MCP_MAX_CONCURRENT_CALLS`로 실제로 동시 호출이 묶이는지**: 시험하지 않았다.
- **Phase 4 쪽 사용**: agent가 AI Pro Plus 검색 결과의 revision을 `a4w/expectedRevision`에 넣는 흐름은 Phase 4에서 붙인다. 지금은 시험이 값을 직접 넣었다.
- **지운 댓글이 Workfluence에 어떻게 남는지**(감사로그 등): 보지 않았다. 목록에서는 사라졌다.
