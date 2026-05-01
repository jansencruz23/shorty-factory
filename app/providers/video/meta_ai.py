"""Playwright-driven adapter for meta.ai video generation.

Selector-driven on purpose. Meta will change their UI; when that happens,
edit META_SELECTORS and the small set of helper functions rather than
rewriting the flow.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from pathlib import Path

from langsmith import traceable
from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    async_playwright,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.exceptions import ProviderSessionExpired, ProviderUIChanged

logger = logging.getLogger(__name__)

META_URL = "https://www.meta.ai"

META_SELECTORS: dict[str, list[str] | str] = {
    "prompt_input": [
        'textarea[placeholder*="Ask" i]',
        'div[contenteditable="true"][role="textbox"]',
        "textarea",
    ],
    "submit_button": [
        'button[aria-label*="Send" i]',
        'button[aria-label*="Submit" i]',
        'button[type="submit"]',
    ],
    "video_mode_toggle": [
        'button:has-text("Video")',
        'button[aria-label*="video" i]',
    ],
    "rendered_video": "video[src]",
    "login_wall": 'a[href*="login"], button:has-text("Log in")',
}

GENERATION_TIMEOUT_MS = 180_000  # 3 min per clip
NAV_TIMEOUT_MS = 30_000


class MetaSessionExpired(ProviderSessionExpired):
    """storage_state.json no longer authenticates — re-run capture_session.py."""

    provider = "meta_ai"


class MetaUIChanged(ProviderUIChanged):
    """A required selector didn't match. Update META_SELECTORS."""

    provider = "meta_ai"


async def _first_visible(page: Page, selectors: list[str], timeout: int = 5000):
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=timeout)
            return loc
        except PWTimeout:
            continue
    raise MetaUIChanged(f"none of {selectors} were visible on page")


async def _ensure_logged_in(page: Page) -> None:
    await page.goto(META_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    await asyncio.sleep(1.5)
    if await page.locator(META_SELECTORS["login_wall"]).first.is_visible():
        raise MetaSessionExpired(
            f"meta.ai shows a login wall. Re-run scripts/capture_session.py to refresh "
            f"{settings.meta_storage_state}."
        )


async def _switch_to_video_mode(page: Page) -> None:
    for sel in META_SELECTORS["video_mode_toggle"]:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                await loc.click()
                await asyncio.sleep(0.5)
                return
        except Exception:
            continue
    # No toggle found — that's expected on the unified "Create" UI.
    # We rely on the prompt prefix ("Create a 5-second cinematic video.")
    # to signal intent. The toggle, when it exists, is just a hint.
    logger.debug("no explicit video-mode toggle; relying on prompt-based intent routing")


async def _submit_prompt(page: Page, prompt: str) -> None:
    input_loc = await _first_visible(page, META_SELECTORS["prompt_input"])
    await input_loc.click()
    # meta.ai's input is a Lexical editor — it re-renders the contenteditable
    # DOM node on input, so Locator.type() races against stale element handles.
    # page.keyboard.type dispatches at the page level (no element handle), so
    # it survives Lexical's re-renders.
    await page.keyboard.type(prompt, delay=random.randint(20, 50))
    await asyncio.sleep(0.4)

    submit_loc = await _first_visible(page, META_SELECTORS["submit_button"], timeout=3000)
    await submit_loc.click()


async def _wait_for_video_and_download(page: Page, dest: Path) -> None:
    video_loc = page.locator(META_SELECTORS["rendered_video"]).last
    try:
        await video_loc.wait_for(state="attached", timeout=GENERATION_TIMEOUT_MS)
    except PWTimeout:
        # Capture what meta.ai is actually showing — rate-limit banner,
        # error toast, queued spinner, etc. — so the failure is debuggable.
        dest.parent.mkdir(parents=True, exist_ok=True)
        shot = dest.with_suffix(".timeout.png")
        try:
            await page.screenshot(path=str(shot), full_page=True)
            logger.error("video timeout — page snapshot saved to %s", shot)
        except Exception as snap_err:
            logger.warning("could not capture timeout screenshot: %s", snap_err)
        raise

    src = await video_loc.get_attribute("src")
    if not src:
        raise MetaUIChanged("rendered <video> has no src attribute")

    response = await page.request.get(src)
    if not response.ok:
        raise MetaUIChanged(f"video fetch failed: HTTP {response.status} for {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await response.body())


# MetaSessionExpired is intentionally NOT in the retry set — retrying won't
# heal a stale storage_state.json; the user has to recapture the session.
# @traceable sits *inside* @retry so each retry attempt is its own span,
# making rate-limit / UI-drift patterns visible in LangSmith.
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=4, max=20),
    retry=retry_if_exception_type((PWTimeout, MetaUIChanged)),
    reraise=True,
)
@traceable(name="meta_ai.generate_clip", run_type="tool")
async def _generate_one(context: BrowserContext, prompt: str, dest: Path) -> None:
    page = await context.new_page()
    try:
        await _ensure_logged_in(page)
        await _switch_to_video_mode(page)
        await _submit_prompt(page, prompt)
        await _wait_for_video_and_download(page, dest)
    finally:
        await page.close()


class MetaAIVideoProvider:
    """VideoProvider adapter using a single Playwright browser context for
    the lifetime of one batch."""

    name = "meta_ai"

    @traceable(name="meta_ai.generate_clips", run_type="tool")
    async def generate_clips(
        self,
        prompts: list[str],
        clip_path_for: Callable[[int], Path],
        progress_cb: Callable[[int], Awaitable[None]] | None = None,
    ) -> list[Path]:
        if not settings.meta_storage_state.exists():
            raise MetaSessionExpired(
                f"{settings.meta_storage_state} not found. "
                f"Run scripts/capture_session.py first."
            )

        out_paths: list[Path] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=settings.playwright_headless)
            context = await browser.new_context(
                storage_state=str(settings.meta_storage_state),
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
                ),
            )
            try:
                for i, prompt in enumerate(prompts):
                    dest = clip_path_for(i)
                    logger.info("generating clip %d/%d -> %s", i + 1, len(prompts), dest)
                    if progress_cb:
                        await progress_cb(i + 1)
                    await _generate_one(context, prompt, dest)
                    out_paths.append(dest)
                    await asyncio.sleep(random.uniform(8, 20))
            finally:
                await context.close()
                await browser.close()

        return out_paths
