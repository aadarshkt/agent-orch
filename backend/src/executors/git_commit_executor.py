"""
Git Commit Executor.

Commits and pushes changes a prior CLI agent made in the mounted workspace.
The upstream node records its working repos (``<node>_repos``); this executor
picks one, commits, and pushes to that repo's ref. Credentials come from the
host git client (the container stays credential-free).
"""
import asyncio
import os
import subprocess
from typing import Any, Dict, List, Optional

from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor


@register_executor("git_commit")
class GitCommitExecutor(BaseExecutor):
    """Commit + push a prior node's working repo changes."""

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
            "repo": {
                "type": "string",
                "title": "Repo",
                "description": "Name of the repo to commit (from the source node's repos). "
                               "Defaults to the only repo.",
            },
            "branch": {
                "type": "string",
                "title": "Branch",
                "description": "Push target. Defaults to the repo's configured ref.",
            },
            "commit_message": {
                "type": "string",
                "title": "Commit message",
                "description": "Defaults to a generated message if empty",
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
        message = params.get("commit_message", f"chore: update from {agent_name}")

        artifacts = dict(state.get("artifacts", {}))
        workspace = artifacts.get(f"{source_node_id}_workspace")
        if not workspace:
            return await self._fail(context, node_id, state, agent_name,
                                    f"No workspace found for node '{source_node_id}'")

        repos = artifacts.get(f"{source_node_id}_repos") or []
        repo = self._resolve_repo(workspace, repos, params.get("repo"))
        if not repo:
            return await self._fail(
                context, node_id, state, agent_name,
                f"No repo to commit for node '{source_node_id}'"
                + (f" (requested '{params.get('repo')}')" if params.get("repo") else "")
                + ". Set 'repo' to choose one.",
            )

        branch = params.get("branch") or repo.get("ref") or "main"
        repo_dir = repo["dir"]

        await self._emit(context, f"[{agent_name}] Committing {repo.get('name')} -> {branch}...")
        result = await asyncio.to_thread(self._commit, repo_dir, branch, message)

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

    # ── helpers ──────────────────────────────────────────────

    def _resolve_repo(self, workspace: str, repos: List[dict], name: Optional[str]) -> Optional[dict]:
        """Pick the repo record to commit: by name, or the only one present."""
        if name:
            for repo in repos:
                if repo.get("name") == name:
                    return repo
            candidate = os.path.join(workspace, name)
            if os.path.isdir(os.path.join(candidate, ".git")):
                return {"name": name, "dir": candidate, "ref": None}
            return None

        if len(repos) == 1:
            return repos[0]

        if not repos:
            # Fallback for workspaces that did not record their repos.
            repos_dir = os.path.join(workspace, "repos")
            if os.path.isdir(repos_dir):
                candidates = sorted(
                    d for d in os.listdir(repos_dir)
                    if os.path.isdir(os.path.join(repos_dir, d, ".git"))
                )
                if len(candidates) == 1:
                    return {
                        "name": candidates[0],
                        "dir": os.path.join(repos_dir, candidates[0]),
                        "ref": None,
                    }
        return None

    def _commit(self, repo_dir: str, branch: str, message: str) -> str:
        def run(*args: str) -> None:
            subprocess.run(list(args), cwd=repo_dir, check=True, capture_output=True)

        run("git", "config", "user.email", "agent@agent-orch")
        run("git", "config", "user.name", "agent-orch")
        run("git", "add", "-A")
        run("git", "commit", "-m", message)
        # HEAD may be detached (checked out at a ref) — push it to the branch.
        run("git", "push", "origin", f"HEAD:{branch}")
        return f"Committed and pushed to {branch}: {message}"

    async def _fail(self, context, node_id, state, agent_name, error) -> Dict[str, Any]:
        await self._emit(context, f"[{agent_name}] {error}")
        return {**state, "status": "failed", "error": error, "current_step": node_id}

    async def _emit(self, context: ExecutionContext, data: str) -> None:
        if context.event_bus:
            await context.event_bus.publish(
                context.thread_id, {"event": "agent", "data": data}
            )
