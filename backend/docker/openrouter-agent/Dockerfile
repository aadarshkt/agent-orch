# A real LLM-backed CLI agent that calls OpenRouter directly. No SDKs, no pip
# installs: it uses the Python stdlib, so the image builds in seconds and works
# with any OpenRouter model (including free ones).
#
# Build:  docker build -t agent-orch-openrouter:latest backend/docker/openrouter-agent
#
# The key is NOT baked in. Put OPENROUTER_API_KEY in backend/.env; the runtime
# preset interpolates it and the executor passes it via the mounted env-file.
FROM python:3.12-slim
COPY run.sh /agent/run.sh
RUN chmod +x /agent/run.sh
WORKDIR /workspace
ENTRYPOINT ["/agent/run.sh"]
