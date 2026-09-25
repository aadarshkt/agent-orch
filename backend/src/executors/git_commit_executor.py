"""
Git Commit Executor.

Commits a prior CLI agent's work and publishes it for review.

Two targets, one code path each:

  - ``origin`` (default): commit + push the upstream nodes' working repo
    (``<node>_repos``) to its own ``origin``. Credentials come from the host
    git client, so the container stays credential-free.

  - ``github`` (when ``github_repo`` is set): publish the listed nodes' working
    repo and their collected artifacts to a GitHub repo — created public if it
    does not exist — then return a commit URL the caller can open to review.
    The token comes from the env var named by ``github_token_env`` (default
    ``GITHUB_TOKEN``).

The GitHub repo is a dedicated review sink: it mirrors the latest run, so the
pull/push to that repo is forced. Older runs stay inspectable in the dashboard
through the persisted checkpointer state (``GET /workflows/{thread_id}/state``).
"""
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor


@register_executor("git_commit")
class GitCommitExecutor(BaseExecutor):
    """Commit + push a prior node's work, optionally to GitHub."""

    runtime_kind = "none"
    display_name = "Git Commit"
    icon = "terminal"

    input_schema = {
        "type": "object",
        "properties": {
            "source_nodes": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "title": "Source nodes",
                "description": "Nodes whose workspaces and artifacts are committed together",
            },
            "repo": {
                "type": "string",
                "title": "Repo",
                "description": "Name of the repo to commit (from the source nodes' repos). "
                               "Defaults to the only repo.",
            },
            "branch": {
                "type": "string",
                "title": "Branch",
                "description": "Push target. Defaults to the repo's configured ref, else 'main'.",
            },
            "commit_message": {
                "type": "string",
                "title": "Commit message",
                "description": "Defaults to a generated message if empty",
            },
            "github_repo": {
                "type": "string",
                "title": "GitHub repo",
                "description": "Publish to this GitHub repo as 'owner/name' (created public if "
                               "missing) and return a commit URL. When empty, pushes to origin.",
            },
            "visibility": {
                "type": "string",
                "enum": ["public", "private"],
                "default": "public",
                "title": "Repo visibility",
                "description": "Visibility used when creating the GitHub repo",
            },
            "github_token_env": {
                "type": "string",
                "default": "GITHUB_TOKEN",
                "title": "Token env var",
                "description": "Host env var holding the GitHub token",
            },
            "publish_artifacts": {
                "type": "boolean",
                "default": True,
                "title": "Publish artifacts",
                "description": "Include the source nodes' collected artifact files in the commit",
            },
        },
        "required": ["source_nodes"],
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

        source_nodes = list(params.get("source_nodes") or [])
        if not source_nodes:
            return await self._fail(context, node_id, state, agent_name,
                                    "No source_nodes given")
        label = ", ".join(source_nodes)
        message = params.get("commit_message", f"chore: update from {agent_name}")

        artifacts = dict(state.get("artifacts", {}))
        workspace = next(
            (artifacts[f"{n}_workspace"] for n in source_nodes
             if artifacts.get(f"{n}_workspace")),
            None,
        )
        if not workspace:
            return await self._fail(context, node_id, state, agent_name,
                                    f"No workspace found for nodes '{label}'")

        repos = next(
            (artifacts[f"{n}_repos"] for n in source_nodes
             if artifacts.get(f"{n}_repos")),
            [],
        )
        repo = self._resolve_repo(workspace, repos, params.get("repo"))
        branch = params.get("branch") or (repo.get("ref") if repo else None) or "main"

        # Each artifact is paired with its own node's workspace so paths stay
        # relative to the node that produced them.
        artifact_pairs = [
            (artifacts.get(f"{n}_workspace"), path)
            for n in source_nodes
            for path in (artifacts.get(f"{n}_artifacts") or [])
        ]

        github_repo = params.get("github_repo")
        commit_url = None

        try:
            if github_repo:
                await self._emit(
                    context,
                    f"[{agent_name}] Publishing {label} output to GitHub "
                    f"{github_repo} -> {branch}...",
                )
                commit_url, result = await asyncio.to_thread(
                    self._publish_github,
                    repo,
                    artifact_pairs,
                    github_repo,
                    params,
                    branch,
                    message,
                )
            else:
                if not repo:
                    return await self._fail(
                        context, node_id, state, agent_name,
                        f"No repo to commit for nodes '{label}'"
                        + (f" (requested '{params.get('repo')}')" if params.get("repo") else "")
                        + ". Set 'repo' to choose one, or 'github_repo' to publish artifacts.",
                    )
                await self._emit(
                    context, f"[{agent_name}] Committing {repo.get('name')} -> {branch}..."
                )
                result = await asyncio.to_thread(self._commit, repo["dir"], branch, message)
        except Exception as e:
            return await self._fail(context, node_id, state, agent_name, str(e))

        new_artifacts = dict(state.get("artifacts", {}))
        new_artifacts[f"{node_id}_commit"] = result
        if commit_url:
            new_artifacts[f"{node_id}_commit_url"] = commit_url

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

    # ── origin push ──────────────────────────────────────────

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

    # ── github publish ───────────────────────────────────────

    def _publish_github(
        self,
        repo: Optional[dict],
        artifact_pairs: List[Tuple[Optional[str], str]],
        github_repo: str,
        params: Dict[str, Any],
        branch: str,
        message: str,
    ) -> Tuple[str, str]:
        """Publish the nodes' repo/artifacts to GitHub; return (commit_url, result)."""
        token = self._github_token(params)
        owner, _, name = github_repo.partition("/")
        if not owner or not name:
            raise ValueError(f"github_repo must be 'owner/name', got '{github_repo}'")

        self._ensure_github_repo(owner, name, params.get("visibility", "public"), token)

        temp_dir = None
        if repo and repo.get("dir"):
            publish_dir = repo["dir"]
        else:
            # Artifacts-only run: publish from a fresh repo.
            temp_dir = tempfile.mkdtemp(prefix="agent-orch-github-")
            publish_dir = temp_dir
            self._git(publish_dir, "init", "-b", branch)

        try:
            if params.get("publish_artifacts", True):
                self._copy_artifacts(publish_dir, artifact_pairs)

            self._git(publish_dir, "config", "user.email", "agent@agent-orch")
            self._git(publish_dir, "config", "user.name", "agent-orch")
            self._git(publish_dir, "add", "-A")
            if self._git(publish_dir, "status", "--porcelain").strip():
                self._git(publish_dir, "commit", "-m", message)
            sha = self._git(publish_dir, "rev-parse", "HEAD").strip()

            remote = self._github_remote(owner, name, token)
            self._git(publish_dir, "push", "--force", remote, f"HEAD:{branch}")
        finally:
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)

        commit_url = self._commit_url(owner, name, sha)
        return commit_url, f"Pushed to {owner}/{name}@{branch} ({sha[:7]}): {commit_url}"

    def _github_remote(self, owner: str, name: str, token: str) -> str:
        return f"https://x-access-token:{token}@github.com/{owner}/{name}.git"

    def _commit_url(self, owner: str, name: str, sha: str) -> str:
        return f"https://github.com/{owner}/{name}/commit/{sha}"

    def _copy_artifacts(
        self,
        publish_dir: str,
        artifact_pairs: List[Tuple[Optional[str], str]],
    ) -> None:
        """Copy artifacts into the publish tree, preserving each one's node-relative path."""
        for workspace, path in artifact_pairs:
            if not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, workspace) if workspace else os.path.basename(path)
            if rel.startswith(".."):
                rel = os.path.join("artifacts", os.path.basename(path))
            dest = os.path.join(publish_dir, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(path, dest)

    def _github_token(self, params: Dict[str, Any]) -> str:
        env_name = params.get("github_token_env") or "GITHUB_TOKEN"
        token = os.environ.get(env_name, "")
        if not token:
            raise RuntimeError(f"GitHub publishing requires a token in env '{env_name}'.")
        return token

    def _ensure_github_repo(self, owner: str, name: str, visibility: str, token: str) -> None:
        """Create the GitHub repo if it does not exist (public by default)."""
        if self._github_get(f"/repos/{owner}/{name}", token) is not None:
            return
        created = self._github_post(
            "/user/repos",
            {"name": name, "private": visibility != "public"},
            token,
        )
        full_name = (created or {}).get("full_name")
        if full_name and full_name != f"{owner}/{name}":
            raise RuntimeError(
                f"github_repo '{owner}/{name}' does not match the authenticated account "
                f"('{full_name}'). Use the authenticated user as the owner."
            )

    def _github_get(self, path: str, token: str) -> Optional[dict]:
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            headers=self._github_headers(token),
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise RuntimeError(f"GitHub GET {path} failed ({e.code}): {e.read().decode(errors='replace')}")

    def _github_post(self, path: str, payload: dict, token: str) -> Optional[dict]:
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers=self._github_headers(token),
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"GitHub POST {path} failed ({e.code}): {e.read().decode(errors='replace')}")

    def _github_headers(self, token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "agent-orch",
        }

    def _git(self, cwd: str, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
        )
        return proc.stdout

    async def _fail(self, context, node_id, state, agent_name, error) -> Dict[str, Any]:
        await self._emit(context, f"[{agent_name}] {error}")
        return {**state, "status": "failed", "error": error, "current_step": node_id}

    async def _emit(self, context: ExecutionContext, data: str) -> None:
        if context.event_bus:
            await context.event_bus.publish(
                context.thread_id, {"event": "agent", "data": data}
            )
