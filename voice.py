"""
Speech in and out, via Fish Audio.

Both directions run on the same provider:
  speak()      → POST https://api.fish.audio/v1/tts   → mp3 bytes
  transcribe() → POST https://api.fish.audio/v1/asr   → transcript

The key stays here, server-side. The browser asks /api/speak for audio and gets
mp3 bytes back — it never sees the key, so nothing leaks into devtools, the page
source, or a screen recording.

Two different identifiers are in play and they are not interchangeable:

  FISH_AUDIO_MODEL     the synthesis engine   (s2.1-pro-free)
  FISH_AUDIO_VOICE_ID  the voice to speak in  (a reference_id from fish.audio)

The model goes in a `model:` HTTP header. The voice goes in the JSON body as
`reference_id`. Sending one in place of the other fails in confusing ways.
"""
import json
import os
import urllib.error
import urllib.request

API_TTS = "https://api.fish.audio/v1/tts"
API_ASR = "https://api.fish.audio/v1/asr"

# The free tier of the current-generation model. Also valid: s2.1-pro, s2-pro, s1.
DEFAULT_MODEL = "s2.1-pro-free"
DEFAULT_VOICE = "612b878b113047d9a770c069c8b4fdfe"

# Fish Audio caps a single synthesis request; long answers are trimmed rather
# than rejected. The HUD also speaks sentence-by-sentence, so this is a backstop.
MAX_TTS_CHARS = 2500


def api_key():
    return (os.environ.get("FISH_AUDIO_API_KEY") or "").strip()


def available():
    return bool(api_key())


def model():
    return (os.environ.get("FISH_AUDIO_MODEL") or "").strip() or DEFAULT_MODEL


def voice_id():
    return (os.environ.get("FISH_AUDIO_VOICE_ID") or "").strip() or DEFAULT_VOICE


def provider():
    return "fish" if available() else "browser"


def _auth():
    return {"authorization": f"Bearer {api_key()}"}


def _http_error(e):
    """Fish Audio returns a JSON body on failure. Surface it instead of '500'."""
    try:
        body = e.read().decode("utf-8", "replace")[:300]
    except Exception:                                     # noqa: BLE001
        body = ""
    try:
        parsed = json.loads(body)
        body = parsed.get("message") or parsed.get("detail") or body
    except Exception:                                     # noqa: BLE001
        pass
    hint = {
        401: "check FISH_AUDIO_API_KEY",
        402: "fish.audio credit exhausted",
        422: "check FISH_AUDIO_VOICE_ID — it must be a reference_id, not a model name",
        503: "fish.audio is overloaded, retry shortly",
    }.get(e.code, "")
    return RuntimeError(f"fish.audio {e.code}: {body or e.reason}"
                        + (f" ({hint})" if hint else ""))


def speak(text):
    """Returns mp3 bytes, or raises. Caller decides what to do on failure."""
    if not available():
        raise RuntimeError("no FISH_AUDIO_API_KEY")
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")

    payload = {
        "text": text[:MAX_TTS_CHARS],
        "reference_id": voice_id(),
        "format": "mp3",
        "mp3_bitrate": 128,
        # A little variation. Pushed higher it wanders; flat at 0.
        "temperature": 0.7,
        "top_p": 0.7,
    }
    req = urllib.request.Request(
        API_TTS,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "audio/mpeg",
            "model": model(),
            **_auth(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise _http_error(e) from None


def transcribe(audio, mime="audio/webm"):
    """Speech to text via Fish Audio ASR. Returns the transcript, or raises.

    Used instead of the browser's Web Speech API because that one only exists in
    Chrome, is a silently-failing stub in Brave, and ships your audio to Google.
    This keeps listening on the same provider as speaking.

    The endpoint takes multipart/form-data with the audio under the field name
    `audio`. JSON is explicitly not accepted.
    """
    if not available():
        raise RuntimeError("no FISH_AUDIO_API_KEY")
    if not audio:
        raise ValueError("empty audio")

    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4",
           "audio/mpeg": "mp3", "audio/wav": "wav"}.get(mime.split(";")[0], "webm")
    boundary = "----supermaks" + os.urandom(8).hex()
    b = boundary.encode()

    body = b"".join([
        b"--", b, b"\r\n",
        f'Content-Disposition: form-data; name="audio"; filename="turn.{ext}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        audio, b"\r\n",
        b"--", b, b"\r\n",
        b'Content-Disposition: form-data; name="ignore_timestamps"\r\n\r\ntrue\r\n',
        b"--", b, b"--\r\n",
    ])

    req = urllib.request.Request(
        API_ASR, data=body, method="POST",
        headers={"content-type": f"multipart/form-data; boundary={boundary}", **_auth()},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return (json.loads(r.read()).get("text") or "").strip()
    except urllib.error.HTTPError as e:
        raise _http_error(e) from None


def check():
    """One cheap round trip, for `/voice` and the boot banner. Never raises."""
    if not available():
        return {"ok": False, "detail": "no FISH_AUDIO_API_KEY — browser speech in use"}
    try:
        n = len(speak("Voice check."))
        return {"ok": True, "detail": f"fish.audio {model()} · voice {voice_id()[:8]}… · {n // 1024}KB"}
    except Exception as e:                                # noqa: BLE001
        return {"ok": False, "detail": str(e)[:200]}
