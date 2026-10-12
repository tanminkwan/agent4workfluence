# Detection agent 이미지. 빌드 context는 저장소 루트다.
# proxy는 docker의 사전 정의 빌드 인자(HTTP_PROXY 등)로만 받고 이미지에 남기지 않는다.
ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

FROM base AS deps
COPY agents/detection/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY packages /src/packages
COPY agents/detection /src/agents/detection
# 타겟 어댑터는 packages/targets 아래 모두를 넣는다 (타겟을 더해도 이 파일을 고치지 않는다)
RUN pip install --no-cache-dir --no-deps /src/packages/core /src/packages/targets/*/ /src/agents/detection

FROM deps AS test
COPY agents/detection/requirements-test.txt /tmp/requirements-test.txt
RUN pip install --no-cache-dir -r /tmp/requirements-test.txt
COPY pytest.ini /src/pytest.ini
WORKDIR /src
CMD ["python", "-m", "pytest", "-q", "packages", "agents/detection/tests"]

FROM deps AS runtime
# 보관소 경로는 .env의 DETECTION_SPEC_STORE_PATH를 빌드 인자로 받는다 (named volume 소유자를 맞추기 위해)
ARG DETECTION_SPEC_STORE_PATH
RUN test -n "$DETECTION_SPEC_STORE_PATH" \
 && useradd --create-home --uid 10001 a4w \
 && mkdir -p "$DETECTION_SPEC_STORE_PATH" \
 && chown a4w:a4w "$DETECTION_SPEC_STORE_PATH"
USER a4w
WORKDIR /home/a4w
CMD ["sh", "-c", "exec uvicorn a4w_detection.app:create_app --factory --host 0.0.0.0 --port \"$DETECTION_PORT\" --workers 1"]
