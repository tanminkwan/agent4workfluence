# Inference agent 이미지. 빌드 context는 저장소 루트다. REST 채널(Phase 4)과 수행 로직을 담는다.
# 공용 MCP server(packages/mcp)가 들어가고, agent마다 같은 container 안의 자식 프로세스로 뜬다.
# proxy는 docker의 사전 정의 빌드 인자(HTTP_PROXY 등)로만 받고 이미지에 남기지 않는다.
ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

FROM base AS deps
COPY agents/inference/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY packages /src/packages
COPY agents/inference /src/agents/inference
# 공유 패키지(packages/*)와 타겟 어댑터(packages/targets/*)는 모두 넣는다 (더해도 이 파일을 고치지 않는다)
RUN pip install --no-cache-dir --no-deps $(ls -d /src/packages/*/ | grep -v '/targets/$') /src/packages/targets/*/ /src/agents/inference

FROM deps AS test
COPY agents/inference/requirements-test.txt /tmp/requirements-test.txt
RUN pip install --no-cache-dir -r /tmp/requirements-test.txt
COPY pytest.ini /src/pytest.ini
WORKDIR /src
# packages/mcp의 시험은 mcp 이미지에서 돈다 (두 곳의 tests 패키지 이름이 같아 한 번에 모으면 서로 가린다)
CMD ["python", "-m", "pytest", "-q", "packages/core", "packages/tools", "packages/clients", "agents/inference/tests"]

FROM deps AS runtime
# 보관소 경로는 .env의 INFERENCE_STORE_PATH, 실행 사용자 uid는 .env의 A4W_UID
ARG INFERENCE_STORE_PATH
ARG A4W_UID
RUN test -n "$INFERENCE_STORE_PATH" && test -n "$A4W_UID" \
 && useradd --create-home --uid "$A4W_UID" a4w \
 && mkdir -p "$INFERENCE_STORE_PATH" \
 && chown a4w:a4w "$INFERENCE_STORE_PATH"
USER a4w
WORKDIR /home/a4w
# worker 1개 고정: 동시 수·대화 진행 중 판정이 프로세스 메모리와 SQLite에 의존한다 (설계 13.1, 16절 5번)
CMD ["sh", "-c", "exec uvicorn a4w_inference.channels.rest.app:create_app --factory --host 0.0.0.0 --port \"$INFERENCE_PORT\" --workers 1"]
