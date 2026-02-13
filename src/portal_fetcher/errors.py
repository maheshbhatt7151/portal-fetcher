"""Portal fetch exceptions."""

from __future__ import annotations

from portal_fetcher.models import FailureReason


class PortalFetchError(Exception):
    """A known, categorised failure during portal interaction."""

    def __init__(self, reason: FailureReason, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(f"[{reason.value}] {message}")
