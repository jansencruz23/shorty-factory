"""Playwright driver for meta.ai video generation.

Selector-driven on purpose. Meta will change their UI; when that happens,
edit `META_SELECTORS` and the small set of helper functions rather than
rewriting the flow.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from pathlib import Path

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    async_playwright,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

META_URL = "https://www.meta.ai"

META_SELECTORS: dict[str, list[str] | str] = {
    "prompt_input": [
        'textarea[placeholder*="Ask" i]',
        'div[contenteditable="true"][role="textbox"]',
        'textarea',
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


class MetaSessionExpired(RuntimeError):
    """storage_state.json no longer authenticates — re-run capture_session.py."""


class MetaUIChanged(RuntimeError):
    """A required selector didn't match. Update META_SELECTORS."""


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
    logger.warning("No explicit video-mode toggle found; proceeding without one.")


async def _submit_prompt(page: Page, prompt: str) -> None:
    input_loc = await _first_visible(page, META_SELECTORS["prompt_input"])
    await input_loc.click()
    await input_loc.type(prompt, delay=random.randint(35, 85))
    await asyncio.sleep(0.4)

    submit_loc = await _first_visible(page, META_SELECTORS["submit_button"], timeout=3000)
    await submit_loc.click()


async def _wait_for_video_and_download(page: Page, dest: Path) -> None:
    video_loc = page.locator(META_SELECTORS["rendered_video"]).last
    await video_loc.wait_for(state="attached", timeout=GENERATION_TIMEOUT_MS)

    src = await video_loc.get_attribute("src")
    if not src:
        raise MetaUIChanged("rendered <video> has no src attribute")

    response = await page.request.get(src)
    if not response.ok:
        raise MetaUIChanged(f"video fetch failed: HTTP {response.status} for {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await response.body())


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=4, max=20),
    retry=retry_if_exception_type((PWTimeout, MetaUIChanged)),
    reraise=True,
)
async def _generate_one(context: BrowserContext, prompt: str, dest: Path) -> None:
    page = await context.new_page()
    try:
        await _ensure_logged_in(page)
        await _switch_to_video_mode(page)
        await _submit_prompt(page, prompt)
        await _wait_for_video_and_download(page, dest)
    finally:
        await page.close()


async def generate_clips(
    prompts: list[str],
    clip_path_for: Callable[[int], Path],
) -> list[Path]:
    """Generate one clip per prompt, sequentially, in a single browser context."""
    if not settings.meta_storage_state.exists():
        raise MetaSessionExpired(
            f"{settings.meta_storage_state} not found. Run scripts/capture_session.py first."
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
                await _generate_one(context, prompt, dest)
                out_paths.append(dest)
                await asyncio.sleep(random.uniform(8, 20))
        finally:
            await context.close()
            await browser.close()

    return out_paths
