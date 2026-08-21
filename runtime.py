"""
Hermes runtime for the SuperMaks HUD.

This dashboard is a face on Hermes Agent, not Claude Code. It launches the
Hermes CLI with the selected profile/source so Hermes keeps access to the same
configured toolsets, MCP servers, browser automation, Google integrations,
third-party tools, skills, memory, and gateway features that are available to the
normal Hermes agent.

Set HERMES_CMD when your shell exposes Hermes under a custom command/path, e.g.
  HERMES_CMD="/path/to/hermes"
or
  HERMES_CMD="python3 -m hermes_cli.main"
"""
import collections
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time

_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
# Without -Q, Hermes wraps the answer in a bordered box and adds a footer —
# none of it is the answer itself, so all of it gets stripped here.
_CLI_NOISE = re.compile(
    r"^(?:Query:|Initializing agent|Resume this session with:|hermes --resume\b|"
    r"hermes -c\b|Session:|Duration:|Messages:|Title:|─+|=+|╭|╰|⚠|"
    r"[-─ ]*⚕\s*Hermes\b)", re.I)
# The "┊ 💻 preparing terminal…" / "┊ 💻 $  ls  1.9s" tool-preview lines that
# -Q would otherwise have hidden. Captured as their own event instead of
# either leaking into the spoken answer or being silently discarded.
_TOOL_LINE = re.compile(r"^┊\s*(.*)$")

def env(name, default=""):
    """SUPERMAKS_X, falling back to the old JARVIS_X so an inherited .env still works."""
    return os.environ.get(f"SUPERMAKS_{name}", os.environ.get(f"JARVIS_{name}", default))


MODEL = env("MODEL", "").strip()
PROFILE = os.environ.get("HERMES_PROFILE", env("PROFILE", "default")).strip() or "default"
SOURCE = os.environ.get("HERMES_SOURCE", "supermaks-dashboard").strip() or "supermaks-dashboard"
WORKDIR = os.path.expanduser(env("WORKDIR", os.getcwd()))
PERMISSION = env("PERMISSION", os.environ.get("HERMES_PERMISSION", "normal")).strip().lower()
RUNTIME = env("RUNTIME", "auto").strip().lower()  # auto|hermes|mock
IDLE_TIMEOUT = int(env("TIMEOUT", "120"))
# IDLE_TIMEOUT only fires on a GAP with zero output — a run that keeps
# trickling the occasional line (a slow tool call over the SSH-to-Mac
# backend, a retry loop) never goes idle long enough to trip it and can run
# forever, holding RUN_LOCK the whole time and 409-blocking every wake behind
# it. This is the independent wall-clock cap: total time, no matter how much
# output showed up along the way.
MAX_RUN_SECONDS = int(env("MAX_RUN_SECONDS", "300"))
# Raw transcripts can contain private prompts. Logging is off by default.
RAW_LOG = env("RAW_LOG", "").strip()
TOOLSETS = os.environ.get("HERMES_TOOLSETS", "").strip()

# One Hermes subprocess at a time. Hermes session state and profile resources are
# shared, so foreground runs and /background missions must not overlap. The active
# process handle lets the browser Escape key stop the backend work, not just the
# frontend fetch.
EXECUTION_LOCK = threading.RLock()
_ACTIVE_PROC = None
_ACTIVE_LOCK = threading.Lock()


def cancel_active():
    """Terminate the currently running Hermes child process, if any."""
    with _ACTIVE_LOCK:
        proc = _ACTIVE_PROC
    if proc is None or proc.poll() is not None:
        return False
    try:
        proc.terminate()
        return True
    except Exception:
        return False


def valid_session(sid):
    # Hermes emits timestamp-based IDs such as 20260808_031038_0ecb14.
    return bool(sid) and bool(_SESSION_ID.match(str(sid)))


def _mono_ms():
    return int(time.monotonic() * 1000)


def _hermes_base():
    configured = os.environ.get("HERMES_CMD", "").strip()
    if configured:
        return shlex.split(configured)
    exe = shutil.which("hermes")
    if exe:
        return [exe]
    # Last-resort module invocation; start.sh prepends common Python bin dirs.
    return ["python3", "-m", "hermes_cli.main"]


def _can_launch_hermes():
    try:
        base = _hermes_base()
        proc = subprocess.run(base + ["--version"], cwd=WORKDIR, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=12)
        return proc.returncode == 0, (proc.stdout or proc.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def runtime_kind():
    if RUNTIME == "mock":
        return "mock"
    if RUNTIME == "hermes":
        return "hermes"
    ok, _ = _can_launch_hermes()
    return "hermes" if ok else "mock"


def build_command(message, session_id=None, system=None):
    # Hermes chat has no separate --system flag. Put the voice persona and the
    # current request into one explicit turn so the model answers as SuperMaks
    # instead of narrating its CLI/runtime.
    prompt = message
    if system:
        prompt = (f"{system}\n\nRequest: {message}\n\n"
                  "Reply with only what SuperMaks should say aloud — no session IDs, "
                  "metadata, headings, or narration.")
    # Deliberately NOT -Q (quiet): quiet mode's own --help says it suppresses
    # "tool previews" — exactly the "$ ls  1.9s" / "reading file…" lines the
    # dashboard's Activity panel is built to show. Non-quiet, non-TTY output
    # is still clean plain text (verified against a real Hermes install) —
    # just a banner/footer wrapped around the answer, which _CLI_NOISE below
    # strips, plus the tool-preview lines _TOOL_LINE below captures instead
    # of letting them leak into the spoken answer.
    cmd = _hermes_base() + ["chat", "-q", prompt, "--source", SOURCE]
    if valid_session(session_id):
        cmd += ["--resume", str(session_id)]
    if PROFILE and PROFILE != "default":
        cmd += ["--profile", PROFILE]
    if MODEL:
        cmd += ["--model", MODEL]
    if TOOLSETS:
        cmd += ["--toolsets", TOOLSETS]
    if PERMISSION == "bypass":
        cmd += ["--yolo"]
    # Hermes one-shot output is currently plain text; slash commands that need a
    # live TUI are handled in commands.py before we get here. Prompts go to the
    # real agent with normal tool access.
    return cmd


def hermes_tools_snapshot(limit=36):
    """Return a compact list of enabled/visible Hermes toolsets/tools for UI status."""
    try:
        base = _hermes_base()
        proc = subprocess.run(base + ["tools", "list"], cwd=WORKDIR, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=20)
        out = (proc.stdout or proc.stderr or "").strip()
        tools = []
        for line in out.splitlines():
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if not clean or clean.startswith(("Usage", "─", "=")):
                continue
            if any(tok in clean.lower() for tok in ("enabled", "available", "tool", "browser", "terminal", "file", "google", "github", "web", "voice")):
                tools.append(clean[:80])
            if len(tools) >= limit:
                break
        return tools or [out[:120]] if out else []
    except Exception:
        return []


def run_hermes(message, session_id=None, system=None):
    with EXECUTION_LOCK:
        yield from _run_hermes_locked(message, session_id, system)


def _run_hermes_locked(message, session_id=None, system=None):
    global _ACTIVE_PROC
    started = _mono_ms()
    cmd = build_command(message, session_id, system)
    yield dict(t="status", model=MODEL or "Hermes default", tools=len(hermes_tools_snapshot()),
               mcp=[], permission=PERMISSION, profile=PROFILE, runtime="hermes",
               session_id=session_id)

    child_env = dict(os.environ)
    # Ensure GUI-started shells can still find common Homebrew/user installs.
    home = os.path.expanduser("~")
    child_env["PATH"] = ":".join(dict.fromkeys([
        child_env.get("PATH", ""),
        "/opt/homebrew/bin", "/usr/local/bin",
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".npm-global", "bin"),
        "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ]))

    try:
        proc = subprocess.Popen(cmd, cwd=WORKDIR, text=True, bufsize=1,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=child_env)
        with _ACTIVE_LOCK:
            _ACTIVE_PROC = proc
    except FileNotFoundError:
        raise RuntimeError("Hermes CLI not found. Set HERMES_CMD to your Hermes executable.")

    q = collections.deque()
    lock = threading.Lock()
    done = threading.Event()
    errbuf = collections.deque(maxlen=120)
    raw = None
    if RAW_LOG:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(os.path.expanduser(RAW_LOG), flags, 0o600)
        os.fchmod(fd, 0o600)
        raw = os.fdopen(fd, "w", encoding="utf-8", errors="replace")
        raw.write("$ " + " ".join(shlex.quote(x) for x in cmd) + "\n\n")
        raw.flush()

    def pump_stdout():
        try:
            for ln in proc.stdout:
                if raw:
                    raw.write("OUT " + ln); raw.flush()
                with lock:
                    q.append(ln)
        finally:
            done.set()

    def pump_stderr():
        try:
            for ln in proc.stderr:
                if raw:
                    raw.write("ERR " + ln); raw.flush()
                errbuf.append(ln.rstrip())
        except Exception:
            pass

    threading.Thread(target=pump_stdout, daemon=True).start()
    threading.Thread(target=pump_stderr, daemon=True).start()

    first = True
    last = time.monotonic()
    text_seen = False
    emitted_session = None
    # The prompt we send is the full persona + request template, which wraps
    # across many lines in the "Query: …" echo — every one of those extra
    # lines must be swallowed too, not just the first, or persona/system text
    # leaks into the spoken answer. The echo block always ends right where
    # "Initializing agent…" begins.
    in_query_echo = False
    try:
        while not done.is_set() or q:
            # Checked every iteration, not just while idle — a run that keeps
            # trickling output never hits the idle-gap check below at all.
            if (_mono_ms() - started) / 1000 > MAX_RUN_SECONDS:
                proc.kill()
                err = " | ".join(list(errbuf)[-3:])
                yield dict(t="error", message=(
                    f"Hermes ran for over {MAX_RUN_SECONDS}s and was stopped. " + err)[:400])
                return
            line = None
            with lock:
                if q:
                    line = q.popleft()
            if line is None:
                if proc.poll() is not None and done.is_set():
                    break
                if time.monotonic() - last > IDLE_TIMEOUT:
                    proc.kill()
                    err = " | ".join(list(errbuf)[-3:])
                    yield dict(t="error", message=(f"Hermes went quiet for {IDLE_TIMEOUT}s and was stopped. " + err)[:400])
                    return
                time.sleep(0.05)
                continue
            last = time.monotonic()
            clean = _ANSI.sub("", line).strip()
            if not clean:
                continue
            if in_query_echo:
                if clean.lower().startswith("initializing agent"):
                    in_query_echo = False
                continue
            if clean.startswith("Query:"):
                in_query_echo = True
                continue
            session_match = re.match(r"^session_id:\s*(\S+)\s*$", clean, re.I)
            if session_match:
                candidate = session_match.group(1)
                if valid_session(candidate):
                    emitted_session = candidate
                continue
            tool_match = _TOOL_LINE.match(clean)
            if tool_match:
                yield dict(t="tool", message=tool_match.group(1).strip())
                continue
            # Be defensive when an older Hermes build ignores quiet mode.
            if _CLI_NOISE.match(clean):
                continue
            if first:
                first = False
                yield dict(t="latency", ms=_mono_ms() - started)
            text_seen = True
            yield dict(t="delta", text=clean + "\n")

        rc = proc.wait(timeout=5)
        # Hermes writes its programmatic session_id marker to stderr in quiet
        # mode, so capture it there without exposing it as response text.
        for errline in errbuf:
            session_match = re.match(r"^session_id:\s*(\S+)\s*$", errline.strip(), re.I)
            if session_match and valid_session(session_match.group(1)):
                emitted_session = session_match.group(1)
        if rc != 0:
            err = " | ".join(list(errbuf)[-6:])
            yield dict(t="error", message=(err or f"Hermes exited with code {rc}")[:500])
            return
        if not text_seen:
            yield dict(t="delta", text="Hermes completed without textual output.")
        yield dict(t="complete", session_id=emitted_session or session_id,
                   ms=_mono_ms() - started)
    finally:
        try:
            if raw:
                raw.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.terminate()
        with _ACTIVE_LOCK:
            if _ACTIVE_PROC is proc:
                _ACTIVE_PROC = None


def run_mock(message, session_id=None, system=None):
    ok, detail = _can_launch_hermes()
    yield dict(t="error", message=("Hermes is not reachable from this dashboard process. "
                                  "Set HERMES_CMD or start from a shell where `hermes` works. "
                                  f"Diagnostic: {detail[:240]}"))


def run(message, session_id=None, system=None):
    if runtime_kind() != "hermes":
        yield from run_mock(message, session_id, system)
        return
    try:
        yield from run_hermes(message, session_id, system)
    except Exception as e:  # noqa: BLE001
        yield dict(t="error", message=f"could not start Hermes core: {e}")
