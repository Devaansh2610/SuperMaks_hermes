# SUPERMAKS · Hermes Mission Control

A voice-first HUD for [Hermes Agent](https://github.com/NousResearch/hermes-agent),
running on your Mac. One arc reactor on screen, a wake phrase, and a Jarvis-shaped
assistant behind it.

It is a visual interface over your existing Hermes profile — not a second agent
runtime — so it inherits the same tools, skills, memory, browser automation, MCP
servers, and third-party integrations. Hermes drives the machine it is already
on, with whatever it is connected to.

Open `preview.html` (or `ui/index.html`) in a browser to see the interface right
now — with no key and no Hermes, it boots against a mock backend.

---

## Install

Needs Python 3, a Chromium-based browser, and Hermes Agent working first:

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes setup
hermes chat -q "Say online"          # must work before going further
```

Then:

```bash
git clone https://github.com/Devaansh2610/SuperMaks_hermes.git
cd SuperMaks_hermes
./install.sh
```

The installer creates a private `.env`, launches the dashboard, and opens
<http://127.0.0.1:8730>. Add your Fish Audio key to `.env` and restart for
proper speech.

## Voice

**Speech out — Fish Audio.** **Speech in — the browser's own recognizer**, which
is free, local to the page, and needs no key.

```bash
FISH_AUDIO_API_KEY=your_private_key
FISH_AUDIO_MODEL=s2.1-pro-free
FISH_AUDIO_VOICE_ID=612b878b113047d9a770c069c8b4fdfe
```

`MODEL` and `VOICE_ID` are different things sent in different places — the model
as an HTTP header, the voice as `reference_id` in the body. Swapping them
produces a confusing 422.

The key never leaves the server: the browser asks `/api/speak` for audio and gets
mp3 bytes back, so it never appears in devtools or a screen recording. Replies
are synthesised **sentence by sentence** with one chunk prefetched, so speech
starts while the rest is still being generated. Test the whole path with
`/voice`.

## Waking it up

The HUD opens **dormant** — near-black, one breathing mark — on every launch, and
again after `WAKE_IDLE_HOURS` (default 4.5) with no prompt. While dormant it
listens, locally in the browser, for any of several wake phrases:

> **"wake up"** · **"daddy's home"** · **"SuperMaks"** · **"Maks"** ·
> **"hey Maks"** · **"Jarvis"**

Several, because one exact string is a single point of failure — a mishearing or
a bit of background noise and nothing happens. Two fallbacks if the mic still
misses: **tap the dormant screen**, or **long-press Control**.

Waking runs `/briefing`: a live look at whatever your profile is connected to —
mail, calendar, GitHub commits and open PRs, messages — opened with "Welcome
home, sir," and closed with one dry, specific remark about something it actually
saw. Not a generic joke; the whole point is that it was really looking.

A track plays at the same time. By default it **opens in its own YouTube tab**
rather than playing in the page — no embedded player, no external script, and
nothing for a browser autoplay policy to block. The greeting speaks immediately
underneath it rather than waiting for the music to finish.

No audio file is bundled with this repo, and none ever will be — that would mean
redistributing someone else's copyrighted recording.

It also opens whatever you list in `WAKE_TABS` at the same moment — each its
own separate window, sized to `WAKE_WINDOW_SCALE` of the screen so they don't
take it over, whatever you want waiting for you.

```bash
WAKE_SONG_SOURCE=open       # open a YouTube window (default) · local · off
WAKE_SONG_URL=https://...   # the track
WAKE_TABS=https://build.nvidia.com/models,https://www.youtube.com
WAKE_BROWSER_APP=Safari     # what WAKE_TABS (and WAKE_SONG_SOURCE=open) opens in
WAKE_WINDOW_SCALE=0.45      # each wake window as a fraction of the screen
```

Opening `WAKE_TABS`, and the song window in `open` mode, both go through the
local server (AppleScript on Safari, `open -na` on anything else), not
`window.open()` from the page — a voice-triggered wake never carries a user
gesture, and Chrome only gives a `window.open()` call a real new window when
it has one. That's also why each link lands as its own separate window rather
than a tab of the last one.

Set `WAKE_SONG_SOURCE=local` with `WAKE_SONG_LOCAL_PATH` to stream a file you
already own instead; it plays in-page at `WAKE_SONG_VOLUME` and ducks to
`WAKE_SONG_DUCK` while SuperMaks is speaking.

## Talking to it

Once awake, **long-press Control** — 320ms, so a stray tap does nothing — arms
push-to-talk. It stops on whichever comes first: releasing the key, or 220ms of
silence. **Clicking the reactor** starts and stops a continuous conversation.

## The screen

Only the reactor. Everything else is off-canvas behind slim edge handles:
**COMMANDS** left, **TELEMETRY** right, **TRANSCRIPT** bottom — or keys `1`/`2`/`3`,
`Esc` to close. Nothing opens on its own. A single caption line under the reactor
shows what is being said, so the minimal view still keeps you informed.

The reactor is a machined object, not a logo: brushed-metal housing with bolts
and engraved graduations, a 72-tooth ring gear, seven orbiting planetary gears on
carrier arms, reciprocating actuators, a stepper-indexed collar, a ten-winding
coil pack with sliding armature caps, tilting stator vanes, a turbine behind the
plasma, and arc discharge. The coils, spectrum ring and core are driven by real
audio — your microphone while listening, the speech playback while talking — so
it is an instrument rather than a screensaver.

## Command matrix

Buttons run their command immediately. Commands taking a payload stay **armed**,
so the next thing you type or say is sent as `/command payload`.

| Command | Action |
|---|---|
| `/new` | Start a fresh Hermes thread |
| `/briefing` | The wake report, on demand |
| `/browser <task>` | Use Hermes' browser/Chrome tools |
| `/goal <text\|status\|clear>` | Set, read, or clear the standing objective |
| `/background <mission>` | Run a Hermes mission asynchronously |
| `/mission [task]` | Read or add to the mission queue |
| `/personality <tone>` | Change the tone overlay |
| `/profile <fact>` | Add a fact injected into every prompt |
| `/tools`, `/toolsets` | What Hermes can currently reach |
| `/connectors`, `/connect <name>` | How integrations are inherited |
| `/voice` | Test the Fish Audio path end to end |
| `/status` | Runtime, profile and voice state |
| `/commands` | Show everything |

Anything else goes straight to Hermes. **Keyboard:** `⌘K` palette · `⌘↵` transmit ·
`Esc` cancel a run, close the palette, or end the conversation.

## Architecture

```text
ui/            dependency-free HUD — canvas reactor, streaming, voice loop
server.py      local HTTP API and NDJSON streaming
runtime.py     Hermes CLI subprocess and session continuity
commands.py    slash commands and the mission queue
voice.py       Fish Audio speech synthesis
persona.md     the spoken SuperMaks persona
build-preview.py  bundles ui/ into the single-file preview
```

No fonts, no libraries, no CDN — it has to work bound to `127.0.0.1` with the
network unplugged.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FISH_AUDIO_API_KEY` | — | enables server-side speech |
| `FISH_AUDIO_MODEL` | `s2.1-pro-free` | synthesis engine |
| `FISH_AUDIO_VOICE_ID` | `612b878b…` | voice `reference_id` |
| `WAKE_SONG_SOURCE` | `open` | `open`, `local`, or `off` |
| `WAKE_SONG_URL` | The Clash | what `open` opens |
| `WAKE_SONG_VOLUME` / `_DUCK` | `0.35` / `0.10` | local playback level, and level while speaking |
| `WAKE_TABS` | NVIDIA NIM, YouTube | comma-separated links opened on wake; blank for none |
| `WAKE_BROWSER_APP` | `Safari` | the .app macOS opens the wake links in, one separate new window per link |
| `WAKE_WINDOW_SCALE` | `0.45` | each wake window's width/height as a fraction of the screen |
| `WAKE_IDLE_HOURS` | `4.5` | hours of quiet before it goes dormant again |
| `HERMES_PROFILE` | `default` | profile whose tools are inherited |
| `HERMES_CMD` | — | absolute path if `hermes` is not on `PATH` |
| `SUPERMAKS_MODEL` | — | passed to `hermes --model`; use a fast one, latency matters |
| `SUPERMAKS_PORT` | `8730` | dashboard port |
| `SUPERMAKS_PERMISSION` | `normal` | `bypass` lets Hermes run tools unprompted |
| `SUPERMAKS_TIMEOUT` | `120` | seconds of silence before a run is killed |

Old `JARVIS_*` variables are still read as a fallback.

## Security

- The server binds `127.0.0.1`; it is never exposed to the network.
- Every state-changing request needs a per-launch random token, same-origin, a
  valid localhost `Host`, an approved content type, and a bounded body.
- Agent runs are serialized so multiple tabs cannot race the Hermes session.
- Raw prompt logging is off unless `SUPERMAKS_RAW_LOG` is set; opt-in logs are
  created `0600` with symlink protection.
- `.env`, state files and caches are git-ignored.

Hermes runs with your account's reach on this machine. `SUPERMAKS_PERMISSION`
decides whether it asks before using a tool — `normal` is the default for a
reason.

## Troubleshooting

**Hermes not found** — `which hermes`, `hermes doctor`, then set `HERMES_CMD`.

**Wake phrase never fires** — needs a Chromium-based browser; Safari and Firefox
have no continuous `SpeechRecognition`. Control and the Voice button still work.

**Microphone does nothing** — it must be `localhost`, not a LAN IP; a plain-http
LAN origin is not a secure context and the browser blocks the mic outright.

**Replies take forever** — a heavy reasoning model will never feel like a voice
assistant. Set `SUPERMAKS_MODEL` to something faster.

**A newly connected tool is missing** — Hermes caches per session; run `/new`.

## Credits and license

Adapted from [`Itsme23476/jarvis-hermes-dashboard`](https://github.com/Itsme23476/jarvis-hermes-dashboard),
itself inspired by [`Itsme23476/jarvis-os`](https://github.com/Itsme23476/jarvis-os).
Rebranded as SuperMaks, moved from ElevenLabs to Fish Audio, and given a new HUD.
Released under the MIT License; see [LICENSE](LICENSE).
