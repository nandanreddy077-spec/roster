"""The landing page's call replay plays pre-rendered xAI TTS audio.

These guard the two ways that silently breaks: the app not serving the files,
and the generated manifest drifting out of sync with the transcript copy in
index-v2.html (a copy edit without re-running scripts/build_demo_voice.py).
Both fail as a missing/late-playing line for every visitor, with no error.
"""

import json
import re
from pathlib import Path

import app as app_module
from fastapi.testclient import TestClient

LANDING = Path(app_module.__file__).parent / "landing" / "index-v2.html"
VOICE_DIR = Path(app_module.__file__).parent / "static" / "voice"
MANIFEST = VOICE_DIR / "manifest.json"


def _norm(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)  # strip inline spans
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = re.sub(r"^\s*(Caller|Customer|.*?AI)\s*:\s*", "", text)
    return re.sub(r"\s+", " ", text).strip().strip('"')


def _transcript_lines() -> dict[str, list[str]]:
    """The spoken lines of each scenario, in order, as rendered on the page."""
    html = LANDING.read_text()
    out: dict[str, list[str]] = {}
    for block in re.finditer(
        r'<div class="transcript" data-call="([a-z]+)"[^>]*>(.*?)</div>', html, re.S
    ):
        out[block.group(1)] = re.findall(r'<p class="tline[^"]*">(.*?)</p>', block.group(2), re.S)
    return out


def _transcript_line_counts() -> dict[str, list[str]]:
    return _transcript_lines()


def test_page_declares_three_scenarios():
    counts = _transcript_line_counts()
    assert set(counts) == {"leak", "quote", "club"}, counts
    assert all(len(v) > 0 for v in counts.values()), counts


def test_manifest_matches_the_transcript_copy():
    """A copy edit without re-running the generator would desync audio from
    text — the transcript would highlight lines that have no clip."""
    if not MANIFEST.exists():
        return  # audio not generated in this checkout; the page falls back
    manifest = json.loads(MANIFEST.read_text())
    page = _transcript_line_counts()
    for call, lines in manifest["calls"].items():
        assert call in page, f"manifest has {call!r}, the page does not"
        assert len(lines) == len(page[call]), (
            f"{call}: manifest has {len(lines)} clips, page has {len(page[call])} lines "
            "— re-run scripts/build_demo_voice.py"
        )
        for i, (clip, dom_text) in enumerate(zip(lines, page[call])):
            m = _norm(clip.get("text", ""))
            d = _norm(dom_text)
            assert m == d, (
                f"{call} line {i}: manifest text and page transcript differ.\n"
                f"  manifest: {m}\n  page:     {d}\n"
                "— re-run scripts/build_demo_voice.py after editing the transcript"
            )


def test_every_manifest_clip_exists_on_disk():
    if not MANIFEST.exists():
        return
    manifest = json.loads(MANIFEST.read_text())
    missing = []
    for call, lines in manifest["calls"].items():
        for entry in lines:
            for voice, url in entry["files"].items():
                if not (VOICE_DIR / Path(url).name).exists():
                    missing.append(url)
    assert not missing, f"manifest references files that aren't there: {missing[:5]}"


def test_caller_voice_never_matches_an_employee_voice():
    """The two sides of the call must not be the same voice, or it reads as
    one person talking to themselves."""
    if not MANIFEST.exists():
        return
    manifest = json.loads(MANIFEST.read_text())
    callers = set(manifest["caller_voices"].values())
    assert not callers & set(manifest["ai_voices"]), (
        f"caller voice(s) {callers & set(manifest['ai_voices'])} also offered as an employee voice"
    )


def test_voice_assets_are_served_publicly():
    """They're on the marketing page, so they must not sit behind the
    founder's HTTP Basic gate."""
    if not MANIFEST.exists():
        return
    client = TestClient(app_module.app)
    r = client.get("/static/voice/manifest.json")
    assert r.status_code == 200, r.status_code
    first = json.loads(MANIFEST.read_text())["calls"]["leak"][0]
    url = next(iter(first["files"].values()))
    audio = client.get(url)
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/"), audio.headers["content-type"]
