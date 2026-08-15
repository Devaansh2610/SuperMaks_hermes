# SUPERMAKS · Hermes Mission Control

A local, voice-driven HUD for [Hermes Agent](https://github.com/NousResearch/hermes-agent)
that runs on a Linux machine and **drives a Mac over SSH**.

It is a visual interface over your existing Hermes profile — not a second agent
runtime — so it inherits the same tools, skills, memory, browser automation, MCP
servers, and third-party integrations. On top of that it adds a Mac bridge: a set
of `mac-*` commands Hermes can call to script, screenshot, type into, and click
around a second machine on the same network.

Open `preview.html` (or `ui/index.html`) in a browser to see the interface
immediately — with no key, no Hermes, and no Mac, it boots against a mock backend.

---

## How the two machines fit together

```
  ┌── Ubuntu laptop ────────────────────────────┐        ┌── Mac ─────────────┐
  │                                             │        │                    │
  │  Hermes Agent ── tools/mac-*  ──── ssh ─────┼───────▶│  Remote Login      │
  │       ▲                                     │        │  osascript         │
  │       │ subprocess                          │        │  screencapture     │
  │  server.py ── mac.py ─────────── ssh ───────┼───────▶│  cliclick          │
  │       │                                     │        │                    │
  │  127.0.0.1:8730 (HUD)                       │        │  browser ◀── ssh   │
  └───────┼─────────────────────────────────────┘        │       -L tunnel    │
          └────────────────────────────────────── ───────┴────────────────────┘
```

Two separate paths reach the Mac, on purpose:

- **`mac.py`** backs the dashboard's Mac panel. It can only run the fixed,
  named actions in `mac.ACTIONS` — lock, mute, volume, screenshot, status. A
  click in the browser can never compose a shell command.
- **`tools/mac-*`** are on Hermes' `PATH`. *Hermes* uses these, and — because
  `mac-sh` and `mac-osa` are genuinely general-purpose, the same power as
  sitting at the Mac's own terminal — every call through them passes a
  confirmation gate first. See **Guardrails** below.

## Prerequisites

1. Linux or macOS with Python 3 and a modern browser.
2. Hermes Agent installed and configured:

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup
```

Confirm `hermes chat -q "Say online"` works before installing the dashboard.

---

## Install

```bash
git clone https://github.com/<you>/supermaks-hermes-dashboard.git && \
cd supermaks-hermes-dashboard && \
./install.sh
```

The installer creates a private `.env`, launches the dashboard, and opens
<http://127.0.0.1:8730>.

No keys are required to start — browser speech APIs are the fallback.

## Voice — Fish Audio

Add a key to `.env` for proper speech in both directions:

```bash
FISH_AUDIO_API_KEY=your_private_key
FISH_AUDIO_MODEL=s2.1-pro-free
FISH_AUDIO_VOICE_ID=612b878b113047d9a770c069c8b4fdfe
```

`MODEL` and `VOICE_ID` are different things and are sent in different places —
the model as an HTTP header, the voice as `reference_id` in the body. Swapping
them produces a confusing 422.

| | |
|---|---|
| TTS | `POST https://api.fish.audio/v1/tts` → mp3 |
| STT | `POST https://api.fish.audio/v1/asr` → transcript |

The key never leaves the server. The browser asks `/api/speak` for audio and
gets mp3 bytes back, so nothing shows up in devtools or a screen recording.

Replies are synthesised **sentence by sentence** with one chunk prefetched
ahead, so speech starts while the rest is still being generated.

Check the whole path with `/voice` in the HUD.

---

## The Mac bridge

### 1. On the Mac

System Settings → General → Sharing → **Remote Login: on**.
Optionally `brew install cliclick` for mouse and keyboard control.

### 2. On the Linux machine

```bash
./setup-mac.sh you@macbook.local
```

That generates a dedicated key, authorizes it, writes a `mac` block into
`~/.ssh/config` with connection multiplexing, tests the link, and reports which
macOS permissions still need granting.

### 3. Grant the permissions macOS will not grant itself

Shell commands work as soon as SSH does. **GUI** actions do not. macOS gates
them behind TCC, and an SSH session is denied by default with no error message
worth reading. On the Mac, open **System Settings → Privacy & Security** and add
`/usr/libexec/sshd-keygen-wrapper` (⌘⇧G in the file picker to type the path) to:

| Permission | Unlocks |
|---|---|
| Accessibility | `mac-type`, `mac-key`, `mac-click` |
| Automation | `mac-osa`, `mac-app`, front-app detection |
| Screen Recording | `mac-shot`, the dashboard's screenshot panel |

GUI actions also need the Mac **awake and logged in at the console** — a locked
login screen has no session to script. `caffeinate -disu` keeps it available.

Verify everything at once:

```bash
./tools/mac-status
```

### Guardrails

`mac-sh` runs arbitrary shell; `mac-osa` runs arbitrary AppleScript. Both are
as powerful as sitting at the Mac in person, so both pass through
`tools/mac-guard.sh` before ssh is ever called:

| Verdict | Example | What happens |
|---|---|---|
| **SAFE** | `ls`, `open -a Safari`, `sw_vers` | runs immediately |
| **CONFIRM** | `rm`, `sudo …`, `mv`, `killall`, `chmod -R`, `curl \| sh` | files a request and **blocks** until a human clicks Approve or Deny in the dashboard, or it times out (`MAC_APPROVAL_TIMEOUT`, default 90s) |
| **DENY** | `rm -rf /`, `diskutil erase…`, `dd if=…`, disabling SIP | refused outright — no confirmation can override it |

This is enforced in the shell script itself, not in the model's instructions —
Hermes cannot argue its way past a check that runs before it gets a chance to.
A pending request shows up as a card in the top-right of the HUD with the
exact command and a one-line reason; nothing runs until you decide. Set
`MAC_CONFIRM_MODE=all` in `.env` to gate every `mac-sh`/`mac-osa` call, not
just the risky ones, or `off` to disable the gate entirely (not recommended).

**What the gate does not cover:** `mac-type` + `mac-key` can type text into
whatever window has focus and press Return — including a Terminal window, if
one happens to be open and focused. That's an inherent property of giving an
agent keyboard control at all, not something a command-text filter can catch.
If that risk matters to you, run with `MAC_CONFIRM_MODE=all` and keep an eye
on the event stream while a session is active.

### The tools Hermes gets

| Command | Does |
|---|---|
| `mac-sh <cmd>` | any shell command on the Mac |
| `mac-osa '<applescript>'` | script any Mac application |
| `mac-app <name>` | bring an app to the front |
| `mac-open <url\|path>` | open a URL, file, or app |
| `mac-say <text>` | speak out of the Mac's speakers |
| `mac-type <text>` | type into whatever is focused |
| `mac-key <combo>` | `cmd+t`, `cmd+shift+4`, `return`, `esc`… |
| `mac-click <x> <y>` | click at coordinates (needs cliclick) |
| `mac-shot [path]` | screenshot, pulled back locally |
| `mac-status` | front app, battery, volume, uptime, permissions |

`start.sh` puts `./tools` on `PATH` for the dashboard's own Hermes subprocess.
To use them from a Hermes session you start yourself, add to your shell rc:

```bash
export PATH="/path/to/supermaks-hermes-dashboard/tools:$PATH"
```

---

## Watching it from the Mac

The server binds `127.0.0.1` only. **Do not expose the port on the LAN** — apart
from the obvious, it breaks two things:

1. The server validates the `Host` header and same-origin on every
   state-changing request, so a `192.168.x.x` origin is rejected outright.
2. Browsers only grant microphone access on a **secure context**. Plain `http://`
   on a LAN IP is not one, so `getUserMedia` is blocked and voice dies.
   `localhost` *is* treated as secure.

Use an SSH tunnel instead. From the **Mac**:

```bash
ssh -N -L 8730:127.0.0.1:8730 you@ubuntu.local
```

Then open <http://127.0.0.1:8730> on the Mac. No code changes, every security
check satisfied, microphone works.

To keep it up automatically, save this as
`~/Library/LaunchAgents/com.supermaks.tunnel.plist` and
`launchctl load` it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.supermaks.tunnel</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/ssh</string><string>-N</string>
    <string>-o</string><string>ServerAliveInterval=30</string>
    <string>-o</string><string>ExitOnForwardFailure=yes</string>
    <string>-L</string><string>8730:127.0.0.1:8730</string>
    <string>you@ubuntu.local</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```

---

## Command matrix

Every button runs its base command immediately. Commands that take a payload
stay **armed**, so the next thing you type or say is sent as `/command payload`.

| Command | Action |
|---|---|
| `/new` | Start a fresh Hermes thread |
| `/mac <task>` | Do something on the Mac; bare `/mac` reads its status |
| `/screen` | Capture the Mac screen into the HUD |
| `/browser <task>` | Use Hermes' browser/Chrome tools |
| `/goal <text\|status\|clear>` | Set, read, or clear the standing objective |
| `/background <mission>` | Run a Hermes mission asynchronously |
| `/mission [task]` | Read or add to the mission queue |
| `/personality <tone>` | Change the tone overlay |
| `/profile <fact>` | Add a fact injected into every prompt |
| `/tools`, `/toolsets` | What Hermes can currently reach |
| `/connectors`, `/connect <name>` | How integrations are inherited |
| `/voice` | Test the Fish Audio path end to end |
| `/status` | Runtime, profile, Mac, and voice state |
| `/commands` | Show everything |

Anything else goes straight to Hermes.

**Keyboard:** `⌘K` / `Ctrl+K` command palette · `⌘↵` transmit · `Esc` cancel a
run, close the palette, or end the voice conversation.

---

## Architecture

```text
ui/            dependency-free HUD — canvas reactor, streaming, voice loop
server.py      local HTTP API and NDJSON streaming
runtime.py     Hermes CLI subprocess and session continuity
commands.py    slash commands, mission queue, the Mac briefing injected into prompts
voice.py       Fish Audio TTS + ASR
mac.py         the Mac bridge used by the dashboard panel
tools/mac-*    the Mac bridge used by Hermes itself
persona.md     the spoken SuperMaks persona
setup-mac.sh   one-shot SSH + permissions setup
```

The HUD ships no fonts and no libraries — it has to work on a machine bound to
`127.0.0.1` with the network unplugged.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FISH_AUDIO_API_KEY` | — | enables server-side speech |
| `FISH_AUDIO_MODEL` | `s2.1-pro-free` | synthesis engine |
| `FISH_AUDIO_VOICE_ID` | `612b878b…` | voice `reference_id` |
| `MAC_ENABLED` | `1` | switch the Mac bridge off entirely |
| `MAC_SSH_HOST` | `mac` | ssh alias or `user@host` |
| `MAC_SHOT_PX` | `1100` | screenshot long edge |
| `MAC_CONFIRM_MODE` | `risky` | `risky` gates destructive commands, `all` gates every `mac-sh`/`mac-osa` call, `off` disables the gate |
| `MAC_APPROVAL_TIMEOUT` | `90` | seconds a blocked action waits for a decision before it's treated as denied |
| `HERMES_PROFILE` | `default` | profile whose tools are inherited |
| `HERMES_CMD` | — | absolute path if `hermes` is not on `PATH` |
| `SUPERMAKS_PORT` | `8730` | dashboard port |
| `SUPERMAKS_PERMISSION` | `normal` | `bypass` lets Hermes run tools unprompted |
| `SUPERMAKS_TIMEOUT` | `120` | seconds of silence before a run is killed |
| `SUPERMAKS_WORKDIR` | `~` | agent working directory |

Old `JARVIS_*` variables are still read as a fallback.

## Security

- The server binds `127.0.0.1`; it is never exposed to the network.
- Every state-changing request needs a per-launch random token, same-origin,
  a valid localhost `Host`, an approved content type, and a bounded body.
- The browser can name a Mac action, never compose one. Free-form Mac control
  exists only in `tools/`, where Hermes — not the page — decides.
- Remote scripts are base64-encoded in transit, so nothing is re-parsed by a
  shell at the far end.
- Agent runs are serialized so multiple tabs cannot race the Hermes session.
- Raw prompt logging is off unless `SUPERMAKS_RAW_LOG` is set; opt-in logs are
  created `0600` with symlink protection.
- `.env`, state files, keys, and caches are git-ignored.
- The Mac key is dedicated to this bridge and set `IdentitiesOnly yes`.

## Troubleshooting

**Hermes not found** — `which hermes`, `hermes doctor`, then set `HERMES_CMD` in `.env`.

**Microphone does nothing** — you are almost certainly on a LAN IP rather than
localhost. Use the SSH tunnel above.

**Mac panel says unreachable** — `ssh mac true` from a terminal. If that prompts
for a password, re-run `./setup-mac.sh`.

**Front app shows "blocked"** — the TCC permissions above are not granted yet.

**A newly connected tool is missing** — Hermes caches per session. Run `/new`.

## Credits and license

Adapted from [`Itsme23476/jarvis-hermes-dashboard`](https://github.com/Itsme23476/jarvis-hermes-dashboard),
itself inspired by [`Itsme23476/jarvis-os`](https://github.com/Itsme23476/jarvis-os).
Rebranded as SuperMaks, moved from ElevenLabs to Fish Audio, given a new HUD, and
extended with the Mac bridge. Released under the MIT License; see [LICENSE](LICENSE).
