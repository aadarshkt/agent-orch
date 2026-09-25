"""
Tests for the CLI agent executor (src.executors.cli_agent_executor).

No Docker and no network: exercises Source parsing, env building/interpolation,
required-env checks, docker command construction, workspace/env-file writing and
artifact collection.
"""
import asyncio
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from src.config.schema import Source
from src.executors.cli_agent_executor import CliAgentExecutor, repositories, _as_source


def _runtime(**kw):
    base = dict(
        name="beacon", env={}, required_env=[], resource_limits={},
        timeout=60, command=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestSourceParsing(unittest.TestCase):
    def test_coerces_dicts_to_source(self):
        srcs = repositories([{"repo": "r", "ref": "main", "path": "a"}])
        self.assertIsInstance(srcs[0], Source)
        self.assertEqual(srcs[0].path, "a")

    def test_path_defaults_to_empty(self):
        self.assertEqual(repositories([{"repo": "r", "ref": "main"}])[0].path, "")

    def test_passthrough_source_instance(self):
        s = Source(repo="r", ref="main")
        self.assertIs(repositories([s])[0], s)

    def test_requires_repo_and_ref(self):
        with self.assertRaises(Exception):
            repositories([{"repo": "r"}])
        with self.assertRaises(Exception):
            repositories([{"ref": "main"}])

    def test_empty_list(self):
        self.assertEqual(repositories(None), [])


class TestAsSource(unittest.TestCase):
    def test_none(self):
        self.assertIsNone(_as_source(None))

    def test_coerces_dict(self):
        src = _as_source({"repo": "r", "ref": "main"})
        self.assertIsInstance(src, Source)
        self.assertEqual(src.path, "")

    def test_passthrough(self):
        s = Source(repo="r", ref="main")
        self.assertIs(_as_source(s), s)


class TestEnv(unittest.TestCase):
    def setUp(self):
        self.executor = CliAgentExecutor()

    def test_injects_agent_paths(self):
        env = self.executor._build_env(_runtime(env={"A": "1"}), {"B": "2"})
        self.assertEqual(env["AGENT_WORKSPACE"], "/workspace")
        self.assertEqual(env["AGENT_REPOS_DIR"], "/workspace/repos")
        self.assertEqual(env["AGENT_SKILLS_DIR"], "/workspace/.agent/skills")
        self.assertEqual(env["AGENT_CONTEXT_DIR"], "/workspace/.agent/context")
        self.assertEqual(env["AGENT_PROMPT_FILE"], "/workspace/.agent/prompt.md")
        self.assertEqual(env["A"], "1")
        self.assertEqual(env["B"], "2")

    def test_agent_env_overrides_runtime_env(self):
        env = self.executor._build_env(_runtime(env={"K": "runtime"}), {"K": "agent"})
        self.assertEqual(env["K"], "agent")

    def test_interpolates_from_host_environment(self):
        os.environ["UNIT_TEST_SECRET"] = "s3cr3t"
        try:
            env = self.executor._build_env(_runtime(env={"TOKEN": "${UNIT_TEST_SECRET}"}), {})
            self.assertEqual(env["TOKEN"], "s3cr3t")
        finally:
            del os.environ["UNIT_TEST_SECRET"]

    def test_missing_interpolation_is_empty(self):
        env = self.executor._build_env(_runtime(env={"TOKEN": "${NOT_SET_ANYWHERE}"}), {})
        self.assertEqual(env["TOKEN"], "")

    def test_required_env_fails_fast(self):
        with self.assertRaises(ValueError):
            self.executor._check_required_env(
                _runtime(required_env=["GITLAB_TOKEN"]), {"GITLAB_TOKEN": ""}
            )

    def test_required_env_present(self):
        self.executor._check_required_env(
            _runtime(required_env=["GITLAB_TOKEN"]), {"GITLAB_TOKEN": "x"}
        )

    def test_forwards_required_env_from_host(self):
        os.environ["UNIT_TEST_FORWARD"] = "fwd"
        try:
            env = self.executor._build_env(_runtime(required_env=["UNIT_TEST_FORWARD"]), {})
            self.assertEqual(env["UNIT_TEST_FORWARD"], "fwd")
        finally:
            del os.environ["UNIT_TEST_FORWARD"]


class TestUpstreamHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-orch-test-")
        self.executor = CliAgentExecutor()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_mounts_upstream_artifacts(self):
        up = os.path.join(self.tmp, "up")
        os.makedirs(up)
        report = os.path.join(up, "report.md")
        with open(report, "w") as f:
            f.write("upstream")

        node_dir = os.path.join(self.tmp, "node")
        os.makedirs(node_dir)
        state = {"artifacts": {"coder_artifacts": [report]}}

        asyncio.run(self.executor._mount_upstream_artifacts(node_dir, state))

        dest = os.path.join(node_dir, ".agent", "context", "upstream", "coder", "report.md")
        self.assertTrue(os.path.isfile(dest))

    def test_ignores_non_artifact_keys(self):
        node_dir = os.path.join(self.tmp, "node")
        os.makedirs(node_dir)
        state = {"artifacts": {"coder_workspace": "/nowhere", "coder_artifacts": ["/missing.md"]}}

        asyncio.run(self.executor._mount_upstream_artifacts(node_dir, state))

        upstream = os.path.join(node_dir, ".agent", "context", "upstream")
        self.assertFalse(os.path.exists(upstream))


class TestDockerCmd(unittest.TestCase):
    def test_uses_env_file_not_dash_e(self):
        cmd = CliAgentExecutor()._build_docker_cmd(
            image="img:1", command=["run"],
            node_dir="/tmp/node",
            resource_limits={"cpus": "2", "memory": "4g"},
        )
        self.assertIn("--env-file", cmd)
        # --env-file is read by the docker client on the host, so it must be
        # the host path inside the mounted node dir (not /workspace/...).
        self.assertIn(os.path.join("/tmp/node", ".agent", "env"), cmd)
        self.assertNotIn("-e", cmd)
        self.assertIn("--cpus", cmd)
        self.assertIn("--memory", cmd)
        self.assertIn("/tmp/node:/workspace", cmd)
        self.assertEqual(cmd[-2:], ["img:1", "run"])

    def test_no_resource_limits(self):
        cmd = CliAgentExecutor()._build_docker_cmd(
            image="img:1", command=[], node_dir="/tmp/n", resource_limits={}
        )
        self.assertNotIn("--cpus", cmd)
        self.assertNotIn("--memory", cmd)


class TestWorkspaceAndArtifacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-orch-test-")
        self._prev_root = os.environ.get("WORKSPACE_ROOT")
        os.environ["WORKSPACE_ROOT"] = self.tmp
        self.executor = CliAgentExecutor()

    def tearDown(self):
        if self._prev_root is None:
            os.environ.pop("WORKSPACE_ROOT", None)
        else:
            os.environ["WORKSPACE_ROOT"] = self._prev_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_make_workspace(self):
        ws = self.executor._make_workspace("thread-1", "node-1")
        self.assertTrue(os.path.isdir(ws))
        self.assertTrue(ws.endswith(os.path.join("thread-1", "node-1")))

    def test_write_env_file(self):
        ws = self.executor._make_workspace("t", "n")
        self.executor._write_env_file(ws, {"A": "1", "B": "two words"})
        with open(os.path.join(ws, ".agent", "env")) as f:
            content = f.read()
        self.assertIn("A=1\n", content)
        self.assertIn("B=two words\n", content)

    def test_collect_artifacts_by_glob(self):
        ws = self.executor._make_workspace("t", "n")
        os.makedirs(os.path.join(ws, "ai_review", "sub"))
        for rel in ("ai_review/a.md", "ai_review/sub/b.md", "ai_review/c.json", "skip.txt"):
            with open(os.path.join(ws, rel), "w") as f:
                f.write("x")
        found = self.executor._collect_artifacts(ws, ["ai_review/**/*.md", "ai_review/**/*.json"])
        names = {os.path.basename(p) for p in found}
        self.assertEqual(names, {"a.md", "b.md", "c.json"})

    def test_collect_artifacts_empty(self):
        ws = self.executor._make_workspace("t", "n")
        self.assertEqual(self.executor._collect_artifacts(ws, ["nope/**/*.md"]), [])


if __name__ == "__main__":
    unittest.main()
