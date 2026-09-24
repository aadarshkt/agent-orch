"""
Git Commit Executor.

Commits and pushes generated artifacts from a prior CLI agent's workspace.
Commit logic is intentionally hardcoded (default message, add-all). The
executor reads the workspace path from the upstream node's artifacts.
"""
import asyncio
import os
import subprocess
from typing import Any, Dict, Optional

from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor


@register_executor("git_commit")
class GitCommitExecutor(BaseExecutor):
    """Commit + push a prior node's workspace changes."""

    runtime_kind = "none"
    display_name = "Git Commit"
    icon = "terminal"

    input_schema = {
        "type": "object",
        "properties": {
            "source_node_id": {
                "type": "string",
                "title": "Source node",
                "description": "Node whose workspace should be committed",
            },
            "branch": {
                "type": "string",
                "default": "main",
                "title": "Branch",
            },
            "commit_message": {
                "type": "string",
                "title": "Commit message",
                "description": "Defaults to a generated message if empty",
            },
            "repo_path": {
                "type": "string",
                "title": "Repo path",
                "description": "Git repo within the workspace to commit (e.g. 'repos/0'). "
                               "Defaults to the workspace root, or the only repo under repos/.",
            },
        },
        "required": ["source_node_id"],
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

        source_node_id = params.get("source_node_id")
        branch = params.get("branch", "main")
        message = params.get("commit_message", f"chore: update from {agent_name}")
        repo_path = params.get("repo_path")

        artifacts = dict(state.get("artifacts", {}))
        workspace = artifacts.get(f"{source_node_id}_workspace")
        if not workspace:
            error = f"No workspace found for node '{source_node_id}'"
            await self._emit(context, f"[{agent_name}] {error}")
            return {**state, "status": "failed", "error": error, "current_step": node_id}

        repo = self._resolve_repo(workspace, repo_path)
        if not repo:
            error = (
                f"No git repository found in workspace for node '{source_node_id}'"
                + (f" at '{repo_path}'" if repo_path else "")
                + ". Set 'repo_path' to choose one."
            )
            await self._emit(context, f"[{agent_name}] {error}")
            return {**state, "status": "failed", "error": error, "current_step": node_id}

        await self._emit(context, f"[{agent_name}] Committing repo {repo}...")

        result = await asyncio.to_thread(self._commit, repo, branch, message)

        new_artifacts = dict(state.get("artifacts", {}))
        new_artifacts[f"{node_id}_commit"] = result

        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": f"{agent_name}: {result}"})

        return {
            **state,
            "messages": messages,
            "artifacts": new_artifacts,
            "current_step": node_id,
            "status": "completed",
            "error": None,
        }

    def _resolve_repo(self, workspace: str, repo_path: Optional[str]) -> Optional[str]:
        """Resolve which git repo inside the workspace to commit."""
        if repo_path:
            candidate = os.path.join(workspace, repo_path)
            return candidate if os.path.isdir(os.path.join(candidate, ".git")) else None

        if os.path.isdir(os.path.join(workspace, ".git")):
            return workspace

        repos_dir = os.path.join(workspace, "repos")
        if os.path.isdir(repos_dir):
            candidates = sorted(
                os.path.join(repos_dir, d)
                for d in os.listdir(repos_dir)
                if os.path.isdir(os.path.join(repos_dir, d, ".git"))
            )
            if len(candidates) == 1:
                return candidates[0]
        return None

    def _commit(self, workspace: str, branch: str, message: str) -> str:
        def run(*args: str) -> str:
            subprocess.run(
                list(args), cwd=workspace, check=True, capture_output=True
            )

        run("git", "add", "-A")
        run("git", "commit", "-m", message)
        run("git", "push", "origin", branch)
        return f"Committed and pushed to {branch}: {message}"

    async def _emit(self, context: ExecutionContext, data: str) -> None:
        if context.event_bus:
            await context.event_bus.publish(
                context.thread_id, {"event": "agent", "data": data}
            )
