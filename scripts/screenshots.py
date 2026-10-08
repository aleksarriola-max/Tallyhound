"""Re-make the README screenshots and the demo GIF from a running copy of the app.

    python -m streamlit run app.py --server.port 8599        # in another terminal, with a fresh state folder
    python scripts/make_example_exports.py
    python scripts/screenshots.py [--url http://localhost:8599] [--out docs]

Needs Playwright with Chromium (pip install playwright && playwright install chromium) and ffmpeg for the GIF.
Everything shown is fictional: the sample company and docs/example-exports.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
EXPORTS = sorted(str(p) for p in (ROOT / "docs" / "example-exports").iterdir())
VIEW = {"width": 1280, "height": 800}


def settle(pg: Page, ms: int = 900) -> None:
    """Wait until Streamlit has finished rerunning, then a little longer for charts to draw."""
    pg.wait_for_timeout(250)
    try:
        pg.wait_for_selector("[data-testid='stStatusWidget']", state="detached", timeout=20000)
    except Exception:
        pass
    pg.wait_for_timeout(ms)


def nav(pg: Page, name: str) -> None:
    pg.locator("section[data-testid='stSidebar']").get_by_text(name, exact=True).click()
    settle(pg)


def tab(pg: Page, name: str) -> None:
    pg.get_by_role("tab", name=name).click()
    settle(pg)


def upload_and_run(pg: Page, slow: int = 0) -> None:
    pg.get_by_role("button", name="Check new files").click()
    settle(pg, 700 + slow)
    pg.locator("input[type=file]").first.set_input_files(EXPORTS)
    settle(pg, 1500 + slow)
    dlg = pg.locator("[data-testid=stDialog]")
    for _ in range(4):                                   # one Save per file whose columns need matching
        btn = dlg.get_by_role("button", name="Save column matching")
        if not btn.count():
            break
        btn.first.scroll_into_view_if_needed()
        pg.wait_for_timeout(400 + slow)
        btn.first.click()
        settle(pg, 600 + slow)
    run = dlg.get_by_role("button", name="Run", exact=True)
    run.scroll_into_view_if_needed()
    pg.wait_for_timeout(500 + slow)
    run.click()
    for _ in range(60):                                  # the rules engine takes a few seconds
        settle(pg, 500)
        if pg.get_by_role("button", name="Go to Review").count() and pg.get_by_role("button", name="Go to Review").is_enabled():
            break
        if "Cases to review" in pg.content() and "Check new files" in pg.content() and not pg.get_by_text("Running").count():
            break
    settle(pg, 800 + slow)


def stills(pg: Page, out: Path) -> None:
    pg.goto(pg.url.split("?")[0], wait_until="networkidle")
    settle(pg, 2500)
    pg.screenshot(path=str(out / "home.png"))
    nav(pg, "Review")
    pg.screenshot(path=str(out / "review.png"))
    tab(pg, "Payment run")
    pg.screenshot(path=str(out / "payment-gate.png"))
    nav(pg, "Reports")
    pg.screenshot(path=str(out / "reports.png"))
    tab(pg, "Patterns")
    pg.screenshot(path=str(out / "patterns.png"))
    nav(pg, "How we know it's right")
    pg.screenshot(path=str(out / "scorecard.png"))
    tab(pg, "Guardrails")
    head = pg.get_by_text("Try to fool the quote check")
    if head.count():
        head.first.evaluate("e => e.scrollIntoView({block: 'start'})")
        pg.wait_for_timeout(800)
    pg.screenshot(path=str(out / "tamper.png"))
    nav(pg, "Home")
    pg.get_by_role("button", name="Check new files").click()
    settle(pg, 700)
    pg.locator("input[type=file]").first.set_input_files(EXPORTS)
    settle(pg, 2000)
    pg.screenshot(path=str(out / "import.png"))


def demo(browser, url: str, out: Path) -> None:
    tmp = Path(tempfile.mkdtemp())
    ctx = browser.new_context(viewport=VIEW, record_video_dir=str(tmp), record_video_size=VIEW)
    pg = ctx.new_page()
    pg.goto(url, wait_until="networkidle")
    settle(pg, 2500)
    upload_and_run(pg, slow=500)
    nav(pg, "Review")
    pg.wait_for_timeout(1200)
    approve = pg.locator("[class*='st-key-appr_'] button").first      # one case, not "Approve all confirmed"
    if approve.count():
        approve.click()
        settle(pg, 1500)
    nav(pg, "Reports")
    tab(pg, "Patterns")
    pg.mouse.wheel(0, 300)
    pg.wait_for_timeout(2500)
    video = pg.video.path() if pg.video else None
    ctx.close()
    if video and shutil.which("ffmpeg"):
        palette = tmp / "palette.png"
        filt = "fps=8,scale=960:-1:flags=lanczos"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2", "-i", video, "-vf", f"{filt},palettegen=max_colors=96",
                        str(palette)], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2", "-i", video, "-i", str(palette), "-lavfi",
                        f"{filt} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4", str(out / "demo.gif")], check=True)
    shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8599/")
    ap.add_argument("--out", default=str(ROOT / "docs"))
    ap.add_argument("--no-gif", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    with sync_playwright() as p:
        b = p.chromium.launch()
        if not a.no_gif:
            demo(b, a.url, out)
        pg = b.new_page(viewport=VIEW)
        pg.goto(a.url, wait_until="networkidle")
        stills(pg, out)
        b.close()
    print(f"Wrote screenshots{'' if a.no_gif else ' and demo.gif'} to {out}")


if __name__ == "__main__":
    main()
