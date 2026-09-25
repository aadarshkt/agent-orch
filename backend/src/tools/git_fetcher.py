"""
Source resolver — the single way to materialize an asset.

Everything a CLI agent needs (the working repo, its skills, extra context, its
prompt) is described by one type: ``Source { repo, ref, path }``.  This module
turns a Source into a local path by checking the repo out at ``ref`` (branch,
tag or sha) into a per-run cache and then copying ``path`` out of it.

Credentials are expected to be configured for the host git client (the backend
runs the git commands); no credential handling lives here.
"""
import hashlib
import os
import shutil
import subprocess
import threading
from typing import Dict

from src.config.schema import Source


_CHECKOUT_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def repo_name(repo: str) -> str:
    """A short, filesystem-safe name for a repo URL (used for mount paths)."""
    name = repo.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name or "repo"


def resolve_source(source: Source, dest: str, cache_root: str) -> str:
    """
    Materialize a Source at ``dest`` and return ``dest``.

    The repo is checked out at ``source.ref`` (cached under ``cache_root`` keyed
    by repo@ref, so repeated resolves in one run are cheap).  Then:

    - ``path == ""``     -> the whole checkout is copied to ``dest``
    - ``path`` is a dir  -> its contents are copied to ``dest``
    - ``path`` is a file -> copied to ``dest`` (``dest`` is the file path)

    Raises FileNotFoundError if ``path`` does not exist at ``ref``.
    """
    checkout = _checkout_cached(source.repo, source.ref, cache_root)

    if not source.path:
        _copy_contents(checkout, dest)
        return dest

    src = os.path.join(checkout, source.path)
    if not os.path.exists(src):
        raise FileNotFoundError(
            f"'{source.path}' not found in {source.repo} at ref '{source.ref}'"
        )

    if os.path.isdir(src):
        _copy_contents(src, dest)
    else:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        shutil.copy2(src, dest)
    return dest


# ── internals ────────────────────────────────────────────────

def _checkout_cached(repo: str, ref: str, cache_root: str) -> str:
    key = hashlib.sha1(f"{repo}@{ref}".encode("utf-8")).hexdigest()[:16]
    path = os.path.join(cache_root, key)
    marker = os.path.join(path, ".agent-orch-ready")

    if os.path.isfile(marker):
        return path

    with _lock_for(key):
        # Re-check inside the lock — another thread may have finished it.
        if os.path.isfile(marker):
            return path
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, exist_ok=True)
        try:
            _clone_at_ref(repo, ref, path)
            with open(marker, "w", encoding="utf-8"):
                pass
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise
    return path


def _clone_at_ref(repo: str, ref: str, dest: str) -> None:
    """Init an empty repo and fetch+checkout ``ref`` (branch/tag/sha)."""
    _git(["init", "-q"], dest)
    _git(["remote", "add", "origin", repo], dest)
    try:
        # Works for branches and tags; some servers also allow sha.
        _git(["fetch", "--depth", "1", "origin", ref], dest)
        _git(["checkout", "-q", "FETCH_HEAD"], dest)
    except subprocess.CalledProcessError:
        # Fallback: full fetch, then checkout the ref directly (handles shas).
        _git(["fetch", "-q", "origin"], dest)
        _git(["checkout", "-q", ref], dest)


def _git(args, cwd: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _copy_contents(src: str, dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    for entry in os.listdir(src):
        s = os.path.join(src, entry)
        d = os.path.join(dest, entry)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True, symlinks=True)
        else:
            shutil.copy2(s, d)


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _CHECKOUT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CHECKOUT_LOCKS[key] = lock
        return lock
