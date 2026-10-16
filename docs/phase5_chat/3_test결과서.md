# Phase 5 test 결과서 — 채팅 채널 (`inference` 안 채팅 어댑터)

- 시험일: 2026-10-15 ~ 2026-10-16
- 대상: 요구사항 [`1_요구사항정의.md`](1_요구사항정의.md), 설계 [`2_설계.md`](2_설계.md) (18절 방 참여자 알림은 2026-10-16 추가). Phase 4 설계 19절(대화 기록에 id 남기기)도 이 phase의 사용자 시험에서 나와 함께 적는다.
- 환경: 시험 이미지 `a4w/inference:0.1.0-test`, 실행 이미지 `a4w/inference:0.1.0`, compose의 `inference`·`manufacture`·`detection`, 시험 스택 Workfluence(`wf.internal:8444`, workf 커밋 `5c1a710` — 방 정보 `members[].username` 추가 뒤, Phase 21 브랜치 상태), 사내 LLM `openai/gpt-oss-120b`
- 채팅 프레임·REST 모양은 2026-10-15~16 시험 스택 기준이다. Phase 21이 병합되기 전이라 바뀔 수 있다.

## 1. 실행한 명령과 결과

| # | 명령 | 결과 |
|---|---|---|
| 1 | `scripts/build.sh inference test && scripts/test.sh inference` | **1107 passed, 14 skipped** (`packages/core·tools·clients` + `agents/inference/tests`). skipped는 live 시험과 LLM 비밀이 필요한 clients live |
| 2 | 그 가운데 `agents/inference/tests` | 917건 수집, 912 passed, 5 skipped(live) |
| 3 | 그 가운데 Phase 5 시험(`test_chat_*`, `test_engine_channel`, `test_engine_memory`, `test_runner_submitter`) | **567건**. Phase 4 시험은 고치지 않고 통과(회귀 0). 바꾼 Phase 4 시험은 기대 모양이 늘어난 2건뿐이다: REST 응답 `actions[]`에 `refs` 칸(19절), `full_environ`에 `CHAT_ENABLED=false` |
| 4 | `A4W_LIVE_TESTS=1 scripts/test.sh inference -q agents/inference/tests/live` | **5 passed** (Phase 4 live 2 + 채팅 live 3, 4절) |
| 5 | `scripts/test.sh {manufacture,detection,mcp}` (2026-10-16, 스페이스 tool 반영 때) | 424 · 406 · 319 passed (Phase 2 결과서 정정 절) |
| 6 | `scripts/build.sh inference test && scripts/test.sh inference` (2026-10-16, 6절 검색어 다듬기 뒤) | **1149 passed, 14 skipped**. 새 시험 32건(`test_chat_mention_strip` 13, `test_engine_search_rewrite` 12, `test_engine_history_tools` 7)과 설정 시험 G5. 기존 시험은 고치지 않고 통과 |

- TDD: S1~S10 단계마다 시험을 먼저 쓰고(red: 모듈 없음·인자 없음으로 실패를 확인) 구현해 green으로 만들었다. S1~S8은 커밋 `762f633`, S9·S9b는 `c6a123c`.
- 시험이 잡아낸 것: 모르는 방의 메시지 처리 순서(X20, S4), 하드코딩 검사에 걸린 WebSocket 주소 조립(S4), 붙잡는 시험에서 가짜 sleep이 즉시 돌아 시간 초과(S5), 토큰 버킷이 창 경계에서 2배를 보낼 수 있음(S6, 설계 5.2를 미끄러지는 창으로 정정), 시험 이미지에 `config/` 예시가 없어 조립 시험이 실패(S10, 시험이 문구를 직접 쓰게 고침).

## 2. 인수 기준별 결과

| AC | 결과 | 근거 |
|---|---|---|
| AC-C1 연결·재연결·보충·중복 제거 | **단위 충족** | `test_chat_supervisor`·`ws_client`(가짜 WebSocket 서버)·`dispatcher`·`store`. 실서버에서 끊고 다시 붙는 것은 하지 않았다(5절) |
| AC-C2 join·leave·모르는 kind·자기 메시지 무시 | **충족** | 단위 시험. 실호출에서도 aiagent 자신의 인사 메시지(seq 3)는 `skipped`로 남았다 |
| AC-C3 1:1 전부, 여럿은 이름 부를 때만 | **충족** | 단위 시험 + live L3 + 사용자 시험(이름 없는 메시지 3건은 `not_mentioned`로 건너뜀) |
| AC-C4 요청 모양과 이어 가기 | **충족** | 단위 시험(`conversationId=chat:<roomId>`, 요청자 이름) + 사용자 시험(앞 차례의 스페이스를 "방금 만든 space"로 이어 받음, 3절) |
| AC-C5 방별 차례·방 간 동시·재시작 뒤 다시 수행 안 함 | **단위 충족** | `test_chat_worker` 30건. 실서비스 재시작 중 요청이 끊기는 상황은 만들지 않았다(5절) |
| AC-C6 평문·길이·clientMsgId·429 재시도·속도 제한 | **단위 충족** | `test_chat_sender`·`rate`·`rest_send`. 실서버 429는 일으키지 않았다 |
| AC-C7 길이 규칙 | **충족** | 단위 시험 + live에서 답이 짧은 기준(600자) 안·마크다운 없음 |
| AC-C8 허용 목록 밖 쓰기 불가 | **단위 충족** | engine 시험 W1~W6(쓰기 tool을 모델에 싣지 않고, 불러도 `WRITE_NOT_ALLOWED`), 조립 시험 W6. 실제 모델로 허용 목록 밖 쓰기 요청은 하지 않았다 |
| AC-C9 시험 스택 실호출 | **충족** | live L1~L3 (4절) |
| AC-C10 모르는 프레임·/health chat 항목 | **충족** | 단위 시험 + 실행 중 `/health`에 `chat.state=connected`, 카운터 보임 |
| AC-C11 의존 방향·Phase 4 회귀 | **충족** | `test_architecture`(engine·runner가 채널을 모름), Phase 4 시험 통과 |
| AC-C12 token·본문이 로그에 없음 | **충족** | 단위 시험(로그 검사, token이 `repr`에 없음). 로그에는 방 id 앞 8자·seq·글자 수만 남는다 |

## 3. 사용자 시험 (2026-10-16, "AI Agent Test" 방)

사용자가 여럿 방을 만들고(참여자 tanminkwan·tiffanie) aiagent를 초대해 직접 시험했다. 지정 시나리오는 "여기 chatting 방에 있는 사람들로 새로운 스페이스를 만들어"다.

| 순서 | 일 | 결과·조치 |
|---|---|---|
| 1 | 이름 없이 보낸 메시지들 | 여럿 방 규칙대로 건너뜀(`not_mentioned`) |
| 2 | "Mr. AI agent! make a space "Crazy Study" including all members in this room" | `create_space` 성공, `add_space_member` tiffanie 성공, tanminkwan은 모델이 스페이스 id를 `"f..."`로 줄여 보내 400, 다음 쓰기에서 **쓰기 한도(3)** 로 멈춤 |
| 3 | 사용자 결정 | `INFERENCE_MAX_WRITE_CALLS` 3 → **10** (`.env`·`.env.example`·Phase 4 설계 표) |
| 4 | "AI Agent, add tanminkwan to the space "Crazy Study"" | 모델이 tool 없이 스페이스 id를 되물음. 대화 기록에 앞 차례 결과(id)가 남지 않았기 때문 |
| 5 | 사용자 지시 "api 처리 후 받은 생성 결과(주로 id)는 기억하고 있게 해" | Phase 4 설계 19절: 결과의 식별자 값(`refs`)을 기록하고 다음 차례 이력 줄에 보인다. TDD(`test_engine_memory` 8건) |
| 6 | "Ai agent 야, 방금 만든 space에 tanminkwan 추가해줘" | **성공** (`list_spaces` → `add_space_member`, 1.3초). Crew: aiagent(owner)·tiffanie·tanminkwan |
| 7 | 답에 스페이스 id가 그대로 나옴 → 사용자 지적 | 채팅 덧붙임에 "답에 id 같은 내부 식별자를 쓰지 않는다" 규칙 추가 |

- 방 참여자는 설계 18절대로 `channel_notes`로 모델에 갔다(에이전트 자신 제외, username 포함).
- 시험이 만든 스페이스 "Crazy Study"는 사용자의 시험 자료로 시험 스택에 남아 있다(지우지 않았다).

## 4. live 시험 (AC-C9, `tests/live/test_live_chat.py`)

고정 시험 방 두 개를 `aiagent-peer`가 2026-10-16에 만들었다(방은 지울 수 없어 재사용한다). 방 id는 `.env`의 `INFERENCE_LIVE_CHAT_DIRECT_ROOM`·`INFERENCE_LIVE_CHAT_GROUP_ROOM`에 있다. 요청자 peer token은 `INFERENCE_TEST_SECRETS`에 `targets/workfluence.peer.env`를 더해 붙인다. 메시지마다 실행 id를 넣어 새로 돈 결과임을 확인했다.

| 시험 | 요청(peer) | aiagent 답 |
|---|---|---|
| L1 1:1 읽기 | "내가 볼 수 있는 스페이스 목록 알려줘 (시험 253541ba)" | 1초 뒤 스페이스 3개를 평문 목록으로 |
| L2 1:1 쓰기 | "'a4w 시험 바탕 페이지' 페이지에 '채팅 시험 57812582' 라고 댓글 달아줘" | 1초 뒤 "댓글을 달았습니다". 시험이 댓글이 정확히 1개인 것을 확인하고 agent token으로 지웠다 |
| L3 여럿 방 | 이름 없는 "오늘 점심 뭐 먹지" → 20초 동안 답 없음. "AI Agent …스페이스 목록 알려줘" | 1초 뒤 한 줄 답 |

## 5. 확인하지 못한 것

- WebSocket 장시간 유지, 서버 쪽 재시작·끊김 뒤 재연결과 보충, `4409`(연결 수 초과), 실서버 429는 시험 스택에서 일으키지 않았다. 모두 가짜 서버 단위 시험으로만 확인했다.
- 수행 중 Inference를 재시작했을 때 방에 중단 알림이 한 번 가는 것은 실서비스에서 보지 않았다(재시작은 요청이 없을 때만 했다).
- 허용 목록 밖 요청자의 쓰기 요청, 다른 참여자 메시지 속 지시문은 실제 모델로 시험하지 않았다(코드가 쓰기 tool을 빼고 실행을 막는 것은 단위 시험으로 확인).
- "답에 id를 쓰지 않는다"는 프롬프트 규칙이라 코드로 강제하지 않는다. 규칙을 더한 뒤 실제 답으로 다시 보지는 않았다.
- 한도에 걸렸을 때 방에 가는 답("한도에 걸려 일부만 처리했습니다")에는 무엇을 했는지가 빠진다. 고치지 않았다.
- 여럿 방에서 여러 사람이 부르면 한 대화(`chat:<roomId>`)로 섞이고, 이력에 요청자 이름이 없다(설계 16절 5번, 한계로 둠).
- httpx가 `verify=<CA 파일 경로 글자열>`에 대해 deprecation 경고를 낸다(동작은 정상). 바꾸지 않았다.
- 시험 스택의 공개 명세가 바뀌었다(workf: 채팅 응답 username 추가, `openapi.sha256` 변경). 채팅 API는 Detection 대상이 아니지만 Detection 변경 식별은 이번에 돌리지 않았다.

## 6. 검색어 다듬기 (2026-10-16 추가, Phase 5 설계 19절·Phase 4 설계 20절)

사용자 방("Talk!!")에서 "You created a space. what the space name was?" → "No, other one." → "The name was 'Crazy Study', check it."에 aiagent가 "스페이스 목록을 조회하는 기능이 없다"고 답했다. `list_spaces`는 서빙 중이었고 seq 6에서는 불렀다. 원인은 tool 검색 질의였다(원문 이어 붙이기, 호명 되풀이, 영어·지시어). 바꾼 것:

1. `INFERENCE_SEARCH_TOP_K` 8 → 12 (사용자 지시)
2. 채팅 요청 글에서 호명 빼기
3. 검색 전에 모델이 검색어를 한 줄로 다듬기 (사용자 지시: "원문을 그대로 RAG search 하면 안 되고 정제해서")
4. 검색 창 안의 앞선 차례에서 성공한 tool을 후보에 넣기 (3만으로는 실호출에서 한 번 빠졌다)

### 6.1 같은 질문의 순위표 (`list_spaces` 순위, 36개 전체에서)

같은 질의를 원래 방식으로 5번 되풀이했을 때 순위·점수가 모두 같았다(검색은 결정적이다). 다듬기는 모델 출력이라 표현이 조금씩 달라지므로 5번 돌렸다. 아래는 다듬기 프롬프트 최종본 기준이다.

| 질문 | 바꾸기 전 | 다듬은 뒤 (5회) | 다듬은 줄 (예) |
|---|---|---|---|
| seq 6 "You created a space. what the space name was?" | 8위 | 3·3·3·3·3위 | 스페이스 이름 확인 |
| seq 8 "No, other one." | 9위 | 1·1·1·1·1위 | 공간 목록을 보고 이름 확인 |
| seq 10 "The name was 'Crazy Study', check it." | 9위 | 1·1·1·1·1위 | 공간 목록을 보고 이름 확인 |
| "네가 만든 스페이스 이름이 뭐였지?" | 3위 | 2·2·2·2·2위 | 스페이스 이름 확인하기 |
| "'Crazy Study' 라는 스페이스가 있는지 확인해줘" | 4위 | 1위(5회) | 스페이스 목록을 보고 이름으로 찾기 |
| "내가 만든 스페이스 목록 보여줘" | 1위 | 1위(5회) | 스페이스 목록을 보고 |
| "Show me the list of spaces" | 1위 | 1위(5회) | 스페이스 목록 보기 |

- 프롬프트 첫 판에서는 seq 10이 3번 중 2번 "Crazy Study 이름 확인"(대상 종류가 빠짐)으로 다듬어져 30위였다. "대상은 고유 이름이 아니라 종류로, 종류는 반드시 넣는다", "확인·찾기는 무엇을 보고 확인하는지 쓴다"를 더한 뒤 위 표가 되었다.

### 6.2 색인 평가 질의로 회귀 확인 (`queries.yaml`, danger 제외 61건, 이력 없음)

| | 원래 질의 | 다듬은 질의 |
|---|---|---|
| 상위 1 적중 | 42/61 | **51/61** |
| 상위 3 적중 | 57/61 | **61/61** |
| 상위 8·12 적중 | 61/61 | 61/61 |

프롬프트 최종본으로 한 번 돌린 결과다. 순위가 내려간 것은 3건이고 모두 1→2위 또는 2→3위였다("장애 대응 관련 문서가 있는지 찾아봐" search_pages 2→3, "페이지가 어떤 라벨로 분류돼 있는지 봐줘" list_page_labels 1→2, "분류 체계 보여줘" list_categories 1→2). 올라간 것은 15건이다. 첫 판 프롬프트로도 적중 수는 같았다(내려간 3건 중 하나만 달랐다).

### 6.3 실호출 (고정 여럿 방, peer가 요청)

| 차례 | 1차 (다듬기만) | 2차 (다듬기 + 앞선 tool 후보) |
|---|---|---|
| "AI Agent, You created a space. what the space name was?" | `list_spaces` 호출, 스페이스 3개를 답함 | tool 없이 앞 차례 목록으로 답함 |
| "AI Agent. No, other one." | tool 없이 "만든 스페이스가 없다" | `list_spaces` 호출, "'Crazy Study' 스페이스가 존재합니다" |
| "AI Agent. The name was 'Crazy Study', check it." | 모델이 `list_spaces`를 불렀으나 **NOT_A_CANDIDATE**(다듬은 줄 17자, 대상 종류가 빠진 것으로 보임). "목록을 확인할 수 없다"고 답함 | 앞 차례 결과로 "'Crazy Study' 스페이스가 존재합니다" |

- 1차의 실패로 4번(앞선 차례의 tool을 후보에 넣기)을 더했다. 다듬기 호출은 요청마다 입력 400~450 토큰, 0.2~0.6초를 더했다.

### 6.4 확인하지 못한 것

- 사용자 방("Talk!!")에서 같은 대화를 다시 하지는 않았다(사용자 시험 몫).
- "네가 만든 스페이스"를 가리는 것은 검색이 아니라 자료 문제다. `list_spaces` 결과로는 누가 만들었는지 알 수 없어, 2차 첫 질문에 "만든 새 스페이스는 없다"고 답했다. 고치지 않았다.
- 다듬기는 프롬프트 규칙이라 모델이 어길 수 있다(6.1의 첫 판, 6.3의 1차). 4번은 이어 가는 요청만 막는다. 대화 첫 요청에서 다듬기가 어긋나면 원래 질의보다 나쁠 수 있다.
- 색인 문서 보강은 이어서 했다(Phase 2 설계 24절, Phase 2 결과서 정정 절, `tv-000005`). 결과는 6.5절.
- 다듬은 줄은 로그에 남기지 않아(본문 비기록 규칙) 실호출의 다듬은 줄은 글자 수로만 추정했다.

### 6.5 색인 문서 보강 뒤 (2026-10-16, `tv-000005`)

사내 LLM이 05:20 무렵부터 503으로 답해 다듬은 질의 측정을 미뤘다가, 다시 살아난 14:52 뒤에 쟀다. 서빙 tool은 `tv-000005`(색인 글 보강) 그대로다.

같은 질문의 `list_spaces` 순위 (6.1과 같은 질문):

| 질문 | 원래 질의: 보강 전 → 뒤 | 다듬은 질의: 보강 전 (5회) → 뒤 (5회) |
|---|---|---|
| seq 6 | 8 → 5위 | 3·3·3·3·3 → 1·2·2·1·2위 |
| seq 8 | 9 → 3위 | 1·1·1·1·1 → 1·1·1·1·1위 |
| seq 10 | 9 → 2위 | 1·1·1·1·1 → 1·1·1·1·1위 |
| "네가 만든 스페이스 이름이 뭐였지?" | 3 → 2위 | 2·2·2·2·2 → 1·1·2·2·1위 |
| "'Crazy Study' 있는지 확인해줘" | 4 → 1위 | 1 → 1위 |
| "내가 만든 스페이스 목록 보여줘" | 1 → 1위 | 1 → 1위 |
| "Show me the list of spaces" | 1 → 1위 | 1 → 1위 |

색인 평가 질의 61건 (36개 전체 순위, 6.2와 같은 방법):

| | 원래 질의 보강 전 | 원래 질의 보강 뒤 | 다듬은 질의 보강 전 | 다듬은 질의 보강 뒤 |
|---|---|---|---|---|
| 상위 1 | 42 | 48 | 51 | **53** |
| 상위 3 | 57 | 59 | 61 | 58 |
| 상위 8 | 61 | 60 | 61 | 61 |
| 상위 12 | 61 | 60 | 61 | 61 |

- 보강 뒤 다듬은 질의가 상위 3 밖인 3건: "장애 대응 관련 문서가 있는지 찾아봐"(search_pages 5위, 다듬은 줄 "문서 목록을 보고 … 찾기"), "검토필요 태그가 달린 문서들 모아줘"(list_pages_by_label 5위, "문서 목록을 보고 … 필터링"), "수정 전 원래 내용 읽어줘"(read_page_version 5위, "문서 내용 보기"). 앞의 둘은 다듬기 규칙("확인·찾기는 무엇을 보고 확인하는지 쓴다")이 "목록을 보고"를 붙여 목록 tool 쪽으로 끌었다. 상위 K(12) 안이라 모델에는 실린다.
- 원래 질의로 놓치던 "1판이랑 3판 차이 알려줘"는 다듬기("판 차이 보기")로 1위가 되었다.

실호출 (고정 여럿 방, peer, 6.3과 같은 세 질문):

| 차례 | 답 | tool |
|---|---|---|
| "You created a space. what the space name was?" | "제가 만든 스페이스 이름은 Crazy Study 입니다." | 없음(앞 차례 대화로 답함) |
| "No, other one." | "제가 만든 새로운 스페이스는 아직 없습니다. 현재 확인 가능한 스페이스는 …" | 없음 |
| "The name was 'Crazy Study', check it." | "'Crazy Study' 스페이스가 존재합니다." | `list_spaces` |

- 검색은 막지 않았다(세 차례 모두 `list_spaces`가 후보에 있었다). 앞 두 차례는 모델이 tool 없이 앞 대화로 답했다. 첫 답은 맞았지만 근거 없이 고른 것이고, 둘째 답은 첫 답과 어긋난다. 만든 사람을 결과에서 알 수 없었던 탓이다(workf가 만든 사람 칸을 배포해 다음에 반영, Phase 2 설계 25절).

### 6.6 만든 사람 칸 반영 뒤 (2026-10-16, `tv-000006`)

workf가 스페이스·페이지·댓글 목록에 만든 사람 칸과 거르기를 배포했고, tool에 반영했다(Phase 2 설계 25절). 같은 고정 여럿 방에서 이어서 물었다.

| 차례 | 답 | tool (인자) |
|---|---|---|
| "AI Agent, You created a space. what the space name was?" | "제가 만든 스페이스는 아직 없습니다. 현재 확인 가능한 스페이스는 …" (틀림) | 없음 — 앞 차례 대화로 답함 |
| "AI Agent, 네가 만든 스페이스 목록 알려줘" | "AI Agent 작업공간, Crazy Study" (맞음) | `list_spaces` (`createdBy=aiagent`) |
| "AI Agent, tanminkwan이 만든 스페이스 있어?" | "현재 없습니다" (맞음) | `list_spaces` (`createdBy=tanminkwan`) |

- 정답 확인: aiagent token으로 `GET /spaces?createdBy=aiagent` → 2개(같은 이름), `createdBy=tanminkwan` → 0개.
- 첫 차례는 이 방의 앞 대화(6.3·6.5에서 "만든 스페이스는 없다"고 답한 기록)를 믿고 tool을 부르지 않았다. 검색·tool 문제가 아니라 모델이 이력의 틀린 답을 되풀이한 것이다. 고치지 않았다.
