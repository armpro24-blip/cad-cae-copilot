FROM node:20-bookworm-slim AS frontend-build

WORKDIR /src/aieng-ui/frontend

COPY aieng-ui/frontend/package*.json ./
# Install exactly the committed lockfile. `npm install` without it re-resolved
# every range on each build, so an unchanged commit started failing when the
# registry moved (nightly red from 2026-09-22 on the same SHA).
RUN npm ci

COPY aieng-ui/frontend/ ./
RUN npm run build


FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AIENG_PLATFORM_DATA=/data \
    AIENG_ROOT=/opt/aieng/aieng \
    AIENG_BACKEND_HOST=0.0.0.0 \
    AIENG_BACKEND_PORT=8000 \
    AIENG_MCP_HOST=0.0.0.0 \
    AIENG_MCP_PORT=8765 \
    AIENG_MCP_MANAGED_APPROVAL=1 \
    AIENG_BACKEND_URL=http://127.0.0.1:8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        calculix-ccx \
        curl \
        libegl1 \
        libgl1 \
        libglx-mesa0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/aieng

COPY aieng/ ./aieng/
COPY aieng-ui/backend/ ./aieng-ui/backend/
COPY aieng-agent-skills/ ./aieng-agent-skills/
COPY AGENTS.md README.md ./
COPY --from=frontend-build /src/aieng-ui/frontend/dist/ ./aieng-ui/frontend/dist/

RUN python -m pip install --upgrade pip \
    && python -m pip install ./aieng \
    && python -m pip install './aieng-ui/backend[full]' \
    && python -c "import build123d; import app.mcp_server"

COPY docker/entrypoint.sh /opt/aieng/docker/entrypoint.sh
RUN chmod +x /opt/aieng/docker/entrypoint.sh && mkdir -p /data

WORKDIR /opt/aieng/aieng-ui/backend

EXPOSE 8000 8765
VOLUME ["/data"]

HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=6 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/aieng/docker/entrypoint.sh"]
