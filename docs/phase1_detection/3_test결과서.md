# Phase 1 test 결과서 — Detection agent

- 작성일: 2026-10-12
- 대상: [`1_요구사항정의.md`](1_요구사항정의.md), [`2_설계.md`](2_설계.md)
- 실행 환경: 모든 시험은 container 안에서 돌렸다. host Docker Engine 29.6.1, Compose v5.3.1, 이미지 `a4w/detection:0.1.0-test`(Python 3.12.15, pytest 9.1.1)

## 1. 결과 요약

| 실행 | 명령 | 결과 |
|---|---|---|
| 전체 (이미지 안 코드) | `scripts/build.sh detection test && scripts/test.sh detection` | **157 passed, 2 skipped** (live는 기본 건너뜀) |
| 전체 + 실호출 | `A4W_LIVE_TESTS=1 scripts/test.sh detection` | **159 passed** |
| compose + curl 재현 | 4절 | 기대대로 동작 |

경고 1건: `fastapi.testclient`가 starlette의 httpx 사용 방식 변경을 알리는 `StarletteDeprecationWarning`이다. 시험 도구 쪽 경고이고 서비스 동작과는 관계없다.

## 2. 묶음별 시험

| 묶음 | 파일 | 수 | 확인 내용 |
|---|---|---|---|
| core | `test_resolver.py` | 8 | `$ref` 풀기, 중첩, 순환 끊기·표시, 형제 참조, 외부·없는 참조 거절, JSON pointer 이스케이프, 입력 불변 |
| core | `test_operations.py` | 7 | `(METHOD, path)` 수집, 경로 수준 parameters 합치기(동작 쪽 우선), 유효 security, 참조 풀기, 빈 키 생략 |
| core | `test_canonical.py` | 6 | 정규 JSON(키 순서 무관·배열 순서 유지·한글), 문서용 키 제거(속성 이름은 보존) |
| core | `test_spec_parsers.py` | 9 | YAML 해석, 날짜를 문자열로, 키를 문자열로, OpenAPI 3.x 아닌 것 거절, JSON 해석기는 자리만 |
| core | `test_spec_sources.py` | 12 | 해시 정규화, 원문 그대로, 해시 형식·2xx 아님·접속 실패·크기 초과 거절, 오류 메시지에 본문 없음, 해시 없는 방식은 자리만 |
| core | `test_config.py` | 8 | `.env` 해석, 타겟 파일 읽기, 파일 없음·잘못된 targetId·필수 값 없음 거절, 형 변환 |
| core | `test_registry.py` | 5 | 등록·생성, 중복·모르는 이름·모르는 종류 거절, 코어 구현 등록 |
| 타겟 | `test_review_policy.py` | 12 | Workfluence v1 정책 표(설계 6절) 전부, 이유 코드 중복 없이 정렬, entry point 등록 |
| unit | `test_diff.py` | 13 | 첫 실행 전체 added, 같으면 변경 없음, added·removed, modified와 changedFields, 설명만 바뀐 docOnly(중첩 포함), 공용 스키마 변경 전파, security·operationId·배열 순서 변경, 문서 수준 변경, 정렬, 결정성 |
| unit | `test_textdiff.py` | 3 | unified diff와 앞뒤 줄 수, 빈 쪽 대비, 같으면 빈 글 |
| unit | `test_ids.py` | 3 | 버전 = sha256, `changeSetId` 규칙·결정성·입력별 차이 |
| unit | `test_service.py` | 12 | 모르는 타겟, 첫 실행, 두 번째는 unchanged(명세를 받지 않음), 변경, 삭제 → 검토, 해시 불일치·해석 실패 시 저장 안 함, 동시 실행 거절, **저장 중 실패 후 재호출로 복구**, 되돌아간 명세, 조회 오류 |
| unit | `test_workfluence_fixtures.py` | 5 | 실제 명세 66개 → 75개: 추가 9개(`chat`)만, 삭제·변경 0, 문서 수준 `info`·`tags` 변경, 검토 불필요, 조각에 `$ref` 없음, 첫 실행 75개 |
| unit | `test_wiring.py` | 12 | 환경변수로 조립, 필수 설정 6개 각각 빠지면 시작 거부, bearer 토큰 필수, 잘못된 인증 모드·모르는 구성요소·타겟 값 누락 거부, 자리만 둔 방식은 쓸 때 501 |
| integration | `test_git_archive.py` | 7 | 커밋·읽기, 새 내용 새 커밋, 같은 내용은 커밋 안 늘림, 되돌아간 내용은 새 커밋, 타겟별 저장소, 바이트 보존(CRLF·끝 줄바꿈 없음), 없는 커밋 오류 |
| integration | `test_sqlite_store.py` | 8 | 빈 보관소, 기록·현재 버전·왕복, 다른 타겟 격리, 되돌아간 버전의 현재 커밋, 첫 변경분 중복 거절(NULL 처리), **원자성**, 스키마 버전, 다시 열어도 유지 |
| integration | `test_file_lock.py` | 4 | 두 번째 잡기 거절, 타겟별 독립, 예외 뒤 풀림, **다른 프로세스 거절** |
| integration | `test_api.py` | 19 | changed→unchanged 응답 모양, 잘못된 요청 4종 400, 404, 409, 오류 6종의 HTTP 상태 대응과 내부 메시지 숨김, 변경분 조회·`includeTextDiff`, 조회 오류, bearer 인증, health 정상·503, OpenAPI 명세 |
| contract | `test_changeset_contract.py` | 4 | 변경분 머리 키, 변경 종류별 항목 키, 조각이 스스로 완결(`$ref` 없음), 조각 키 범위 |
| live | `test_live_workfluence.py` | 2 | 시험 스택 해시 = 명세 본문 sha256, 첫 실행 changed → 두 번째 unchanged |
| | **합계** | **159** | |

## 3. TDD 진행 기록

| 단계 | red (구현 전) | green |
|---|---|---|
| core OpenAPI·해석·받기·설정·등록부 | 시험 파일 7개 수집 오류(모듈 없음) | 55 passed |
| 비교기·줄 diff·Workfluence 정책 | 시험 파일 3개 수집 오류 | 누적 83 passed |
| Git·SQLite·잠금 | 시험 파일 3개 수집 오류 | 19 passed |
| DetectService·식별자 | 시험 파일 2개 수집 오류 | unit 31 passed |
| API·계약 | 시험 파일 2개 수집 오류 | 23 passed |

- red는 "구현 모듈이 없어 수집 단계에서 실패"한 형태였다. 단언(assert)이 실패하는 red는 아니었다.
- **red를 거치지 않은 시험**: `test_wiring.py`(조립 지점을 API와 함께 먼저 구현함), `test_workfluence_fixtures.py`, `test_live_workfluence.py`는 구현 뒤에 썼다. 앞의 두 개는 이미 만든 동작을 고정하는 시험이다.
- live 시험은 처음에 2개 모두 실패했다(`ConnectError`). 원인은 container 안에서 `wf.internal` 이름을 찾지 못한 것이다(host의 `/etc/hosts`에만 있음). `.env`의 `TARGET_HOST_ALIAS`로 container에 host 항목을 넘겨 고쳤다(5절).

## 4. compose + curl 재현 (AC-7)

`scripts/build.sh detection runtime` → `docker compose up -d detection` 뒤 host에서 실행했다.

| # | 요청 | 결과 |
|---|---|---|
| 1 | `GET /health` | `{"status":"ok","specStore":"ok"}` |
| 2 | `POST /v1/detect {"targetId":"workfluence"}` (빈 보관소) | `changed`, `changeSetId: cs-1884c7c69cfc70ba39d9e2b1`, `fromVersion: null`, `toVersion: 8c83f9c6…b1e6`, `summary: added 75, removed 0, modified 0, docOnly 0, documentChanges 5`, `needsReview: false` |
| 3 | 같은 요청 다시 | `unchanged`, `currentVersion: 8c83f9c6…b1e6` |
| 4 | `GET /v1/changesets/cs-1884…?targetId=workfluence` | 동작 75개. 첫 항목 `DELETE /attachments/{id}`, 키 `after, change, method, operationId, path` |
| 5 | `POST /v1/detect {"targetId":"nope"}` | 404 `TARGET_NOT_FOUND` |
| 6 | `GET /v1/changesets/cs-000?targetId=workfluence` | `CHANGESET_NOT_FOUND` |
| 7 | container 안 `git log` | 커밋 1개 `spec 8c83f9c6…b1e6` |
| 8 | `docker compose restart` 뒤 로그·재호출 | 시작 때 저절로 실행하지 않음(로그에 호출 없음). 재호출은 `unchanged`(보관소 유지) |
| 9 | `docker compose down -v` 뒤 다시 올려 첫 호출 | 같은 `changeSetId: cs-1884c7c69cfc70ba39d9e2b1` (결정성) |
| 10 | container 사용자·보관소 소유 | `uid=10001(a4w)`, `/data/spec-store` 소유 `a4w` |

TLS는 `TLS_VERIFY=true`와 시험 스택 인증서(CA 파일)로 검증했다. 검증을 끄지 않았다.

## 5. 인수 기준 판정

| # | 기준 | 판정 | 근거 |
|---|---|---|---|
| AC-1 | 빈 보관소 첫 호출 → 전체 added | 통과 | live 시험, 4절 #2 |
| AC-2 | 같은 명세 두 번째 → unchanged, 새 버전·변경분 없음 | 통과 | live 시험, `test_service`, 4절 #3·#7 |
| AC-3 | 66개 → 75개 변경에서 `chat` 9개가 added로 식별 | 통과 | `test_workfluence_fixtures` (고정 자료). container 경유로 66 → 75 전환을 재현하지는 않았다(6절) |
| AC-4 | 삭제·입력·응답·scope·공용 스키마 변경 → removed·modified, 검토 필요 | 통과 | `test_diff`, `test_review_policy`, `test_service` |
| AC-5 | 변경분 조회만으로 동작 조각 완결 | 통과 | contract 시험, fixtures 시험 |
| AC-6 | 해시 불일치·해석 실패·모르는 타겟·동시 실행 → 정해진 오류 코드 | 통과 | `test_service`, `test_api`, `test_file_lock` |
| AC-7 | compose로 올리고 curl로 재현 | 통과 | 4절 |
| AC-8 | URL·경로·포트·token 하드코딩 없음 | 통과 | 소스 검색(`https?://`, `wf.internal`, IP, 포트, 경로). 검토 중 Dockerfile·compose의 container 경로와 타겟 패키지 이름이 박혀 있던 것을 찾아 설정으로 바꿨다 |

## 6. 확인하지 못한 것

- **container 경유 66 → 75 전환**: 시험 스택은 지금 75개 명세만 낸다. 옛 명세를 내는 서버를 따로 띄워 container로 전환을 재현하지는 않았다. 같은 비교 로직을 고정 자료로 시험했다.
- **실제 동시 요청 409**: API 수준 409는 가짜 잠금으로, 실제 파일 잠금은 다른 프로세스로 시험했다. 실행 중인 container에 동시에 curl을 보내 409를 받는 것은 해 보지 않았다.
- **bearer 모드의 container 실행**: 인증은 API 시험으로만 확인했다. `DETECTION_AUTH_MODE=bearer`로 container를 띄워 보지는 않았다.
- **삭제·변경이 있는 실제 명세**: 실제 Workfluence 명세에는 아직 삭제·변경 사례가 없다. 손으로 만든 명세로만 시험했다.
- **큰 명세·느린 응답**: 크기 한도와 시간 제한은 가짜 응답으로만 시험했다.
