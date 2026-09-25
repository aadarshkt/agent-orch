# A minimal stand-in for a real CLI agent, for testing the cli_agent executor.
# It honors the AGENT_* env contract and writes a file the workflow declares as
# an artifact. No git, no network, no API keys.
#
# Build:  docker build -t agent-orch-test-cli:latest backend/docker/test-agent
FROM alpine:3.20
RUN apk add --no-cache bash
COPY run.sh /agent/run.sh
RUN chmod +x /agent/run.sh
WORKDIR /workspace
ENTRYPOINT ["/agent/run.sh"]
