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
import uuid

import mac
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
    if mac.configured():
        out.append(MAC_BRIEF)
    return "\n\n".join(out)


# Injected into every run so the agent knows the Mac exists and how to reach it.
# Without this it will answer "I can't control your Mac" while holding the tools.
MAC_BRIEF = """## Your Mac
You run on a Linux machine, but you also control the user's Mac over SSH. These
commands are on your PATH — use your terminal tool to run them. Do not describe
them to the user, just use them.

- `mac-sh <command>`      run any shell command on the Mac
- `mac-osa <applescript>` run AppleScript (control any scriptable Mac app)
- `mac-app <name>`        activate an application
- `mac-open <url|path>`   open a URL, file, or app
- `mac-say <text>`        speak through the Mac's own speakers
- `mac-type <text>`       type text into whatever is focused
- `mac-key <combo>`       press a key combo, e.g. cmd+t
- `mac-click <x> <y>`     click at screen coordinates
- `mac-shot [path]`       screenshot the Mac and save it locally
- `mac-status`            front app, battery, volume, uptime

If one of these reports a permission error, tell the user in one line that the
Mac needs Accessibility or Screen Recording granted, and stop."""


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


_CMD = re.compile(r"^\s*/(new|profile|goal|personality|kanban|mission|missions|background|tools|toolsets|connectors|connect|status|commands|help|browser|clear|mac|screen|voice|briefing)\b\s*(.*)$", re.I | re.S)


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


def _commands_reply():
    return """Available dashboard slash commands:
/new — reset the Hermes thread
/goal <text|status|clear> — set/read/clear standing objective
/profile <fact> — add a local profile note injected into Hermes prompts
/personality <tone> — set the SuperMaks tone overlay
/briefing — the once-a-day wake report: unread mail and today's calendar
/kanban [task] — read/add mission queue item
/mission [task] — alias for /kanban
/background <mission> — run a Hermes mission asynchronously
/mac <task> — do something on the Mac over SSH
/screen — pull a fresh screenshot of the Mac
/tools — show Hermes tool status from `hermes tools list`
/connectors — explain that connected Hermes tools transfer into this dashboard
/connect <name> — ask Hermes how to connect a third-party app generally
/toolsets — ask Hermes to list available toolsets
/voice — test the Fish Audio voice path end to end
/status — local Hermes runtime/profile/Mac/voice status
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
        vo = (f"fish.audio {voice.model()} / voice {voice.voice_id()[:8]}…"
              if voice.available() else "browser Web Speech (no FISH_AUDIO_API_KEY)")
        return dict(message=None, note="status read", reply=(
            f"SuperMaks online. profile={profile}; runtime={runtime}; "
            f"workdir={os.getcwd()}; voice={vo}; mac={mac.summary()}."))

    if cmd == "briefing":
        # Fired once a day by the wake phrase, or manually. The exact opening
        # line is deliberate — everything after it is genuinely live, pulled
        # through whatever mail/calendar tools this Hermes profile has.
        return dict(message=(
            "This is today's once-a-day wake briefing. Using your mail and calendar tools, "
            "check how many new or unread emails have arrived today and what is on the "
            "calendar for today.\n\n"
            "Open with exactly the words \"Welcome home, sir.\" Then, in one or two more "
            "sentences: state the email count and today's schedule, including one dry, "
            "witty aside about whatever is actually on the calendar today — not a generic "
            "joke, something that responds to the real content. Close with one short line "
            "asking whether they would like anything done about the emails — offer, don't act.\n\n"
            "If mail or calendar tools aren't available, say so in one plain line instead of "
            "guessing at numbers."
        ), note="daily wake briefing")

    if cmd == "voice":
        result = voice.check()
        return dict(message=None, note="voice check",
                    reply=("Voice path good — " if result["ok"] else "Voice path failed — ")
                          + result["detail"])

    if cmd == "screen":
        image, err = mac.screenshot()
        if err:
            return dict(message=None, reply=f"Couldn't see the Mac: {err}", note="screenshot failed")
        # The HUD picks the image up from its own /api/mac/screenshot poll; here
        # we just confirm, because this reply gets read aloud.
        return dict(message=None, reply="Mac screen captured.", note="screenshot ok")

    if cmd == "mac":
        if not mac.configured():
            return dict(message=None, note="mac disabled",
                        reply="The Mac bridge is switched off. Set MAC_ENABLED=1 and MAC_SSH_HOST.")
        if not arg:
            s = mac.status(force=True)
            if not s.get("reachable"):
                return dict(message=None, note="mac unreachable",
                            reply=f"I can't reach the Mac. {s.get('detail', '')}")
            return dict(message=None, note="mac status", reply=(
                f"{s.get('name', mac.HOST)} is up. "
                + ", ".join(filter(None, [
                    f"{s['front']} is in front" if s.get("front") else "",
                    f"battery {s['battery']}" if s.get("battery") else "",
                    f"volume {s['volume']}" if s.get("volume") else "",
                    f"up {s['uptime']}" if s.get("uptime") else "",
                ])) + "."))
        return dict(message=(
            f"Do this on my Mac using your mac-* tools: {arg}\n"
            "Use mac-osa or mac-sh as appropriate. Report only the outcome, in one line."
        ), note=f"mac mission: {arg[:50]}")

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
