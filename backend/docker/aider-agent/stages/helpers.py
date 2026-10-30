"""Small parsers the stage scripts share (stdlib only).

    helpers.py reply <aider-history.md>            last assistant reply
    helpers.py files <repo> <inputs.json> [triage.json]
                                                   repo files the incident points at
    helpers.py triage <reply.md> <repo>            triage reply -> outputs JSON
"""
import json
import os
import re
import sys


def last_reply(history_path):
    """Aider's chat history prefixes user turns with '#### ' and tool output
    with '> '; the assistant's words are everything else after the last user turn."""
    try:
        with open(history_path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    last_user = max((i for i, l in enumerate(lines) if l.startswith("#### ")), default=-1)
    reply = [l for l in lines[last_user + 1:] if not l.startswith("> ") and l != ">"]
    return "\n".join(reply).strip()


def repo_files(repo):
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv")]
        for name in files:
            out.append(os.path.relpath(os.path.join(root, name), repo))
    return out


def resolve(repo, path, known):
    """Map a frame path (often absolute, from another machine) onto the repo."""
    path = (path or "").strip().strip("`'\"").replace("\\", "/")
    if not path:
        return None
    if path in known:
        return path
    parts = path.lstrip("./").split("/")
    for start in range(len(parts)):  # longest suffix that exists in the repo
        candidate = "/".join(parts[start:])
        if candidate in known:
            return candidate
    return None


def candidate_files(repo, inputs_path, triage_path=None):
    known = set(repo_files(repo))
    found = []

    def add(path):
        rel = resolve(repo, path, known)
        if rel and rel not in found:
            found.append(rel)

    if triage_path and os.path.isfile(triage_path):
        with open(triage_path, encoding="utf-8") as f:
            for path in (json.load(f).get("files") or []):
                add(path)
    if inputs_path and os.path.isfile(inputs_path):
        with open(inputs_path, encoding="utf-8") as f:
            incident = (json.load(f).get("incident")) or {}
        for frame in reversed(incident.get("frames") or []):  # innermost first
            if frame.get("in_app", True):
                add(frame.get("file"))
        text = " ".join(str(incident.get(k) or "") for k in ("stacktrace", "logs", "message", "culprit"))
        for match in re.findall(r"[\w./-]+\.[A-Za-z]{1,5}", text):
            add(match)
    return found[:8]


def _section(reply, *names):
    pattern = r"^#{1,4}\s*(?:%s)\b.*$" % "|".join(re.escape(n) for n in names)
    m = re.search(pattern, reply, re.IGNORECASE | re.MULTILINE)
    if not m:
        return ""
    rest = reply[m.end():]
    nxt = re.search(r"^#{1,4}\s+\S", rest, re.MULTILINE)
    return (rest[: nxt.start()] if nxt else rest).strip()


def _first_paragraph(text, limit=400):
    para = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    para = re.sub(r"\s+", " ", para).strip()
    return para[:limit]


def triage_outputs(reply, repo):
    actionable = True
    m = re.search(r"^\W*ACTIONABLE:\s*\**\s*(yes|no)", reply, re.IGNORECASE | re.MULTILINE)
    if m:
        actionable = m.group(1).lower() == "yes"
    files = []
    m = re.search(r"^\W*FILES:\s*(.+)$", reply, re.IGNORECASE | re.MULTILINE)
    known = set(repo_files(repo))
    if m:
        for raw in re.split(r"[,\s]+", m.group(1)):
            rel = resolve(repo, raw, known)
            if rel and rel not in files:
                files.append(rel)
    return {
        "actionable": actionable,
        "summary": _first_paragraph(_section(reply, "Summary")) or _first_paragraph(reply),
        "root_cause": _first_paragraph(_section(reply, "Root cause"), 300),
        "files": files,
    }


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "reply":
        print(last_reply(argv[2]))
    elif cmd == "files":
        print("\n".join(candidate_files(argv[2], argv[3], argv[4] if len(argv) > 4 else None)))
    elif cmd == "triage":
        with open(argv[2], encoding="utf-8") as f:
            print(json.dumps(triage_outputs(f.read(), argv[3])))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
