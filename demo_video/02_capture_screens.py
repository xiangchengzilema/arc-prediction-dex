"""Capture 5 short MP4 clips from the live local Pythia app for the demo video.

Each clip is recorded as a sequence of PNG frames captured via Selenium
(taking screenshots at a fixed interval), then assembled into an MP4 by
moviepy in step 4.

Output: demo_video/clips/<name>.mp4 (small, ~0.5-2 MB each)

Requires the local Flask app to be running at http://localhost:5003
"""
import os
import sys
import time
import shutil
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

ROOT = Path(__file__).resolve().parent
BASE_URL = os.environ.get("PYTHIA_BASE", "http://localhost:5003")
OUT_FRAMES = ROOT / "frames"
OUT_CLIPS = ROOT / "clips"

# 1280x800 ≈ 16:10 — fits inside slide layout slot
VIEWPORT = (1280, 800)
FPS = 15   # smooth motion (was 4 — looked choppy)

CLIPS = [
    {
        "name": "home_scroll",
        "duration": 16.0,
        "url": "/",
        "actions": [
            ("wait", 2.0),
            ("scroll_smooth", 600, 4.0),
            ("scroll_smooth", 500, 4.0),
            ("scroll_smooth_top", None, 4.0),
            ("wait", 2.0),
        ],
    },
    {
        "name": "market_detail",
        "duration": 17.0,
        "url": None,  # picked at runtime
        "actions": [
            ("wait", 2.0),
            ("scroll_smooth", 500, 3.5),
            ("scroll_smooth", 400, 3.5),
            ("scroll_smooth", 400, 3.5),
            ("scroll_smooth_top", None, 2.5),
            ("wait", 2.0),
        ],
    },
    {
        "name": "trade",
        "duration": 16.0,
        "url": None,  # filled at runtime — picks the highest-liquidity market
        "actions": [
            ("wait", 1.5),
            ("scroll_smooth", 500, 2.0),
            ("set_user_id", "video_demo_user", 1.5),
            ("type_amount", 5, 2.0),
            ("flash_buy_btn", None, 2.0),
            ("real_click_buy", None, 2.5),     # actually click + wait for toast
            ("nav_to_portfolio_video_user", None, 4.5),  # show new position
        ],
    },
    {
        "name": "agent",
        "duration": 17.0,
        "url": "/agent",
        "actions": [
            ("wait", 3.0),
            ("scroll_smooth", 400, 3.0),
            ("scroll_smooth", 600, 4.0),
            ("scroll_smooth_top", None, 3.0),
            ("wait", 4.0),
        ],
    },
    {
        # Re-purpose "keeper" clip: show home (cards with lifecycle pips) → detail page (4-step lifecycle bar).
        # /api/keeper/status was an ugly JSON page — replaced with the real product visualization.
        "name": "keeper",
        "duration": 19.0,
        "url": "/",
        "actions": [
            ("wait", 2.0),
            ("scroll_smooth", 600, 4.0),
            ("nav_to_detail", None, 1.0),
            ("wait", 3.0),
            ("scroll_smooth", 400, 4.0),
            ("scroll_smooth_top", None, 3.0),
            ("wait", 2.0),
        ],
    },
]


def build_driver():
    o = Options()
    o.add_argument(f"--window-size={VIEWPORT[0]},{VIEWPORT[1]}")
    o.add_argument("--hide-scrollbars")
    o.add_argument("--disable-extensions")
    o.add_argument("--no-sandbox")
    # NOT headless: we want real layout & fonts
    return webdriver.Chrome(options=o)


def smooth_scroll_to(driver, target_y, duration):
    cur = driver.execute_script("return window.pageYOffset;")
    steps = max(1, int(duration * FPS))
    for i in range(steps):
        t = (i + 1) / steps
        eased = 1 - (1 - t) ** 3
        y = int(cur + (target_y - cur) * eased)
        driver.execute_script(f"window.scrollTo(0, {y});")
        time.sleep(1.0 / FPS)


def capture_clip(driver, clip, frame_dir):
    name = clip["name"]
    url = clip.get("url")
    if url:
        driver.get(BASE_URL + url)

    frame_dir.mkdir(parents=True, exist_ok=True)
    for f in frame_dir.glob("*.png"):
        f.unlink()

    captured = [0]

    def shoot():
        path = frame_dir / f"f_{captured[0]:04d}.png"
        driver.save_screenshot(str(path))
        captured[0] += 1

    def wait_with_shots(seconds):
        end = time.time() + seconds
        while time.time() < end:
            shoot()
            time.sleep(1.0 / FPS)

    for action in clip["actions"]:
        name_a, value, *rest = action if len(action) >= 3 else (*action, 0)
        # parse: (kind, value, seconds)
        if len(action) == 3:
            kind, val, secs = action
        else:
            kind, val = action
            secs = 1.0

        if kind == "wait":
            wait_with_shots(val)
        elif kind == "scroll_smooth":
            # smooth scroll by `val` pixels in `secs` seconds, capturing frames
            cur = driver.execute_script("return window.pageYOffset;")
            target = cur + val
            steps = max(1, int(secs * FPS))
            for i in range(steps):
                t = (i + 1) / steps
                eased = 1 - (1 - t) ** 3
                y = int(cur + (target - cur) * eased)
                driver.execute_script(f"window.scrollTo(0, {y});")
                shoot()
                time.sleep(1.0 / FPS)
        elif kind == "scroll_smooth_top":
            cur = driver.execute_script("return window.pageYOffset;")
            steps = max(1, int(secs * FPS))
            for i in range(steps):
                t = (i + 1) / steps
                eased = 1 - (1 - t) ** 3
                y = int(cur * (1 - eased))
                driver.execute_script(f"window.scrollTo(0, {y});")
                shoot()
                time.sleep(1.0 / FPS)
        elif kind == "hover_trade_panel":
            try:
                el = driver.find_element(By.ID, "amount")
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center'});", el)
            except Exception:
                pass
            wait_with_shots(secs)
        elif kind == "type_amount":
            try:
                el = driver.find_element(By.ID, "amount")
                el.clear()
                el.send_keys(str(val))
            except Exception:
                pass
            wait_with_shots(secs)
        elif kind == "flash_buy_btn":
            try:
                btn = driver.find_element(By.ID, "btn-buy")
                driver.execute_script("""
                  const b = arguments[0];
                  b.style.transition = 'box-shadow .3s';
                  b.style.boxShadow = '0 0 30px rgba(124,92,255,0.9)';
                  b.scrollIntoView({block:'center'});
                """, btn)
            except Exception:
                pass
            wait_with_shots(secs)
        elif kind == "nav_to_detail":
            try:
                # Click first market card to navigate to detail page
                card = driver.find_element(By.CSS_SELECTOR, "a.px-market")
                href = card.get_attribute("href")
                driver.get(href)
            except Exception:
                pass
            wait_with_shots(secs)
        elif kind == "set_user_id":
            try:
                el = driver.find_element(By.ID, "user-id")
                el.clear()
                el.send_keys(val)
            except Exception:
                pass
            wait_with_shots(secs)
        elif kind == "real_click_buy":
            try:
                btn = driver.find_element(By.ID, "btn-buy")
                btn.click()
            except Exception:
                pass
            wait_with_shots(secs)
        elif kind == "nav_to_portfolio_video_user":
            try:
                driver.get(BASE_URL + "/portfolio?user=video_demo_user")
            except Exception:
                pass
            wait_with_shots(secs)
        else:
            # default — just wait & shoot
            wait_with_shots(secs if isinstance(secs, (int, float)) else 1.0)

    print(f"  [{name}] {captured[0]} frames captured to {frame_dir}")


def get_first_market_id(driver):
    """Find a market_id by hitting the API. Returns the highest-liquidity OPEN
    market for the trade clip (lowest slippage on small buys)."""
    driver.get(BASE_URL + "/api/markets?status=OPEN")
    body = driver.find_element(By.TAG_NAME, "pre").text
    import json
    d = json.loads(body)
    markets = d.get("markets") or []
    if not markets:
        return None
    # Sort by liquidity desc — highest liquidity first
    markets.sort(key=lambda m: -(m.get("total_liquidity") or 0))
    return markets[0]["market_id"]


def main():
    OUT_FRAMES.mkdir(parents=True, exist_ok=True)
    OUT_CLIPS.mkdir(parents=True, exist_ok=True)

    driver = build_driver()
    try:
        # Get a market id for the detail / trade clips
        mid = get_first_market_id(driver)
        if mid:
            for c in CLIPS:
                if c["name"] in ("market_detail", "trade") and c["url"] is None:
                    c["url"] = f"/market/{mid}"

        for clip in CLIPS:
            print(f"\n=== Capturing {clip['name']} ({clip['duration']}s) ===")
            frame_dir = OUT_FRAMES / clip["name"]
            capture_clip(driver, clip, frame_dir)
    finally:
        driver.quit()

    print("\nDone capturing frames. Step 4 will assemble these into MP4 clips.")


if __name__ == "__main__":
    main()
