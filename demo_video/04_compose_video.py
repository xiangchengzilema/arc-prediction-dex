"""Compose the final demo video — v2 with subtitles + improved demo clips.

Changes vs v1:
- Demo recordings now run at 15fps (4x smoother), captured at full audio length,
  so they no longer loop awkwardly.
- Subtitles overlay at the bottom of every slide, synced word-by-word approx.
- Voice upgraded to en-US-AndrewMultilingualNeural (more natural male).
"""
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import (ImageClip, AudioFileClip, ImageSequenceClip,
                      CompositeVideoClip, concatenate_videoclips,
                      TextClip)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from script import SLIDES  # noqa: E402

SLIDES_DIR = ROOT / "slides"
FRAMES_DIR = ROOT / "frames"
AUDIO_DIR = ROOT / "audio"
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1920, 1080
FPS = 30
CAPTURE_FPS = 15   # frames now captured at 15 fps for smooth motion

# Demo slide screen-recording inset position (matches render_demo placeholder)
DEMO_INSET = {"x": 110, "y": 320, "w": 1100, "h": 690}

# Subtitle position (bottom band)
SUBTITLE_Y = 920
SUBTITLE_FONT_PATH = "C:/Windows/Fonts/arialbd.ttf"
SUBTITLE_FONT_SIZE = 38

# Approx words-per-second for split sizing (Andrew at -3%: ~2.6 wps)
WPS = 2.6


def load_clip_frames(name: str) -> list:
    folder = FRAMES_DIR / name
    if not folder.exists():
        return []
    return sorted([str(p) for p in folder.glob("f_*.png")])


def fit_into_box(image_path: str, box_w: int, box_h: int) -> Image.Image:
    """Resize image to fit inside (box_w, box_h) keeping aspect ratio."""
    img = Image.open(image_path).convert("RGB")
    iw, ih = img.size
    scale = min(box_w / iw, box_h / ih)
    new_w = int(iw * scale)
    new_h = int(ih * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (box_w, box_h), (0, 0, 0))
    canvas.paste(img, ((box_w - new_w) // 2, (box_h - new_h) // 2))
    return canvas


def split_voice_to_subtitles(voice: str, total_duration: float) -> list:
    """Split voice text into ~7-10 word chunks and time-align across duration.

    Returns list of (start_sec, end_sec, text).
    """
    # Split on punctuation that ends a phrase
    import re
    # Replace dashes/commas with spaces; keep period boundaries
    sentences = re.split(r'(?<=[\.\!\?])\s+', voice.strip())
    chunks = []
    for s in sentences:
        words = s.split()
        # Group into chunks of 6-9 words
        i = 0
        while i < len(words):
            target = 8 if len(words) - i >= 8 else len(words) - i
            # Try to break at a comma if any
            chunk_words = words[i:i + target]
            chunks.append(" ".join(chunk_words).strip())
            i += target
    # Filter empty
    chunks = [c for c in chunks if c]
    if not chunks:
        return []

    # Allocate time proportional to char length
    total_chars = sum(len(c) for c in chunks)
    cursor = 0.0
    out = []
    for c in chunks:
        share = len(c) / total_chars
        dur = total_duration * share
        out.append((cursor, cursor + dur, c))
        cursor += dur
    return out


def make_subtitle_image(text: str, max_width: int = 1600) -> Image.Image:
    """Render subtitle as RGBA PNG with shadow + bold white text on translucent strip."""
    fnt = ImageFont.truetype(SUBTITLE_FONT_PATH, SUBTITLE_FONT_SIZE)
    # Wrap into 2 lines max
    words = text.split()
    lines = []
    line = ""
    for w in words:
        cand = (line + " " + w).strip()
        bbox = fnt.getbbox(cand)
        if (bbox[2] - bbox[0]) > max_width and line:
            lines.append(line)
            line = w
        else:
            line = cand
    if line:
        lines.append(line)
    # Compute size
    line_h = SUBTITLE_FONT_SIZE + 14
    total_h = line_h * len(lines) + 30
    # Find widest line
    widest = 0
    for ln in lines:
        bbox = fnt.getbbox(ln)
        widest = max(widest, bbox[2] - bbox[0])
    pad_x = 32
    pad_y = 16
    img_w = widest + 2 * pad_x
    img_h = total_h + pad_y

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Translucent dark strip background
    d.rounded_rectangle((0, 0, img_w, img_h), radius=8, fill=(0, 0, 0, 175))
    # Text — drop shadow then white
    yy = pad_y // 2
    for ln in lines:
        bbox = fnt.getbbox(ln)
        lw = bbox[2] - bbox[0]
        x = (img_w - lw) // 2
        # shadow
        d.text((x + 2, yy + 2), ln, fill=(0, 0, 0, 220), font=fnt)
        d.text((x, yy), ln, fill=(255, 255, 255, 255), font=fnt)
        yy += line_h
    return img


def build_subtitle_clips(voice: str, total_duration: float):
    """Return list of ImageClips for subtitles."""
    chunks = split_voice_to_subtitles(voice, total_duration)
    clips = []
    for start, end, text in chunks:
        if not text.strip():
            continue
        img = make_subtitle_image(text)
        arr = np.array(img)
        clip = ImageClip(arr, transparent=True, duration=end - start)
        x = (W - img.size[0]) // 2
        clip = clip.with_position((x, SUBTITLE_Y)).with_start(start)
        clips.append(clip)
    return clips


def build_demo_visual(slide, total_dur):
    """Slide PNG + screen-recording inset, no subtitles yet."""
    slide_path = SLIDES_DIR / f"slide_{slide['id']:02d}.png"
    base = ImageClip(str(slide_path)).with_duration(total_dur)

    frames = load_clip_frames(slide["screen_clip"])
    if not frames:
        return base

    # Use captured frames; if shorter than dur, hold the last frame
    inset_imgs = []
    n_target_frames = int(total_dur * CAPTURE_FPS)
    for i in range(n_target_frames):
        if i < len(frames):
            src = frames[i]
        else:
            src = frames[-1]   # hold last frame instead of looping
        fitted = fit_into_box(src, DEMO_INSET["w"], DEMO_INSET["h"])
        inset_imgs.append(np.array(fitted))

    inset_clip = ImageSequenceClip(inset_imgs, fps=CAPTURE_FPS)
    inset_clip = inset_clip.with_position((DEMO_INSET["x"], DEMO_INSET["y"]))
    inset_clip = inset_clip.with_duration(total_dur)

    return CompositeVideoClip([base, inset_clip], size=(W, H))


def build_static_visual(slide, total_dur):
    slide_path = SLIDES_DIR / f"slide_{slide['id']:02d}.png"
    return ImageClip(str(slide_path)).with_duration(total_dur)


def main():
    segments = []
    total_duration = 0.0

    for slide in SLIDES:
        print(f"\n=== Slide {slide['id']} ({slide['kind']}) ===")
        audio_path = AUDIO_DIR / f"slide_{slide['id']:02d}.mp3"
        if not audio_path.exists():
            print(f"  ⚠ missing audio: {audio_path.name}")
            continue
        audio = AudioFileClip(str(audio_path))
        # Tight pacing: slide ends ~0.25s after voice for breathing room.
        # Don't pad to slide["seconds"] — that left dead air on every cut.
        target_dur = audio.duration + 0.25

        if slide["kind"] == "demo":
            visual = build_demo_visual(slide, target_dur)
        else:
            visual = build_static_visual(slide, target_dur)

        # Subtitles — overlay synced to audio
        subs = build_subtitle_clips(slide["voice"], audio.duration)
        if subs:
            visual = CompositeVideoClip([visual] + subs, size=(W, H))
            visual = visual.with_duration(target_dur)

        # Pad audio with a tiny silence (0.25s) to match visual
        sr = 44100
        silence_dur = max(0, target_dur - audio.duration)
        if silence_dur > 0:
            from moviepy.audio.AudioClip import AudioArrayClip
            from moviepy import concatenate_audioclips
            silence = AudioArrayClip(
                np.zeros((int(silence_dur * sr), 2)),
                fps=sr,
            )
            full_audio = concatenate_audioclips([audio, silence])
        else:
            full_audio = audio

        clip = visual.with_audio(full_audio)
        segments.append(clip)
        total_duration += target_dur
        print(f"  dur={target_dur:.1f}s · audio={audio.duration:.1f}s · subs={len(subs)}")

    print(f"\n=== Concatenating {len(segments)} segments → {total_duration:.1f}s total ===")
    final = concatenate_videoclips(segments, method="compose")
    out_path = OUTPUT_DIR / "pythia_demo.mp4"
    final.write_videofile(
        str(out_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        bitrate="5000k",
        threads=4,
        preset="medium",
    )
    print(f"\n  Wrote {out_path} ({out_path.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
