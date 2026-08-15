"""
The Mac bridge.

SuperMaks runs on the Ubuntu machine. The Mac is a remote body it drives over
SSH on the LAN. Everything here is one of two shapes:

  1. Named, fixed actions the HUD can trigger from a button. No free-form shell
     reaches this path — the UI can only name an action from ACTIONS below.
  2. Status/screenshot polling, so the right-hand panel shows a live Mac.

Arbitrary control (open this, script that, click there) is deliberately NOT
exposed here. That belongs to Hermes, which has the `mac-*` tools in ./tools on
its PATH and decides for itself. Keeping the two apart means a stray click in
the browser can never run a shell command, while the agent stays fully capable.

Requires, on the Ubuntu side:
  ~/.ssh/config with a `mac` Host block, or MAC_SSH_HOST=user@host
  a key already authorized on the Mac (see setup-mac.sh)
"""
import base64
import os
import shlex
import subprocess
import threading
import time

HOST = (os.environ.get("MAC_SSH_HOST") or "mac").strip()
ENABLED = (os.environ.get("MAC_ENABLED", "1").strip().lower()
           not in {"0", "false", "no", "off"})
TIMEOUT = int(os.environ.get("MAC_TIMEOUT", "20"))
SHOT_MAX_PX = int(os.environ.get("MAC_SHOT_PX", "1100"))

# Reuse one SSH connection. Without this every action pays a full handshake and
# the status poll alone would keep a TCP/crypto setup running every few seconds.
_MUX = os.path.expanduser(os.environ.get("MAC_SSH_MUX", "~/.ssh/supermaks-%r@%h:%p"))
_SSH_OPTS = [
    "-o", "BatchMode=yes",              # never block on a password prompt
    "-o", "ConnectTimeout=6",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ControlMaster=auto",
    "-o", f"ControlPath={_MUX}",
    "-o", "ControlPersist=120",
    "-o", "ServerAliveInterval=10",
]

# Status is polled by every open tab. Cache it so ten tabs are still one SSH call.
_CACHE = {"at": 0.0, "data": None}
_CACHE_TTL = float(os.environ.get("MAC_STATUS_TTL", "4"))
_CACHE_LOCK = threading.Lock()


def configured():
    return ENABLED and bool(HOST)


def _ssh(remote_cmd, timeout=None):
    """Run one command on the Mac. Returns (rc, stdout, stderr). Never raises."""
    if not configured():
        return 1, "", "Mac bridge disabled (set MAC_ENABLED=1 and MAC_SSH_HOST)"
    argv = ["ssh", *_SSH_OPTS, HOST, "--", remote_cmd]
    try:
        p = subprocess.run(argv, text=True, timeout=timeout or TIMEOUT,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"Mac did not answer within {timeout or TIMEOUT}s"
    except FileNotFoundError:
        return 127, "", "ssh not found on this machine"
    except Exception as e:                                # noqa: BLE001
        return 1, "", str(e)[:200]


def shell(argv):
    """Run an argv list on the Mac, quoted so the remote shell can't reinterpret it."""
    return _ssh(" ".join(shlex.quote(str(a)) for a in argv))


def script(source, timeout=None):
    """Run a whole multi-line script on the Mac.

    ssh flattens its argv into one string that the remote login shell re-parses,
    so a script carrying its own quoting loses a quoting level in transit.
    Base64 avoids parsing altogether: the wire carries one alphanumeric blob.
    """
    blob = base64.b64encode(source.encode()).decode()
    return _ssh(f"printf %s {blob} | base64 -d | /bin/bash", timeout=timeout)


def osascript(script, timeout=None):
    return _ssh("osascript -e " + shlex.quote(script), timeout=timeout)


# ── status ───────────────────────────────────────────────────
# One round trip, tab-separated. Anything the Mac refuses (usually a TCC
# permission the SSH session hasn't been granted) comes back empty rather than
# failing the whole probe.
_PROBE = r'''
printf 'name\t%s\n' "$(scutil --get ComputerName 2>/dev/null)"
printf 'uptime\t%s\n' "$(uptime 2>/dev/null | sed 's/.*up //; s/,[[:space:]]*[0-9]* user.*//')"
printf 'battery\t%s\n' "$(pmset -g batt 2>/dev/null | grep -Eo '[0-9]+%' | head -1)"
printf 'power\t%s\n' "$(pmset -g batt 2>/dev/null | head -1 | grep -Eo "'.*'" | tr -d "'")"
printf 'volume\t%s\n' "$(osascript -e 'output volume of (get volume settings)' 2>/dev/null)"
printf 'front\t%s\n' "$(osascript -e 'tell application "System Events" to get name of first application process whose frontmost is true' 2>/dev/null)"
printf 'display\t%s\n' "$(pmset -g powerstate IODisplayWrangler 2>/dev/null | tail -1 | awk '{print $2}')"
printf 'load\t%s\n' "$(sysctl -n vm.loadavg 2>/dev/null | tr -d '{}' | awk '{print $1}')"
printf 'disk\t%s\n' "$(df -h / 2>/dev/null | tail -1 | awk '{print $4}')"
'''


def status(force=False):
    now = time.time()
    with _CACHE_LOCK:
        if not force and _CACHE["data"] and now - _CACHE["at"] < _CACHE_TTL:
            return _CACHE["data"]

    if not configured():
        data = {"reachable": False, "host": HOST, "detail": "Mac bridge disabled"}
    else:
        rc, out, err = script(_PROBE, timeout=12)
        if rc != 0:
            data = {"reachable": False, "host": HOST,
                    "detail": (err or f"ssh exited {rc}")[:200]}
        else:
            fields = {}
            for line in out.splitlines():
                key, _, value = line.partition("\t")
                value = value.strip()
                if value:
                    fields[key.strip()] = value
            data = {"reachable": True, "host": HOST, **fields}
            # An empty `front` almost always means one specific thing, and it is
            # worth saying plainly rather than showing a blank field forever.
            if not fields.get("front"):
                data["gui_blocked"] = True
                data["detail"] = ("GUI query returned nothing — grant Accessibility and "
                                  "Automation to sshd-keygen-wrapper on the Mac")
    with _CACHE_LOCK:
        _CACHE.update(at=now, data=data)
    return data


def screenshot():
    """Returns (base64_jpeg, error). Downscaled on the Mac so the LAN carries a
    thumbnail, not a 6MB Retina capture."""
    if not configured():
        return None, "Mac bridge disabled"
    remote = f"""
f=/tmp/supermaks-shot.jpg
s=/tmp/supermaks-shot-small.jpg
screencapture -x -t jpg "$f" 2>/dev/null || exit 1
sips -Z {SHOT_MAX_PX} "$f" --out "$s" >/dev/null 2>&1 || cp "$f" "$s"
openssl base64 -A -in "$s"
rm -f "$f" "$s"
"""
    rc, out, err = script(remote, timeout=30)
    if rc != 0 or not out:
        return None, (err or "screencapture returned nothing — grant Screen Recording "
                             "to sshd-keygen-wrapper on the Mac")[:200]
    try:
        base64.b64decode(out, validate=True)              # reject a shell error page
    except Exception:                                     # noqa: BLE001
        return None, "screenshot payload was not valid base64"
    return out, None


# ── the button actions ───────────────────────────────────────
# name -> (label, builder). The builder returns a remote command string. Only
# these names can ever be triggered from the browser.
def _osa(script):
    return "osascript -e " + shlex.quote(script)


ACTIONS = {
    "lock":        ("Lock screen",   lambda a: _osa('tell application "System Events" to keystroke "q" using {control down, command down}')),
    "sleep":       ("Sleep display", lambda a: "pmset displaysleepnow"),
    "wake":        ("Wake",          lambda a: "caffeinate -u -t 2"),
    "volume_up":   ("Volume up",     lambda a: _osa("set volume output volume ((output volume of (get volume settings)) + 10)")),
    "volume_down": ("Volume down",   lambda a: _osa("set volume output volume ((output volume of (get volume settings)) - 10)")),
    "mute":        ("Mute",          lambda a: _osa("set volume with output muted")),
    "unmute":      ("Unmute",        lambda a: _osa("set volume without output muted")),
    "playpause":   ("Play / pause",  lambda a: _osa('tell application "System Events" to key code 100')),
    "notify":      ("Notification",  lambda a: _osa(f'display notification {_q(a or "SuperMaks online")} with title "SuperMaks"')),
    "say":         ("Speak on Mac",  lambda a: "say " + shlex.quote((a or "Online.")[:400])),
    "clipboard":   ("Read clipboard", lambda a: "pbpaste"),
    "front":       ("Front app",     lambda a: _osa('tell application "System Events" to get name of first application process whose frontmost is true')),
}


def _q(text):
    """Quote a string for embedding inside an AppleScript literal."""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"')[:400] + '"'


def action(name, arg=""):
    entry = ACTIONS.get(name)
    if not entry:
        return {"ok": False, "error": f"unknown action: {str(name)[:40]}"}
    label, build = entry
    rc, out, err = _ssh(build(arg))
    if name in {"volume_up", "volume_down", "mute", "unmute"}:
        status(force=True)                                # reflect it immediately
    return {"ok": rc == 0, "action": name, "label": label,
            "output": out[:2000], "error": err[:300] if rc != 0 else ""}


def summary():
    """One line for /status and the boot banner."""
    if not configured():
        return "disabled"
    s = status()
    if not s.get("reachable"):
        return f"{HOST} unreachable — {s.get('detail', '')[:80]}"
    bits = [s.get("name") or HOST]
    if s.get("front"):
        bits.append(f"front={s['front']}")
    if s.get("battery"):
        bits.append(f"batt={s['battery']}")
    return " · ".join(bits)
