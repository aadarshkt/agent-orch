"""
Tests for the Source resolver (src.tools.git_fetcher.resolve_source).

Uses a local temp git repo as the origin — no network required.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

from src.config.schema import Source
from src.tools.git_fetcher import resolve_source, repo_name


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _has_git():
    return shutil.which("git") is not None


@unittest.skipUnless(_has_git(), "git is required")
class TestResolveSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-orch-test-")
        self.origin = os.path.join(self.tmp, "origin")
        self.cache = os.path.join(self.tmp, "cache")
        os.makedirs(self.origin)
        os.makedirs(self.cache)

        _git(["init", "-q"], self.origin)
        _git(["config", "user.email", "a@b.c"], self.origin)
        _git(["config", "user.name", "test"], self.origin)

        with open(os.path.join(self.origin, "review.md"), "w") as f:
            f.write("prompt body\n")
        skill_dir = os.path.join(self.origin, "Skills", "CodeReviewAgent")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("skill body\n")

        _git(["add", "-A"], self.origin)
        _git(["commit", "-q", "-m", "init"], self.origin)
        _git(["branch", "-M", "main"], self.origin)
        _git(["tag", "v1"], self.origin)
        self.sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.origin,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _resolve(self, path, dest_name, ref="main"):
        dest = os.path.join(self.tmp, dest_name)
        return resolve_source(Source(repo=self.origin, ref=ref, path=path), dest, self.cache)

    def test_whole_repo_by_branch(self):
        dest = self._resolve("", "out-branch")
        self.assertTrue(os.path.isfile(os.path.join(dest, "review.md")))
        self.assertTrue(os.path.isdir(os.path.join(dest, ".git")))

    def test_whole_repo_by_tag(self):
        dest = self._resolve("", "out-tag", ref="v1")
        self.assertTrue(os.path.isfile(os.path.join(dest, "review.md")))

    def test_whole_repo_by_sha(self):
        dest = self._resolve("", "out-sha", ref=self.sha)
        self.assertTrue(os.path.isfile(os.path.join(dest, "review.md")))

    def test_single_file_path(self):
        dest = self._resolve("review.md", "out-file")
        self.assertTrue(os.path.isfile(dest))
        with open(dest) as f:
            self.assertIn("prompt body", f.read())

    def test_directory_path(self):
        dest = self._resolve("Skills/CodeReviewAgent", "out-dir")
        self.assertTrue(os.path.isfile(os.path.join(dest, "SKILL.md")))

    def test_missing_path_raises(self):
        with self.assertRaises(FileNotFoundError):
            self._resolve("does/not/exist.md", "out-missing")

    def test_cache_is_reused(self):
        self._resolve("", "out-cache-1")
        entries = os.listdir(self.cache)
        self.assertEqual(len(entries), 1, entries)
        self._resolve("", "out-cache-2")
        self.assertEqual(os.listdir(self.cache), entries)


class TestRepoName(unittest.TestCase):
    def test_strips_git_suffix(self):
        self.assertEqual(repo_name("https://gitlab.kpit.com/eco/app.git"), "app")

    def test_handles_trailing_slash(self):
        self.assertEqual(repo_name("https://gitlab.kpit.com/eco/app/"), "app")


if __name__ == "__main__":
    unittest.main()
