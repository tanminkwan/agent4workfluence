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
  RUN+=(-v "$ROOT:/src:ro" -e PYTHONPATH="$(cd "$ROOT" && printf '/src/%s:' packages/core/src packages/targets/*/src agents/"$SERVICE"/src)"
        -e PYTHONDONTWRITEBYTECODE=1 -e PYTEST_ADDOPTS="-p no:cacheprovider")
fi
# live 시험: A4W_LIVE_TESTS=1 이면 실행 설정(.env의 proxy·NO_PROXY)과 타겟 설정을 붙인다
if [ "${A4W_LIVE_TESTS:-}" = "1" ]; then
  ALIAS="$(grep -E '^TARGET_HOST_ALIAS=' "$ENV_FILE" | tail -n1 | cut -d= -f2-)"
  RUN+=(--env-file "$ENV_FILE" -e A4W_LIVE_TESTS=1 -v "$ROOT/config/targets:$(grep -E '^DETECTION_TARGETS_DIR=' "$ENV_FILE" | tail -n1 | cut -d= -f2-):ro")
  if [ -n "$ALIAS" ]; then RUN+=(--add-host "$ALIAS"); fi
fi
if [ "$#" -gt 0 ]; then
  "${RUN[@]}" "$REF" python -m pytest "$@"
else
  "${RUN[@]}" "$REF"
fi
