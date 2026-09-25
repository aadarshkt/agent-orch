# Configuration Review Comments

## Review Target
- File: [backend/config/ecocharge_cli_master.yaml](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/config/ecocharge_cli_master.yaml)
- Related files:
  - [backend/config/runtimes.yaml](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/config/runtimes.yaml)
  - [backend/config/ecocharge_master.yaml](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/config/ecocharge_master.yaml)
  - [backend/src/executors/cli_agent_executor.py](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/src/executors/cli_agent_executor.py)
  - [backend/src/executors/git_commit_executor.py](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/src/executors/git_commit_executor.py)
  - [backend/src/tools/git_fetcher.py](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/src/tools/git_fetcher.py)

---

### Comment 1: Redundant `GITLAB_TOKEN` in `beacon` CLI runtime `required_env`

#### Location
- [backend/config/ecocharge_cli_master.yaml:L49-L50](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/config/ecocharge_cli_master.yaml#L49-L50)
- [backend/config/runtimes.yaml:L22-L23](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/config/runtimes.yaml#L22-L23)
- [backend/config/ecocharge_master.yaml:L57-L58](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/config/ecocharge_master.yaml#L57-L58)

#### Context & Finding
In `ecocharge_cli_master.yaml`, the `beacon` runtime specifies:
```yaml
runtimes:
  - name: beacon
    kind: cli
    ...
    required_env:
      - GITLAB_TOKEN
```

All git repository handling and token authorization in this architecture occur outside the container runtime:

1. **Pre-execution git checkout**:
   - Handled on the host by [git_fetcher.py](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/src/tools/git_fetcher.py#L9-L11).
   - Clones and resolves all `repos`, `skills`, `context`, and `prompt` files directly into host cache and copies them to the node workspace before container startup.
   - Relies on host git configuration and credentials.
2. **Container workspace mount**:
   - The CLI container receives the materialized workspace via Docker bind mount at `/workspace` ([cli_agent_executor.py:L294](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/src/executors/cli_agent_executor.py#L294)).
   - Repositories live under `/workspace/repos/<repo_name>`, skills under `/workspace/.agent/skills/`, and the prompt under `/workspace/.agent/prompt.md`.
   - The container does not clone or pull over git network protocols.
3. **Post-execution commit & push**:
   - Committing and pushing modified files back to GitLab is executed host-side by the downstream node using [GitCommitExecutor](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/src/executors/git_commit_executor.py#L6-L7).
   - Explicit architectural intent: *"Credentials come from the host git client (the container stays credential-free)"*.

#### Issues Caused by `GITLAB_TOKEN` in `required_env`
1. **Unnecessary execution blocker**:
   - [CliAgentExecutor._check_required_env](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/src/executors/cli_agent_executor.py#L252-L258) strictly validates all entries in `required_env`.
   - If `GITLAB_TOKEN` is not exported or defined in `backend/.env`, the executor raises a `ValueError` (`Runtime 'beacon' is missing required env: GITLAB_TOKEN`) and immediately aborts the run, even though the beacon process never touches the variable.
2. **Unnecessary secret leakage into container**:
   - Environment variables listed in `required_env` are written into `.agent/env` on disk and mounted into the container via `--env-file` ([cli_agent_executor.py:L230-L235](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/src/executors/cli_agent_executor.py#L230-L235)).
   - Supplying an unused GitLab personal/project token exposes sensitive host credentials inside the agent container.

#### Origin
This was an artifact carried over from the source `.gitlab-ci.yml` pipeline (`ecochargeapp-feature-vw-poc`), where CI jobs run inside GitLab CI runners and natively receive `$GITLAB_TOKEN` or `$CI_JOB_TOKEN` for CI-level reporting.

#### Recommendation
Remove `GITLAB_TOKEN` from `required_env` for the `beacon` CLI runtime across the configuration files unless the Beacon binary contains an in-container GitLab MCP tool or skill that directly queries the GitLab REST API.

Proposed change:
```yaml
    required_env: []
```

Also update the documentation comment in [backend/config/ecocharge_cli_master.yaml:L23](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/config/ecocharge_cli_master.yaml#L23):
```diff
- #  - GITLAB_TOKEN, FIGMA_ACCESS_TOKEN and GIT_REPO_URL are read from backend/.env.
+ #  - FIGMA_ACCESS_TOKEN and GIT_REPO_URL are read from backend/.env.
```

---

### Related Observation: `GIT_REPO_URL` in `figma_to_code` Agent Env

#### Location
- [backend/config/ecocharge_cli_master.yaml:L145-L147](file:///Users/aadarshkt/Desktop/Projects/agent-orch/backend/config/ecocharge_cli_master.yaml#L145-L147)

#### Context & Finding
`figma_to_code` defines:
```yaml
      env:
        GIT_REPO_URL: "${GIT_REPO_URL}"
        BASE_BRANCH: "feature/vw-poc"
```
Because the working repo is already cloned and mounted at `/workspace/repos/ecochargeapp`, verify whether `Skills/FigmaToCodeAgent` expects `GIT_REPO_URL` or if it should operate strictly against `AGENT_REPOS_DIR`. If it is only used for git operations that `git_commit` now performs, this variable can also be removed.
