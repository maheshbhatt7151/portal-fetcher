"""Flow-driven adapter — reads automation steps + extraction rules from YAML.

Any portal can be automated by writing a YAML config with two sections:
  flow:   list of action steps (goto, fill, click, select_option, etc.)
  extract: field definitions (method: selectors or table_cells)

No Python changes needed to add a new portal.
"""

from __future__ import annotations

import asyncio
import re
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


class ConfigDrivenAdapter(BasePortalAdapter):
    """Generic adapter that drives any portal using a YAML flow config."""

    def __init__(self, *args: Any, selector_config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cfg = selector_config

    # ── template variable substitution ────────────────────────

    def _sub(self, text: str) -> str:
        """Replace {portal_url}, {login_user}, {login_pass}, {subscriber} in text."""
        if not isinstance(text, str):
            return text
        return (
            text.replace("{portal_url}", self.portal_url)
            .replace("{login_user}", self.login_user)
            .replace("{login_pass}", self.login_pass)
            .replace("{subscriber}", self.subscriber)
        )

    # ── helpers ───────────────────────────────────────────────

    async def _text_of(self, page: Page, selector: str) -> str | None:
        loc = page.locator(selector).first
        try:
            await loc.wait_for(state="attached", timeout=3000)
            return (await loc.inner_text()).strip() or None
        except PwTimeout:
            return None

    async def _extract_table_cells(self, page: Page) -> dict[str, str]:
        """Extract label→value pairs from all tables with <td><span>Label</span></td><td>Value</td> pattern."""
        return await page.evaluate("""() => {
            const result = {};
            const rows = document.querySelectorAll('tr');
            for (const row of rows) {
                const cells = row.querySelectorAll('td');
                if (cells.length >= 2) {
                    const labelSpan = cells[0].querySelector('span');
                    const labelText = labelSpan ? labelSpan.innerText.trim() : cells[0].innerText.trim();
                    if (labelText && labelText.length < 80) {
                        // Get the raw text of the value cell, cleaning up whitespace
                        let val = cells[1].innerText.trim();
                        // Remove excessive whitespace
                        val = val.replace(/\\s+/g, ' ').substring(0, 200);
                        if (val) result[labelText] = val;
                    }
                }
            }
            return result;
        }""")

    # ── main flow ─────────────────────────────────────────────

    async def execute(self, page: Page) -> FetchResult:
        details = UserDetails(user_id=self.subscriber)

        # Execute flow steps
        flow = self.cfg.get("flow", [])
        if not flow:
            raise PortalFetchError(
                FailureReason.PAGE_STRUCTURE_CHANGED,
                "YAML config has no 'flow' section.",
            )

        for i, step in enumerate(flow):
            action = step.get("action", "")
            try:
                await self._execute_step(page, step)
            except PortalFetchError:
                raise
            except PwTimeout as exc:
                await self.screenshots.capture(page, f"timeout_step_{i}")
                raise PortalFetchError(
                    FailureReason.PORTAL_TIMEOUT,
                    f"Timeout at step {i} ({action}): {exc}",
                )
            except Exception as exc:
                raise PortalFetchError(
                    FailureReason.PAGE_STRUCTURE_CHANGED,
                    f"Error at step {i} ({action}): {type(exc).__name__}: {exc}",
                )

        # Extract data
        await self._extract_data(page, details)

        portal_name = self.cfg.get("portal", {}).get("name", "unknown")
        return FetchResult(
            success=True,
            portal=portal_name,
            subscriber=self.subscriber,
            details=details,
            screenshots=self.screenshots.paths,
        )

    # ── step execution ───────────────────────────────────────

    async def _execute_step(self, page: Page, step: dict[str, Any]) -> None:
        action = step.get("action", "")

        if action == "goto":
            url = self._sub(step["url"])
            wait_for = step.get("wait_for")
            try:
                await page.goto(url, wait_until="domcontentloaded")
                if wait_for:
                    await page.wait_for_selector(self._sub(wait_for), timeout=30000)
            except PwTimeout:
                await self.screenshots.capture(page, "portal_timeout")
                raise PortalFetchError(
                    FailureReason.PORTAL_TIMEOUT,
                    f"Page did not load or element '{wait_for}' not found.",
                )

        elif action == "fill":
            selector = self._sub(step["selector"])
            value = self._sub(step["value"])
            await page.locator(selector).first.fill(value)

        elif action == "click":
            selector = self._sub(step["selector"])
            await page.locator(selector).first.click()

        elif action == "select_option":
            selector = self._sub(step["selector"])
            value = self._sub(step.get("value", ""))
            label = self._sub(step.get("label", ""))
            if value:
                await page.select_option(selector, value=value)
            elif label:
                await page.select_option(selector, label=label)

        elif action == "press_key":
            selector = self._sub(step["selector"])
            key = step["key"]
            await page.press(selector, key)

        elif action == "wait_for_load":
            await page.wait_for_load_state("domcontentloaded")

        elif action == "wait_for":
            selector = self._sub(step["selector"])
            timeout = step.get("timeout", 15000)
            await page.wait_for_selector(selector, timeout=timeout)

        elif action == "sleep":
            seconds = step.get("seconds", 2)
            await asyncio.sleep(seconds)

        elif action == "screenshot":
            name = step.get("name", "step")
            await self.screenshots.capture(page, name)

        elif action == "click_link":
            selector = self._sub(step["selector"])
            text_contains = self._sub(step.get("text_contains", ""))
            links = await page.query_selector_all(selector)
            clicked = False

            # First try to find a visible link whose text contains the subscriber
            for link in links:
                visible = await link.is_visible()
                if not visible:
                    continue
                link_text = (await link.inner_text()).strip()
                if text_contains and text_contains.lower() not in link_text.lower():
                    continue
                await link.click()
                clicked = True
                break

            # Fallback: click any visible matching link
            if not clicked:
                for link in links:
                    visible = await link.is_visible()
                    if visible:
                        await link.click()
                        clicked = True
                        break

            if not clicked:
                # Last resort: navigate directly to the href
                if links:
                    href = await links[0].get_attribute("href") or ""
                    if href:
                        await page.goto(href, wait_until="domcontentloaded")
                        clicked = True

            if not clicked:
                raise PortalFetchError(
                    FailureReason.SUBSCRIBER_NOT_FOUND,
                    f"No clickable link found for selector '{selector}' "
                    f"containing '{text_contains}'.",
                )

        elif action == "check_login_error":
            error_sel = self._sub(step.get("selector", ""))
            if error_sel:
                try:
                    el = page.locator(error_sel).first
                    await el.wait_for(state="visible", timeout=2000)
                    text = (await el.inner_text()).strip()
                    if text:
                        await self.screenshots.capture(page, "login_error")
                        raise PortalFetchError(
                            FailureReason.LOGIN_FAILED,
                            f"Login failed: {text}",
                        )
                except PwTimeout:
                    pass  # No error visible — login succeeded

        elif action == "check_otp":
            otp_sel = self._sub(step.get("selector", ""))
            if otp_sel:
                try:
                    await page.wait_for_selector(otp_sel, state="visible", timeout=2000)
                    await self.screenshots.capture(page, "otp_required")
                    raise PortalFetchError(
                        FailureReason.OTP_REQUIRED,
                        "Portal requires OTP verification.",
                    )
                except PwTimeout:
                    pass

        elif action == "check_captcha":
            captcha_sel = self._sub(step.get("selector", ""))
            if captcha_sel:
                try:
                    await page.wait_for_selector(captcha_sel, state="visible", timeout=2000)
                    await self.screenshots.capture(page, "captcha_required")
                    raise PortalFetchError(
                        FailureReason.CAPTCHA_REQUIRED,
                        "Portal requires CAPTCHA.",
                    )
                except PwTimeout:
                    pass

        elif action == "dismiss_popup":
            selectors = step.get("selectors", [])
            for sel in selectors:
                sel = self._sub(sel)
                try:
                    btn = page.locator(sel).first
                    await btn.wait_for(state="visible", timeout=2000)
                    await btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    break
                except PwTimeout:
                    continue

        else:
            pass  # Unknown action — skip silently

    # ── data extraction ──────────────────────────────────────

    async def _extract_data(self, page: Page, details: UserDetails) -> None:
        extract = self.cfg.get("extract", {})
        if not extract:
            return

        method = extract.get("method", "selectors")
        fields = extract.get("fields", {})

        if method == "table_cells":
            await self._extract_table_cells_method(page, details, fields, extract)
        else:
            await self._extract_selectors_method(page, details, fields, extract)

    async def _extract_selectors_method(
        self,
        page: Page,
        details: UserDetails,
        fields: dict[str, str],
        extract: dict[str, Any],
    ) -> None:
        """Extract fields using CSS selectors (H8 OneBroadband style)."""
        if fields.get("user_id"):
            details.user_id = await self._text_of(page, fields["user_id"]) or self.subscriber
        if fields.get("name"):
            details.name = await self._text_of(page, fields["name"])
        if fields.get("status"):
            details.status = await self._text_of(page, fields["status"])
        if fields.get("router_mac"):
            details.router_mac = await self._text_of(page, fields["router_mac"])
        if fields.get("mobile"):
            details.mobile = await self._text_of(page, fields["mobile"])
        if fields.get("email"):
            details.email = await self._text_of(page, fields["email"])

        # Extra fields
        for key, selector in fields.items():
            if key not in ("user_id", "name", "status", "router_mac", "mobile", "email"):
                val = await self._text_of(page, selector)
                if val:
                    details.extra_fields[key] = val

        # Identity check
        displayed = details.user_id or ""
        if displayed and self.subscriber.lower() not in displayed.lower():
            raise PortalFetchError(
                FailureReason.IDENTITY_MISMATCH,
                f"Detail page shows '{displayed}', expected '{self.subscriber}'.",
            )

        # Current plan extraction
        cp = extract.get("current_plan", {})
        if cp:
            plan_name = await self._text_of(page, cp["plan_name"]) if cp.get("plan_name") else None
            plan_label = await self._text_of(page, cp["label"]) if cp.get("label") else None
            validity = await self._text_of(page, cp["validity"]) if cp.get("validity") else None
            validity_unit = await self._text_of(page, cp["validity_unit"]) if cp.get("validity_unit") else None
            activation = await self._text_of(page, cp["activation_date"]) if cp.get("activation_date") else None
            expiry = await self._text_of(page, cp["expiry_date"]) if cp.get("expiry_date") else None

            duration = f"{validity} {validity_unit}" if validity and validity_unit else None
            details.current_plan = PlanDetails(
                plan_name=plan_name or plan_label,
                duration=duration,
                start_date=activation,
                expiry_date=expiry,
            )
            await self.screenshots.capture(page, "current_plan")

        # Future plan
        fp = extract.get("future_plan", {})
        if fp.get("tab"):
            try:
                tab = page.locator(fp["tab"])
                await tab.wait_for(state="visible", timeout=3000)
                await tab.click()
                await asyncio.sleep(1)
                content = await self._text_of(page, fp["content"]) if fp.get("content") else None
                empty_marker = fp.get("empty_marker", "no recent")
                if content and empty_marker.lower() not in content.lower():
                    details.future_plan = PlanDetails(plan_name=content)
                await self.screenshots.capture(page, "future_plan")
            except PwTimeout:
                pass

    async def _extract_table_cells_method(
        self,
        page: Page,
        details: UserDetails,
        fields: dict[str, str],
        extract: dict[str, Any],
    ) -> None:
        """Extract fields by matching label text in table cells (Weebo style)."""
        cell_data = await self._extract_table_cells(page)

        # Map YAML field names to label text
        for field_name, label_text in fields.items():
            val = cell_data.get(label_text)
            if not val:
                continue
            # Clean up common noise (icons, verification text)
            val = re.split(r'\s*Not verified', val)[0]
            val = re.split(r'\s*Not Opted', val)[0]
            val = val.strip()

            if field_name == "user_id":
                details.user_id = val
            elif field_name == "name":
                details.name = val
            elif field_name == "status":
                details.status = val
            elif field_name == "router_mac":
                details.router_mac = val
            elif field_name == "mobile":
                details.mobile = val
            elif field_name == "email":
                details.email = val
            else:
                details.extra_fields[field_name] = val

        # Identity check
        displayed = details.user_id or ""
        if displayed and self.subscriber.lower() not in displayed.lower():
            raise PortalFetchError(
                FailureReason.IDENTITY_MISMATCH,
                f"Detail page shows '{displayed}', expected '{self.subscriber}'.",
            )

        # Current plan from table cells
        cp = extract.get("current_plan", {})
        if cp:
            plan_name = cell_data.get(cp.get("plan_name", ""), None)
            expiry = cell_data.get(cp.get("expiry_date", ""), None)
            start = cell_data.get(cp.get("start_date", ""), None)
            speed = cell_data.get(cp.get("speed", ""), None)
            if plan_name or expiry:
                details.current_plan = PlanDetails(
                    plan_name=plan_name,
                    speed=speed,
                    start_date=start,
                    expiry_date=expiry,
                )

        await self.screenshots.capture(page, "extracted_data")
