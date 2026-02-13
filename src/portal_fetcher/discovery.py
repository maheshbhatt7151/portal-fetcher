"""DOM discovery engine — logs in and dumps interactive elements to structured JSON.

Usage:
    portal-fetcher discover --portal-url <url> --login-user X --login-pass Y

Produces a JSON snapshot of all inputs, buttons, links, tables, and selects
found on each page visited during the login flow. Use this output to create
a new YAML selector config for an unknown portal.
"""

from __future__ import annotations

import asyncio
from typing import Any

from playwright.async_api import Page, TimeoutError as PwTimeout

from portal_fetcher.screenshots import ScreenshotManager


async def snapshot_page_structure(page: Page) -> dict[str, Any]:
    """Capture all interactive elements on the current page.

    This function is also called by the auto-snapshot logic in cli.py
    when PAGE_STRUCTURE_CHANGED is raised.
    """
    return await page.evaluate("""() => {
        function attrs(el) {
            const result = {};
            for (const attr of el.attributes) {
                result[attr.name] = attr.value;
            }
            return result;
        }
        function info(el) {
            return {
                tag: el.tagName.toLowerCase(),
                id: el.id || null,
                name: el.getAttribute('name') || null,
                type: el.getAttribute('type') || null,
                className: el.className || null,
                text: (el.innerText || '').trim().substring(0, 200),
                value: el.value || null,
                visible: el.offsetParent !== null || el.style.display !== 'none',
                attributes: attrs(el),
            };
        }

        const snapshot = {
            url: window.location.href,
            title: document.title,
            inputs: [],
            buttons: [],
            links: [],
            selects: [],
            tables: [],
            labels: [],
        };

        document.querySelectorAll('input, textarea').forEach(el => {
            snapshot.inputs.push(info(el));
        });
        document.querySelectorAll('button, input[type="submit"], input[type="button"]').forEach(el => {
            snapshot.buttons.push(info(el));
        });
        document.querySelectorAll('a[href]').forEach(el => {
            const i = info(el);
            i.href = el.getAttribute('href');
            snapshot.links.push(i);
        });
        document.querySelectorAll('select').forEach(el => {
            const i = info(el);
            i.options = Array.from(el.options).map(o => ({
                value: o.value,
                text: o.text.trim(),
                selected: o.selected,
            }));
            snapshot.selects.push(i);
        });
        document.querySelectorAll('table').forEach(el => {
            const rows = Array.from(el.querySelectorAll('tr')).slice(0, 5);
            const i = info(el);
            i.row_count = el.querySelectorAll('tr').length;
            i.sample_rows = rows.map(r => (r.innerText || '').trim().substring(0, 300));
            snapshot.tables.push(i);
        });
        document.querySelectorAll('label, span[id*="lbl"], span[id*="Label"]').forEach(el => {
            const i = info(el);
            if (i.text) snapshot.labels.push(i);
        });

        return snapshot;
    }""")


async def discover_portal(
    page: Page,
    portal_url: str,
    login_user: str,
    login_pass: str,
    screenshots: ScreenshotManager,
) -> dict[str, Any]:
    """Run a discovery flow: navigate, login, and snapshot each page."""
    result: dict[str, Any] = {"pages": []}

    # Step 1: Navigate to portal
    await page.goto(portal_url, wait_until="domcontentloaded")
    await asyncio.sleep(2)
    await screenshots.capture(page, "discover_login_page")

    login_snapshot = await snapshot_page_structure(page)
    login_snapshot["label"] = "login_page"
    result["pages"].append(login_snapshot)

    # Step 2: Try to login
    # Find username field
    username_input = page.locator("input[type='text'], input[type='email'], input[id*='user' i], input[id*='login' i], input[name*='user' i]").first
    password_input = page.locator("input[type='password']").first

    try:
        await username_input.wait_for(state="visible", timeout=5000)
        await username_input.fill(login_user)
    except PwTimeout:
        result["error"] = "Could not find username field"
        return result

    try:
        await password_input.wait_for(state="visible", timeout=5000)
        await password_input.fill(login_pass)
    except PwTimeout:
        result["error"] = "Could not find password field"
        return result

    # Find and click submit button
    submit_btn = page.locator(
        "input[type='submit'], button[type='submit'], "
        "button:has-text('Login'), button:has-text('Sign in'), "
        "input[value*='Log' i], input[value*='Sign' i]"
    ).first
    try:
        await submit_btn.click()
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(3)
    except PwTimeout:
        result["error"] = "Could not find or click submit button"
        return result

    await screenshots.capture(page, "discover_post_login")

    post_login_snapshot = await snapshot_page_structure(page)
    post_login_snapshot["label"] = "post_login_page"
    result["pages"].append(post_login_snapshot)

    # Step 3: Try to find and navigate to any linked pages (accounts, dashboard, etc.)
    links = await page.locator("a[href]").all()
    nav_links = []
    for link in links[:20]:
        try:
            href = await link.get_attribute("href") or ""
            text = (await link.inner_text()).strip()
            if text and href and not href.startswith("#") and not href.startswith("javascript:"):
                nav_links.append({"text": text, "href": href})
        except Exception:
            continue

    result["navigation_links"] = nav_links

    # Step 4: Navigate to promising pages (Accounts, Dashboard, etc.)
    base_url = portal_url.rsplit("/", 1)[0]
    for keyword in ["account", "dashboard", "subscriber", "search", "customer"]:
        for link_info in nav_links:
            if keyword in link_info["text"].lower() or keyword in link_info["href"].lower():
                try:
                    href = link_info["href"]
                    if not href.startswith("http"):
                        href = f"{base_url}/{href.lstrip('/')}"
                    await page.goto(href, wait_until="domcontentloaded")
                    await asyncio.sleep(2)
                    await screenshots.capture(page, f"discover_{keyword}_page")

                    page_snapshot = await snapshot_page_structure(page)
                    page_snapshot["label"] = f"{keyword}_page"
                    result["pages"].append(page_snapshot)
                except Exception:
                    continue
                break  # Only visit first match per keyword

    # Save discovery output
    import json
    output_path = screenshots.run_dir / "discovery.json"
    output_path.write_text(json.dumps(result, indent=2))
    result["output_path"] = str(output_path)

    return result
