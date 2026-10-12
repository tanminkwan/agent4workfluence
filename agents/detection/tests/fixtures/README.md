# 시험 고정 자료

| 파일 | 출처 | sha256 | 동작 수 |
|---|---|---|---|
| `workfluence/openapi-66ops.yaml` | Workfluence 저장소 브랜치 `tanminkwan-rest-api`, 커밋 `6c48159`(2026-10-12)의 `docs/spinoff/public-api/openapi.yaml` | `5ed30661…b295` | 66 |
| `workfluence/openapi-75ops.yaml` | 시험 스택 `https://wf.internal:8444/api/v1/openapi.yaml`, 2026-10-12에 받음 (브랜치 `impl-phase21`, `chat` 추가) | `8c83f9c6…b1e6` | 75 |

- 두 파일은 받은 그대로이며 손으로 고치지 않는다.
- 시험 스택의 명세는 배포 때마다 바뀐다. 고정 자료 시험은 이 두 파일만 쓰고, 실호출은 `live` 시험이 따로 확인한다.
