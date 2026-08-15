"""
SuperMaks Live — a HUD that drives Hermes Agent, with a Mac on the end of it.

    python3 server.py

The brain is your own Hermes Agent CLI/profile. Hermes keeps access to the same
configured tools, browser/Chrome automation, MCP servers, skills, memory, and
third-party integrations that your normal Hermes sessions have. Voice runs on
Fish Audio when a key is present, with browser Web Speech as the fallback. The
Mac bridge (mac.py) gives the HUD a live view of a second machine over SSH.
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
import mac              # noqa: E402
import runtime          # noqa: E402
import voice            # noqa: E402

PORT = int(env("PORT", "8730"))
API_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "X-Supermaks-Token"
RUN_LOCK = threading.Lock()
MAX_JSON_BODY = 1024 * 1024
MAX_AUDIO_BODY = 12 * 1024 * 1024

# The wake-phrase jingle. "youtube" needs no setup and ships nothing — the
# browser embeds the official video client-side, nothing is stored here.
# "local" streams a file YOU already own from this machine; "off" disables it.
# No audio file is ever bundled with this repo — that would mean redistributing
# someone else's copyrighted recording, which this project isn't going to do.
WAKE_SONG_SOURCE = env("WAKE_SONG_SOURCE", "youtube").strip().lower()
WAKE_SONG_YOUTUBE_ID = env("WAKE_SONG_YOUTUBE_ID", "xMaE6toi4mk").strip()
WAKE_SONG_LOCAL_PATH = env("WAKE_SONG_LOCAL_PATH", "").strip()
WAKE_SONG_SECONDS = int(env("WAKE_SONG_SECONDS", "105"))

# The SuperMaks persona, appended to every run. Without it, the model answers as
# a coding agent and narrates its own tooling — which is not what you want spoken
# out loud. Edit persona.md to change how it talks.
_PERSONA_FILE = ROOT / "persona.md"


def persona():
    try:
        base = _PERSONA_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        base = ""
    extra = commands.context_block()      # profile / goal / personality / queue
    return "\n\n".join(x for x in (base, extra) if x)


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
                stt=vs, tts=vs,
                mac_enabled=mac.configured(),
                mac_host=mac.HOST,
                wake_song=dict(
                    source=WAKE_SONG_SOURCE,
                    youtube_id=WAKE_SONG_YOUTUBE_ID if WAKE_SONG_SOURCE == "youtube" else "",
                    local_ready=bool(WAKE_SONG_LOCAL_PATH) and pathlib.Path(WAKE_SONG_LOCAL_PATH).is_file(),
                    seconds=WAKE_SONG_SECONDS,
                ),
                session=SESSION["id"]))

        if p == "/api/jobs":
            if not self._token_ok():
                return self._json({"error": "unauthorized"}, 401)
            # finished background missions, reported once each
            return self._json(dict(done=commands.take_finished(),
                                   running=[j for j in commands.jobs_snapshot()
                                            if j["status"] == "running"]))

        if p == "/api/mac":
            if not self._token_ok():
                return self._json({"error": "unauthorized"}, 401)
            return self._json(mac.status())

        if p == "/api/wake-song":
            if not self._token_ok():
                return self._json({"error": "unauthorized"}, 401)
            if WAKE_SONG_SOURCE != "local" or not WAKE_SONG_LOCAL_PATH:
                return self._json({"error": "no local wake song configured"}, 404)
            f = pathlib.Path(WAKE_SONG_LOCAL_PATH)
            if not f.is_file():
                return self._json({"error": "WAKE_SONG_LOCAL_PATH does not exist"}, 404)
            ctype = mimetypes.guess_type(f.name)[0] or "audio/mpeg"
            return self._bytes(f.read_bytes(), ctype)

        if p == "/api/mac/approvals":
            if not self._token_ok():
                return self._json({"error": "unauthorized"}, 401)
            return self._json({"pending": mac.list_approvals()})

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

        if p == "/api/mac/screenshot":
            image, err = mac.screenshot()
            if err:
                return self._json({"error": err}, 503)
            return self._json({"image": image})

        if p == "/api/mac/approvals/decide":
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            ok = mac.decide_approval(body.get("id", ""), bool(body.get("approve")))
            return self._json({"ok": ok}, 200 if ok else 404)

        if p == "/api/mac/action":
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            # Only names present in mac.ACTIONS get through — the browser can
            # name an action, never compose a command.
            result = mac.action(str(body.get("name", ""))[:40],
                                str(body.get("arg", ""))[:400])
            return self._json(result, 200 if result.get("ok") else 502)

        if p == "/api/new":
            runtime.cancel_active()
            SESSION["id"] = None
            return self._json({"ok": True})

        if p == "/api/cancel":
            stopped = runtime.cancel_active()
            SESSION["id"] = None
            return self._json({"ok": True, "stopped": stopped})

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
        system = "\n\n".join(x for x in (persona(), extra) if x) or None
        fresh = bool(payload.get("fresh"))
        if not message:
            return self._json({"error": "empty message"}, 400)
        if fresh:
            SESSION["id"] = None

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
            system = "\n\n".join(x for x in (persona(), extra) if x) or None

        try:
            for ev in runtime.run(message, SESSION["id"], system):
                # Only remember a session that actually COMPLETED. Storing it from
                # `status` (which fires at init) means one broken run poisons every
                # run after it with --resume <half-born session>.
                sid = ev.get("session_id")
                if ev.get("t") == "complete" and runtime.valid_session(sid):
                    SESSION["id"] = sid
                if ev.get("t") == "error":
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
  mac bridge   {mac.summary()}
  wake song    {WAKE_SONG_SOURCE}{(' · ' + WAKE_SONG_YOUTUBE_ID) if WAKE_SONG_SOURCE == 'youtube' else ''}
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
