"""
CLI Agent Executor.

Runs a CLI agent inside a container (or host subprocess fallback). The full
lifecycle: clone repo(s) into a fresh workspace -> fetch prompt/skills -> run
the runtime's image -> stream output to SSE -> record artifacts.

The executor is intentionally "yolo": one-shot, non-interactive. A timeout
marks the node failed.
"""
import asyncio
import os
import tempfile
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor
from src.registry.runtime_registry import resolve_runtime, get_registry_creds
from src.tools.git_fetcher import clone_repo, fetch_file


@register_executor("cli_agent")
class CliAgentExecutor(BaseExecutor):
    """Run a CLI agent in a container with repo + prompt + skills."""

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
            },
            "repo_urls": {
                "type": "array",
                "items": {"type": "string", "format": "uri"},
                "title": "Repository URLs",
                "description": "Repos to clone into the workspace",
            },
            "branch": {
                "type": "string",
                "default": "main",
                "title": "Branch",
            },
            "prompt_url": {
                "type": "string",
                "format": "uri",
                "title": "Prompt URL",
                "description": "GitLab raw file URL for prompt.md",
            },
            "prompt": {
                "type": "string",
                "title": "Inline Prompt",
                "description": "Alternative to prompt_url",
                "ui:widget": "textarea",
            },
            "skill_urls": {
                "type": "array",
                "items": {"type": "string", "format": "uri"},
                "title": "Skill URLs",
                "description": "GitLab raw file URLs for skill.md files",
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

        repo_urls = params.get("repo_urls", [])
        branch = params.get("branch", "main")
        prompt_url = params.get("prompt_url", "")
        inline_prompt = params.get("prompt", "")
        skill_urls = params.get("skill_urls", [])
        env_overrides = params.get("env", {})
        timeout = params.get("timeout", runtime.timeout)
        command_override = params.get("command")

        workspace = self._make_workspace(context.thread_id, node_id)
        await self._emit(context, f"[{agent_name}] Workspace: {workspace}")

        # 1. Clone repo(s)
        for i, url in enumerate(repo_urls):
            dest = workspace if len(repo_urls) == 1 else os.path.join(workspace, "repos", str(i))
            await self._emit(context, f"[{agent_name}] Cloning {url} (branch {branch})...")
            await asyncio.to_thread(clone_repo, url, dest, branch)

        # 2. Fetch skills
        if skill_urls:
            skills_dir = os.path.join(workspace, "skills")
            for url in skill_urls:
                name = self._basename(url) or "skill.md"
                await self._emit(context, f"[{agent_name}] Fetching skill {name}...")
                await asyncio.to_thread(fetch_file, url, os.path.join(skills_dir, name))

        # 3. Resolve prompt (URL takes precedence over inline)
        prompt_text = inline_prompt
        if prompt_url:
            prompt_path = os.path.join(workspace, "prompt.md")
            await self._emit(context, f"[{agent_name}] Fetching prompt...")
            await asyncio.to_thread(fetch_file, prompt_url, prompt_path)
            prompt_text = await asyncio.to_thread(self._read, prompt_path)

        # 4. Run the container
        image = runtime.image
        if not image:
            raise ValueError(f"Runtime '{runtime_name}' has no image configured")

        await self._docker_login(context)
        cmd = self._build_docker_cmd(
            image=image,
            command=command_override or runtime.command or [],
            workspace=workspace,
            env={**runtime.env, **env_overrides},
            resource_limits=runtime.resource_limits or {},
            prompt=prompt_text,
        )
        await self._emit(context, f"[{agent_name}] Running {image}...")

        try:
            return_code, output = await self._run(cmd, timeout, context)
        except TimeoutError:
            artifacts = dict(state.get("artifacts", {}))
            artifacts[f"{node_id}_workspace"] = workspace
            artifacts[f"{node_id}_output"] = f"Timed out after {timeout}s"
            messages = list(state.get("messages", []))
            messages.append({"role": "assistant", "content": f"{agent_name} timed out."})
            await self._emit(context, f"[{agent_name}] Timed out after {timeout}s.")
            return {
                **state,
                "messages": messages,
                "artifacts": artifacts,
                "current_step": node_id,
                "status": "failed",
                "error": f"Timed out after {timeout}s",
            }

        artifacts = dict(state.get("artifacts", {}))
        artifacts[f"{node_id}_workspace"] = workspace
        artifacts[f"{node_id}_exit_code"] = return_code
        artifacts[f"{node_id}_output"] = output

        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": output[-2000:] or f"{agent_name} completed."})

        status = "completed" if return_code == 0 else "failed"
        await self._emit(context, f"[{agent_name}] Finished (exit {return_code}).")

        return {
            **state,
            "messages": messages,
            "artifacts": artifacts,
            "current_step": node_id,
            "status": status,
            "error": None if return_code == 0 else f"CLI exited with code {return_code}",
        }

    # ── helpers ──────────────────────────────────────────────

    def _make_workspace(self, thread_id: str, node_id: str) -> str:
        root = os.getenv("WORKSPACE_ROOT", tempfile.gettempdir())
        path = os.path.join(root, "agent-orch", thread_id, node_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _basename(self, url: str) -> str:
        return os.path.basename(urlparse(url).path)

    def _read(self, path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def _build_docker_cmd(
        self,
        image: str,
        command: List[str],
        workspace: str,
        env: Dict[str, str],
        resource_limits: Dict[str, Any],
        prompt: str,
    ) -> List[str]:
        cmd = ["docker", "run", "--rm"]
        if resource_limits.get("cpus"):
            cmd += ["--cpus", str(resource_limits["cpus"])]
        if resource_limits.get("memory"):
            cmd += ["--memory", str(resource_limits["memory"])]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += ["-v", f"{workspace}:/workspace", "-w", "/workspace"]
        cmd.append(image)
        cmd += [str(c) for c in command]
        if prompt:
            cmd.append(prompt)
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

    async def _emit(self, context: ExecutionContext, data: str) -> None:
        if context.event_bus:
            await context.event_bus.publish(
                context.thread_id, {"event": "agent", "data": data}
            )
