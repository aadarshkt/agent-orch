"""
Git fetcher — raw single-file fetch and full-repo clone.

CLI agents receive prompt/skills as individual GitLab *file* URLs and their
working repo as *repository* URLs. This module resolves both:

- A URL containing ``/-/raw/`` is treated as a raw file and fetched to a path.
- Any other URL is treated as a repository and cloned.

Git credentials are expected to already be configured on the host (or passed
via env); no credential handling lives here.
"""
import os
import subprocess
import urllib.request
from urllib.parse import urlparse

RAW_MARKER = "/-/raw/"


def is_file_url(url: str) -> bool:
    return RAW_MARKER in url


def fetch_file(url: str, dest_path: str) -> str:
    """Fetch a raw file URL and write it to dest_path. Returns dest_path."""
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "agent-orch"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        content = resp.read()
    with open(dest_path, "wb") as f:
        f.write(content)
    return dest_path


def clone_repo(url: str, dest_path: str, branch: str = "main") -> str:
    """Clone a repository (shallow, single branch) into dest_path."""
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    subprocess.run(
        ["git", "clone", "--branch", branch, "--depth", "1", url, dest_path],
        check=True,
        capture_output=True,
    )
    return dest_path


def fetch_source(url: str, dest_path: str, branch: str = "main") -> str:
    """
    Dispatch a URL to either a raw-file fetch or a repo clone.

    Returns "file" or "repo" depending on what was fetched.
    """
    if is_file_url(url):
        fetch_file(url, dest_path)
        return "file"
    clone_repo(url, dest_path, branch)
    return "repo"
