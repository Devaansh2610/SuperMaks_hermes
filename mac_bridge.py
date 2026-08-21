"""
mac_bridge.py — reach the Mac's AppleScript/GUI layer, whether SuperMaks is
running natively ON that Mac (the single-machine setup on `main`) or on a
separate "Controller" box driving the Mac over SSH (the dual-Hermes setup —
see dual-setup/).

Selected automatically, nothing else needs to know which:
  - no Mac SSH target configured, and this process is already on macOS
    -> run locally, exactly like before.
  - a Mac SSH target IS configured
    -> SSH there instead, even if this process is on Linux.

The SSH target is read from SuperMaks' own SUPERMAKS_MAC_SSH_HOST / _USER /
_KEY (in .env), falling back to the TERMINAL_SSH_HOST / _USER / _KEY that
dual-setup/setup-ubuntu.sh already writes to ~/.hermes/.env for Hermes' own
SSH backend — so the same connection doesn't have to be configured twice.
"""
import os
import pathlib
import shlex
import shutil
import subprocess
import sys


def _hermes_env(name):
    path = pathlib.Path.home() / ".hermes" / ".env"
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _setting(local_name, hermes_name):
    v = os.environ.get(f"SUPERMAKS_MAC_{local_name}", "").strip()
    return v or _hermes_env(hermes_name)


HOST = _setting("SSH_HOST", "TERMINAL_SSH_HOST")
USER = _setting("SSH_USER", "TERMINAL_SSH_USER")
_KEY_RAW = _setting("SSH_KEY", "TERMINAL_SSH_KEY")
KEY = os.path.expanduser(_KEY_RAW) if _KEY_RAW else ""
TIMEOUT = int(os.environ.get("SUPERMAKS_MAC_SSH_TIMEOUT", "20"))

REMOTE = bool(HOST)

# One multiplexed connection reused across calls — without this, opening the
# song plus every wake tab would each pay a full SSH handshake instead of
# riding one already-open socket.
_MUX = os.path.expanduser("~/.ssh/supermaks-mac-%r@%h:%p")
_SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=6",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ControlMaster=auto",
    "-o", f"ControlPath={_MUX}",
    "-o", "ControlPersist=120",
]


def available():
    """True if there's any Mac we can plausibly reach — either we ARE one,
    or a remote one is configured."""
    return REMOTE or sys.platform == "darwin"


def run(argv, timeout=None):
    """Run argv on the Mac: locally if this process already IS the Mac, over
    SSH if a remote one is configured (even from a non-Mac host). Returns
    (True, stdout) on success or (False, error message)."""
    if not available():
        return False, "no Mac reachable (set SUPERMAKS_MAC_SSH_HOST to drive one remotely)"
    if REMOTE:
        target = f"{USER}@{HOST}" if USER else HOST
        remote_cmd = " ".join(shlex.quote(a) for a in argv)
        full = ["ssh", *_SSH_OPTS]
        if KEY:
            full += ["-i", KEY]
        full += [target, "--", remote_cmd]
    else:
        full = argv
    try:
        p = subprocess.run(full, check=True, text=True, timeout=timeout or TIMEOUT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True, p.stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        detail = getattr(e, "stderr", None)
        detail = detail.strip()[:200] if detail else str(e)[:200]
        return False, detail


def _hermes_argv_prefix():
    """Only matters for the LOCAL case (this process already IS the Mac) —
    over SSH, the remote shell's own PATH resolves plain "hermes" already,
    same as osascript/open above rely on."""
    if REMOTE:
        return ["hermes"]
    configured = os.environ.get("HERMES_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    exe = shutil.which("hermes")
    return [exe] if exe else ["hermes"]


def run_hermes_on_mac(request, timeout=180):
    """Delegate a request that genuinely needs real screen/GUI interaction to
    the Mac's OWN Hermes session — the one with a real display and
    computer_use actually usable — instead of a Controller-side Hermes
    profile trying (and failing) to automate whatever machine IT happens to
    be running on. Runs locally if this process already IS the Mac; over SSH
    via run() above otherwise. A generator yielding delta/error events, so
    it plugs directly into commands.start_background() as a runner.
    """
    prompt = ("You have a real screen here and computer_use is enabled — use "
              "it freely for this request. Keep the reply to one or two "
              f"short spoken sentences, no narration of the clicks.\n\n"
              f"Request: {request}")
    argv = _hermes_argv_prefix() + [
        "chat", "-q", prompt, "--no-restore-cwd",
        "--source", "supermaks-mac-gui", "-t", "computer_use",
    ]
    ok, out = run(argv, timeout=timeout)
    if ok:
        yield dict(t="delta", text=out)
    else:
        yield dict(t="error", message=out)
