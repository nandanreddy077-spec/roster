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
  * `speak`   — what's synthesised.

The caller lines already sound human because they're SHORT — a sentence or
two, one idea each. The employee lines used to sound robotic because they
were long, comma-spliced paragraphs ("I'm looking at your account now, and I
see we replaced ..., so that's ..., and I have ...") — TTS reads a long
sentence flat. So the employee lines are now written the same way a person
actually talks: short sentences, one thought each, a real opener ("Oh no.
Okay."), contractions, and a `[pause]` only at the genuine beats. NO
`<soft>` / `[whisper]` / `[breath]` tags — they drop xAI to a breathy
near-whisper.
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
            "Oh no. Okay — let's get someone out to you tonight. I'm pulling up your account now. Looks like we replaced your pressure valve back in March, so that's still under warranty. I've got a tech, Dave, who can be there between 7:30 and 9. Want me to hold that for you?",
            "Oh no. Okay. [pause] Let's get someone out to you tonight. I'm pulling up your account now. [pause] Looks like we replaced your pressure valve back in March. So that's still under warranty. I've got a tech, Dave. He can be there between seven thirty and nine. [pause] Want me to hold that for you?",
        ),
        (
            "caller",
            "Yes, please — that would be a huge help.",
            "Yes, please, that would be a huge help.",
        ),
        (
            "ai",
            "You're all set. Dave will text you when he's about 15 minutes out. Try to keep the area around the heater clear if you can. Hang in there — we've got you.",
            "You're all set. Dave will text you when he's about 15 minutes out. [pause] Try to keep the area around the heater clear if you can. Hang in there. We've got you.",
        ),
    ],
    "quote": [
        (
            "ai",
            "Hi Marcus, it's Roster calling for Kestrel. I'm following up on that rooftop unit estimate — the $4,200. Just wanted to see if you had any questions.",
            "Hi Marcus, it's Roster calling for Kestrel. [pause] I'm following up on that rooftop unit estimate. The one for forty-two hundred dollars. [pause] Just wanted to see if you had any questions.",
        ),
        (
            "caller",
            "Yeah — honestly, it's a bit more than I budgeted for this quarter.",
            "Yeah, honestly, it's a bit more than I budgeted for this quarter.",
        ),
        (
            "ai",
            "Totally understand. We can split it into three monthly payments, no interest. And I'll lock in this quarter's pricing so nothing changes. Want me to send that over?",
            "Totally understand. [pause] We can split it into three monthly payments, no interest. And I'll lock in this quarter's pricing, so nothing changes. [pause] Want me to send that over?",
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
            "Hi Janet, it's Roster with Kestrel. Your Comfort Club plan renews on the 14th, and you're due for your fall tune-up. Want me to get that on the schedule while I have you?",
            "Hi Janet, it's Roster with Kestrel. [pause] Your Comfort Club plan renews on the fourteenth. And you're due for your fall tune up. [pause] Want me to get that on the schedule while I have you?",
        ),
        (
            "caller",
            "Sure. Is the card you have on file still good?",
            "Sure. Is the card you have on file still good?",
        ),
        (
            "ai",
            "Looks like it expired last month. I can text you a secure link to update it — takes about ten seconds.",
            "Looks like it expired last month. [pause] I can text you a secure link to update it. Takes about ten seconds.",
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
