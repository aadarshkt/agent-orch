# Backend image for agent-orch (FastAPI + LangGraph).
#
# Build from the repository root:
#   docker build -f deploy/backend.Dockerfile -t agent-orch-backend:latest .
#
# The image ships the Docker *CLI* only (no daemon). At runtime the host's
# docker socket is mounted in, because the cli_agent executor shells out to
# `docker run` to start ephemeral agent containers.

FROM python:3.12-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt


FROM python:3.12-slim

# git: used by git_fetcher / git_commit executors (they run on this host side)
# libpq5: runtime dependency of psycopg
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app/backend
COPY backend/ /app/backend/

ENV PYTHONPATH=/app/backend \
    RUNTIMES_CONFIG=/app/backend/config/runtimes.yaml

EXPOSE 8010

# Single worker on purpose: the SSE event bus is in-process (src/engine/pubsub.py),
# so multiple workers would not see each other's events.
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
