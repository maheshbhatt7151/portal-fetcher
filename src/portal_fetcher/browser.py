"""Playwright browser lifecycle helper."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

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
