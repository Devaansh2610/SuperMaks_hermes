"""
Subagent discovery.

Whatever subagents you define inside Hermes should appear in the HUD on their
own — you add one, it shows up, without touching this project.

The awkward part is that there's no single guaranteed command for listing
them, and it differs by Hermes version. So rather than hardcode one guess that
silently returns nothing forever, this probes a list of plausible commands,
keeps the first that actually answers, and remembers it. If none answer, the
panel says so plainly instead of pretending you have no subagents.

Override the whole thing with HERMES_AGENTS_CMD if your build differs:
    HERMES_AGENTS_CMD="hermes agents list --json"
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")

# Tried in order, first one that answers wins. Cheap: this runs once, then the
# working form is cached for the life of the process.
_CANDIDATES = [
    ["agents", "list", "--json"],
    ["agents", "list"],
    ["agents"],
    ["subagents", "list"],
    ["subagents"],
    ["agent", "list"],
]

TTL = float(os.environ.get("SUPERMAKS_AGENTS_TTL", "10"))
_cache = {"at": 0.0, "data": None}
_lock = threading.Lock()
_resolved = {"argv": None, "tried": False}

# A line that's really a header, a usage banner, or a rule — not an agent.
_NOISE = re.compile(
    r"^(?:usage|available|commands?|options?|flags?|name\s+|-+$|=+$|─+$|"
    r"no\s+(?:agents|subagents)|nothing|error:|warning:)", re.I)

_BAD = ("error", "failed", "missing", "unavailable", "not found", "invalid",
        "disabled", "unreachable", "broken")


def _hermes_base():
    configured = os.environ.get("HERMES_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    exe = shutil.which("hermes")
    return [exe] if exe else ["python3", "-m", "hermes_cli.main"]


def _run(argv, timeout=12):
    try:
        p = subprocess.run(argv, text=True, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=os.path.expanduser("~"))
        return p.returncode, _ANSI.sub("", p.stdout or ""), _ANSI.sub("", p.stderr or "")
    except Exception:                                     # noqa: BLE001
        return 1, "", ""


def _looks_like_help(text):
    """A CLI that doesn't know the subcommand usually prints its own usage."""
    head = text[:400].lower()
    return "usage:" in head or "unknown command" in head or "no such command" in head


def _parse(text):
    """Pull agent entries out of either JSON or a plain listing."""
    text = (text or "").strip()
    if not text:
        return []

    # JSON first — much more reliable when the CLI offers it
    try:
        doc = json.loads(text)
        items = doc if isinstance(doc, list) else (
            doc.get("agents") or doc.get("subagents") or doc.get("items") or [])
        out = []
        for it in items:
            if isinstance(it, str):
                out.append({"name": it[:48], "status": "ok", "detail": ""})
            elif isinstance(it, dict):
                name = str(it.get("name") or it.get("id") or it.get("agent") or "").strip()
                if not name:
                    continue
                raw = str(it.get("status") or it.get("state") or "").strip()
                enabled = it.get("enabled")
                bad = any(b in raw.lower() for b in _BAD) or enabled is False
                out.append({"name": name[:48],
                            "status": "error" if bad else "ok",
                            "detail": (raw or str(it.get("description") or ""))[:70]})
        if out:
            return out[:40]
    except (ValueError, AttributeError):
        pass

    # plain text listing
    out = []
    for line in text.splitlines():
        clean = line.strip().lstrip("-*•·").strip()
        if not clean or _NOISE.match(clean) or len(clean) > 160:
            continue
        # "name  —  description" / "name: description" / "name   description"
        parts = re.split(r"\s{2,}|\s+[—:|]\s+", clean, maxsplit=1)
        name = parts[0].strip()
        detail = parts[1].strip() if len(parts) > 1 else ""
        if not name or len(name) > 48:
            continue
        low = clean.lower()
        out.append({"name": name, "detail": detail[:70],
                    "status": "error" if any(b in low for b in _BAD) else "ok"})
    return out[:40]


def _resolve():
    """Find a command that lists agents. Runs once; the answer is cached."""
    if _resolved["tried"]:
        return _resolved["argv"]
    _resolved["tried"] = True

    override = os.environ.get("HERMES_AGENTS_CMD", "").strip()
    candidates = [shlex.split(override)] if override else \
                 [_hermes_base() + c for c in _CANDIDATES]

    for argv in candidates:
        rc, out, err = _run(argv)
        body = out or err
        if rc == 0 and body.strip() and not _looks_like_help(body):
            _resolved["argv"] = argv
            return argv
    return None


def snapshot(force=False):
    now = time.time()
    with _lock:
        if not force and _cache["data"] and now - _cache["at"] < TTL:
            return _cache["data"]

    argv = _resolve()
    if not argv:
        data = {"supported": False, "agents": [],
                "detail": "no subagent listing command found — set HERMES_AGENTS_CMD if your Hermes exposes one"}
    else:
        rc, out, err = _run(argv)
        body = out or err
        if rc != 0:
            data = {"supported": True, "agents": [], "detail": (err or "listing failed")[:120]}
        else:
            found = _parse(body)
            data = {"supported": True, "agents": found,
                    "detail": "" if found else "no subagents defined yet"}
    data["command"] = " ".join(argv) if argv else ""

    with _lock:
        _cache.update(at=now, data=data)
    return data
