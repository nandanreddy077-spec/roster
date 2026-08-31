#!/usr/bin/env python3
"""Pre-render the landing page's call-replay audio with xAI TTS.

Why pre-render instead of calling xAI live from the page:
  * the browser would need XAI_API_KEY, which must never ship to a client;
  * the realtime voice-agent API (xai_voice_adapter) is keyed to an inbound
    SIP call_id, so it cannot synthesise arbitrary marketing lines at all;
  * a public landing page would otherwise pay per visitor, and be a free
    TTS endpoint for anyone who found it.

Run once, and again whenever the dialogue below changes:

    XAI_API_KEY=... python3 scripts/build_demo_voice.py

Writes agent/static/voice/*.mp3 plus manifest.json.

Sounding real is mostly in the SCRIPT, not the model. Each line has:
  * `display` — the clean sentence shown in the transcript on the page;
  * `speak`   — what's synthesised: contractions and one or two `[pause]`s
                for natural rhythm. NO `<soft>` / `[whisper]` / `[breath]`
                tags — they make xAI drop to a breathy near-whisper, which
                reads as timid, not warm. A real receptionist is clear,
                upbeat and in control at a normal speaking volume.
Both sides speak at normal speed (1.0). Numbers and times are written out
longhand so `text_normalization` never has to guess.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.x.ai/v1/tts"
OUT = Path(__file__).resolve().parent.parent / "static" / "voice"

# xAI publishes 28 voices (GET /v1/tts/voices). A landing-page dropdown with
# 28 entries is worse than six good ones, so this is a curated spread across
# both genders with `eve` first -- the same voice DEFAULT_VOICE gives real
# calls. Swap these and re-run to change what the picker offers.
AI_VOICES = ["eve", "ara", "aurora", "atlas", "orion", "rex"]
VOICE_GENDER = {
    "eve": "female",
    "ara": "female",
    "aurora": "female",
    "atlas": "male",
    "orion": "male",
    "rex": "male",
    "cosmo": "male",
    "iris": "female",
}
# Two caller voices, one per gender. The page plays whichever contrasts with
# the chosen employee voice, so the two speakers never sound like one person.
CALLER_VOICES = {"male": "cosmo", "female": "iris"}

AI_SPEED = 1.0
CALLER_SPEED = 1.0

# (role, display, speak)
CALLS = {
    "leak": [
        (
            "caller",
            "Hi, my water heater just burst — there's water all over my garage floor. Is there any way you can get someone out tonight?",
            "Hi, my water heater just burst, there's water all over my garage floor. Is there any way you can get someone out tonight?",
        ),
        (
            "ai",
            "Oh no, I'm sorry to hear that. Let's get you taken care of. I'm looking at your account now, and I see we replaced your pressure valve back in March, so that's still under warranty. I have a technician, Dave, available between 7:30 and 9 tonight. Would you like me to hold that window for you?",
            "Oh no, I'm sorry to hear that. Let's get you taken care of. [pause] I'm looking at your account now, and I see we replaced your pressure valve back in March, so that's still under warranty. I have a technician, Dave, available between seven thirty and nine tonight. Would you like me to hold that window for you?",
        ),
        (
            "caller",
            "Yes, please — that would be a huge help.",
            "Yes, please, that would be a huge help.",
        ),
        (
            "ai",
            "You're all set. Dave will text you when he's about 15 minutes out. If you can, try to keep the area around the heater clear, and we'll take good care of you tonight.",
            "You're all set. Dave will text you when he's about 15 minutes out. [pause] If you can, try to keep the area around the heater clear, and we'll take good care of you tonight.",
        ),
    ],
    "quote": [
        (
            "ai",
            "Hi Marcus, this is Roster calling on behalf of Kestrel. I'm following up on the estimate we sent for the rooftop unit — that was for $4,200. I wanted to check in and see if you had any questions.",
            "Hi Marcus, this is Roster calling on behalf of Kestrel. I'm following up on the estimate we sent for the rooftop unit, that was for forty-two hundred dollars. I wanted to check in and see if you had any questions.",
        ),
        (
            "caller",
            "Yeah — honestly, it's a bit more than I budgeted for this quarter.",
            "Yeah, honestly, it's a bit more than I budgeted for this quarter.",
        ),
        (
            "ai",
            "I completely understand. We can split that into three monthly payments at no interest, and I can lock in this quarter's pricing so nothing changes. Would you like me to send that over?",
            "I completely understand. We can split that into three monthly payments at no interest, and I can lock in this quarter's pricing so nothing changes. [pause] Would you like me to send that over?",
        ),
        (
            "caller",
            "Yeah, that works. Go ahead and send it.",
            "Yeah, that works. Go ahead and send it.",
        ),
    ],
    "club": [
        (
            "ai",
            "Hi Janet, this is Roster with Kestrel. Your Comfort Club plan comes up for renewal on the 14th, and you're also due for your fall tune-up. Would you like me to get that scheduled while I have you?",
            "Hi Janet, this is Roster with Kestrel. Your Comfort Club plan comes up for renewal on the fourteenth, and you're also due for your fall tune up. Would you like me to get that scheduled while I have you?",
        ),
        (
            "caller",
            "Sure. Is the card you have on file still good?",
            "Sure. Is the card you have on file still good?",
        ),
        (
            "ai",
            "It looks like it expired last month. I can text you a secure link to update it — it only takes about ten seconds.",
            "It looks like it expired last month. I can text you a secure link to update it, it only takes about ten seconds.",
        ),
        (
            "caller",
            "Perfect. Go ahead and send it.",
            "Perfect. Go ahead and send it.",
        ),
    ],
}


def synth(text: str, voice: str, speed: float, key: str) -> tuple[bytes, float]:
    """One line of speech. Returns (mp3 bytes, duration seconds).

    with_timestamps gives us the real duration, so the transcript highlight
    lines up with the audio exactly instead of being guessed from word count.
    """
    body = json.dumps(
        {
            "text": text,
            "voice_id": voice,
            "language": "en",
            "speed": speed,
            "with_timestamps": True,
            "text_normalization": True,
            "output_format": {"codec": "mp3", "sample_rate": 24000, "bit_rate": 96000},
        }
    ).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.loads(r.read().decode())
    return base64.b64decode(payload["audio"]), float(payload.get("duration") or 0.0)


def main() -> int:
    key = os.environ.get("XAI_API_KEY")
    if not key:
        print("XAI_API_KEY is not set. Export it and re-run.", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "caller_voices": CALLER_VOICES,
        "ai_voices": AI_VOICES,
        "gender": VOICE_GENDER,
        "calls": {},
    }
    chars = 0

    for call, lines in CALLS.items():
        entries = []
        for i, (role, display, speak) in enumerate(lines):
            is_ai = role == "ai"
            voices = AI_VOICES if is_ai else sorted(set(CALLER_VOICES.values()))
            speed = AI_SPEED if is_ai else CALLER_SPEED
            files, dur = {}, 0.0
            for v in voices:
                name = f"{call}-{i}-{v}.mp3"
                audio, d = synth(speak, v, speed, key)
                (OUT / name).write_bytes(audio)
                files[v] = f"/static/voice/{name}"
                dur = max(dur, d)
                chars += len(speak)
                print(f"  {name}  {d:5.2f}s  {len(audio) // 1024}KB")
            entries.append({"role": role, "text": display, "dur": round(dur, 2), "files": files})
        manifest["calls"][call] = entries
        print(f"{call}: {len(entries)} lines")

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {OUT}/manifest.json")
    print(f"~{chars} characters synthesised  (~${chars / 1_000_000 * 4.20:.4f} at $4.20/M)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
