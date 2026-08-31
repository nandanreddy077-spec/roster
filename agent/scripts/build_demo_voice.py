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
  * `speak`   — what's actually synthesised: contractions, the way people
                really trail off, and xAI speech tags ([pause], [breath],
                <soft>...</soft>). Punctuation does most of the pacing work
                per xAI's own guidance; tags are used sparingly on top.
The employee speaks a touch slower (calm, in control); a stressed caller
speaks a touch faster. `text_normalization` is a safety net — numbers and
times are already written out longhand in `speak`.
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

AI_SPEED = 0.96
CALLER_SPEED = 1.06

# (role, display, speak)
CALLS = {
    "leak": [
        (
            "caller",
            "Yeah, hi — my water heater just burst. There's water all over the garage floor. Is there any way someone can come out tonight?",
            "[breath] Yeah, hi, um... my water heater just burst. There's water all over the garage floor. Is there any way, like, someone can come out tonight?",
        ),
        (
            "ai",
            "Oh no. Okay — let's get someone out to you. I'm looking at your account now, and it looks like we replaced that pressure valve back in March, so that's still under warranty. I've got Dave free between 7:30 and 9 tonight. Want me to hold that for you?",
            "Oh no. Okay — <soft>let's get someone out to you.</soft> [pause] I'm looking at your account right now... and it looks like we replaced that pressure valve back in March, so that's still under warranty. [pause] I've got Dave free between seven thirty and nine tonight. Want me to hold that window for you?",
        ),
        (
            "caller",
            "Yes. Please. That would be amazing.",
            "Yes. [breath] Please. That would be amazing.",
        ),
        (
            "ai",
            "Done. He'll text you when he's about 15 minutes out. Try to keep the area clear if you can — and hang in there, okay?",
            "Okay, you're all set. He'll text you when he's about fifteen minutes out. [pause] Try to keep that area clear if you can — and hang in there, alright?",
        ),
    ],
    "quote": [
        (
            "ai",
            "Hey Marcus, it's Roster calling for Kestrel — just circling back on that rooftop unit estimate, the $4,200. No pressure at all, just wondering where your head's at on it.",
            "Hey Marcus, it's Roster calling for Kestrel — just circling back on that rooftop unit estimate, the forty-two hundred. [pause] No pressure at all... just wondering where your head's at on it.",
        ),
        (
            "caller",
            "Yeah, honestly? It's a bit more than I've got room for this quarter.",
            "Yeah, honestly? [breath] It's a bit more than I've got room for this quarter.",
        ),
        (
            "ai",
            "Totally get it. We can actually split that into three payments, no interest — and I can lock in this quarter's pricing so it doesn't creep up on you. Want me to send that over?",
            "Totally get it. [pause] So — we can actually split that into three payments, no interest. And I can lock in this quarter's pricing so it doesn't creep up on you later. Want me to send that over?",
        ),
        (
            "caller",
            "Yeah. Yeah, do that.",
            "Yeah. [pause] Yeah, do that.",
        ),
    ],
    "club": [
        (
            "ai",
            "Hi Janet, it's Roster for Kestrel — your Comfort Club plan comes up for renewal on the 14th, and you're actually due a fall tune-up. Want me to get that on the books while I've got you?",
            "Hi Janet, it's Roster calling for Kestrel — so your Comfort Club plan comes up for renewal on the fourteenth, and you're actually due a fall tune-up. Want me to just get that on the books while I've got you?",
        ),
        (
            "caller",
            "Sure. Is the card on file still good?",
            "Sure. [pause] Is the card you've got on file still good?",
        ),
        (
            "ai",
            "Looks like it expired last month, actually. I can text you a quick secure link to update it — takes maybe ten seconds.",
            "Looks like it expired last month, actually. [pause] I can text you a quick, secure link to update it — takes maybe ten seconds.",
        ),
        (
            "caller",
            "Oh — yeah, perfect. Send it over.",
            "Oh — [breath] yeah, perfect. Send it over.",
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
