# Template for a CLI agent image (one image per agent).
# Static runtime: CLI binary, language runtime, packages, and bundled skills
# are baked in here. Dynamic inputs (repo, branch, prompt, env) are injected
# at execution time by the CliAgentExecutor.
FROM python:3.12-slim

# Install the CLI binary and its packages (customize for your agent)
COPY requirements.txt /opt/agent/requirements.txt
RUN pip install --no-cache-dir -r /opt/agent/requirements.txt

# Optionally bake skills into the image
# COPY skills /opt/skills

WORKDIR /workspace
ENTRYPOINT ["python", "-m", "eco_agent"]
