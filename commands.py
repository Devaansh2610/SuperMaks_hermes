"""
Command matrix for the Hermes-backed SuperMaks dashboard.

Local commands keep the HUD fast for mission control/status while prompts and
unknown slash commands are handed to Hermes Agent. Hermes-native tool access comes
from the runtime subprocess (`hermes chat -q ...`) using the configured profile.
"""
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import voice

ROOT = pathlib.Path(__file__).resolve().parent
STATE_FILE = pathlib.Path(os.environ.get("SUPERMAKS_STATE",
                                         os.environ.get("JARVIS_STATE", ROOT / "state.json")))
_LOCK = threading.Lock()
_DEFAULT = {"profile": [], "goal": "", "personality": "", "tasks": [], "missions": []}


def _load_unlocked():
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {**_DEFAULT, **d}
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT)


def load():
    with _LOCK:
        return _load_unlocked()


def _save_unlocked(d):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_name(f".{STATE_FILE.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, STATE_FILE)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def save(d):
    with _LOCK:
        _save_unlocked(d)


def update_state(mutator):
    """Apply one read-modify-write transaction without losing concurrent updates."""
    with _LOCK:
        d = _load_unlocked()
        result = mutator(d)
        _save_unlocked(d)
        return result


def context_block():
    d = load()
    out = []
    if d["profile"]:
        out.append("## User profile notes from the SuperMaks dashboard\n" + "\n".join(f"- {x}" for x in d["profile"][-40:]))
    if d["goal"]:
        out.append(f"## Standing objective\n{d['goal']}\nKeep it in mind; mention it only when relevant.")
    if d["personality"]:
        out.append(f"## Tone overlay\n{d['personality']}\nThis adjusts delivery; stay concise.")
    open_tasks = [t for t in d["tasks"] if not t.get("done")]
    if open_tasks:
        out.append("## Mission queue\n" + "\n".join(f"- {t['text']}" for t in open_tasks[:20]))
    return "\n\n".join(out)


JOBS = {}
_JOB_LOCK = threading.Lock()


def start_background(mission, runner):
    jid = uuid.uuid4().hex[:8]
    with _JOB_LOCK:
        JOBS[jid] = {"id": jid, "status": "running", "mission": mission,
                     "result": "", "started": time.time(), "finished": None}
    update_state(lambda d: d["missions"].append(
        {"id": jid, "mission": mission, "status": "running", "at": time.time()}))

    def _work():
        text = ""
        status = "done"
        try:
            for ev in runner(mission):
                if ev.get("t") == "delta":
                    text += ev["text"]
                elif ev.get("t") == "error":
                    status = "failed"
                    text = text or ("failed: " + str(ev.get("message", ""))[:260])
        except Exception as e:  # noqa: BLE001
            status = "failed"
            text = f"failed: {e}"[:260]
        with _JOB_LOCK:
            JOBS[jid].update(status="done", result=text.strip(), finished=time.time())
        def finish(d2):
            for item in d2.get("missions", []):
                if item.get("id") == jid:
                    item.update(status=status, finished=time.time(), result=text.strip()[:500])
        update_state(finish)

    threading.Thread(target=_work, daemon=True).start()
    return jid


def jobs_snapshot():
    with _JOB_LOCK:
        return [dict(j) for j in JOBS.values()]


def take_finished():
    with _JOB_LOCK:
        done = [dict(j) for j in JOBS.values() if j["status"] == "done"]
        for j in done:
            JOBS.pop(j["id"], None)
        return done


_CMD = re.compile(r"^\s*/(new|profile|goal|personality|kanban|mission|missions|background|tools|toolsets|connectors|connect|status|commands|help|browser|clear|voice|briefing|github)\b\s*(.*)$", re.I | re.S)


def _hermes_command(*args):
    configured = os.environ.get("HERMES_CMD", "").strip()
    if configured:
        return shlex.split(configured) + list(args)
    executable = shutil.which("hermes")
    if executable:
        return [executable, *args]
    return ["python3", "-m", "hermes_cli.main", *args]


def _shell(cmd, timeout=25):
    try:
        p = subprocess.run(cmd, text=True, cwd=os.getcwd(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        return (p.stdout or p.stderr or "").strip()[:3500]
    except Exception as e:  # noqa: BLE001
        return f"Unavailable: {e}"


_GOOGLE_API = pathlib.Path.home() / ".hermes/skills/productivity/google-workspace/scripts/google_api.py"


def _google_python():
    """Anaconda `python` has googleapiclient; system python3 often does not."""
    override = os.environ.get("GOOGLE_API_PYTHON", "").strip()
    if override:
        return override
    return shutil.which("python") or shutil.which("python3") or "python"


def _capture(cmd, timeout=18):
    """Run a briefing fetch. Never swallow failures into an empty string."""
    try:
        p = subprocess.run(cmd, text=True, cwd=os.getcwd(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"[timed out after {timeout}s — treat as a fetch failure, not an empty inbox]"
    except Exception as e:  # noqa: BLE001
        return f"[unavailable: {e}]"
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        return f"[failed exit {p.returncode}] {(err or out)[:900]}"
    if not out:
        return "[empty result]"
    return out


def _lines_from_json(raw, formatter, empty_note):
    raw = (raw or "").strip()
    if raw.startswith("["):
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return raw[:1800]
        if not items:
            return empty_note
        if not isinstance(items, list):
            return raw[:1800]
        return "\n".join(formatter(x) for x in items[:8]) or empty_note
    return raw[:1800]


# ── weather + news: free, keyless APIs, fetched directly instead of asking
# the model to browse for them. A browser/web-search tool call is a whole
# extra reasoning+network round trip through the model; a raw HTTP GET here
# takes well under a second and runs in the same thread pool as gmail/cal/gh.
_DELHI_LAT, _DELHI_LON = "28.6139", "77.2090"

_WMO_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow fall", 73: "moderate snow fall", 75: "heavy snow fall", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def _http_get(url, timeout=6):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SuperMaks/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return None, str(e)[:200]


def _weather():
    """Open-Meteo — free, no API key, no signup. https://open-meteo.com"""
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={_DELHI_LAT}&longitude={_DELHI_LON}"
           "&current_weather=true&timezone=Asia%2FKolkata")
    raw, err = _http_get(url)
    if err:
        return f"[weather fetch failed: {err}]"
    try:
        cw = json.loads(raw).get("current_weather") or {}
    except json.JSONDecodeError as e:
        return f"[weather fetch failed: bad response — {e}]"
    temp, wind, code = cw.get("temperature"), cw.get("windspeed"), cw.get("weathercode")
    if temp is None:
        return "[weather fetch failed: no current_weather in response]"
    desc = _WMO_WEATHER_CODES.get(code, "conditions unclear")
    return f"New Delhi right now: {temp}°C, {desc}, wind {wind} km/h."


def _delhi_news():
    """Google News RSS search — free, no API key, no signup, no rate-limit
    registration. https://news.google.com/rss/search?q=..."""
    url = "https://news.google.com/rss/search?q=Delhi%20when:1d&hl=en-IN&gl=IN&ceid=IN:en"
    raw, err = _http_get(url)
    if err:
        return f"[news fetch failed: {err}]"
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return f"[news fetch failed: bad response — {e}]"
    titles = [(it.findtext("title") or "").strip()
              for it in root.findall("./channel/item")[:6]]
    titles = [t for t in titles if t]
    return "\n".join(f"- {t}" for t in titles) if titles else "No recent Delhi headlines returned."


def _wake_snapshot():
    py = _google_python()
    script = str(_GOOGLE_API)
    now = datetime.now().astimezone()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day1 = day0 + timedelta(days=1)
    today = day0.strftime("%Y-%m-%d")

    jobs = {
        "gmail": lambda: _capture([py, script, "gmail", "search", "is:unread", "--max", "5"]),
        "cal": lambda: _capture([py, script, "calendar", "list",
                                  "--start", day0.isoformat(), "--end", day1.isoformat(), "--max", "12"]),
        "gh": lambda: _capture(["gh", "repo", "list", "--limit", "8",
                                 "--json", "name,updatedAt,description,url"]),
        "weather": _weather,
        "news": _delhi_news,
    }

    results = {k: "[not fetched]" for k in jobs}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futs = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futs):
            results[futs[fut]] = fut.result()

    gmail = _lines_from_json(
        results["gmail"],
        lambda m: f"- {m.get('from','?')} · {m.get('subject','(no subject)')} · {m.get('date','')}",
        "No unread mail.",
    )
    cal = _lines_from_json(
        results["cal"],
        lambda e: f"- {e.get('start','?')} · {e.get('summary','(no title)')}"
                  + (f" @ {e['location']}" if e.get("location") else ""),
        f"No events on the primary calendar today ({today}).",
    )
    gh = _lines_from_json(
        results["gh"],
        lambda r: f"- {r.get('name','?')} · updated {r.get('updatedAt','?')}",
        "No GitHub repos returned.",
    )
    return gmail, cal, gh, results["weather"], results["news"]


def _commands_reply():
    return """Available dashboard slash commands:
/new — reset the Hermes thread
/goal <text|status|clear> — set/read/clear standing objective
/profile <fact> — add a local profile note injected into Hermes prompts
/personality <tone> — set the SuperMaks tone overlay
/briefing — the once-a-day wake report: unread mail and today's calendar
/github — recent commits and repo activity
/kanban [task] — read/add mission queue item
/mission [task] — alias for /kanban
/background <mission> — run a Hermes mission asynchronously
/tools — show Hermes tool status from `hermes tools list`
/connectors — explain that connected Hermes tools transfer into this dashboard
/connect <name> — ask Hermes how to connect a third-party app generally
/toolsets — ask Hermes to list available toolsets
/voice — test the Fish Audio voice path end to end
/status — local Hermes runtime/profile/voice status
/browser <task> — ask Hermes to use browser/Chrome tools
/clear — clear the response panel locally
/help or /commands — show this list

You can also type normal Hermes prompts. Unknown slash commands are forwarded to Hermes."""


def handle(message, runner=None):
    m = _CMD.match(message or "")
    if not m:
        return None
    cmd, arg = m.group(1).lower(), m.group(2).strip()
    d = load()

    if cmd in ("help", "commands"):
        return dict(message=None, reply=_commands_reply(), note="commands shown")

    if cmd == "clear":
        return dict(message=None, reply="Display cleared. Hermes session is unchanged.", note="display clear")

    if cmd == "new":
        return dict(fresh=True, message=arg or "Start a fresh session. Greet me in one concise line.", note="Hermes thread reset")

    if cmd == "status":
        profile = os.environ.get("HERMES_PROFILE",
                                 os.environ.get("SUPERMAKS_PROFILE",
                                                os.environ.get("JARVIS_PROFILE", "default")))
        runtime = os.environ.get("HERMES_CMD", "hermes")
        vo = (f"fish.audio {voice.model()} / voice {voice.voice_id()[:8]}..."
              if voice.available() else "browser Web Speech (no FISH_AUDIO_API_KEY)")
        return dict(message=None, note="status read", reply=(
            f"SuperMaks online. profile={profile}; runtime={runtime}; "
            f"workdir={os.getcwd()}; voice={vo}."))

    if cmd == "briefing":
        fetched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        gmail_out, cal_out, gh_out, weather_out, news_out = _wake_snapshot()
        data = (
            f"Fetched live at {fetched_at} (not cached).\n\n"
            f"Gmail (top 5 unread):\n{gmail_out}\n\n"
            f"Calendar (primary, today local):\n{cal_out}\n\n"
            f"GitHub repos (recently updated):\n{gh_out}\n\n"
            f"New Delhi weather (Open-Meteo):\n{weather_out}\n\n"
            f"Delhi headlines, last 24h (Google News):\n{news_out}"
        )
        return dict(message=(
            "This is the wake briefing. All of the data below — Gmail, Google Calendar, GitHub, "
            "weather, and news — was fetched just now by the dashboard itself. Do not call tools "
            "again for any of it, including weather or news; both are already fresh. Do not invent "
            "an empty inbox if mail is listed. Empty JSON [] means that source really had nothing. "
            "A line starting with [ (e.g. \"[weather fetch failed: ...]\") means that source failed "
            "— say so, don't invent a number or calm conditions in its place.\n\n"
            + data + "\n\n"
            "Then speak in this shape:\n"
            "1. Open with exactly: \"Welcome home, sir.\"\n"
            "2. One or two sentences with the one or two things that matter most from mail/calendar/"
            "GitHub. Real numbers, real names.\n"
            "3. One short sentence on the current New Delhi weather, from the data above.\n"
            "4. Skim the Delhi headlines above. If — and only if — one of them is genuinely alarming "
            "or major, one to two lines summarizing it; otherwise skip this line entirely, don't say "
            "there's nothing to report.\n"
            "5. One dry, personal aside about something specific you noticed.\n"
            "6. Close with one short offer of something you could do.\n\n"
            "Keep the whole thing tight — about six spoken sentences total."
        ), note="wake briefing (live fetch)")

    if cmd == "github":
        out = _shell(["gh", "repo", "list", "--limit", "10", "--json", "name,updatedAt,description,url"])
        if out and out != "Unavailable:":
            return dict(message=None, reply=out, note="github repos listed")
        # fallback to generic prompt if gh not available
        return dict(message="Show my recent GitHub activity: commits, pushes, open PRs across my repos. Keep it concise.", note="github activity requested")

    if cmd == "voice":
        result = voice.check()
        return dict(message=None, note="voice check",
                    reply=("Voice path good — " if result["ok"] else "Voice path failed — ")
                          + result["detail"])


    if cmd in ("tools", "toolsets"):
        if cmd == "tools":
            out = _shell(_hermes_command("tools", "list"))
            reply = out or "No tool list returned. Start from a shell where `hermes tools list` works, or set HERMES_CMD."
            return dict(message=None, reply=reply, note="Hermes tools listed")
        return dict(message="List my enabled Hermes toolsets and connected third-party tools. Include browser/Chrome availability if present. Keep it concise.", note="toolsets requested")

    if cmd == "connectors":
        return dict(message=None, note="connector bridge explained", reply=(
            "This dashboard does not need separate app wiring. It launches Hermes with your active profile, so any third-party app already connected in Hermes transfers here automatically.\n\n"
            "Connect apps in Hermes itself with: hermes tools, hermes mcp, hermes gateway setup, or the relevant Hermes skill. Then restart this dashboard.\n\n"
            "Use /tools or /toolsets here to see what the dashboard can currently reach."
        ))

    if cmd == "connect":
        target = (arg or "third-party app").strip()
        return dict(message=(
            f"Explain the correct Hermes-native way to connect {target} as a third-party app/tool. "
            "Be generic: the dashboard should inherit whatever is connected to Hermes, not maintain separate credentials. "
            "Include the relevant Hermes command family if known, like hermes tools, hermes mcp, hermes gateway setup, or a skill."
        ), note=f"connector help: {target[:40]}")

    if cmd == "browser":
        task = arg or "open or inspect Google Chrome using the configured Hermes browser/computer-use tools and report what is available"
        return dict(message=f"Use my Hermes browser/Google Chrome tooling if available: {task}", note="browser mission armed")

    if cmd == "profile":
        if not arg:
            facts = d["profile"]
            return dict(message=None, reply=("\n".join(f"- {x}" for x in facts[-20:]) if facts else "I don't know anything about you yet."), note="profile read")
        update_state(lambda state: state["profile"].append(arg))
        return dict(message=f'The user saved this dashboard profile note: "{arg}". Acknowledge in one short line.', note=f"profile += {arg[:50]}")

    if cmd == "goal":
        if arg.lower() == "clear":
            update_state(lambda state: state.__setitem__("goal", ""))
            return dict(message=None, reply="Standing objective cleared.", note="goal cleared")
        if not arg or arg.lower() == "status":
            return dict(message=None, reply=d["goal"] or "No standing objective set.", note="goal read")
        update_state(lambda state: state.__setitem__("goal", arg))
        return dict(message=f'The user set this standing objective: "{arg}". Acknowledge in one concise line.', note=f"goal set: {arg[:50]}")

    if cmd == "personality":
        if not arg:
            return dict(message=None, reply=d["personality"] or "Running the default SuperMaks persona.", note="persona read")
        update_state(lambda state: state.__setitem__("personality", arg))
        return dict(message=f'Tone overlay changed to: "{arg}". Reply in one short line using that tone.', note=f"persona: {arg[:50]}")

    if cmd in ("kanban", "mission", "missions"):
        if arg:
            update_state(lambda state: state["tasks"].append(
                {"text": arg, "done": False, "at": time.time()}))
            return dict(message=f'Added to the mission queue: "{arg}". Confirm in one short line.', note=f"mission += {arg[:50]}")
        open_t = [t["text"] for t in d["tasks"] if not t.get("done")]
        if not open_t:
            return dict(message=None, reply="Mission queue is empty.", note="queue read")
        return dict(message=None, reply="Mission queue:\n" + "\n".join(f"- {t}" for t in open_t), note=f"queue read ({len(open_t)})")

    if cmd == "background":
        if not arg:
            return dict(message=None, reply="Give me a mission.", note="background empty")
        if runner is None:
            return dict(message=None, reply="Background missions aren't available.", note="background unavailable")
        jid = start_background(arg, runner)
        return dict(message=None, reply=f"Background Hermes mission {jid} launched. I'll report when it lands.", note=f"mission {jid} started: {arg[:50]}")

    return None