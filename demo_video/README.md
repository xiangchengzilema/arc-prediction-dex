# Pythia demo video pipeline

Generates a 2:39 hackathon submission video automatically:
slides + real screen recordings + ElevenLabs voice + subtitles.

## Output

Final video: `output/pythia_demo.mp4` (1920x1080, 30fps, ~35MB)
Submitted to Agora Hackathon as: https://youtu.be/d-wqUb86cqU

## Pipeline

```
script.py            ← 9-slide spec + narration text
01_render_slides.py  → slides/*.png       (Pillow renders the PPT-style slides)
02_capture_screens.py→ frames/*/f_*.png   (Selenium captures the live app at 15fps)
03_make_audio.py     → audio/*.mp3        (ElevenLabs Adam voice, en-US-AndrewMultilingualNeural fallback)
04_compose_video.py  → output/pythia_demo.mp4
```

## Run it

```bash
# 0. Make sure the local Flask app is up
python ../app.py &

# 1. Render slides
python 01_render_slides.py

# 2. Capture screen recordings (Selenium opens a Chrome window)
python 02_capture_screens.py

# 3. Generate voice
#    With ElevenLabs (premium):
export ELEVENLABS_API_KEY="sk_xxxxxxxx"
python 03_make_audio.py
#    Or fallback to free Microsoft edge-tts (no key needed):
unset ELEVENLABS_API_KEY
python 03_make_audio.py

# 4. Compose the video (~10–15 minutes on a laptop)
python 04_compose_video.py
```

## Editing the script

`script.py` defines 9 slides with kinds:

- `cover` — title page with brand mark
- `demo` — left-side screen recording + right-side bullets
- `three_cols` — three architecture cards
- `honesty` — what's real vs simulated, two-column
- `roadmap` — 5-phase horizontal timeline

Each slide carries `voice` (the narration) and `seconds` (a hint, but actual
length is driven by audio).

## Voice options

ElevenLabs voice IDs (set via `ELEVENLABS_VOICE` env var):

- `pNInz6obpgDQGcFmaJgB` — Adam, deep & calm (default)
- `onwK4e9ZLuTAKqWW03F9` — Daniel, British
- `IKne3meq5aSn9XLyUdCD` — Charlie, younger & energetic

## Generated artifacts (gitignored)

- `frames/` — ~130 MB of PNG screenshots
- `audio/` — 2.5 MB of MP3 narration
- `slides/` — 700 KB of slide PNGs
- `output/` — 35 MB final mp4

Only the scripts under this directory are committed.
