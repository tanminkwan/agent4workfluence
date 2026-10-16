# Phase 2 test 결과서 — Manufacture (`manufacture`) + Detection 보강

- 작성일: 2026-10-13
- 대상: [`1_요구사항정의.md`](1_요구사항정의.md), [`2_설계.md`](2_설계.md) (구현 중 바뀐 것은 설계 22절)
- 실행 환경: 모든 시험은 container 안에서 돌렸다(host에 Python 설치 없음). 이미지 `a4w/detection:0.1.0-test`, `a4w/manufacture:0.1.0-test`, 베이스 `python:3.12-slim`.
- 외부: Workfluence 시험 스택 `https://wf.internal:8444`(명세 sha256 `8c83f9c6…`, 동작 75개), AI Pro Plus 시험 서버(host `28000`, nobody 모드), 사내 OpenAI 호환 LLM(`zai-org/GLM-5.2`, host proxy 경유)

## 1. 실행한 명령과 결과

| # | 명령 | 결과 |
|---|---|---|
| 1 | `scripts/build.sh detection test && scripts/test.sh detection` | **368 passed**, 9 skipped(live), 0 failed (8.2절 peer 반영 뒤) |
| 2 | `scripts/build.sh manufacture test && scripts/test.sh manufacture` | **371 passed**, 9 skipped(live), 0 failed (8.2절 peer 반영 뒤) |
| 3 | `A4W_LIVE_TESTS=1 scripts/test.sh detection -q agents/detection/tests/live` | **5 passed** (Phase 1 실호출 2 + 검증 API 실호출 3) |
| 4 | `A4W_LIVE_TESTS=1 scripts/test.sh manufacture --src -q -s agents/manufacture/tests/live/test_live_llm.py` | **3 passed** (313.8초, LLM 실호출) |
| 5 | `A4W_LIVE_TESTS=1 scripts/test.sh manufacture --src -q -s agents/manufacture/tests/live/test_live_added_api.py` | **1 passed** (56.0초, LLM + Detection 검증 + AI Pro Plus) |
| 6 | compose 흐름(3절): 첫 반영 `POST /v1/apply` → 결과 조회 | `partial` (28 반영, 2 보류), 아래 3절 |
| 7 | `docker compose exec manufacture python -m a4w_manufacture.evaluate_index --target workfluence` | 적중률 **0.927** (51/55), 기준 0.9 통과, 종료 코드 0 |
| 8 | 위험 동작 7개 검증 1회 (Detection 검증 API 직접 호출, 4절) | 7 passed, 정리 ok |
| 9 | `packages/clients/tests/live/test_live_llm_record.py` (A4W_LIVE_TESTS=1, 녹화본을 `tests/contract/recordings/`에) | **4 passed**: 실제 엔드포인트 응답 녹화(정상·`length`·401), 시간 초과 분류 |
| 10 | `A4W_LIVE_TESTS=1 scripts/test.sh manufacture --src -q -s agents/manufacture/tests/live/test_live_apply.py` | **1 passed** (293.2초). 모든 연결 실제: LLM·Detection·AI Pro Plus (3.1절) |

- 1·2의 수에는 공유 패키지(`packages/core`, `tools`, `clients`, `targets/workfluence`) 시험이 양쪽 이미지에 함께 들어 있다. Phase 1 시험(159건)은 1에 포함되어 그대로 통과했다(FR-337).
- TDD: 묶음마다 실패하는 시험을 먼저 올리고(red: 모듈 없음·기대 불일치) 통과시켰다(green). 각 단계의 red 확인은 작업 기록에만 있고 이 문서에 수를 남기지 않는다.

### 1.1 묶음별

| 묶음 | 위치 | 내용 |
|---|---|---|
| `a4w_tools` | `packages/tools/tests` | 정의·manifest 스키마, 맥락 고정값·`omitted`, 요청 만들기, 응답 가공, 호출 규약(한도·429·5xx·쓰기 시간 초과·접속 실패), 실행기(단순·상위·handler 자리), `ToolsetReader`, `RawCaller` |
| `a4w_clients` | `packages/clients/tests` | LLM 계약(정상, 스키마 불일치 후 재시도, JSON 아님, `length` 계속, 401·403, 429 대기·소진, 시간 초과, 5xx, json_schema 모드, reasoning_effort, 자리만 둔 provider), AI Pro Plus(컬렉션·도메인 준비, 앞머리 규약, upsert·delete·목록·검색, 5xx 재시도, bearer 헤더) |
| core·Workfluence 어댑터 | `packages/core/tests`, `packages/targets/workfluence/tests` | 열린 registry, 파일 잠금(키·스레드 넘김), config scope, JWT scope, 오류 해석·안내문, 시험 스페이스 준비(잔재 정리·바탕 자료·첨부·버전 다시 읽기)·정리·실패 보고 |
| Detection 보강 | `agents/detection/tests` | 받은 정의 검사(형식·금지 scope·명세에 없는 경로·쓰기 위치·키), 시나리오 값 채우기, 자동 시나리오, 검증 순서·판정·정리, 맥락=시험 환경, 접수 멱등·잠금·복구·기록, 타겟 정보, 검증·타겟 정보 API, wiring(비밀 있음·없음) |
| manufacture | `agents/manufacture/tests` | 대상 선정(설계 5.2절 수 그대로), 기계 변환, 조립·검사(숨김·`omitted`·보이는 칸 상한·id 든 예시 등), 지문·카탈로그, LLM 보조(이름 고정·칸 설명 옮기기·재시도·재사용·상한), Detection 클라이언트, 색인 맞추기, 반영 흐름 18건, 실제 카탈로그 고정 자료 4건, seed 도구(저장소 seed = 명세로 다시 만든 것), 색인 평가, tool-store·SQLite, API 13건, wiring, 단절(import·설정) |

## 2. 인수 기준

| # | 기준 | 결과 | 근거 |
|---|---|---|---|
| AC-1 | 첫 반영은 seed 그대로, LLM 없음, 대상·제외가 맞음 | **충족** | 고정 자료 시험(added 30 = 단순 27 + 상위 3, ADMIN 12·STRUCTURE 14·NOT_NEEDED 3·SPEC_META 3·GROUP_DISABLED 7, codeOwned 9) + compose 실반영(3절, LLM 호출 0) |
| AC-1a | 바뀐 동작만 LLM, 이름 유지 | **충족** | 단위 시험(가짜 LLM). Workfluence는 구조 변경이 needsReview라 결과는 보류(`SPEC_REVIEW`)이고, 승인 뒤 정리 반영으로 들어감 |
| AC-2 | 정의 스키마 통과, 잘못된 LLM 출력 거절 | **충족** | 검사 코드 전부 단위 시험(매핑 변경, 칸 누락·지어냄, 필수 칸 숨김, 기본값 오류, 이름, id 든 예시, 글 길이, 보이는 칸 상한) |
| AC-3 | 추가분만 더하고 기존 이름 그대로 | **충족** | 단위 시험(추가 동작 → create, 기존 tool revision 그대로) |
| AC-3a | 66 → 75는 채팅 9개 `codeOwnedChanges`만 | **충족** | 고정 자료 시험(두 명세 차이는 added 9뿐, 결과 noop) |
| AC-3b | 추가 API가 제대로 반영 (1)~(6) | **충족** (조건 있음) | (1)~(4) LLM 실호출 3건(실제 동작 1 + 가상 동작 2). (5)(6) 실제 동작 `comments.list`: Detection 검증 passed, 예시 질의 3건 순위 1·2·1. 가상 동작은 시험 스택에 없어 (5)(6)을 하지 않음(요구사항 7절) |
| AC-4 | 삭제·scope 변경·위험 동작 보류, 나머지 반영 | **충족** | 단위 시험(삭제·위험·거절 후 재보류 안 함) + 고정 자료(위험 7개 보류). scope 변경은 판단 코드만 있고 시험은 단위 수준 |
| AC-5 | 순서·동시·없는 변경분·없는 타겟 오류, 멱등 | **충족** | API 시험(409 `CHANGESET_OUT_OF_ORDER`·`APPLY_IN_PROGRESS`(applyId), 404 두 가지, 같은 changeSetId 200, 실패한 반영은 다시 실행) |
| AC-6 | 도중 실패 시 포인터 그대로, 되돌리기 | **충족** | 단위(검증 전체 실패 → 포인터·명세 버전·동작 상태 그대로) + API(되돌리기, head 위에 다음 반영) |
| AC-7 | `openai_compatible` 계약 시험 | **충족** | 실제 엔드포인트 녹화본 재생 3건(정상·`length`·401) + 손으로 만든 응답 15건(녹화로 만들 수 없는 경우: 스키마 불일치 후 정상, 429, 5xx 등). 실제 401은 `text/plain`에 `{"message"}`, `length`는 `content: null`이었다 |
| AC-8 | container 안 실제 LLM 호출 | **충족** | live 3건, 호출당 1회, 사고 토큰 1816~5199 |
| AC-9 | 검증 통과 tool만, 쓰기 자료 정리 | **충족** | 실반영: 28 passed만 묶음에 들어감, 시험 스페이스 정리 ok. 단위: 검증 실패 tool은 빠지고 다음 반영에서 다시 |
| AC-10 | 색인되고 예시 질의 기준 이상 | **충족** | 28건 색인, 평가 0.927 ≥ 0.9 (5절) |
| AC-11 | 실행기가 웹·MCP 없이 import | **충족** | `a4w_tools`는 fastapi·mcp를 import하지 않음(Detection이 라이브러리로 씀) |
| AC-12 | compose로 Detection → Manufacture 흐름 | **충족** | 3절 수동 실행 기록 |
| AC-13 | 하드코딩 없음 | **충족** (검토) | URL·포트·경로·token·모델명·한도는 설정. 남은 코드 상수: point ID 이름공간 UUID, 시험 스페이스 바탕 자료의 글, 상위 tool 후보 수(10), 응답 기본 상한(실행기 인자 기본 8000) — 식별 규칙·시험 자료 글이라 설정 대상에서 뺐다 |
| AC-14 | manufacture에 타겟 token·주소 없음, 검증은 Detection으로만 | **충족** | 단절 시험(import·설정 문자열), compose 설정(타겟 비밀·`wf.internal` 미부착) |
| AC-15 | Detection이 잘못된 요청 거절, 스페이스 정리, Phase 1 그대로 | **충족** | 단위·API·live(admin tool 거절, 검증 뒤 정리, Phase 1 159건) |

## 3. compose 흐름 실행 기록 (AC-12, AC-1, AC-9)

```bash
scripts/build.sh detection runtime && scripts/build.sh manufacture runtime
docker compose up -d detection manufacture
# Phase 1에서 만든 첫 변경분(빈 명세 → 8c83f9c6…). id는 결정적이다
CS=cs-1884c7c69cfc70ba39d9e2b1
curl -s -X POST localhost:18002/v1/apply -H 'Content-Type: application/json' \
     -d "{\"targetId\":\"workfluence\",\"changeSetId\":\"$CS\"}"
# → 202 {"status":"running","applyId":"ap-20261013T083235Z-e9a3bb",...}
curl -s localhost:18002/v1/applies/ap-20261013T083235Z-e9a3bb
```

| 항목 | 값 |
|---|---|
| `status` | `partial` (30초 안에 끝남) |
| `toolVersion` | `tv-000001-34e2b594` |
| `summary` | added 28, pending 2, excluded 39, codeOwned 9, failed 0 |
| 검증 (Detection) | passed 28, unverified 2, 시험 스페이스 `prepared: true`, `cleanup: ok` |
| 색인 | upserted 28, failed 0, drift false |
| LLM | 호출 0 (전부 seed) |
| 보류 | `mark_all_notifications_read`, `mark_notification_read` — 이유 `UNVERIFIED` |

- 보류 2개는 의도한 결과다. 검증 token이 사용자 본인(root) 계정이라, 알림 읽음 처리를 실제로 부르면 사용자의 실제 알림을 건드린다. 그래서 시나리오를 두지 않았고, 사람이 승인해야 들어간다(설계 22절 I17).
- 상위 tool 3개(제목으로 찾기)도 실제로 통과했다. 시험 바탕 페이지 제목에 검증 id를 넣어 검색이 하나로 좁혀졌다.
- 이 반영의 보류 2개는 승인하지 않고 그대로 두었다(사람 결정).

### 3.1 모든 연결을 실제로 붙인 반영 흐름 (명령 10)

ApplyService에 실제 LLM(GLM-5.2), 실행 중인 Detection(인증 없음 = 시험 모드, 시험 스택 실호출 검증), AI Pro Plus(nobody 모드)를 붙였다. 카탈로그 사본에서 seed 2개를 뺐고, 색인은 서빙 색인과 섞이지 않게 시험용 컬렉션 `a4w_tools_livetest`에 올린 뒤 문서를 지웠다.

| 단계 | 결과 |
|---|---|
| 1. 첫 반영 (Detection의 실제 첫 변경분) | `partial`: added 28 (seed 26 + **LLM create 2**: `list_comments`, `list_page_labels`), 보류 2(알림), 검증 passed 28·failed 0, 색인 28, LLM 호출 2 |
| 2. `comments.create`에 칸 하나를 더한 변경분 | LLM **update** 1회, 이름 `add_comment` 유지, 검증 passed, Workfluence 정책대로 보류 `SPEC_REVIEW` |

## 4. 위험 동작 묶음 실호출 (기본 꺼짐)

위험 동작은 기본 설정에서 tool이 되지 않아 3절 반영에서 검증되지 않는다. 시나리오가 실제로 맞는지 보려고, 저장소 seed 7개와 시나리오를 Detection 검증 API에 한 번 직접 넣었다.

| tool | 결과 | 호출 수 |
|---|---|---|
| delete_page | passed | 2 (만들기 → 지우기) |
| move_page | passed | 2 |
| restore_page_version | passed | 3 (만들기 → 고치기 → 되돌리기) |
| upload_attachment | passed | 1 |
| delete_attachment | passed | 2 |
| list_trashed_pages | passed | 1 |
| restore_trashed_page | passed | 3 (만들기 → 지우기 → 되살리기) |

시험 스페이스 정리 ok. pytest 시험이 아니라 일회성 확인이다(6절).

## 5. 색인 평가 (AC-10, FR-296)

| 지표 | 값 |
|---|---|
| 질의 | 55 (위험 동작 7건 제외) |
| 상위 5 적중 | 51 → **0.927** (기준 `INDEX_EVAL_MIN_HIT_RATE` 0.9) |
| 상위 1 적중 | 38 → 0.691 |
| 적중 점수 | 최소 0.597, 평균 0.787 |
| 놓친 질의의 1위 점수 | 0.803 ~ 0.816 |

- 놓친 4건은 모두 알림 읽음 tool 2개의 질의다. 이 tool들은 보류 중이라 색인에 없다. 색인된 tool만 보면 놓친 질의가 없다.
- 상위 1이 아닌 질의는 대부분 비슷한 tool끼리의 순서 차이다(예: "두 번째 버전 본문" → 1위 `compare_page_versions`, 2위 `read_page_version`).
- **score 컷은 정할 수 없다.** 놓친 질의의 1위 점수(0.80~0.82)가 적중 평균(0.79)보다 높다. 점수로 자르지 말고 상위 N개를 모델에게 주는 방식이 맞다는 근거다. 최종 결정은 Phase 4(설계 14.5절).

## 6. 확인하지 못한 것

| 항목 | 상태 |
|---|---|
| LLM 계약 시험의 응답 녹화 | (해결) 정상·`length`·401을 녹화해 재생 시험으로 쓴다. 스키마 불일치·429·5xx는 엔드포인트에서 일부러 만들 수 없어 손으로 만든 응답으로 남는다 |
| LLM의 429·제한 시간 실제 동작 | 엔드포인트가 429를 내는 상황을 만들지 못했다. 실제 제한 시간 초과도 겪지 않았다 |
| 실제 반영 흐름에서의 LLM 경로 | (해결) 3.1절: ApplyService 안에서 LLM create 2·update 1이 실제 연결로 돌았다. 다만 2단계 변경분은 Detection이 만든 것이 아니라 시험이 만든 것이다(시험 스택 명세가 바뀌지 않았다) |
| 위험 동작을 켠 반영 | 반영 흐름으로는 돌리지 않았다. Detection 검증만 일회성으로 확인했다(4절) |
| 승인 → 정리 반영의 실호출 | 단위·API 시험만. 시험 스택에서는 보류 2개를 승인하지 않았다 |
| 되돌리기의 실호출 | 단위·API 시험만 |
| bearer 인증 | 시험 모드로 생략한다(사용자 결정, 2026-10-13): manufacture↔Detection은 인증 없음, AI Pro Plus는 nobody. bearer는 단위 시험만 있다 |
| AI Pro Plus 운영 | 시험 서버(nobody)만 썼다 |
| 상위 tool 제안(`LLM_PROPOSE_COMPOSITES`) | 구현하지 않았다. 설정은 false로만 있다(설계 22절 I19) |
| 66 → 75 전환의 container 재현 | Phase 1에서 남은 항목. 이번에도 고정 자료로만 확인했다 |
| 검증 token | (해결) 처음에는 root 계정 token(read·write·admin)으로 시험했고, 뒤에 agent 전용 계정 `aiagent`(member)의 read·write token(만료 2027-10-13)으로 바꿨다. root token은 폐기됐다(2026-10-13). agent token 결과는 8절 |

## 7. 시험 스택·외부에 남은 것

- Workfluence 시험 스택: 검증마다 만든 시험 스페이스(`aiagent-manufacture-vf-…`)는 지웠고 휴지통에 30일 남는다(공개 API에 완전 삭제가 없다). 감사로그에 검증 호출이 남는다.
- AI Pro Plus: 컬렉션 `a4w_tools_workfluence`와 도메인 `a4w-tools-workfluence`를 만들었고, tool 28건이 올라가 있다. live 시험이 올린 시험용 문서(`livetest/…`)는 지웠다. 명령 10이 시험용 컬렉션 `a4w_tools_livetest`·도메인 `a4w-tools-livetest`를 만들었다(문서는 지워 비어 있다. 컬렉션 삭제는 구현하지 않았다).
- compose: `detection`, `manufacture`가 떠 있다. `spec-store` 볼륨의 파일 소유자를 새 실행 uid(1001)로 바꿨다(설계 22절 I3).

## 8. agent token으로 다시 확인 (2026-10-13)

검증 token을 agent 전용 계정 `aiagent`(member, read·write)로 바꾼 뒤 다시 돌렸다. Detection 타겟 정보의 scope는 `read, write`(admin 없음)다.

| 확인 | 결과 |
|---|---|
| Detection live (`A4W_LIVE_TESTS=1 scripts/test.sh detection --src -q agents/detection/tests/live`) | 5 passed |
| seed 34 + 상위 3 = 37개 전부 Detection 검증 API로 실호출 (일회성) | **37 passed**, 시험 스페이스 정리 ok |
| 모든 연결 반영 (명령 10, 반영 id를 실행마다 유일하게 고친 뒤) | 1 passed (96.8초): 첫 반영 `applied`, added 30, 검증 passed 30·unverified 0, 색인 30, LLM create 2. 바뀐 동작 반영: LLM update 1, 검증 passed, 보류 `SPEC_REVIEW` |
| compose 스택 정리 반영 (`POST /v1/apply {"targetId"}`) | `applied`, `tv-000002-8a2b35b1`: 보류였던 알림 tool 2개를 다시 검증해 passed, 색인 2 → 서빙 30개 전부 실검증 |
| 색인 평가 (명령 7) | **1.0** (55/55), 상위 1 0.745, 적중 점수 최소 0.597·평균 0.792 |

- 알림 tool 2개: 이전에는 검증 token이 사용자 본인 계정이라 시나리오를 두지 않았다(I17). agent 계정이 되어 시나리오를 넣었다. `mark_all_notifications_read`는 성공 경로로 검증한다. `mark_notification_read`는 검증 때 aiagent에게 온 알림을 만들 수 없다(자기 멘션은 알림이 생기지 않고, 다른 계정이 없다). 그래서 명세의 실패 경로(알림이 아닌 id → 404 `NOT_FOUND`)로 검증했다. 이 tool의 성공 경로는 8.2절에서 확인했다.

### 8.1 정정

앞선 판(커밋 `1d775eb`)은 "agent token으로 모든 연결 반영 1 passed(238.6초, 검증 passed 28)를 다시 확인했다"고 적었다. 이것은 틀렸다. 그 시험은 반영 id를 `ap-live-1`로 고정해 두어 검증 id(`vf-ap-live-1`)가 앞선 실행과 같았고, Detection은 멱등 규칙대로 새로 검증하지 않고 root token 때의 기록을 돌려주었다. 그래서 그 결과는 agent token 확인이 아니었다.
- 고친 것: live 시험의 반영 id를 실행마다 유일하게 했다(설계 22절 I27). Detection은 같은 검증 id로 **내용이 다른** 요청이 오면 옛 결과를 돌려주지 않고 거절한다(I25). 위 8절 표는 고친 뒤 다시 돌린 결과다.
- 운영의 반영 id는 시각 + 난수라 이 일이 생기지 않는다.

### 8.2 검증 상대 계정(peer)으로 알림 성공 경로 확인 (2026-10-13)

workf가 두 번째 계정 `aiagent-peer`(member, read·write, 만료 2027-10-13)를 발급했다. 비밀은 `config/secrets/targets/workfluence.peer.env`(600)에 있고 Detection에만 붙는다. 시험 환경 준비 단계는 다음과 같다(설계 11.6.1, I29). 시험 스페이스에 peer를 editor로 넣는다. peer가 검증 계정을 `@aiagent`로 멘션하는 댓글을 단다. 검증 계정의 `GET /notifications`에서 그 댓글의 알림 id를 찾아 `notificationId`로 넘긴다. 정리할 때는 peer를 먼저 빼고 스페이스를 지운다(다른 멤버가 있으면 owner도 삭제가 403이다). `mark_notification_read` 시나리오는 `${sandbox.notificationId}`로 성공(`ok: true`)을 기대하게 바꿨다.

| 확인 | 결과 |
|---|---|
| 단위 (`scripts/test.sh detection`, 이미지 재빌드 뒤) | 368 passed (peer 준비·정리, peer 없을 때 기존 동작, 알림을 못 찾을 때 시험 추가) |
| 단위 (`scripts/test.sh manufacture`, 이미지 재빌드 뒤) | 371 passed |
| Detection runtime 재빌드·재시작 후 37개 전부 검증 API로 실호출 (일회성) | **37 passed**, `prepared: true`, `cleanup: ok`. 로그: `POST /spaces/{id}/members` 201 → `POST /notifications/5fca6d75…/read` 201 → `DELETE /spaces/{id}/members/aiagent-peer` 200 → 스페이스 삭제 |
| Detection live | 5 passed |
| 모든 연결 반영 (명령 10) | 1 passed (136.9초) |

- 이제 `mark_notification_read`도 성공 경로로 검증된다. 실패 경로(404)는 시나리오에서 뺐다.
- peer 비밀이 없으면 준비 단계는 예전처럼 동작한다. 이때는 `notificationId` 값이 없다. 처음에는 이 시나리오가 `failed`(`SCENARIO_INVALID`)였는데, 8.3절에서 `unverified`로 바꿨다.
- 시험 스택에 남는 것: peer의 멘션 댓글은 시험 스페이스와 함께 휴지통으로 간다. 검증 계정의 알림 기록은 남는다.

### 8.3 맥락 키 값이 없는 환경은 `unverified` (2026-10-14)

사용자 결정으로 바꿨다(설계 I30). 시나리오가 `${sandbox.<키>}`를 쓰는데 이번 시험 환경에 그 값이 없으면 `unverified`(`SANDBOX_VALUE_MISSING`)다. 맥락 키인지는 guard가 요청 때 이미 확인한다. 그래서 값이 없다는 것은 환경이 그 값을 못 만들었다는 뜻이다(예: peer 비밀이 없음). manufacture는 `VERIFY_NO_SCENARIO`(기본 `pending`)에 따라 보류한다. `${saved.*}`·`${setup.*}` 값이 없는 것은 시나리오의 잘못이라 그대로 `failed`(`SCENARIO_INVALID`)다.

| 확인 | 결과 |
|---|---|
| 먼저 쓴 시험 3개 (red) | 맥락 키 값 없음(call·setup) 2 failed, 저장 값 없음 1 passed |
| `scripts/test.sh detection --src -q agents/detection/tests packages` | 371 passed, 9 skipped |
| `scripts/test.sh manufacture --src -q agents/manufacture/tests packages` | 371 passed, 9 skipped |

- 작업 트리를 붙여 container 안에서 돌렸다. 이미지는 다시 빌드하지 않았다.
- peer 비밀을 뺀 상태로 실호출하지는 않았다. 단위 시험과 코드 확인만 했다.

### 8.4 검증 전용 계정으로 바꿈 (2026-10-14)

Detection 검증 token과 agent token이 둘 다 `aiagent` 계정의 같은 token이었다. 사용자 결정에 따라 검증 계정을 따로 두었다. workf가 `aiagent-verify` 계정(member, active)을 만들고 `read write` token을 발급했다. 만료는 2027-10-14다. 만료 없음은 안 되고 서버 최대가 365일이다.

- **비밀 파일:** Detection 타겟 비밀 `config/secrets/targets/workfluence.env`(600)를 새 token으로 바꿨다. 이전 내용은 `workfluence.agent.env`와 같았으므로 잃은 것은 없다.
- **역할 분담:** `aiagent`는 agent 전용(`workfluence.agent.env`)으로 남기고, `aiagent-peer`는 검증 상대로 그대로 쓴다.
- **타겟 설정:** `VERIFY_AGENT_USERNAME`을 `aiagent-verify`로 바꿨다.
- **workf 답:** 호출 한도는 token 단위다(계정당 token 최대 10개). 채팅 보내기만 사용자 단위로 분당 60이다.

| 확인 | 결과 |
|---|---|
| Detection runtime 다시 빌드(`642d150` 포함), 다시 시작한 뒤 `GET /v1/targets/workfluence/profile` | `tokenScopes: [read, write]`, 검증 가능 |
| `A4W_LIVE_TESTS=1 scripts/test.sh detection --src -q agents/detection/tests/live` | 5 passed |
| seed tool 34개 전부를 새 검증 id `vf-verify-acct-20261014T010044Z`로 검증 API에 실호출 (시나리오 22개, 나머지는 자동 시나리오) | **34 passed**, `prepared: true`, `cleanup: ok` |

- 로그 순서: 시험 스페이스 생성 201 → peer 멤버 추가 201 → `POST /notifications/{id}/read` 201 → peer 빼기 → 스페이스 삭제 200. 준비 단계가 검증 계정 token으로 `GET /notifications`를 불러 peer 멘션의 알림을 찾았다. 그래서 `aiagent-verify`가 멘션 알림을 받는 것도 확인됐다.
- 정리 중 peer 빼기가 한 번 429(쓰기 한도)였다. `Retry-After`만큼 기다렸다가 다시 보내 200이 됐다. 검증 하나가 쓰기를 30번 가까이 하므로 한도에 닿을 수 있다. 계정을 나눈 지금은 agent 쪽 한도와 겹치지 않는다.
- 확인하지 못한 것: manufacture의 모든 연결 반영(명령 10)은 다시 돌리지 않았다. 검증 API만 직접 불렀다.


## 정정 (2026-10-15): 채팅 동작 처리

위 결과는 채팅 동작을 `codeOwned`(변경을 `codeOwnedChanges`로 알림)로 처리하던 때의 기록이다. 사용자 정정으로 채팅 API는 Detection·Manufacture 대상이 아니게 되어, Workfluence 규칙(`catalog/rules.yaml`)의 `/chat/` 규칙을 `codeOwned`에서 제외(`NOT_NEEDED`)로 바꿨다. 코어의 일반 `codeOwned` 기능은 남겨 두었고 Workfluence는 쓰지 않는다.

- 달라진 숫자: 제외 `NOT_NEEDED` 3 → 12(채팅 9 포함), 제외 합계 39 → 48(GROUP_DISABLED 7 포함), codeOwned 9 → 0. AC-3a는 "채팅 9개 추가는 tool을 바꾸지 않고 알림도 없다(noop)"로 바뀐다.
- 시험 수정: `test_selection`·`test_workfluence_fixtures`·`test_apply_service`의 채팅 관련 기대값. 실행(`scripts/test.sh manufacture --src -q packages/core packages/tools packages/clients packages/targets agents/manufacture/tests`): **409 passed**, 9 skipped(live), 0 failed. detection 쪽 `agents/detection/tests`는 바뀐 것이 없고 161 passed, 5 skipped.
- 확인하지 못한 것: 실행 중인 compose의 `manufacture` container는 이전 규칙 이미지다. 이미지를 다시 빌드·재기동하고 실호출로 다시 반영해 보기 전까지 실제 서빙 결과가 새 규칙인지 확인하지 못했다.

## 정정 (2026-10-16): 스페이스 동작을 tool 대상에 넣음 (FR-217, 설계 23절)

사용자 결정으로 스페이스 동작 8개를 tool 대상에 넣었다. 분류·템플릿 변경 6개는 계속 제외한다.

### 바꾼 것

- `catalog/rules.yaml`: `spaces.delete`·`spaces.setStatus`는 danger, `spaces.create`·`update`·`addMember`·`removeMember`·`setMemberRole`은 authoring, `spaces.members`는 GET 규칙으로 read.
- seed: `authoring.yaml`에 8개를 쓰고 `a4w-seedtool build`로 `seed/tools/*.json` 8개를 만들었다. 기존 seed 34개는 바뀌지 않았다(같은 기계 변환). 질의 세트는 62개에서 70개가 되었다.
- 검증 시나리오 8개: Crew 넣기·빼기·역할 시나리오는 setup으로 앞머리 이름의 스페이스를 따로 만들어 쓴다. 시험 스페이스의 peer는 건드리지 않는다.
- 시험 환경(`WorkfluenceSpaceSandbox`): 정리가 앞머리로 시작하는 스페이스를 모두 찾아 "중지면 active로 → owner가 아닌 Crew 빼기 → 지우기" 순서로 지운다. 실패한 스페이스가 있어도 나머지는 계속 지운다. 맥락 키 `peerUsername`을 더했다(peer가 있을 때만 값이 있다).
- agent 설정 `AGENT_WRITE_MARKS`에 5개를 더했다(예시 파일과 실제 설정).

### 실행한 명령과 결과

| 명령 | 결과 |
|---|---|
| `scripts/test.sh detection --src -q packages/targets/workfluence/tests` (red) | 새 시험 6건 실패 확인 |
| `scripts/test.sh manufacture --src -q …test_selection.py …test_workfluence_fixtures.py` (red) | 13건 실패 확인 |
| `scripts/build.sh {detection,manufacture,inference,mcp} test` 후 `scripts/test.sh <s> -q` | detection 406 passed, manufacture 424 passed, mcp 319 passed |
| `scripts/test.sh inference` (이미지 기본 명령) | 1057 passed, 11 skipped |
| `A4W_LIVE_TESTS=1 scripts/test.sh detection -q` | 411 passed, 4 skipped |
| compose 재기동 후 정리 반영 `POST /v1/apply {"targetId":"workfluence"}` (`ap-20261016T010949Z-91fe21`) | `applied`, `tv-000003-3c858529`, added 6, 검증 passed 6, 색인 upserted 6, LLM 호출 0 |
| 반영 뒤 시험 스택에서 `GET /spaces?q=aiagent-manufacture-` | 앞머리로 시작하는 스페이스 0개(정리 확인) |
| Inference REST 읽기 요청("스페이스 <id>의 Crew 목록") | `list_space_members` 성공, 서빙 36개 |

### 확인하지 못한 것

- `delete_space`·`set_space_status`는 danger 묶음이 꺼져 있어 실호출 검증을 하지 않았다. 단위 시험의 선정·seed·시나리오 형식만 확인했다. 정리 순서의 "중지면 active로 되돌림"은 가짜 서버로만 시험했다.
- 스페이스 이름 없이 "Crew 목록 알려줘"라고 하면 모델이 스페이스를 되물었다(tool 호출 없음). 되묻기 규칙대로의 동작이다.
- 쓰기 tool(`create_space`·`add_space_member` 등)을 Inference가 실제로 부르는 시험은 Phase 5 "열심공부" 시나리오로 한다(workf의 username 배포 뒤).

## 정정 (2026-10-16): 색인 문서 보강 (설계 24절)

Phase 5 채팅 시험의 검색 오답("스페이스 목록 tool이 없다", Phase 5 결과서 6절)에 이어 사용자 지시로 색인 글을 보강했다. 지금 서빙하는 36개는 모두 seed이고, 색인 글은 개발 세션이 `authoring.yaml`에 쓴 것이다. GLM(`zai-org/GLM-5.2`)은 seed가 없거나 명세가 바뀐 동작만 쓴다.

### 바꾼 것

- seed 45개(단순 42, 상위 3)의 `index.summary`·`index.examples`: 요약에 비슷한 tool과 다른 점, 예시 3개 → 8개(한국어 요청 5, "대상 + 동작" 1, 영어 2). `queries.yaml`(평가 질의)과 같은 문장은 쓰지 않았다(겹친 1건을 고침). `content` 길이 135~260자(상한 600).
- `TextLimits.min_examples`(`.env` `TOOL_TEXT_LIMITS`에 `min_examples=6`, 없으면 3).
- GLM 프롬프트 `_common.v2.md`(`PROMPT_VERSIONS` `common=v2`): 같은 색인 글 규칙.
- 반영 규칙: 명세 조각이 그대로여도 seed 글이 바뀌면 seed로 다시 만든다(설계 24.2). 첫 정리 반영에서 상위 3개만 바뀐 것을 보고 더했다.

### 실행한 명령과 결과

| 명령 | 결과 |
|---|---|
| `scripts/test.sh manufacture --src -q …test_assembly_checks.py` (red → green) | min_examples 시험 3건 실패 확인 → 29 passed |
| `scripts/test.sh manufacture --src -q …test_apply_service.py` (red → green) | seed 다시 만들기 시험 3건 실패 확인 → 22 passed |
| `a4w-seedtool build --spec tests/fixtures/workfluence/openapi-75ops.yaml --limits …,min_examples=6` | tools 42, composites 3, queries 70. 명세 조각 지문은 그대로, revision만 바뀜 |
| `scripts/build.sh {manufacture,detection,mcp} test` 후 `scripts/test.sh <s>` | manufacture **431 passed**, detection 406 passed, mcp 319 passed |
| 정리 반영 1 (`ap-20261016T050905Z-5294d1`, 규칙 보강 전) | `tv-000004`, updated 3(상위 tool만), 검증 passed 3 |
| 정리 반영 2 (`ap-20261016T051630Z-553858`, 규칙 보강 뒤) | `applied`, `tv-000005-abd11aa1`, updated 33, 검증 passed 33, 색인 upserted 33, LLM 호출 0. Inference MCP가 36개로 갱신 |
| `python -m a4w_manufacture.evaluate_index --target workfluence` (원래 질의, 상위 5) | 바꾸기 전 60/61·상위 1 42 → 바꾼 뒤 **60/61·상위 1 48** |

색인 평가(원래 질의) 질의별 변화: 올라감 13, 내려감 6(가장 큰 것 "위키에 공간이 몇 개나 있는지 궁금해" list_spaces 1→4, 나머지는 한 칸), 놓침 1건은 그대로("1판이랑 3판 차이 알려줘" compare_page_versions 상위 5 밖). 평가 질의를 보고 예시를 더 고치지 않았다(평가가 부풀려진다).

### 확인하지 못한 것

- GLM이 `_common.v2`로 쓰는 경우(새 동작·명세 변경)는 이번에 생기지 않아 실제 출력을 보지 않았다. live LLM 시험(`test_live_llm`)도 돌리지 않았다.
- `seedtool`은 저장소 기준 명세(8c83f9c6)로 만들었다. 시험 스택 명세(3f0a9480)와는 채팅 동작만 다르다(Detection 변경 식별 cs-b33a5a88714e6ed0385e77d3).

## 정정 (2026-10-16): 만든 사람·고친 사람 칸 반영 (설계 25절)

workf 배포(impl-phase21 87bc85b, 명세 3f0a9480 → 4283c9bf)를 반영했다.

| 명령 | 결과 |
|---|---|
| `POST /v1/detect {"targetId":"workfluence"}` | `cs-72b23a1591a6f5103cee8364`, modified 16, needsReview(`OPERATION_CHANGED`) |
| `scripts/test.sh manufacture --src -q …test_apply_service.py` (red → green) | 명세가 바뀐 동작에 맞는 seed가 있으면 seed로(LLM 없음) 시험 1건 실패 확인 → 통과. seed가 없으면 LLM 시험은 그대로 통과 |
| seed 글 수정(설계 25절 표) 후 `a4w-seedtool build --spec tests/fixtures/workfluence/openapi-75ops-4283c9bf.yaml` | tools 42, composites 3. 새 고정 명세를 더하고 seed 기준 시험은 그것을 본다(`tests/specs.py` `SEED_SPEC`). 이전 75ops 고정 명세는 명세 이력 시험(66→75)에 남김 |
| `scripts/build.sh {manufacture,detection,mcp,inference} test` 후 `scripts/test.sh <s>` | manufacture **433 passed**, detection 406, mcp 319, inference 1149 passed |
| 변경 반영 `ap-20261016T063118Z-1e09c5` (changeSetId) | `partial`: 13개 seed로 다시 만듦(LLM 0), 검증 passed 13, 모두 `SPEC_REVIEW` 보류. 3개는 위험 묶음이라 GROUP_DISABLED |
| 보류 13건 승인(사용자 지시) 후 정리 반영 `ap-20261016T063444Z-a1ce81` | `applied`, `tv-000006-3784047a`, updated 13, 검증 passed 13, 색인 upserted 13. Inference MCP 갱신(36개) |

### 확인하지 못한 것

- 댓글 거르기(`author`)는 시험 스택에 댓글 있는 페이지가 없어(workf 알림) 실호출로 보지 않았다. 검증 시나리오는 기존 그대로(댓글 거르기 없이) 통과했다.
- 페이지 목록의 `createdBy`·`updatedBy` 거르기는 Inference 실호출로 시험하지 않았다(스페이스만, Phase 5 결과서 6.6).

## 정정 (2026-10-16): 맥락 값을 바꿀 수 있게 (설계 26절)

| 명령 | 결과 |
|---|---|
| `scripts/test.sh mcp --src -q packages/tools/tests` (red → green) | 맥락 기본값 시험 3건 실패 확인 → 88 passed |
| `scripts/test.sh manufacture --src -q …` (red → green) | 변환·조립·선정 시험 4건 실패 확인 → 통과. 카탈로그 기대가 바뀐 기존 2건 고침 |
| `scripts/build.sh {mcp,detection,manufacture,inference} test` 후 `scripts/test.sh <s>` | mcp 324, detection 411, manufacture **442**, inference 1163 passed |
| seed 다시 만들기 (기준 명세 4283c9bf) | create_page·list_pages·search_pages에 `space` 선택 칸과 `contextDefault: homeSpace` |
| 재기동 후 정리 반영 `ap-20261016T073216Z-4a7952` | `applied`, `tv-000007-2df77909`, updated 3, 검증 passed 3(시나리오는 space 없이 → 시험 스페이스로 채워짐), LLM 0 |
| 상위 tool `*_by_title` 3개에 `space` 선택 칸 (시험 1건 red → green, manufacture 443 passed) 후 정리 반영 `ap-20261016T075419Z-dc2bad` | `applied`, `tv-000008-ebccb4cb`, updated 3, 검증 passed 3, LLM 0 |
