"""
GitHub PR Executor.

Turns a CLI agent's working tree into a draft pull request a developer can
review in minutes:

  - The branch is the source node's commits on top of the checked-out base.
    The issue-fix stages leave a failing regression test as its own commit and
    the fix uncommitted; this executor commits the fix, so the PR reads
    "test proves the bug" -> "change fixes it", and either commit can be
    checked out on its own.
  - The body leads with the facts a reviewer needs before the diff (what
    broke, root cause, was it reproduced, do tests pass, what the automated
    reviewer said), then exact commands to verify locally, then the long
    material (agent reports, stack trace) folded away in <details>.
  - The PR is a draft. Nothing merges without a human.

The target repo is the source node's working repo (its git URL), so the same
workflow serves any GitHub repo the token can push to. Re-running for the same
incident force-pushes the same branch and updates the existing PR.
"""
import asyncio
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from src.engine.templating import first_output, node_outputs
from src.registry.base_executor import BaseExecutor, ExecutionContext
from src.registry.executor_registry import register_executor

# GitHub rejects bodies over 65536 characters.
BODY_LIMIT = 60000
SECTION_LIMIT = 12000

# Build and tool droppings that must never land in a PR. Written to
# .git/info/exclude, which is local to the clone and never committed.
JUNK_PATTERNS = [
    ".aider*", "__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/",
    ".ruff_cache/", "*.egg-info/", ".venv/", "node_modules/", ".agent/",
]

_GITHUB_URL = re.compile(
    r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"([^/]+)/([^/]+?)(?:\.git)?/?$"
)
_VERDICT = re.compile(r"VERDICT:\s*\**\s*([A-Za-z ]+?)\s*\**\s*(?:[-:.—]\s*(.*))?$", re.MULTILINE)


def parse_github_repo(url: str) -> Tuple[str, str]:
    """``https://github.com/o/n(.git)`` or ``git@github.com:o/n.git`` -> (o, n)."""
    m = _GITHUB_URL.match((url or "").strip())
    if not m:
        raise ValueError(f"Not a GitHub repo URL: '{url}'")
    return m.group(1), m.group(2)


def parse_verdict(review: str) -> Tuple[Optional[str], str]:
    """Pull ``VERDICT: SHIP - reason`` out of a reviewer report."""
    matches = _VERDICT.findall(review or "")
    if not matches:
        return None, ""
    verdict, reason = matches[-1]
    return verdict.strip().upper(), (reason or "").strip()


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n\n... truncated ({len(text) - limit} more characters)"


def _fence(text: str, lang: str = "") -> str:
    fence = "~~~~" if "```" in text else "```"
    return f"{fence}{lang}\n{text.rstrip()}\n{fence}"


def build_pr_body(
    incident: Dict[str, Any],
    sections: List[Dict[str, Any]],
    commits: List[Dict[str, str]],
    files: List[Dict[str, Any]],
    branch: str,
    test_command: str = "",
    outputs: Optional[Dict[str, Dict[str, Any]]] = None,
    review_text: str = "",
    run_ref: str = "",
    closes: str = "",
) -> str:
    """Render the PR description. Pure: everything it shows is passed in."""
    outputs = outputs or {}
    verdict, reason = parse_verdict(review_text)
    root_cause = first_output(outputs, "root_cause")
    reproduced = first_output(outputs, "reproduced")
    tests_passed = first_output(outputs, "tests_passed")

    summary = first_output(outputs, "summary") or ""
    lines: List[str] = ["## Summary", ""]
    lines.append(summary.strip() or f"Proposed fix for: {incident.get('title') or 'an alert'}.")
    lines.append("")

    facts: List[Tuple[str, str]] = []
    if incident.get("title"):
        title = incident["title"].replace("|", "\\|")
        facts.append(("Alert", f"[{title}]({incident['url']})" if incident.get("url") else title))
    where = " / ".join(str(v) for v in (incident.get("source"), incident.get("project"),
                                         incident.get("environment")) if v)
    if where:
        facts.append(("Source", where))
    if incident.get("occurrences"):
        facts.append(("Occurrences", str(incident["occurrences"])))
    if root_cause:
        facts.append(("Root cause", str(root_cause).replace("\n", " ").replace("|", "\\|")))
    if reproduced is not None:
        facts.append(("Reproduced", "Yes, the new test failed before the fix"
                      if reproduced else "No, the fix is not backed by a failing test"))
    if tests_passed is not None:
        facts.append(("Tests after fix", "Passing" if tests_passed else "Failing"))
    if verdict:
        facts.append(("Automated review", f"{verdict}{f' ({reason})' if reason else ''}"))
    if run_ref:
        facts.append(("Run", run_ref))
    if facts:
        lines += ["| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in facts] + [""]

    lines += ["## How to review", ""]
    if len(commits) >= 2:
        lines.append("Review the commits in order:")
        lines.append("")
        for i, c in enumerate(commits, 1):
            lines.append(f"{i}. `{c['sha'][:7]}` {c['subject']}")
        lines.append("")
    if test_command and commits:
        lines.append("Verify locally:")
        lines.append("")
        cmd = [f"git fetch origin {branch}"]
        if len(commits) >= 2:
            cmd += [f"git checkout {commits[0]['sha'][:7]}   # regression test only: expect a failure",
                    test_command]
        cmd += [f"git checkout {commits[-1]['sha'][:7]}   # with the fix: expect a pass", test_command]
        lines += [_fence("\n".join(cmd), "sh"), ""]
    if files:
        lines.append("Files changed:")
        lines.append("")
        for f in files:
            lines.append(f"- `{f['path']}` (+{f['added']} / -{f['deleted']})")
        lines.append("")

    for section in sections:
        content = _truncate(section.get("content") or "", SECTION_LIMIT)
        if not content:
            continue
        if section.get("collapsed"):
            lines += [f"<details><summary>{section['title']}</summary>", "", content, "", "</details>", ""]
        else:
            lines += [f"## {section['title']}", "", content, ""]

    if incident.get("stacktrace") or incident.get("logs"):
        evidence = incident.get("stacktrace") or ""
        if incident.get("logs"):
            evidence = f"{evidence}\n\n{str(incident['logs'])[-4000:]}".strip()
        lines += ["<details><summary>Original stack trace / logs</summary>", "",
                  _fence(_truncate(evidence, SECTION_LIMIT)), "", "</details>", ""]

    lines += ["---", ""]
    if closes:
        lines += [f"Closes {closes}", ""]
    lines.append("Opened automatically by agent-orch as a draft. Nothing merges without a human "
                 "review; push to this branch or close the PR to discard it.")
    return _truncate("\n".join(lines), BODY_LIMIT)


@register_executor("github_pr")
class GitHubPrExecutor(BaseExecutor):
    """Open (or update) a draft GitHub pull request from a CLI agent's working repo."""

    runtime_kind = "none"
    display_name = "GitHub Pull Request"

    input_schema = {
        "type": "object",
        "properties": {
            "source_node": {
                "type": "string",
                "title": "Source node",
                "description": "Node whose working repo holds the change",
            },
            "repo": {
                "type": "string",
                "title": "Repo",
                "description": "Repo name within the source node, when it has several",
            },
            "base": {
                "type": "string",
                "title": "Base branch",
                "description": "PR base. Defaults to the ref the repo was checked out at.",
            },
            "branch": {
                "type": "string",
                "title": "Head branch",
                "description": "Branch to push. Supports {{ }} placeholders.",
            },
            "title": {"type": "string", "title": "PR title"},
            "commit_message": {
                "type": "string",
                "title": "Commit message",
                "description": "Message for the uncommitted changes (the fix)",
            },
            "draft": {"type": "boolean", "default": True, "title": "Draft"},
            "labels": {"type": "array", "items": {"type": "string"}, "title": "Labels"},
            "reviewers": {"type": "array", "items": {"type": "string"}, "title": "Reviewers"},
            "test_command": {
                "type": "string",
                "title": "Test command",
                "description": "Shown in the PR's local verification steps",
            },
            "sections": {
                "type": "array",
                "title": "Body sections",
                "description": "Upstream artifacts to include: {title, from: '<node>/<file>', collapsed}",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "from": {"type": "string"},
                        "collapsed": {"type": "boolean"},
                    },
                    "required": ["title", "from"],
                },
            },
            "review_from": {
                "type": "string",
                "title": "Review report",
                "description": "'<node>/<file>' holding a 'VERDICT:' line for the summary table",
            },
            "github_token_env": {
                "type": "string",
                "default": "GITHUB_TOKEN",
                "title": "Token env var",
            },
        },
        "required": ["source_node"],
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
        try:
            pr = await asyncio.to_thread(self._open_pr, params, state, context.thread_id)
        except Exception as e:
            await self._emit(context, f"[{agent_name}] {e}")
            return {**state, "status": "failed", "error": str(e), "current_step": node_id}

        await self._emit(context, f"[{agent_name}] {pr['action']} pull request: {pr['url']}")
        artifacts = dict(state.get("artifacts", {}))
        artifacts[f"{node_id}_pr_url"] = pr["url"]
        artifacts[f"{node_id}_pr_number"] = pr["number"]
        artifacts[f"{node_id}_commit_url"] = pr["url"]
        artifacts[f"{node_id}_commit"] = f"{pr['action']} PR #{pr['number']} from {pr['branch']}"
        artifacts[f"{node_id}_outputs"] = pr
        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": f"{agent_name}: {pr['url']}"})
        return {
            **state,
            "messages": messages,
            "artifacts": artifacts,
            "current_step": node_id,
            "status": "completed",
            "error": None,
        }

    # ── the work (runs in a worker thread) ───────────────────

    def _open_pr(self, params: Dict[str, Any], state: Dict[str, Any], thread_id: str) -> Dict[str, Any]:
        source = params.get("source_node")
        if not source:
            raise ValueError("'source_node' is required")
        artifacts = state.get("artifacts") or {}
        repo = self._pick_repo(artifacts.get(f"{source}_repos") or [], params.get("repo"), source)
        owner, name = parse_github_repo(repo.get("repo") or "")
        incident = (state.get("inputs") or {}).get("incident") or {}

        token_env = params.get("github_token_env") or "GITHUB_TOKEN"
        token = os.environ.get(token_env, "")
        if not token:
            raise RuntimeError(f"Opening a PR requires a GitHub token in env '{token_env}'.")

        repo_dir = repo["dir"]
        base_sha = repo.get("base_sha") or self._git(repo_dir, "rev-list", "--max-parents=0", "HEAD").split()[0]
        base = params.get("base") or repo.get("ref") or "main"
        title = (params.get("title") or f"fix: {incident.get('title') or 'automated fix'}").strip()[:250]
        branch = params.get("branch") or f"agent-orch/fix-{incident.get('slug') or thread_id[:8]}"
        branch = re.sub(r"[^\w./-]+", "-", branch).strip("-/.")

        commits = self._commit_changes(repo_dir, base_sha, params.get("commit_message") or title)
        if not commits:
            raise RuntimeError("The source node made no changes, so there is nothing to open a PR for.")
        files = self._changed_files(repo_dir, base_sha)

        remote = f"https://x-access-token:{token}@github.com/{owner}/{name}.git"
        self._git(repo_dir, "push", "--force", remote, f"HEAD:refs/heads/{branch}")

        outputs = node_outputs(artifacts)
        sections = [
            {**s, "content": self._artifact_text(artifacts, s.get("from", ""))}
            for s in params.get("sections") or []
        ]
        closes = ""
        reply = incident.get("reply") or {}
        if incident.get("source") == "github" and reply.get("repo") == f"{owner}/{name}":
            closes = f"#{reply.get('issue_number')}"
        body = build_pr_body(
            incident=incident,
            sections=sections,
            commits=commits,
            files=files,
            branch=branch,
            test_command=params.get("test_command") or "",
            outputs=outputs,
            review_text=self._artifact_text(artifacts, params.get("review_from") or ""),
            run_ref=self._run_ref(thread_id),
            closes=closes,
        )

        api = GitHubApi(token)
        pr, action = api.open_or_update_pr(
            owner, name, title=title, head=branch, base=base, body=body,
            draft=params.get("draft", True),
        )
        number = pr["number"]
        if params.get("labels"):
            api.try_request("POST", f"/repos/{owner}/{name}/issues/{number}/labels",
                            {"labels": list(params["labels"])})
        if params.get("reviewers"):
            api.try_request("POST", f"/repos/{owner}/{name}/pulls/{number}/requested_reviewers",
                            {"reviewers": list(params["reviewers"])})
        return {
            "url": pr["html_url"],
            "number": number,
            "branch": branch,
            "base": base,
            "repo": f"{owner}/{name}",
            "action": action,
            "draft": bool(pr.get("draft")),
            "commits": [c["sha"] for c in commits],
        }

    def _pick_repo(self, repos: List[dict], name: Optional[str], source: str) -> dict:
        if name:
            for repo in repos:
                if repo.get("name") == name:
                    return repo
            raise ValueError(f"Node '{source}' has no repo named '{name}'")
        if len(repos) != 1:
            raise ValueError(
                f"Node '{source}' has {len(repos)} repos; set 'repo' to choose one"
                if repos else f"Node '{source}' did not check out a repo"
            )
        return repos[0]

    def _commit_changes(self, repo_dir: str, base_sha: str, message: str) -> List[Dict[str, str]]:
        """Commit anything left uncommitted; return the branch's commits, oldest first."""
        exclude = os.path.join(repo_dir, ".git", "info", "exclude")
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        with open(exclude, "a", encoding="utf-8") as f:
            f.write("\n# agent-orch\n" + "\n".join(JUNK_PATTERNS) + "\n")
        self._git(repo_dir, "add", "-A")
        if self._git(repo_dir, "status", "--porcelain").strip():
            self._git(repo_dir, "-c", "user.name=agent-orch", "-c", "user.email=agent@agent-orch",
                      "commit", "-q", "-m", message)
        log = self._git(repo_dir, "log", "--reverse", "--format=%H%x1f%s", f"{base_sha}..HEAD")
        return [
            {"sha": sha, "subject": subject}
            for sha, _, subject in (line.partition("\x1f") for line in log.splitlines() if line)
        ]

    def _changed_files(self, repo_dir: str, base_sha: str) -> List[Dict[str, Any]]:
        files = []
        for line in self._git(repo_dir, "diff", "--numstat", f"{base_sha}..HEAD").splitlines():
            added, deleted, path = (line.split("\t", 2) + ["", "", ""])[:3]
            files.append({"path": path, "added": added, "deleted": deleted})
        return files

    def _artifact_text(self, artifacts: Dict[str, Any], ref: str) -> str:
        """Read '<node>/<file>' from that node's collected artifacts."""
        node, _, rel = (ref or "").partition("/")
        if not node or not rel:
            return ""
        workspace = artifacts.get(f"{node}_workspace") or ""
        for path in artifacts.get(f"{node}_artifacts") or []:
            relpath = os.path.relpath(path, workspace) if workspace else os.path.basename(path)
            if relpath == rel or os.path.basename(path) == rel:
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        return f.read()
                except OSError:
                    return ""
        return ""

    def _run_ref(self, thread_id: str) -> str:
        public = os.getenv("AGENT_ORCH_PUBLIC_URL", "").rstrip("/")
        if public:
            return f"[{thread_id[:8]}]({public}/workflows/{thread_id}/state)"
        return f"`{thread_id}`"

    def _git(self, cwd: str, *args: str) -> str:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        if proc.returncode != 0:
            # Never echo a token-bearing remote URL back into logs.
            detail = re.sub(r"x-access-token:[^@]+@", "x-access-token:***@", proc.stderr.strip())
            raise RuntimeError(f"git {args[0]} failed: {detail}")
        return proc.stdout

    async def _emit(self, context: ExecutionContext, data: str) -> None:
        if context.event_bus:
            await context.event_bus.publish(context.thread_id, {"event": "agent", "data": data})


class GitHubApi:
    """The handful of GitHub REST calls the PR flow needs (stdlib only)."""

    def __init__(self, token: str, base: str = "https://api.github.com"):
        self.token = token
        self.base = base

    def request(self, method: str, path: str, payload: Optional[dict] = None) -> Any:
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "User-Agent": "agent-orch",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise GitHubError(e.code, e.read().decode(errors="replace")) from None

    def try_request(self, method: str, path: str, payload: Optional[dict] = None) -> Any:
        """Best effort, for extras (labels, reviewers) that must not sink the PR."""
        try:
            return self.request(method, path, payload)
        except GitHubError as e:
            print(f"GitHub {method} {path} skipped ({e.status}): {e.body[:200]}")
            return None

    def open_or_update_pr(
        self, owner: str, name: str, *, title: str, head: str, base: str, body: str, draft: bool
    ) -> Tuple[dict, str]:
        existing = self.request(
            "GET",
            f"/repos/{owner}/{name}/pulls?state=open&head={urllib.parse.quote(f'{owner}:{head}')}",
        ) or []
        if existing:
            number = existing[0]["number"]
            pr = self.request("PATCH", f"/repos/{owner}/{name}/pulls/{number}",
                              {"title": title, "body": body})
            return pr, "Updated"
        payload = {"title": title, "head": head, "base": base, "body": body, "draft": bool(draft)}
        try:
            return self.request("POST", f"/repos/{owner}/{name}/pulls", payload), "Opened"
        except GitHubError as e:
            # Draft PRs are unavailable on some plans for private repos.
            if draft and e.status == 422 and "draft" in e.body.lower():
                payload["draft"] = False
                return self.request("POST", f"/repos/{owner}/{name}/pulls", payload), "Opened"
            raise


class GitHubError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"GitHub API error {status}: {body[:300]}")
        self.status = status
        self.body = body
