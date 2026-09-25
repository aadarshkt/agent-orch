"""
CLI Agent Executor.

Runs a CLI agent inside a container. The full lifecycle:

    resolve Sources (repos / skills / context / prompt) -> mount the node
    workspace -> inject env -> run the runtime image -> stream output to SSE
    -> collect declared artifacts.

Every input is a ``Source { repo, ref, path }``. The executor never assumes a
path convention: it materializes each Source, mounts the node directory at
/workspace, and tells the container where everything is via env vars:

    AGENT_WORKSPACE     /workspace
    AGENT_REPOS_DIR     /workspace/repos
    AGENT_SKILLS_DIR    /workspace/.agent/skills
    AGENT_CONTEXT_DIR   /workspace/.agent/context
    AGENT_PROMPT_FILE   /workspace/.agent/prompt.md

The executor is intentionally one-shot and non-interactive; a timeout marks the
node failed.
"""
import asyncio
import glob as globlib
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor
from src.registry.runtime_registry import resolve_runtime, get_registry_creds
from src.config.schema import Source
from src.tools.git_fetcher import resolve_source, repo_name

# One shape for every asset.
SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "title": "Repo", "description": "Git URL"},
        "ref": {"type": "string", "title": "Ref", "description": "branch | tag | sha"},
        "path": {
            "type": "string",
            "title": "Path",
            "description": "Directory or file inside the repo; empty = whole repo",
            "default": "",
        },
    },
    "required": ["repo", "ref"],
}

_ENV_REF = re.compile(r"\$\{(\w+)\}")


def _interpolate(value: str) -> str:
    """Replace ${VAR} with the host environment value (missing -> empty)."""
    return _ENV_REF.sub(lambda m: os.getenv(m.group(1), ""), str(value))

@register_executor("cli_agent")
class CliAgentExecutor(BaseExecutor):
    """Run a CLI agent in a container from Source-based inputs."""

    runtime_kind = "cli"
    display_name = "CLI Agent"
    icon = "terminal"

    input_schema = {
        "type": "object",
        "properties": {
            "runtime": {
                "type": "string",
                "title": "Runtime",
                "description": "Named CLI runtime preset (from runtimes.yaml)",
                "ui:widget": "runtime",
            },
            "repos": {
                "type": "array",
                "items": SOURCE_SCHEMA,
                "title": "Working repos",
                "description": "Repos to clone, mount and (optionally) commit",
            },
            "skills": {
                "type": "array",
                "items": SOURCE_SCHEMA,
                "title": "Skills",
                "description": "Skill bundles, materialized under .agent/skills/",
            },
            "context": {
                "type": "array",
                "items": SOURCE_SCHEMA,
                "title": "Context",
                "description": "Read-only inputs, materialized under .agent/context/",
            },
            "prompt": {
                **SOURCE_SCHEMA,
                "title": "Prompt",
                "description": "Prompt file, materialized to .agent/prompt.md",
            },
            "artifacts": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Artifacts",
                "description": "Globs (relative to the workspace) to collect and hand off",
            },
            "command": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Command override",
            },
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "title": "Env overrides",
            },
            "timeout": {
                "type": "number",
                "title": "Timeout (seconds)",
            },
        },
        "required": ["runtime"],
    }

    async def execute(
        self,
        node_config: Dict[str, Any],
        state: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        node_id = node_config["id"]
        agent_name = node_config.get("agent_name", node_id)
        params = node_config.get("params", {})

        runtime_name = params.get("runtime")
        if not runtime_name:
            raise ValueError("'runtime' is required for cli_agent")
        runtime = resolve_runtime(runtime_name)
        if not runtime.image:
            raise ValueError(f"Runtime '{runtime_name}' has no image configured")

        repos = params.get("repos") or []
        skills = params.get("skills") or []
        extra_context = params.get("context") or []
        prompt = _as_source(params.get("prompt"))
        artifact_globs = params.get("artifacts") or []
        env_overrides = params.get("env") or {}
        timeout = params.get("timeout", runtime.timeout)
        command_override = params.get("command")

        node_dir = self._make_workspace(context.thread_id, node_id)
        cache_root = os.path.join(os.path.dirname(node_dir), ".cache")
        await self._emit(context, f"[{agent_name}] Workspace: {node_dir}")

        # 1. Working repos -> repos/<name>
        repos_info: List[Dict[str, Any]] = []
        for source in repositories(repos):
            name = repo_name(source.repo)
            dest = os.path.join(node_dir, "repos", name)
            await self._emit(context, f"[{agent_name}] Checking out {source.repo} @ {source.ref}...")
            await asyncio.to_thread(resolve_source, source, dest, cache_root)
            repos_info.append({"name": name, "ref": source.ref, "dir": dest})

        # 2. Skills -> .agent/skills/<name>
        for source in repositories(skills):
            name = _asset_name(source)
            dest = os.path.join(node_dir, ".agent", "skills", name)
            await self._emit(context, f"[{agent_name}] Resolving skill {name}...")
            await asyncio.to_thread(resolve_source, source, dest, cache_root)

        # 3. Explicit context -> .agent/context/<name>
        for source in repositories(extra_context):
            name = _asset_name(source)
            dest = os.path.join(node_dir, ".agent", "context", name)
            await self._emit(context, f"[{agent_name}] Resolving context {name}...")
            await asyncio.to_thread(resolve_source, source, dest, cache_root)

        # 3b. Auto-mount upstream artifacts -> .agent/context/upstream/<node>/
        await self._mount_upstream_artifacts(node_dir, state)

        # 4. Prompt -> .agent/prompt.md
        if prompt:
            dest = os.path.join(node_dir, ".agent", "prompt.md")
            await self._emit(context, f"[{agent_name}] Resolving prompt...")
            await asyncio.to_thread(resolve_source, prompt, dest, cache_root)

        # 5. Build env + write the env-file (secrets never touch the process args)
        env = self._build_env(runtime, env_overrides)
        self._check_required_env(runtime, env)
        self._write_env_file(node_dir, env)

        await self._docker_login(context)
        cmd = self._build_docker_cmd(
            image=runtime.image,
            command=command_override or runtime.command or [],
            node_dir=node_dir,
            resource_limits=runtime.resource_limits or {},
        )
        await self._emit(context, f"[{agent_name}] Running {runtime.image}...")

        try:
            return_code, output = await self._run(cmd, timeout, context)
        except TimeoutError:
            await self._emit(context, f"[{agent_name}] Timed out after {timeout}s.")
            return self._result(
                state, node_dir, node_id, artifact_globs,
                status="failed", error=f"Timed out after {timeout}s",
                message=f"{agent_name} timed out.", repos_info=repos_info,
            )

        found = self._collect_artifacts(node_dir, artifact_globs)
        status = "completed" if return_code == 0 else "failed"
        await self._emit(context, f"[{agent_name}] Finished (exit {return_code}).")

        return self._result(
            state, node_dir, node_id, artifact_globs,
            status=status, error=None if return_code == 0 else f"CLI exited with code {return_code}",
            message=output[-2000:] or f"{agent_name} completed.",
            exit_code=return_code, output=output, artifacts_found=found,
            repos_info=repos_info,
        )

    # ── helpers ──────────────────────────────────────────────

    def _make_workspace(self, thread_id: str, node_id: str) -> str:
        root = os.getenv("WORKSPACE_ROOT", tempfile.gettempdir())
        path = os.path.join(root, "agent-orch", thread_id, node_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _write_env_file(self, node_dir: str, env: Dict[str, str]) -> None:
        path = os.path.join(node_dir, ".agent", "env")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for key, value in env.items():
                f.write(f"{key}={str(value).replace(chr(10), ' ')}\n")

    def _build_env(self, runtime, overrides: Dict[str, str]) -> Dict[str, str]:
        env = {
            "AGENT_WORKSPACE": "/workspace",
            "AGENT_REPOS_DIR": "/workspace/repos",
            "AGENT_SKILLS_DIR": "/workspace/.agent/skills",
            "AGENT_CONTEXT_DIR": "/workspace/.agent/context",
            "AGENT_PROMPT_FILE": "/workspace/.agent/prompt.md",
        }
        # Forward required_env vars from the host environment (backend/.env) so
        # declared-but-unset secrets actually reach the container and a missing
        # one fails fast in _check_required_env.
        for key in (runtime.required_env or []):
            host_value = os.getenv(key)
            if host_value is not None:
                env[key] = host_value
        for key, value in {**runtime.env, **overrides}.items():
            env[key] = _interpolate(value)
        return env

    def _check_required_env(self, runtime, env: Dict[str, str]) -> None:
        missing = [k for k in (runtime.required_env or []) if not env.get(k)]
        if missing:
            raise ValueError(
                f"Runtime '{runtime.name}' is missing required env: {', '.join(missing)}"
            )

    async def _mount_upstream_artifacts(self, node_dir: str, state: Dict[str, Any]) -> None:
        artifacts = state.get("artifacts", {}) or {}
        upstream_dir = os.path.join(node_dir, ".agent", "context", "upstream")
        for key, paths in artifacts.items():
            if not key.endswith("_artifacts") or not isinstance(paths, list):
                continue
            node = key[: -len("_artifacts")]
            for path in paths:
                if os.path.isfile(path):
                    dest_dir = os.path.join(upstream_dir, node)
                    await asyncio.to_thread(_copy_file, path, dest_dir)

    def _collect_artifacts(self, node_dir: str, patterns: List[str]) -> List[str]:
        found: List[str] = []
        for pattern in patterns:
            for path in sorted(globlib.glob(os.path.join(node_dir, pattern), recursive=True)):
                if os.path.isfile(path):
                    found.append(path)
        return found

    def _build_docker_cmd(
        self,
        image: str,
        command: List[str],
        node_dir: str,
        resource_limits: Dict[str, Any],
    ) -> List[str]:
        cmd = ["docker", "run", "--rm"]
        if resource_limits.get("cpus"):
            cmd += ["--cpus", str(resource_limits["cpus"])]
        if resource_limits.get("memory"):
            cmd += ["--memory", str(resource_limits["memory"])]
        # Secrets go through a mounted env-file, never -e (which leaks to
        # the process table and `docker inspect`). Note: --env-file is read by
        # the docker client on the *host*, so it must be the host path (the
        # file lives inside the mounted node directory), not /workspace/...
        cmd += ["--env-file", os.path.join(node_dir, ".agent", "env")]
        cmd += ["-v", f"{node_dir}:/workspace", "-w", "/workspace"]
        cmd.append(image)
        cmd += [str(c) for c in command]
        return cmd

    async def _docker_login(self, context: ExecutionContext) -> None:
        creds = get_registry_creds()
        if not (creds["username"] and creds["password"]):
            return
        cmd = ["docker", "login"]
        if creds["registry"]:
            cmd.append(creds["registry"])
        cmd += ["-u", creds["username"], "--password-stdin"]
        await self._emit(context, "Authenticating to image registry...")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate(input=creds["password"].encode())

    async def _run(
        self,
        cmd: List[str],
        timeout: int,
        context: ExecutionContext,
    ) -> tuple:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        lines: List[str] = []

        async def pump():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode(errors="replace").rstrip("\n")
                lines.append(text)
                if text:
                    await self._emit(context, text)

        pump_task = asyncio.create_task(pump())
        try:
            return_code = await asyncio.wait_for(proc.wait(), timeout=timeout)
            await pump_task
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            await asyncio.gather(pump_task, return_exceptions=True)
            raise TimeoutError(f"CLI agent timed out after {timeout}s")

        return return_code, "\n".join(lines)

    def _result(
        self,
        state: Dict[str, Any],
        node_dir: str,
        node_id: str,
        artifact_globs: List[str],
        status: str,
        error,
        message: str,
        exit_code=None,
        output=None,
        artifacts_found=None,
        repos_info=None,
    ) -> Dict[str, Any]:
        artifacts = dict(state.get("artifacts", {}))
        artifacts[f"{node_id}_workspace"] = node_dir
        artifacts[f"{node_id}_repos"] = repos_info or []
        if exit_code is not None:
            artifacts[f"{node_id}_exit_code"] = exit_code
        if output is not None:
            artifacts[f"{node_id}_output"] = output
        if artifacts_found is None:
            artifacts_found = self._collect_artifacts(node_dir, artifact_globs)
        artifacts[f"{node_id}_artifacts"] = artifacts_found

        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": message})

        return {
            **state,
            "messages": messages,
            "artifacts": artifacts,
            "current_step": node_id,
            "status": status,
            "error": error,
        }

    async def _emit(self, context: ExecutionContext, data: str) -> None:
        if context.event_bus:
            await context.event_bus.publish(
                context.thread_id, {"event": "agent", "data": data}
            )


# ── module-level helpers ─────────────────────────────────────

def repositories(raw) -> List[Source]:
    """Coerce a list of raw Source dicts into Source models."""
    return [item if isinstance(item, Source) else Source(**item) for item in raw or []]


def _as_source(raw) -> Optional[Source]:
    """Coerce a single raw Source dict into a Source model (or None)."""
    if not raw:
        return None
    return raw if isinstance(raw, Source) else Source(**raw)


def _asset_name(source) -> str:
    """Name of an asset within its destination dir (basename of path, else repo name)."""
    if source.path:
        return os.path.basename(source.path.rstrip("/")) or repo_name(source.repo)
    return repo_name(source.repo)


def _copy_file(path: str, dest_dir: str) -> None:
    import shutil

    os.makedirs(dest_dir, exist_ok=True)
    shutil.copy2(path, os.path.join(dest_dir, os.path.basename(path)))
