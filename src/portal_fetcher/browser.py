"""Playwright browser lifecycle helper."""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Optional

from playwright.async_api import Page, async_playwright


@asynccontextmanager
async def create_browser_page(
    headless: bool = True,
    timeout: int = 30000,
) -> AsyncIterator[Page]:
    """Launch Chromium and yield a single Page, then clean up."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            ignore_https_errors=True,
        )
        context.set_default_timeout(timeout)
        page = await context.new_page()
        try:
            yield page
        finally:
            await context.close()
            await browser.close()


async def start_screencast(
    page: Page,
    on_frame: Callable[[bytes], None],
    quality: int = 60,
    max_width: int = 1280,
    max_height: int = 900,
):
    """Start CDP screencast, calling on_frame(jpeg_bytes) for each frame.

    Returns the CDP session so the caller can stop it later with:
        await cdp.send("Page.stopScreencast")
    """
    cdp = await page.context.new_cdp_session(page)

    def handle_frame(params: dict) -> None:
        frame_bytes = base64.b64decode(params["data"])
        on_frame(frame_bytes)
        # Acknowledge frame for CDP flow control
        asyncio.get_event_loop().create_task(
            cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        )

    cdp.on("Page.screencastFrame", handle_frame)
    await cdp.send("Page.startScreencast", {
        "format": "jpeg",
        "quality": quality,
        "maxWidth": max_width,
        "maxHeight": max_height,
        "everyNthFrame": 2,
    })
    return cdp
