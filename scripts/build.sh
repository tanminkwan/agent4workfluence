#!/usr/bin/env bash
# 서비스 이미지를 빌드한다. docker compose와 독립이다.
#   사용법: scripts/build.sh <service> [runtime|test]
#   이미지 이름·태그와 베이스 이미지는 .env(또는 ENV_FILE)에서 읽는다.
#   host의 proxy 환경변수를 빌드 인자로 넘긴다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="${1:?사용법: scripts/build.sh <service> [runtime|test]}"
STAGE="${2:-runtime}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"

env_value() {  # .env에서 KEY의 값만 읽는다 (파일을 실행하지 않는다)
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  [ -n "$line" ] || { echo "설정 없음: $key ($ENV_FILE)" >&2; exit 1; }
  printf '%s' "${line#*=}"
}

PREFIX="$(printf '%s' "$SERVICE" | tr '[:lower:]-' '[:upper:]_')"
IMAGE="$(env_value "${PREFIX}_IMAGE")"
TAG="$(env_value "${PREFIX}_TAG")"
BASE="$(env_value PYTHON_BASE_IMAGE)"
DOCKERFILE="$ROOT/agents/$SERVICE/Dockerfile"
[ -f "$DOCKERFILE" ] || { echo "Dockerfile 없음: $DOCKERFILE" >&2; exit 1; }

case "$STAGE" in
  runtime) REF="$IMAGE:$TAG" ;;
  test)    REF="$IMAGE:$TAG-test" ;;
  *) echo "stage는 runtime 또는 test" >&2; exit 1 ;;
esac

ARGS=(--build-arg "PYTHON_BASE_IMAGE=$BASE")
# 서비스가 요구하는 추가 빌드 인자 (Dockerfile의 ARG 중 .env에 있는 것)
for arg in $(grep -oE '^ARG [A-Z0-9_]+' "$DOCKERFILE" | cut -d' ' -f2 | sort -u); do
  [ "$arg" = PYTHON_BASE_IMAGE ] && continue
  ARGS+=(--build-arg "$arg=$(env_value "$arg")")
done
for v in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
  if [ -n "${!v:-}" ]; then ARGS+=(--build-arg "$v=${!v}"); fi
done

docker build -f "$DOCKERFILE" --target "$STAGE" -t "$REF" "${ARGS[@]}" "$ROOT"
echo "$REF"
