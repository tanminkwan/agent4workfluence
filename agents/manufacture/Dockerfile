# Manufacture agent 이미지. 빌드 context는 저장소 루트다. 변경 반영과 tool 정의 조회 API를 연다.
# proxy는 docker의 사전 정의 빌드 인자(HTTP_PROXY 등)로만 받고 이미지에 남기지 않는다.
ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

FROM base AS deps
COPY agents/manufacture/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY packages /src/packages
COPY agents/manufacture /src/agents/manufacture
# 공유 패키지(packages/*)와 타겟 어댑터(packages/targets/*)는 모두 넣는다 (더해도 이 파일을 고치지 않는다)
RUN pip install --no-cache-dir --no-deps $(ls -d /src/packages/*/ | grep -v '/targets/$') /src/packages/targets/*/ /src/agents/manufacture

FROM deps AS test
COPY agents/manufacture/requirements-test.txt /tmp/requirements-test.txt
RUN pip install --no-cache-dir -r /tmp/requirements-test.txt
COPY pytest.ini /src/pytest.ini
WORKDIR /src
CMD ["python", "-m", "pytest", "-q", "packages", "agents/manufacture/tests"]

FROM deps AS runtime
# 보관소 경로는 .env의 MANUFACTURE_TOOL_STORE_PATH, 실행 사용자 uid는 .env의 A4W_UID
ARG MANUFACTURE_TOOL_STORE_PATH
ARG A4W_UID
RUN test -n "$MANUFACTURE_TOOL_STORE_PATH" && test -n "$A4W_UID" \
 && useradd --create-home --uid "$A4W_UID" a4w \
 && mkdir -p "$MANUFACTURE_TOOL_STORE_PATH" \
 && chown a4w:a4w "$MANUFACTURE_TOOL_STORE_PATH"
USER a4w
WORKDIR /home/a4w
CMD ["sh", "-c", "exec uvicorn a4w_manufacture.app:create_app --factory --host 0.0.0.0 --port \"$MANUFACTURE_PORT\" --workers 1"]
