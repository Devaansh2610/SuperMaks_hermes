"""
SuperMaks Live — a HUD that drives Hermes Agent.

    python3 server.py

Runs on the same machine as Hermes. The brain is your own Hermes CLI/profile,
so its configured tools, browser automation, MCP servers, skills, memory and
third-party integrations are all live — including anything it uses to drive
this machine directly. Speech out is Fish Audio; speech in is the browser's
own recognizer.
"""
import json
import mimetypes
import os
import pathlib
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent
UI = ROOT / "ui"

# load .env before importing anything that reads os.environ
_env = ROOT / ".env"
if _env.exists():
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def env(name, default=""):
    """SUPERMAKS_X, falling back to the old JARVIS_X so an inherited .env still works."""
    return os.environ.get(f"SUPERMAKS_{name}", os.environ.get(f"JARVIS_{name}", default))


import commands         # noqa: E402
import mac_bridge       # noqa: E402
import runtime          # noqa: E402
import voice            # noqa: E402

PORT = int(env("PORT", "8730"))
API_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Supermaks-Token"
RUN_LOCK = threading.Lock()
MAX_JSON_BODY = 1024 * 1024
MAX_AUDIO_BODY = 12 * 1024 * 1024

# The wake-phrase jingle. "open" hands the track straight to YouTube in its own
# window. "local" streams a file YOU already own from this machine; "off"
# disables it. No audio is ever bundled with this repo: that would mean
# redistributing someone else's copyrighted recording, which this project isn't
# going to do.
WAKE_SONG_SOURCE = env("WAKE_SONG_SOURCE", "open").strip().lower()
WAKE_SONG_LOCAL_PATH = env("WAKE_SONG_LOCAL_PATH", "").strip()
WAKE_SONG_SECONDS = int(env("WAKE_SONG_SECONDS", "105"))
# Plays UNDER the greeting, not before it, so these are background levels:
# VOLUME on its own, DUCK while SuperMaks is actually speaking.
WAKE_SONG_VOLUME = float(env("WAKE_SONG_VOLUME", "0.35"))
WAKE_SONG_DUCK = float(env("WAKE_SONG_DUCK", "0.10"))
# Used when WAKE_SONG_SOURCE=open — the track opens in its own YouTube window.
WAKE_SONG_URL = env("WAKE_SONG_URL", "https://www.youtube.com/watch?v=xMaE6toi4mk&list=RDxMaE6toi4mk&start_radio=1&pp=ygUcc2hvdWxkIEkgc3RheSBvciBzaG91bGQgaSBnb6AHAQ%3D%3D").strip()

# Opened alongside the greeting, all at once. Comma-separated; blank to disable.
# The wake song joins this batch when WAKE_SONG_SOURCE=open, so everything the
# wake opens goes through one code path and one popup-blocker fallback.
WAKE_TABS = [u.strip() for u in env(
    "WAKE_TABS",
    "https://build.nvidia.com/models,https://www.youtube.com"
).split(",") if u.strip()]

# The .app SuperMaks asks macOS to open the wake links in.
WAKE_BROWSER_APP = env("WAKE_BROWSER_APP", "Safari")


def _applescript_string(u):
    return u.replace("\\", "\\\\").replace('"', '\\"')


def _run_osascript(script):
    """Runs an AppleScript on the Mac — local or remote, see mac_bridge.
    Returns (ok, stdout) on success or (False, error)."""
    return mac_bridge.run(["osascript", "-e", script])


def _screen_size():
    """(width, height) of the main display, via Finder — falls back to a
    reasonable guess if that ever fails rather than blowing up the wake."""
    ok, out = _run_osascript('tell application "Finder" to get bounds of window of desktop')
    if ok and out:
        try:
            x0, y0, x1, y1 = (int(n.strip()) for n in out.split(","))
            return x1 - x0, y1 - y0
        except ValueError:
            pass
    return 1440, 900


def _tile_layout(n, screen_w, screen_h):
    """First window gets the left HALF of the screen (full height); every
    other window splits the right half into equal QUARTER-height strips
    (literally quarters when there are 2 of them, thinner if there are more)
    — a deliberate, readable tiling instead of a uniform small-scale stack.
    Returns a list of (x0, y0, x1, y1), one per window, index-aligned to urls.
    """
    half_w = screen_w // 2
    rects = [(0, 0, half_w, screen_h)]
    remaining = max(1, n - 1)
    strip_h = screen_h // remaining
    for i in range(n - 1):
        y0 = i * strip_h
        y1 = screen_h if i == remaining - 1 else y0 + strip_h
        rects.append((half_w, y0, screen_w, y1))
    return rects


def open_wake_links(urls):
    """Ask macOS to open every wake URL as its own separate new browser
    window, tiled — first one half the screen, the rest quartered into the
    other half — instead of a uniform stack.

    `window.open()` from the page itself can't do this reliably: Chrome only
    honors popup-window features (and only avoids merging consecutive calls
    into tabs of one window) when each call carries its own fresh user
    gesture, which a voice-triggered wake never has. Driving the browser via
    AppleScript (Safari) or `open -na` (anything else) sidesteps that
    entirely — each is a genuine OS-level request, not a script call the
    browser can lump in with the others.

    Runs on whatever Mac mac_bridge resolves to — this machine, or a remote
    one over SSH if SuperMaks itself isn't running on the Mac (dual-Hermes).
    """
    if not mac_bridge.available():
        return False, "no Mac reachable"
    screen_w, screen_h = _screen_size()
    rects = _tile_layout(len(urls), screen_w, screen_h)

    if WAKE_BROWSER_APP.strip().lower() == "safari":
        # `set bounds of <the document var>` reliably fails right after
        # `make new document` (-10006, "Can't set bounds of document
        # 'Untitled'") — Safari hasn't attached the new document to a window
        # yet at that point. `front window` right after creation is the new
        # window every time and accepts bounds immediately; verified live.
        lines = []
        for u, (x0, y0, x1, y1) in zip(urls, rects):
            lines.append(
                f'make new document with properties {{URL:"{_applescript_string(u)}"}}\n'
                f'    set bounds of front window to {{{x0}, {y0}, {x1}, {y1}}}')
        opens = "\n    ".join(lines)
        return _run_osascript(f'''
tell application "Safari"
    activate
    {opens}
end tell
''')
    for u, (x0, y0, x1, y1) in zip(urls, rects):
        ok, reason = mac_bridge.run(
            ["open", "-na", WAKE_BROWSER_APP, "--args", "--new-window",
             f"--window-size={x1 - x0},{y1 - y0}", f"--window-position={x0},{y0}", u])
        if not ok:
            return False, reason
    return True, None

# The HUD goes dormant (wake-phrase-only) on every launch, and re-arms itself
# after this many hours with no prompt — so a laptop left running all day
# drops back into "asleep, waiting to be woken" on its own.
WAKE_IDLE_HOURS = float(env("WAKE_IDLE_HOURS", "4.5"))

# The SuperMaks persona used to be injected into every request from
# persona.md here — now it lives in Hermes' own SOUL.md on Ubuntu, loaded
# fresh by Hermes itself on every message, so nothing needs sending from
# this end at all. What's left to inject per-request is just genuinely
# dynamic state SOUL.md can't hold: profile facts / goal / personality
# overlay / mission queue, all mutable at runtime via /profile, /goal, etc.
def persona():
    return commands.context_block()


# One continuing Hermes conversation until the user hits /new.
SESSION = {"id": None}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if env("VERBOSE"):
            sys.stderr.write("  " + (fmt % args) + "\n")

    # ── helpers ──────────────────────────────────────────────
    def _json(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _bytes(self, data, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read(self, limit):
        n = int(self.headers.get("Content-Length", 0))
        if n < 0 or n > limit:
            raise ValueError(f"request body exceeds {limit} bytes")
        return self.rfile.read(n) if n else b""

    def _host_ok(self):
        host = self.headers.get("Host", "")
        return host in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        return origin in {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}

    def _token_ok(self):
        supplied = self.headers.get(TOKEN_HEADER, "")
        return bool(supplied) and secrets.compare_digest(supplied, API_TOKEN)

    def _voice_state(self):
        return "fish" if voice.available() else "browser"

    # ── GET ──────────────────────────────────────────────────
    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "invalid host"}, 403)
        p = urlparse(self.path).path

        if p == "/api/status":
            vs = self._voice_state()
            return self._json(dict(
                runtime=runtime.runtime_kind(),
                permission=runtime.PERMISSION,
                profile=runtime.PROFILE,
                source=runtime.SOURCE,
                workdir=runtime.WORKDIR,
                model=runtime.MODEL or "Hermes default",
                tools=runtime.hermes_tools_snapshot(),
                browser_stt=True,
                browser_tts=True,
                voice_mode=env("VOICE_MODE", vs),
                voice_provider=vs,
                voice_model=voice.model() if voice.available() else "browser",
                voice_id=voice.voice_id() if voice.available() else "browser",
                stt="browser", tts=vs,
                wake_song=dict(
                    source=WAKE_SONG_SOURCE,
                    local_ready=bool(WAKE_SONG_LOCAL_PATH) and pathlib.Path(WAKE_SONG_LOCAL_PATH).is_file(),
                    seconds=WAKE_SONG_SECONDS,
                    url=WAKE_SONG_URL,
                    volume=WAKE_SONG_VOLUME,
                    duck=WAKE_SONG_DUCK,
                ),
                wake_tabs=WAKE_TABS,
                wake_idle_hours=WAKE_IDLE_HOURS,
                session=SESSION["id"]))

        if p == "/api/jobs":
            if not self._token_ok():
                return self._json({"error": "unauthorized"}, 401)
            # finished background missions, reported once each
            return self._json(dict(done=commands.take_finished(),
                                   running=[j for j in commands.jobs_snapshot()
                                            if j["status"] == "running"]))


        rel = "index.html" if p == "/" else p.lstrip("/")
        f = (UI / rel).resolve()
        if not str(f).startswith(str(UI.resolve())) or not f.is_file():
            return self._bytes(b"not found", "text/plain", 404)
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        data = f.read_bytes()
        if rel == "index.html":
            data = data.replace(b"__SUPERMAKS_TOKEN__", API_TOKEN.encode())
        return self._bytes(data, ctype)

    # ── POST ─────────────────────────────────────────────────
    def do_POST(self):
        p = urlparse(self.path).path
        if not self._host_ok() or not self._origin_ok():
            return self._json({"error": "request origin rejected"}, 403)
        if not self._token_ok():
            return self._json({"error": "unauthorized"}, 401)

        json_paths = {"/api/run", "/api/speak", "/api/new", "/api/cancel",
                      "/api/wake/open-tabs",
                      "/api/mac/action", "/api/mac/screenshot", "/api/mac/approvals/decide"}
        ctype = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if p in json_paths and ctype != "application/json":
            return self._json({"error": "application/json required"}, 415)
        if p == "/api/listen" and not ctype.startswith("audio/"):
            return self._json({"error": "audio content type required"}, 415)
        try:
            raw = self._read(MAX_AUDIO_BODY if p == "/api/listen" else MAX_JSON_BODY)
        except (TypeError, ValueError):
            return self._json({"error": "request body too large"}, 413)

        if p == "/api/speak":
            try:
                text = (json.loads(raw or b"{}").get("text") or "").strip()
                return self._bytes(voice.speak(text), "audio/mpeg")
            except Exception as e:                        # noqa: BLE001
                return self._json({"error": str(e)[:200]}, 503)

        if p == "/api/listen":
            try:
                mime = self.headers.get("Content-Type", "audio/webm")
                return self._json({"text": voice.transcribe(raw, mime)})
            except Exception as e:                        # noqa: BLE001
                return self._json({"error": str(e)[:300], "text": ""}, 503)


        if p == "/api/new":
            runtime.cancel_active()
            SESSION["id"] = None
            return self._json({"ok": True})

        if p == "/api/cancel":
            # Stopping a run early isn't evidence the underlying Hermes
            # session is broken — same reasoning as the killed-error case in
            # _stream_run() below, just a second reset path that one didn't
            # cover: hitting Control/Escape used to nuke the session every
            # time regardless, which is why conversations kept restarting.
            stopped = runtime.cancel_active()
            return self._json({"ok": True, "stopped": stopped})

        if p == "/api/wake/open-tabs":
            urls = ([WAKE_SONG_URL] if WAKE_SONG_SOURCE == "open" and WAKE_SONG_URL else []) + WAKE_TABS
            if not urls:
                return self._json({"ok": True, "opened": 0})
            ok, reason = open_wake_links(urls)
            if not ok:
                return self._json({"ok": False, "error": reason}, 501)
            return self._json({"ok": True, "opened": len(urls)})

        if p == "/api/run":
            if not RUN_LOCK.acquire(blocking=False):
                return self._json({"error": "SuperMaks is already processing a request"}, 409)
            try:
                return self._stream_run(raw)
            finally:
                RUN_LOCK.release()

        return self._json({"error": "no such endpoint"}, 404)

    # ── the run: NDJSON stream of events ─────────────────────
    def _stream_run(self, raw):
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        message = (payload.get("message") or "").strip()
        extra = (payload.get("system") or "").strip()
        fresh = bool(payload.get("fresh"))
        if not message:
            return self._json({"error": "empty message"}, 400)
        if fresh:
            SESSION["id"] = None

        # persona() is now just profile/goal/personality/mission-queue —
        # small, and mutable at runtime (via /profile, /goal, ...) — so it's
        # sent every turn rather than only the first: the old "only on a
        # fresh session" gate existed specifically because persona.md used
        # to make this block big enough that repeating it was real waste.
        # That's gone; this isn't worth gating.
        def system_for_turn():
            return "\n\n".join(x for x in (persona(), extra) if x) or None

        system = system_for_turn()

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(ev):
            self.wfile.write((json.dumps(ev) + "\n").encode())
            self.wfile.flush()

        # ── the command matrix: /new /profile /goal /personality /mac /mission …
        cmd = commands.handle(
            message,
            runner=lambda m: runtime.run(m, None, persona()))
        if cmd:
            if cmd.get("note"):
                emit(dict(t="note", message=cmd["note"]))
            if cmd.get("fresh"):
                SESSION["id"] = None
            if cmd.get("message") is None:
                # answered locally — no model call needed
                emit(dict(t="delta", text=cmd.get("reply", "Done.")))
                emit(dict(t="complete", ms=0))
                return
            message = cmd["message"]
            system = system_for_turn()

        try:
            for ev in runtime.run(message, SESSION["id"], system):
                # Only remember a session that actually COMPLETED. Storing it from
                # `status` (which fires at init) means one broken run poisons every
                # run after it with --resume <half-born session>.
                sid = ev.get("session_id")
                if ev.get("t") == "complete" and runtime.valid_session(sid):
                    SESSION["id"] = sid
                # A cancel or a watchdog kill (ev["killed"]) stopped OUR process
                # because a turn ran too long — it isn't Hermes reporting the
                # session itself is broken, so keep it and let the next message
                # resume the conversation. Only a genuine failure (bad exit,
                # can't launch, --resume itself rejected) drops it.
                if ev.get("t") == "error" and not ev.get("killed"):
                    SESSION["id"] = None       # drop a stale session so the next run is fresh
                emit(ev)
        except (BrokenPipeError, ConnectionResetError):
            pass                                          # client navigated away
        except Exception as e:                            # noqa: BLE001
            try:
                emit(dict(t="error", message=str(e)[:300]))
            except OSError:
                pass


def main():
    kind = runtime.runtime_kind()
    brain = ("Hermes Agent (profile tools, skills, memory, MCP, browser integrations live)"
             if kind == "hermes" else "MOCK — Hermes CLI not reachable")
    vo = (f"fish.audio · {voice.model()} · voice {voice.voice_id()[:8]}…" if voice.available()
          else "none (add FISH_AUDIO_API_KEY for voice)")
    perm = runtime.PERMISSION
    perm_note = ("  can run tools without asking — set SUPERMAKS_PERMISSION=default to require approval"
                 if perm == "bypass" else "")

    print(f"""
  SUPERMAKS · Hermes Live HUD
  ──────────────────────────────────────────────
  brain        {brain}
  profile      {runtime.PROFILE}
  workdir      {runtime.WORKDIR}
  permission   {perm}{perm_note}
  voice        {vo} + browser Web Speech fallback
  wake song    {WAKE_SONG_SOURCE}
  wake idle    dormant on launch, and again after {WAKE_IDLE_HOURS}h with no prompt
  open         http://localhost:{PORT}

  Viewing from the Mac? Do not expose this port. From the Mac run:
    ssh -N -L {PORT}:127.0.0.1:{PORT} <this-machine>
  then open http://127.0.0.1:{PORT} there. localhost is a secure context, so the
  microphone works; a LAN IP over plain http is not, and the browser blocks it.
""", flush=True)

    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if env("OPEN", "1") != "0":
        webbrowser.open(f"http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  down.")
        srv.shutdown()


if __name__ == "__main__":
    main()
