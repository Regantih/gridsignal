"""Scripted screen capture of the demo, as a fallback for a deployed URL.

Starts the dashboard on a spare port, walks the four views with Playwright, drives the whole
failure-to-recovery loop, and writes stills plus a WebM (converted to MP4 when ffmpeg is on
PATH) to docs/media/. No credentials: the app runs keyless and offline, and nothing here reads
an API key.

    pip install -e ".[capture]"
    python -m playwright install chromium
    python scripts/capture_demo.py
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs" / "media"
VIEWPORT = {"width": 1600, "height": 1000}
# Long enough to read on screen, short enough to keep the clip under a couple of minutes.
BEAT = 2.5


def to_mp4(webm: Path, mp4: Path) -> None:
    """Playwright records WebM only; most submission forms want MP4. Skipped without ffmpeg."""
    if shutil.which("ffmpeg") is None:
        print("  (ffmpeg not found, skipping MP4)")
        return
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(webm),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(mp4),
        ],
        check=True,
    )
    print("  demo.mp4")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_dashboard(port: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "app" / "dashboard.py"),
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for(port: int, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"dashboard did not come up on port {port}")


def settle(page: Page, expect: str | None = None) -> None:
    """Streamlit reruns on every click, and the old view stays on screen while it does.

    Waiting for the running indicator to clear is not enough on its own: the rerun may not have
    started yet. Waiting for a heading only the new view renders is, so pass one whenever the
    click changes the page.
    """
    if expect is not None:
        page.get_by_text(expect, exact=False).first.wait_for(timeout=60_000)
    page.wait_for_selector("[data-testid='stStatusWidget']", state="detached", timeout=60_000)
    page.wait_for_timeout(int(BEAT * 1000))


def top(page: Page) -> None:
    """Streamlit scrolls its own main container, so `window.scrollTo` is not enough."""
    page.evaluate(
        "() => { window.scrollTo(0, 0);"
        " document.querySelectorAll('section.main, [data-testid=\"stMain\"]')"
        ".forEach(el => el.scrollTo(0, 0)); }"
    )
    page.wait_for_timeout(400)


def shot(page: Page, name: str, expect: str | None = None) -> None:
    settle(page, expect)
    top(page)
    page.screenshot(path=str(MEDIA / f"{name}.png"), full_page=True)
    print(f"  {name}.png")


def choose(page: Page, label: str) -> None:
    page.get_by_test_id("stSidebar").get_by_text(label, exact=True).click()


def walk(page: Page, base: str) -> None:
    page.goto(base, wait_until="load")
    sidebar = page.get_by_test_id("stSidebar")

    shot(page, "01-control-room-stable", "Fleet map")

    sidebar.get_by_role("button", name="Trigger BAT-042 Failure").click()
    shot(page, "02-control-room-incident", "Human approval required")

    page.get_by_role("button", name="Approve Recovery Plan").click()
    shot(page, "03-control-room-recovered", "Recovery complete")

    choose(page, "Agent Mesh")
    shot(page, "04-agent-mesh", "Agent registry")

    choose(page, "Grid Signals")
    shot(page, "05-grid-signals-insight", "Backtest: GridSignal")

    choose(page, "Member App")
    shot(page, "06-member-app", "Base Power —")

    choose(page, "Control Room")
    settle(page, "Fleet map")
    choose(page, "Scarcity day")
    choose(page, "10,000 devices")
    sidebar.get_by_role("button", name="Trigger BAT-042 Failure").click()
    settle(page, "Human approval required")
    page.get_by_role("button", name="Approve Recovery Plan").click()
    shot(page, "07-scale-scarcity", "Recovery complete")

    sidebar.get_by_role("button", name="Reset Demo").click()
    shot(page, "08-reset", "Commitment covered")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="reuse an already running dashboard")
    args = parser.parse_args()

    MEDIA.mkdir(parents=True, exist_ok=True)
    server = None
    port = args.port
    if not port:
        port = free_port()
        server = start_dashboard(port)
    wait_for(port)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                viewport=VIEWPORT,
                record_video_dir=str(MEDIA / "raw"),
                record_video_size=VIEWPORT,
            )
            page = context.new_page()
            walk(page, f"http://127.0.0.1:{port}")
            video = page.video
            context.close()
            if video is not None:
                shutil.move(video.path(), MEDIA / "demo.webm")
                print("  demo.webm")
                to_mp4(MEDIA / "demo.webm", MEDIA / "demo.mp4")
            browser.close()
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=30)
        shutil.rmtree(MEDIA / "raw", ignore_errors=True)

    print(f"wrote {MEDIA.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
