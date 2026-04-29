"""One-time helper: open a headed Chromium, let the user log into meta.ai,
then save cookies + localStorage to settings.meta_storage_state so the
Playwright driver can reuse the session.

Usage:
    uv run python scripts/capture_session.py
"""
from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from app.config import settings


async def main() -> None:
    print("Opening a headed Chromium. Log in to meta.ai in the window that appears.")
    print("When the chat input is visible, return to this terminal and press ENTER.")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto("https://www.meta.ai")

        # Block on user input from the terminal (run via asyncio.to_thread so we
        # don't freeze the event loop).
        await asyncio.to_thread(input, "Press ENTER once you're logged in... ")

        settings.meta_storage_state.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(settings.meta_storage_state))
        print(f"Saved session to {settings.meta_storage_state}")
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
