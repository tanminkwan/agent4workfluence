#!/usr/bin/env bash
# 서비스 시험을 test 이미지 안에서 돌린다.
#   사용법: scripts/test.sh <service> [--src] [pytest 인자...]
#   --src : 이미지 안의 코드 대신 현재 작업 트리를 붙여 돌린다 (TDD 반복용).
#           이 경우에도 실행은 container 안이다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="${1:?사용법: scripts/test.sh <service> [--src] [pytest 인자...]}"; shift
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
PREFIX="$(printf '%s' "$SERVICE" | tr '[:lower:]-' '[:upper:]_')"
IMAGE="$(grep -E "^${PREFIX}_IMAGE=" "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
TAG="$(grep -E "^${PREFIX}_TAG=" "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
REF="$IMAGE:$TAG-test"

RUN=(docker run --rm)
if [ "${1:-}" = "--src" ]; then
  shift
  # 공유 패키지(packages/*, packages/targets/*)는 모두 붙인다 (패키지를 더해도 이 파일을 고치지 않는다)
  # 서비스 코드(agents/<s>/src)는 있을 때만 (공유 패키지 시험 이미지는 없다)
  SRC_DIRS=(packages/*/src packages/targets/*/src)
  if [ -d "$ROOT/agents/$SERVICE/src" ]; then SRC_DIRS+=(agents/"$SERVICE"/src); fi
  RUN+=(-v "$ROOT:/src:ro" -e PYTHONPATH="$(cd "$ROOT" && printf '/src/%s:' "${SRC_DIRS[@]}")"
        -e PYTHONDONTWRITEBYTECODE=1 -e PYTEST_ADDOPTS="-p no:cacheprovider")
fi
env_value() { grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2-; }
# live 시험: A4W_LIVE_TESTS=1 이면 실행 설정(.env의 proxy·NO_PROXY), 타겟 설정, 서비스별 비밀을 붙인다
if [ "${A4W_LIVE_TESTS:-}" = "1" ]; then
  RUN+=(--env-file "$ENV_FILE" -e A4W_LIVE_TESTS=1)
  TARGETS_DIR="$(env_value "${PREFIX}_TARGETS_DIR")"
  if [ -n "$TARGETS_DIR" ]; then RUN+=(-v "$ROOT/config/targets:$TARGETS_DIR:ro"); fi
  # agent 설정 (비밀 아님, Phase 3 설계 3.1절): <SERVICE>_AGENTS_DIR이 있으면 config/agents를 붙인다
  AGENTS_DIR="$(env_value "${PREFIX}_AGENTS_DIR" || true)"
  if [ -n "$AGENTS_DIR" ]; then RUN+=(-v "$ROOT/config/agents:$AGENTS_DIR:ro"); fi
  # <SERVICE>_TEST_SECRETS: config/secrets 아래 디렉터리(→ <SERVICE>_SECRETS_DIR) 또는 .env 파일(→ --env-file)
  for item in $(env_value "${PREFIX}_TEST_SECRETS" | tr ',' ' '); do
    if [ -d "$ROOT/config/secrets/$item" ]; then
      RUN+=(-v "$ROOT/config/secrets/$item:$(env_value "${PREFIX}_SECRETS_DIR"):ro")
    elif [ -f "$ROOT/config/secrets/$item" ]; then
      RUN+=(--env-file "$ROOT/config/secrets/$item")
    else
      echo "비밀이 없다: config/secrets/$item" >&2; exit 1
    fi
  done
  ALIAS="$(env_value TARGET_HOST_ALIAS)"
  if [ -n "$ALIAS" ]; then RUN+=(--add-host "$ALIAS"); fi
  # 서비스별로 더 닿아야 하는 이름 (쉼표 구분 "이름:IP" 또는 "이름:host-gateway", 예: AI Pro Plus)
  for alias in $(env_value "${PREFIX}_TEST_HOSTS" | tr ',' ' '); do RUN+=(--add-host "$alias"); done
fi
if [ "$#" -gt 0 ]; then
  "${RUN[@]}" "$REF" python -m pytest "$@"
else
  "${RUN[@]}" "$REF"
fi
