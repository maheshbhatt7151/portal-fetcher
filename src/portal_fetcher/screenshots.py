"""Screenshot manager — sequential naming grouped by run."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page


class ScreenshotManager:
    """Saves numbered screenshots into a per-run directory."""

    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self.run_dir = self.output_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self.paths: list[str] = []

    async def capture(self, page: Page, label: str) -> str:
        """Take a full-page screenshot and return its path."""
        self._counter += 1
        safe_label = label.replace(" ", "_").replace("/", "_")
        filename = f"{self._counter:02d}_{safe_label}.png"
        filepath = self.run_dir / filename
        await page.screenshot(path=str(filepath), full_page=True)
        self.paths.append(str(filepath))
        return str(filepath)
