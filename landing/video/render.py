"""Render promo.html to a 1080x1920 MP4 by stepping its render(t) frame by frame."""

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FPS = 30
MUSIC = ROOT / "assets/music/lofi/795da5a7-b63b-43e9-8f50-61b9e2b1a5f0.mp3"  # CC0 lofi loop
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "clipz-demo.mp4"


def main() -> None:
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception:
            browser = pw.chromium.launch(executable_path="/usr/bin/chromium")
        page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
        page.goto((HERE / "promo.html").as_uri() + "?capture=1")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(500)
        duration = float(page.evaluate("window.DUR"))
        frames = int(duration * FPS)

        fade_out = duration - 1.5
        ffmpeg = subprocess.Popen(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                "-i", str(MUSIC),
                "-filter_complex",
                f"[1:a]atrim=0:{duration},afade=t=in:d=0.6,afade=t=out:st={fade_out}:d=1.5,volume=0.9[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest",
                str(OUT),
            ],
            stdin=subprocess.PIPE,
        )
        assert ffmpeg.stdin is not None
        for f in range(frames):
            page.evaluate(f"render({f / FPS})")
            ffmpeg.stdin.write(page.screenshot(type="jpeg", quality=95))
            if f % 150 == 0:
                print(f"frame {f}/{frames}", flush=True)
        ffmpeg.stdin.close()
        ffmpeg.wait()
        browser.close()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
