# CLAUDE.md

이 프로젝트는 API 명세를 확인하고(Detection), 그에 맞는 tool을 만들고(Manufacture), 그 tool로 요청을 수행하는(Inference) agent 3종을 만든다. 첫 타겟은 Workfluence다. 전체 정의는 `docs/과제정의서.md`가 정본이다.

## 개발 원칙 (반드시 지킨다)

1. **SOLID 원칙 준수.** 코어와 타겟 어댑터, 채널(REST·채팅) 어댑터는 인터페이스로 분리하고 구체 구현에 의존하지 않는다(DIP). 새 타겟·채널은 기존 코드를 고치지 않고 더한다(OCP).
2. **하드코딩하지 않는다.** URL, token, 모델명, 경로, 임계값, 한도, 제외 규칙은 설정으로 둔다.
3. **TDD (red → green).** 실패하는 테스트를 먼저 쓰고(red), 통과시키는 최소 구현을 한 뒤(green), 정리한다. 구현 전에 테스트가 먼저 있어야 한다.
4. **phase 분할.** 작업을 phase로 나누고, phase마다 과제를 끝까지 완료한 뒤 다음 phase로 넘어간다. 문서가 끝나기 전에 다음 phase 구현을 시작하지 않는다.
5. **산출물은 `docs/`에 phase별로 쓴다.** 요구사항(과제) 정의 → 설계 → test 결과서 순서로 남긴다.

```
docs/
  과제정의서.md            # 전체 과제 (phase 0)
  phase1_<이름>/
    1_요구사항정의.md
    2_설계.md
    3_test결과서.md
```

문서의 흐름도·구성도·chart는 mermaid로 작성한다(ASCII 그림 금지).

test 결과서에는 실행한 명령, 통과·실패 건수, 확인하지 못한 것(실호출 검증 여부 포함)을 사실대로 적는다.

## 범용성

과제 범위의 타겟은 Workfluence 하나지만, 타겟 추가를 전제로 범용성을 고려한다.

- 코어와 타겟을 분리한다. Workfluence 고유 값(URL·태그·채팅 API 등)은 코어에 넣지 않고 타겟 설정이나 타겟 어댑터로 둔다.
- 타겟 설정은 타겟별 환경변수(`.env`)로 받는다. 산출물은 `targetId`로 묶는다.
- Workfluence 조건에 해당하는 경우만 구현한다. 다른 타겟을 위한 경우는 interface만 만들고 구현은 비워 둔다.

## 비밀 관리

- token, api key는 `.env`(권한 600, `.gitignore` 대상)로만 다룬다. 코드·문서·로그·커밋에 남기지 않는다.
- `.env`를 커밋하지 않는다.

## 외부 시스템과 문의 대상

- Workfluence 공개 API: 서버 `GET {BASE}/api/v1/openapi.yaml`(변경 확인 `openapi.sha256`). 모르는 점은 다른 Claude 세션 `workf`에게 SendMessage로 묻는다.
- AI Pro Plus(Qdrant 기반 RAG 관리 시스템, REST API): 이 프로젝트가 개발하지 않고 클라이언트로만 쓴다. 모르는 점은 세션 `aipro-plus-8f`에게 묻는다. test 환경은 api-key 없이 nobody 모드, production은 api key를 쓴다.

## Git

- 브랜치는 `main`을 쓴다.
- 커밋과 push는 사용자가 요청할 때만 한다.
