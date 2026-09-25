# A real, open-source CLI agent (Aider, Apache-2.0) for exercising cli_agent
# runs that actually reason about and edit a mounted repo.
#
# Build:  docker build -t agent-orch-aider:latest backend/docker/aider-agent
#
# Register it as a runtime and give the agent a `repos` Source so Aider has a
# working tree. Model + key come from the runtime env (interpolated from
# backend/.env), for example:
#
#   runtimes:
#     - name: aider
#       kind: cli
#       image: agent-orch-aider:latest
#       command: ["/agent/run.sh"]
#       env:
#         AIDER_MODEL: "gpt-4o-mini"          # or any aider-supported model
#         OPENAI_API_KEY: "${OPENAI_API_KEY}" # interpolated from backend/.env
#       timeout: 600
#
# Any aider-supported provider works; swap the key/model env vars to match.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir aider-chat

COPY run.sh /agent/run.sh
RUN chmod +x /agent/run.sh

WORKDIR /workspace
ENTRYPOINT ["/agent/run.sh"]
