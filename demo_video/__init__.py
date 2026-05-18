"""Pythia hackathon demo video — 9-slide narrated walkthrough.

Pipeline (run in order):
  1. python demo_video/01_render_slides.py   -> demo_video/slides/*.png
  2. python demo_video/02_capture_screens.py -> demo_video/slides/screen_*.png
  3. python demo_video/03_make_audio.py      -> demo_video/audio/*.mp3
  4. python demo_video/04_compose_video.py   -> demo_video/output/pythia_demo.mp4

Or just:
  python demo_video/make_all.py
"""
