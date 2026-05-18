"""Generate one MP3 per slide using ElevenLabs (premium real-human voices).

Reads API key from env var ELEVENLABS_API_KEY — never written to disk.

Voice options (uncomment one):
- Adam   pNInz6obpgDQGcFmaJgB — deep, calm, technical-presentation default
- Daniel onwK4e9ZLuTAKqWW03F9 — British, professional speaker
- Charlie IKne3meq5aSn9XLyUdCD — younger, energetic builder

Falls back to edge-tts AndrewMultilingualNeural if ELEVENLABS_API_KEY missing.
"""
import os
import sys
import asyncio
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from script import SLIDES  # noqa: E402

OUT = ROOT / "audio"
OUT.mkdir(parents=True, exist_ok=True)

# ─── ElevenLabs config ─────────────────────────────────────────────
EL_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
EL_VOICE_ID = os.environ.get("ELEVENLABS_VOICE", "pNInz6obpgDQGcFmaJgB")  # Adam by default
EL_MODEL = "eleven_multilingual_v2"  # most stable, broadly available

# ─── Edge-tts fallback (used if no API key) ────────────────────────
EDGE_VOICE = "en-US-AndrewMultilingualNeural"
EDGE_RATE = "-3%"
EDGE_VOLUME = "+0%"


def synthesize_elevenlabs(text: str, out_path: Path):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{EL_VOICE_ID}"
    headers = {
        "xi-api-key": EL_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text,
        "model_id": EL_MODEL,
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.75,
            "style": 0.10,
            "use_speaker_boost": True,
        },
    }
    r = requests.post(url, headers=headers, json=body, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)


async def synthesize_edge(text: str, out_path: Path):
    import edge_tts
    communicate = edge_tts.Communicate(text=text, voice=EDGE_VOICE,
                                        rate=EDGE_RATE, volume=EDGE_VOLUME)
    await communicate.save(str(out_path))


def main():
    if EL_API_KEY:
        print(f"  Using ElevenLabs · voice={EL_VOICE_ID} · model={EL_MODEL}\n")
        for s in SLIDES:
            out = OUT / f"slide_{s['id']:02d}.mp3"
            print(f"  generating {out.name} ({len(s['voice'])} chars)...", end=" ", flush=True)
            try:
                synthesize_elevenlabs(s["voice"], out)
                size_kb = out.stat().st_size / 1024
                print(f"OK ({size_kb:.1f} KB)")
            except requests.HTTPError as e:
                msg = e.response.text[:200] if e.response is not None else str(e)
                print(f"FAIL — {e}: {msg}")
                return
        print(f"\n  Generated {len(SLIDES)} ElevenLabs clips in {OUT}")
        return

    # Fallback path
    print("  ELEVENLABS_API_KEY not set → falling back to edge-tts\n")

    async def run():
        for s in SLIDES:
            out = OUT / f"slide_{s['id']:02d}.mp3"
            print(f"  generating {out.name} ({len(s['voice'])} chars)...", end=" ", flush=True)
            await synthesize_edge(s["voice"], out)
            size_kb = out.stat().st_size / 1024
            print(f"OK ({size_kb:.1f} KB)")
        print(f"\n  Generated {len(SLIDES)} edge-tts clips in {OUT}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
