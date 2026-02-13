"""H8 OneBroadband partner portal adapter.

Selectors discovered via live --no-headless testing on 2026-02-12.
Portal is ASP.NET WebForms at partner.onebroadband.in.
"""

from __future__ import annotations

import asyncio

from playwright.async_api import Page, TimeoutError as PwTimeout

from portal_fetcher.adapters.base import BasePortalAdapter
from portal_fetcher.errors import PortalFetchError
from portal_fetcher.models import (
    FailureReason,
    FetchResult,
    PlanDetails,
    UserDetails,
)


class H8OneBroadbandAdapter(BasePortalAdapter):
    """Adapter for https://partner.onebroadband.in ASP.NET portal."""

    # ── helpers ───────────────────────────────────────────────

    async def _text_of(self, page: Page, selector: str) -> str | None:
        """Return inner text of first match, or None if missing."""
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

        return FetchResult(
            success=True,
            portal="h8_onebroadband",
            subscriber=self.subscriber,
            details=details,
            screenshots=self.screenshots.paths,
        )

    # ── step implementations ──────────────────────────────────

    async def _open_portal(self, page: Page) -> None:
        """Navigate to the portal and wait for the login form."""
        try:
            await page.goto(self.portal_url, wait_until="domcontentloaded")
            await page.wait_for_selector(
                "input[id*='txtUserName'], input[name*='UserName']",
                timeout=30000,
            )
        except PwTimeout:
            await self.screenshots.capture(page, "portal_timeout")
            raise PortalFetchError(
                FailureReason.PORTAL_TIMEOUT,
                "Login page did not load within 30 seconds.",
            )

    async def _login(self, page: Page) -> None:
        """Fill credentials, submit, and handle post-login popups."""
        await page.locator(
            "input[id*='txtUserName'], input[name*='UserName']"
        ).first.fill(self.login_user)

        await page.locator(
            "input[id*='txtPassword'], input[name*='Password']"
        ).first.fill(self.login_pass)

        await self.screenshots.capture(page, "login_filled")

        # Submit — the button varies; cast a wide net
        await page.locator(
            "input[id*='btnLogin'], input[id*='btnSubmit'], "
            "button:has-text('Login'), input[type='submit'][value*='Log']"
        ).first.click()

        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)  # ASP.NET postback settle time

        # ── detect failures ──
        # OTP
        try:
            await page.wait_for_selector(
                "input[id*='txtOTP'], input[id*='otp']",
                state="visible", timeout=2000,
            )
            await self.screenshots.capture(page, "otp_required")
            raise PortalFetchError(
                FailureReason.OTP_REQUIRED,
                "Portal requires OTP verification.",
            )
        except PwTimeout:
            pass

        # CAPTCHA
        try:
            await page.wait_for_selector(
                "img[id*='captcha'], img[id*='Captcha'], div[class*='captcha']",
                state="visible", timeout=2000,
            )
            await self.screenshots.capture(page, "captcha_required")
            raise PortalFetchError(
                FailureReason.CAPTCHA_REQUIRED,
                "Portal requires CAPTCHA.",
            )
        except PwTimeout:
            pass

        # Login error banner
        error_el = page.locator(
            "[id*='lblError'], .error-message, [class*='alert-danger']"
        ).first
        try:
            await error_el.wait_for(state="visible", timeout=2000)
            error_text = (await error_el.inner_text()).strip()
            if error_text:
                await self.screenshots.capture(page, "login_error")
                raise PortalFetchError(
                    FailureReason.LOGIN_FAILED,
                    f"Login failed: {error_text}",
                )
        except PwTimeout:
            pass

        # Partner agreement / announcement popup
        await self._dismiss_agreement_popup(page)

        await self.screenshots.capture(page, "post_login")

    async def _dismiss_agreement_popup(self, page: Page) -> None:
        """Close partner agreement / announcement popup if present."""
        for sel in [
            "input[id*='btnAgree']",
            "input[id*='btnClose']",
            "button:has-text('Close')",
            "button:has-text('OK')",
            "a:has-text('Close')",
        ]:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2000)
                await btn.click()
                await page.wait_for_load_state("domcontentloaded")
                return
            except PwTimeout:
                continue

    async def _navigate_to_accounts(self, page: Page) -> None:
        """Go to Accounts.aspx.

        The #lbAccount link is duplicated (desktop + responsive menu) and one
        copy is hidden, so clicking it is unreliable.  Navigate by URL instead.
        """
        base_url = self.portal_url.rsplit("/", 1)[0]
        try:
            await page.goto(
                f"{base_url}/Accounts.aspx", wait_until="domcontentloaded",
            )
            # Wait for the search field to confirm we're on the right page
            await page.wait_for_selector(
                "#ContentPlaceHolder1_txtserch", timeout=15000,
            )
        except PwTimeout:
            await self.screenshots.capture(page, "accounts_page_timeout")
            raise PortalFetchError(
                FailureReason.PAGE_STRUCTURE_CHANGED,
                "Accounts page did not load or search field missing.",
            )

    async def _search_subscriber(self, page: Page) -> None:
        """Search by User ID and validate exactly one Active result."""
        # Fill the search field  (portal typo: "txtserch")
        search_field = page.locator("#ContentPlaceHolder1_txtserch")
        await search_field.click()
        await search_field.fill(self.subscriber)

        await self.screenshots.capture(page, "search_filled")

        # Click Search — this triggers an ASP.NET UpdatePanel partial postback
        await page.locator("#ContentPlaceHolder1_btnserch").click()

        # Wait for the grid to update (no full navigation, just AJAX)
        await asyncio.sleep(4)

        await self.screenshots.capture(page, "search_results")

        # Grid id is ContentPlaceHolder1_gdcomp — it's a <table>
        grid = page.locator("#ContentPlaceHolder1_gdcomp")
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

        data_rows = row_count - 1  # first row is header
        if data_rows > 1:
            raise PortalFetchError(
                FailureReason.MULTIPLE_RESULTS,
                f"Expected 1 result, got {data_rows} for '{self.subscriber}'.",
            )

        # Validate first data row
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
        """Click subscriber link in the grid to open AccountView.aspx."""
        # The grid link id is ContentPlaceHolder1_gdcomp_LinkButton1_0
        user_link = page.locator(
            "#ContentPlaceHolder1_gdcomp_LinkButton1_0, "
            f"#ContentPlaceHolder1_gdcomp a:has-text('{self.subscriber}')"
        ).first
        try:
            await user_link.click()
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)
        except PwTimeout:
            await self.screenshots.capture(page, "user_link_missing")
            raise PortalFetchError(
                FailureReason.PAGE_STRUCTURE_CHANGED,
                f"Could not click user link for '{self.subscriber}'.",
            )

        await self.screenshots.capture(page, "user_detail_page")

        # ── Extract fields using exact IDs from the live portal ──
        details.user_id = (
            await self._text_of(page, "#ContentPlaceHolder1_lbl_USERNAME")
            or self.subscriber
        )
        details.name = await self._text_of(
            page, "#ContentPlaceHolder1_lbl_AccName"
        )
        details.status = await self._text_of(
            page, "#ContentPlaceHolder1_lbl_RemainingDays"
        )
        details.router_mac = await self._text_of(
            page, "#ContentPlaceHolder1_lbl_MACID"
        )

        # Identity check
        displayed = details.user_id or ""
        if displayed and self.subscriber.lower() not in displayed.lower():
            raise PortalFetchError(
                FailureReason.IDENTITY_MISMATCH,
                f"Detail page shows '{displayed}', expected '{self.subscriber}'.",
            )

    async def _extract_plans(self, page: Page, details: UserDetails) -> None:
        """Read plan grid from the Plan tab (visible by default)."""
        # Current plan — header label
        plan_label = await self._text_of(
            page, "#ContentPlaceHolder1_lbl_CurrentPlan"
        )

        # Plan grid row 0 has detailed info
        plan_name = await self._text_of(
            page,
            "#ContentPlaceHolder1_tbAdditonal_tpPlan_gdPlan_lblPlan_0",
        )
        validity = await self._text_of(
            page,
            "#ContentPlaceHolder1_tbAdditonal_tpPlan_gdPlan_lblvalidity_0",
        )
        validity_unit = await self._text_of(
            page,
            "#ContentPlaceHolder1_tbAdditonal_tpPlan_gdPlan_Label41_0",
        )
        activation = await self._text_of(
            page,
            "#ContentPlaceHolder1_tbAdditonal_tpPlan_gdPlan_lblActivationDate_0",
        )
        expiry = await self._text_of(
            page,
            "#ContentPlaceHolder1_tbAdditonal_tpPlan_gdPlan_lblExpiryDate_0",
        )

        duration = None
        if validity and validity_unit:
            duration = f"{validity} {validity_unit}"

        details.current_plan = PlanDetails(
            plan_name=plan_name or plan_label,
            speed=None,  # not shown separately; embedded in plan name
            duration=duration,
            start_date=activation,
            expiry_date=expiry,
        )

        await self.screenshots.capture(page, "current_plan")

        # Future Plan tab
        future_tab = page.locator(
            "#__tab_ContentPlaceHolder1_tbAdditonal_tpFutureplan"
        )
        try:
            await future_tab.wait_for(state="visible", timeout=3000)
            await future_tab.click()
            await asyncio.sleep(1)

            future_plan_text = await self._text_of(
                page,
                "#ContentPlaceHolder1_tbAdditonal_tpFutureplan",
            )

            # The panel contains "There are no recent Plan" when empty
            if future_plan_text and "no recent" not in future_plan_text.lower():
                details.future_plan = PlanDetails(plan_name=future_plan_text)

            await self.screenshots.capture(page, "future_plan")
        except PwTimeout:
            pass
