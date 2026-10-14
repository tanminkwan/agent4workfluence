# 공용 MCP server(a4w_mcp) 시험 이미지. 빌드 context는 저장소 루트다.
# runtime 단계는 없다. Phase 4 inference 이미지가 이 패키지를 넣는다 (Phase 3 설계 10.1절).
# proxy는 docker의 사전 정의 빌드 인자(HTTP_PROXY 등)로만 받고 이미지에 남기지 않는다.
ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

FROM base AS deps
COPY packages/mcp/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY packages /src/packages
# 공유 패키지(packages/*)와 타겟 어댑터(packages/targets/*)는 모두 넣는다 (더해도 이 파일을 고치지 않는다)
RUN pip install --no-cache-dir --no-deps $(ls -d /src/packages/*/ | grep -v '/targets/$') /src/packages/targets/*/ \
 && pip check

FROM deps AS test
COPY packages/mcp/requirements-test.txt /tmp/requirements-test.txt
RUN pip install --no-cache-dir -r /tmp/requirements-test.txt
COPY pytest.ini /src/pytest.ini
WORKDIR /src
CMD ["python", "-m", "pytest", "-q", "packages"]
