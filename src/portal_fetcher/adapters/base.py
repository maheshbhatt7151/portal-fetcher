"""Base adapter interface that every portal adapter must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod

from playwright.async_api import Page

from portal_fetcher.models import FetchResult
from portal_fetcher.screenshots import ScreenshotManager


class BasePortalAdapter(ABC):
    """Abstract base for portal-specific automation."""

    def __init__(
        self,
        portal_url: str,
        login_user: str,
        login_pass: str,
        subscriber: str,
        screenshots: ScreenshotManager,
    ) -> None:
        self.portal_url = portal_url
        self.login_user = login_user
        self.login_pass = login_pass
        self.subscriber = subscriber
        self.screenshots = screenshots

    @abstractmethod
    async def execute(self, page: Page) -> FetchResult:
        """Run the full portal flow and return structured results.

        Implementations own login → search → extract.
        Must raise PortalFetchError for known failures.
        """
        ...
