"""Config-driven adapter — reads all selectors from YAML.

Same flow as the hardcoded H8 adapter, but every CSS selector comes
from the YAML config via selector_store.get_selector().
"""

from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import Page, TimeoutError as PwTimeout

from portal_fetcher.adapters.base import BasePortalAdapter
from portal_fetcher.errors import PortalFetchError
from portal_fetcher.models import (
    FailureReason,
    FetchResult,
    PlanDetails,
    UserDetails,
)
from portal_fetcher.selector_store import get_selector, get_selector_list


class ConfigDrivenAdapter(BasePortalAdapter):
    """Generic adapter that drives any portal using a YAML selector config."""

    def __init__(self, *args: Any, selector_config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cfg = selector_config

    def _sel(self, dotted_key: str) -> str:
        """Get a required selector or raise PAGE_STRUCTURE_CHANGED."""
        val = get_selector(self.cfg, dotted_key)
        if not val:
            raise PortalFetchError(
                FailureReason.PAGE_STRUCTURE_CHANGED,
                f"Missing selector in config: '{dotted_key}'",
            )
        return val

    def _sel_opt(self, dotted_key: str) -> str | None:
        """Get an optional selector (returns None if missing)."""
        return get_selector(self.cfg, dotted_key)

    # ── helpers ───────────────────────────────────────────────

    async def _text_of(self, page: Page, selector: str) -> str | None:
        loc = page.locator(selector).first
        try:
            await loc.wait_for(state="attached", timeout=3000)
            return (await loc.inner_text()).strip() or None
        except PwTimeout:
            return None

    # ── main flow ─────────────────────────────────────────────

    async def execute(self, page: Page) -> FetchResult:
        details = UserDetails(user_id=self.subscriber)

        await self._open_portal(page)
        await self._login(page)
        await self._navigate_to_accounts(page)
        await self._search_subscriber(page)
        await self._open_user_details(page, details)
        await self._extract_plans(page, details)

        portal_name = get_selector(self.cfg, "portal.name") or "unknown"
        return FetchResult(
            success=True,
            portal=portal_name,
            subscriber=self.subscriber,
            details=details,
            screenshots=self.screenshots.paths,
        )

    # ── step implementations ──────────────────────────────────

    async def _open_portal(self, page: Page) -> None:
        try:
            await page.goto(self.portal_url, wait_until="domcontentloaded")
            await page.wait_for_selector(
                self._sel("login.username_field"), timeout=30000,
            )
        except PwTimeout:
            await self.screenshots.capture(page, "portal_timeout")
            raise PortalFetchError(
                FailureReason.PORTAL_TIMEOUT,
                "Login page did not load within 30 seconds.",
            )

    async def _login(self, page: Page) -> None:
        await page.locator(self._sel("login.username_field")).first.fill(self.login_user)
        await page.locator(self._sel("login.password_field")).first.fill(self.login_pass)
        await self.screenshots.capture(page, "login_filled")

        await page.locator(self._sel("login.submit_button")).first.click()
        await page.wait_for_load_state("domcontentloaded")

        settle = self.cfg.get("login", {}).get("settle_seconds", 2)
        await asyncio.sleep(settle)

        # OTP detection
        otp_sel = self._sel_opt("login.otp_field")
        if otp_sel:
            try:
                await page.wait_for_selector(otp_sel, state="visible", timeout=2000)
                await self.screenshots.capture(page, "otp_required")
                raise PortalFetchError(FailureReason.OTP_REQUIRED, "Portal requires OTP verification.")
            except PwTimeout:
                pass

        # CAPTCHA detection
        captcha_sel = self._sel_opt("login.captcha_image")
        if captcha_sel:
            try:
                await page.wait_for_selector(captcha_sel, state="visible", timeout=2000)
                await self.screenshots.capture(page, "captcha_required")
                raise PortalFetchError(FailureReason.CAPTCHA_REQUIRED, "Portal requires CAPTCHA.")
            except PwTimeout:
                pass

        # Login error banner
        error_sel = self._sel_opt("login.error_label")
        if error_sel:
            error_el = page.locator(error_sel).first
            try:
                await error_el.wait_for(state="visible", timeout=2000)
                error_text = (await error_el.inner_text()).strip()
                if error_text:
                    await self.screenshots.capture(page, "login_error")
                    raise PortalFetchError(FailureReason.LOGIN_FAILED, f"Login failed: {error_text}")
            except PwTimeout:
                pass

        # Dismiss popup
        for sel in get_selector_list(self.cfg, "login.popup_dismiss"):
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2000)
                await btn.click()
                await page.wait_for_load_state("domcontentloaded")
                break
            except PwTimeout:
                continue

        await self.screenshots.capture(page, "post_login")

    async def _navigate_to_accounts(self, page: Page) -> None:
        base_url = self.portal_url.rsplit("/", 1)[0]
        page_path = get_selector(self.cfg, "accounts.page_path") or "/Accounts.aspx"
        search_field_sel = self._sel("accounts.search_field")

        try:
            await page.goto(f"{base_url}{page_path}", wait_until="domcontentloaded")
            await page.wait_for_selector(search_field_sel, timeout=15000)
        except PwTimeout:
            await self.screenshots.capture(page, "accounts_page_timeout")
            raise PortalFetchError(
                FailureReason.PAGE_STRUCTURE_CHANGED,
                "Accounts page did not load or search field missing.",
            )

    async def _search_subscriber(self, page: Page) -> None:
        search_field = page.locator(self._sel("accounts.search_field"))
        await search_field.click()
        await search_field.fill(self.subscriber)
        await self.screenshots.capture(page, "search_filled")

        await page.locator(self._sel("accounts.search_button")).click()

        wait_secs = self.cfg.get("accounts", {}).get("search_wait_seconds", 4)
        await asyncio.sleep(wait_secs)

        await self.screenshots.capture(page, "search_results")

        grid = page.locator(self._sel("accounts.grid"))
        try:
            await grid.wait_for(state="attached", timeout=10000)
        except PwTimeout:
            raise PortalFetchError(
                FailureReason.SUBSCRIBER_NOT_FOUND,
                f"No results grid for subscriber '{self.subscriber}'.",
            )

        rows = grid.locator("tr")
        row_count = await rows.count()

        if row_count <= 1:
            raise PortalFetchError(
                FailureReason.SUBSCRIBER_NOT_FOUND,
                f"No results for subscriber '{self.subscriber}'.",
            )

        data_rows = row_count - 1
        if data_rows > 1:
            raise PortalFetchError(
                FailureReason.MULTIPLE_RESULTS,
                f"Expected 1 result, got {data_rows} for '{self.subscriber}'.",
            )

        first_row = rows.nth(1)
        row_text = await first_row.inner_text()

        if self.subscriber.lower() not in row_text.lower():
            raise PortalFetchError(
                FailureReason.IDENTITY_MISMATCH,
                f"Result row does not contain '{self.subscriber}': {row_text[:200]}",
            )

        if "active" not in row_text.lower():
            raise PortalFetchError(
                FailureReason.INACTIVE_SUBSCRIBER,
                f"Subscriber '{self.subscriber}' appears inactive: {row_text[:200]}",
            )

    async def _open_user_details(self, page: Page, details: UserDetails) -> None:
        grid_link_sel = self._sel("accounts.grid_link")
        fallback_sel = self._sel_opt("accounts.grid_link_fallback")
        if fallback_sel:
            fallback_sel = fallback_sel.replace("{subscriber}", self.subscriber)
            combined = f"{grid_link_sel}, {fallback_sel}"
        else:
            combined = grid_link_sel

        user_link = page.locator(combined).first
        try:
            await user_link.click()
            await page.wait_for_load_state("domcontentloaded")
            settle = self.cfg.get("detail", {}).get("settle_seconds", 2)
            await asyncio.sleep(settle)
        except PwTimeout:
            await self.screenshots.capture(page, "user_link_missing")
            raise PortalFetchError(
                FailureReason.PAGE_STRUCTURE_CHANGED,
                f"Could not click user link for '{self.subscriber}'.",
            )

        await self.screenshots.capture(page, "user_detail_page")

        # Extract fields
        fields = self.cfg.get("detail", {}).get("fields", {})
        if fields.get("user_id"):
            details.user_id = await self._text_of(page, fields["user_id"]) or self.subscriber
        if fields.get("name"):
            details.name = await self._text_of(page, fields["name"])
        if fields.get("status"):
            details.status = await self._text_of(page, fields["status"])
        if fields.get("router_mac"):
            details.router_mac = await self._text_of(page, fields["router_mac"])

        # Identity check
        displayed = details.user_id or ""
        if displayed and self.subscriber.lower() not in displayed.lower():
            raise PortalFetchError(
                FailureReason.IDENTITY_MISMATCH,
                f"Detail page shows '{displayed}', expected '{self.subscriber}'.",
            )

    async def _extract_plans(self, page: Page, details: UserDetails) -> None:
        cp = self.cfg.get("detail", {}).get("current_plan", {})

        plan_label = await self._text_of(page, cp["label"]) if cp.get("label") else None
        plan_name = await self._text_of(page, cp["plan_name"]) if cp.get("plan_name") else None
        validity = await self._text_of(page, cp["validity"]) if cp.get("validity") else None
        validity_unit = await self._text_of(page, cp["validity_unit"]) if cp.get("validity_unit") else None
        activation = await self._text_of(page, cp["activation_date"]) if cp.get("activation_date") else None
        expiry = await self._text_of(page, cp["expiry_date"]) if cp.get("expiry_date") else None

        duration = None
        if validity and validity_unit:
            duration = f"{validity} {validity_unit}"

        details.current_plan = PlanDetails(
            plan_name=plan_name or plan_label,
            speed=None,
            duration=duration,
            start_date=activation,
            expiry_date=expiry,
        )

        await self.screenshots.capture(page, "current_plan")

        # Future plan tab
        fp = self.cfg.get("detail", {}).get("future_plan", {})
        future_tab_sel = fp.get("tab")
        if future_tab_sel:
            future_tab = page.locator(future_tab_sel)
            try:
                await future_tab.wait_for(state="visible", timeout=3000)
                await future_tab.click()
                await asyncio.sleep(1)

                content_sel = fp.get("content")
                future_plan_text = await self._text_of(page, content_sel) if content_sel else None

                empty_marker = fp.get("empty_marker", "no recent")
                if future_plan_text and empty_marker.lower() not in future_plan_text.lower():
                    details.future_plan = PlanDetails(plan_name=future_plan_text)

                await self.screenshots.capture(page, "future_plan")
            except PwTimeout:
                pass
