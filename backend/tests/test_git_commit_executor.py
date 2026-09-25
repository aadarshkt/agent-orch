"""
Tests for the git_commit executor (src.executors.git_commit_executor).

No network and no real GitHub: exercises repo resolution, artifact copying,
token handling and the full publish flow against a local bare repo standing in
for the remote (the github remote/commit-url builders are stubbed).
"""
import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager

from src.registry.base_executor import ExecutionContext
from src.executors.git_commit_executor import GitCommitExecutor


@contextmanager
def github_token(value):
    """Force GITHUB_TOKEN to `value` (None = unset) and restore it afterwards.

    Keeps the tests hermetic: an ambient/.env token must never turn a unit test
    into a live GitHub call.
    """
    prev = os.environ.get("GITHUB_TOKEN")
    if value is None:
        os.environ.pop("GITHUB_TOKEN", None)
    else:
        os.environ["GITHUB_TOKEN"] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = prev


class _LocalGithubExecutor(GitCommitExecutor):
    """GitCommitExecutor whose 'github' remote is a local bare repo."""

    def __init__(self, remote_path: str):
        self.remote_path = remote_path
        self.created = []

    def _ensure_github_repo(self, owner, name, visibility, token):
        self.created.append((owner, name, visibility, token))

    def _github_remote(self, owner, name, token):
        return self.remote_path

    def _commit_url(self, owner, name, sha):
        return f"https://example.test/{owner}/{name}/commit/{sha}"


class TestGithubPublish(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-orch-git-test-")
        self.remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "--bare", self.remote],
                       check=True, capture_output=True)
        self.workspace = os.path.join(self.tmp, "ws")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _artifact(self, rel: str, content: str = "node output",
                  workspace: str = None) -> str:
        workspace = workspace or self.workspace
        path = os.path.join(workspace, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    def _remote_tree(self) -> str:
        return subprocess.run(
            ["git", f"--git-dir={self.remote}", "ls-tree", "-r", "--name-only", "main"],
            capture_output=True, text=True, check=True,
        ).stdout

    def test_publishes_artifacts_and_returns_commit_url(self):
        artifact = self._artifact("ai_review/report.md")
        with github_token("test-token"):
            executor = _LocalGithubExecutor(self.remote)
            commit_url, result = executor._publish_github(
                None, [(self.workspace, artifact)], "owner/repo",
                {"visibility": "public"}, "main", "test commit",
            )

        self.assertEqual(executor.created, [("owner", "repo", "public", "test-token")])
        self.assertIn("https://example.test/owner/repo/commit/", commit_url)
        self.assertIn("owner/repo@main", result)
        self.assertIn("ai_review/report.md", self._remote_tree())

    def test_aggregates_artifacts_from_multiple_nodes(self):
        code_ws = os.path.join(self.tmp, "ws-code")
        tests_ws = os.path.join(self.tmp, "ws-tests")
        code = self._artifact("todo.py", "x = 1\n", workspace=code_ws)
        tests = self._artifact("test_todo.py", "def test_x(): pass\n", workspace=tests_ws)

        with github_token("test-token"):
            executor = _LocalGithubExecutor(self.remote)
            executor._publish_github(
                None,
                [(code_ws, code), (tests_ws, tests)],
                "owner/repo", {}, "main", "bundle",
            )

        tree = self._remote_tree()
        # Each artifact keeps the path relative to its own node's workspace.
        self.assertIn("todo.py", tree)
        self.assertIn("test_todo.py", tree)

    def test_artifact_outside_workspace_is_namespaced(self):
        publish_dir = os.path.join(self.tmp, "publish")
        os.makedirs(publish_dir)
        outside = os.path.join(self.tmp, "elsewhere", "notes.md")
        os.makedirs(os.path.dirname(outside))
        with open(outside, "w") as f:
            f.write("x")

        GitCommitExecutor()._copy_artifacts(
            publish_dir, [(self.workspace, outside)]
        )

        self.assertTrue(os.path.isfile(os.path.join(publish_dir, "artifacts", "notes.md")))

    def test_copy_handles_artifact_without_workspace(self):
        publish_dir = os.path.join(self.tmp, "publish-no-ws")
        os.makedirs(publish_dir)
        artifact = os.path.join(self.tmp, "loose", "notes.md")
        os.makedirs(os.path.dirname(artifact))
        with open(artifact, "w") as f:
            f.write("x")

        GitCommitExecutor()._copy_artifacts(publish_dir, [(None, artifact)])

        self.assertTrue(os.path.isfile(os.path.join(publish_dir, "notes.md")))

    def test_ignores_missing_artifacts(self):
        publish_dir = os.path.join(self.tmp, "publish")
        os.makedirs(publish_dir)
        GitCommitExecutor()._copy_artifacts(
            publish_dir, [(self.workspace, os.path.join(self.tmp, "nope.md"))]
        )
        self.assertEqual(os.listdir(publish_dir), [])

    def test_malformed_repo_slug(self):
        with github_token("test-token"):
            with self.assertRaises(ValueError):
                _LocalGithubExecutor(self.remote)._publish_github(
                    None, [], "not-a-slug", {}, "main", "msg",
                )

    def test_missing_token_fails(self):
        with github_token(None):
            with self.assertRaises(RuntimeError):
                GitCommitExecutor()._publish_github(
                    None, [], "owner/repo", {}, "main", "msg"
                )

    def test_token_env_name_is_configurable(self):
        os.environ["MY_GH_TOKEN"] = "custom"
        try:
            token = GitCommitExecutor()._github_token({"github_token_env": "MY_GH_TOKEN"})
        finally:
            os.environ.pop("MY_GH_TOKEN", None)
        self.assertEqual(token, "custom")


class TestResolveRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-orch-git-test-")
        self.executor = GitCommitExecutor()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_repo_is_used(self):
        repo = {"name": "app", "dir": self.tmp, "ref": "main"}
        self.assertEqual(self.executor._resolve_repo(self.tmp, [repo], None), repo)

    def test_by_name(self):
        a = {"name": "a", "dir": os.path.join(self.tmp, "a"), "ref": None}
        b = {"name": "b", "dir": os.path.join(self.tmp, "b"), "ref": None}
        self.assertEqual(self.executor._resolve_repo(self.tmp, [a, b], "b"), b)

    def test_falls_back_to_workspace_repos_dir(self):
        repo_dir = os.path.join(self.tmp, "repos", "app")
        os.makedirs(os.path.join(repo_dir, ".git"))
        resolved = self.executor._resolve_repo(self.tmp, [], None)
        self.assertEqual(resolved["name"], "app")
        self.assertEqual(resolved["dir"], repo_dir)

    def test_none_when_nothing_to_commit(self):
        self.assertIsNone(self.executor._resolve_repo(self.tmp, [], None))


class TestExecuteRouting(unittest.TestCase):
    def setUp(self):
        self.executor = GitCommitExecutor()
        self.tmp = tempfile.mkdtemp(prefix="agent-orch-git-test-")
        self.context = ExecutionContext(thread_id="t1")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, params, artifacts):
        node_config = {"id": "publish", "agent_name": "publish", "params": params}
        state = {"artifacts": artifacts, "messages": []}
        return asyncio.run(self.executor.execute(node_config, state, self.context))

    def test_no_source_nodes_fails(self):
        result = self._run({}, {})
        self.assertEqual(result["status"], "failed")
        self.assertIn("No source_nodes", result["error"])

    def test_missing_workspace_fails(self):
        result = self._run({"source_nodes": ["n4"]}, {})
        self.assertEqual(result["status"], "failed")
        self.assertIn("No workspace", result["error"])

    def test_origin_mode_without_repo_fails(self):
        result = self._run(
            {"source_nodes": ["n4"]},
            {"n4_workspace": self.tmp},
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("No repo to commit", result["error"])

    def test_github_mode_without_token_fails_cleanly(self):
        with github_token(None):
            result = self._run(
                {"source_nodes": ["n4"], "github_repo": "owner/repo"},
                {"n4_workspace": self.tmp, "n4_artifacts": []},
            )
        self.assertEqual(result["status"], "failed")
        self.assertIn("GITHUB_TOKEN", result["error"])

    def test_github_mode_records_commit_url(self):
        class _Stub(GitCommitExecutor):
            def _publish_github(self, repo, artifact_pairs, github_repo,
                                params, branch, message):
                return ("https://example.test/o/r/commit/abc", "Pushed to o/r@main")

        node_config = {
            "id": "publish",
            "agent_name": "publish",
            "params": {"source_nodes": ["n4"], "github_repo": "o/r"},
        }
        state = {"artifacts": {"n4_workspace": self.tmp, "n4_artifacts": []}, "messages": []}
        result = asyncio.run(_Stub().execute(node_config, state, self.context))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["artifacts"]["publish_commit_url"],
                         "https://example.test/o/r/commit/abc")
        self.assertIn("publish_commit", result["artifacts"])

    def test_passes_artifacts_from_every_source_node(self):
        calls = {}

        class _Stub(GitCommitExecutor):
            def _publish_github(self, repo, artifact_pairs, github_repo,
                                params, branch, message):
                calls["pairs"] = artifact_pairs
                return ("url", "ok")

        code_ws = os.path.join(self.tmp, "code")
        tests_ws = os.path.join(self.tmp, "tests")
        os.makedirs(code_ws)
        os.makedirs(tests_ws)
        code_file = os.path.join(code_ws, "todo.py")
        tests_file = os.path.join(tests_ws, "test_todo.py")

        node_config = {
            "id": "publish",
            "agent_name": "publish",
            "params": {"source_nodes": ["code", "tests"], "github_repo": "o/r"},
        }
        state = {
            "artifacts": {
                "code_workspace": code_ws,
                "code_artifacts": [code_file],
                "tests_workspace": tests_ws,
                "tests_artifacts": [tests_file],
            },
            "messages": [],
        }
        asyncio.run(_Stub().execute(node_config, state, self.context))

        self.assertIn((code_ws, code_file), calls["pairs"])
        self.assertIn((tests_ws, tests_file), calls["pairs"])


if __name__ == "__main__":
    unittest.main()
