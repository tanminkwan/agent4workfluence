#!/usr/bin/env bash
# Manufacture LLM 평가를 container 안에서 돌린다 (제품 코드가 아닌 평가 기록용 스크립트).
#   docs/phase2_manufacture/eval/run.sh <script> <spec.yaml> [인자...]
#   예) run.sh eval.py /path/openapi.yaml zai-org/GLM-5.2 none 3
#       PROBE_MODEL=zai-org/GLM-5.2 run.sh probe_added.py /path/openapi.yaml
# LLM 주소·key는 config/secrets/manufacture-llm.env (LLM_BASE_URL, LLM_API_KEY)에서 읽는다.
# 결과 파일은 EVAL_OUT(기본 /w/out → 이 디렉터리 밖의 임시 디렉터리)에 쓴다.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd); root=$(cd "$here/../../.." && pwd)
script=$1; spec=$2; shift 2
work=$(mktemp -d); cp "$here"/*.py "$work"/; cp "$spec" "$work/openapi.yaml"
set -a; . "$root/.env"; set +a
docker run --rm -v "$work":/w -w /w --env-file "$root/config/secrets/manufacture-llm.env" \
  -e HTTP_PROXY -e HTTPS_PROXY -e NO_PROXY -e PROBE_MODEL -e PROBE_EFFORT -e PROBE_RUNS -e EVAL_PICKER_MODEL \
  "${DETECTION_IMAGE}:${DETECTION_TAG}-test" python "/w/$script" "$@"
echo "작업 디렉터리: $work" >&2
