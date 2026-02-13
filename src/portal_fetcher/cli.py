"""CLI entry point for portal-fetcher."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Callable, Optional

import click
from playwright.async_api import TimeoutError as PwTimeout

from portal_fetcher.adapters import ADAPTER_REGISTRY, detect_and_get_adapter, get_adapter
from portal_fetcher.browser import create_browser_page
from portal_fetcher.errors import PortalFetchError
from portal_fetcher.models import FailureReason, FetchResult
from portal_fetcher.screenshots import ScreenshotManager


@click.group()
@click.version_option(package_name="portal-fetcher", prog_name="Portal Fetcher (V4)")
def main() -> None:
    """Portal Fetcher V4 — auto-detect portal, extract customer details."""


@main.command()
def list_portals() -> None:
    """Show available portal adapters."""
    from portal_fetcher.selector_store import list_configs

    yaml_configs = set(list_configs())
    all_portals = sorted(set(ADAPTER_REGISTRY.keys()) | yaml_configs)
    click.echo("Available portals:")
    for name in all_portals:
        source = "yaml" if name in yaml_configs else "hardcoded"
        click.echo(f"  - {name}  ({source})")


@main.command()
@click.option("--portal", default="", help="Portal adapter name (auto-detected from URL if omitted).")
@click.option("--portal-url", required=True, help="Portal login URL.")
@click.option(
    "--login-user",
    required=True,
    envvar="PORTAL_USER",
    help="Login username [env: PORTAL_USER].",
)
@click.option(
    "--login-pass",
    required=True,
    envvar="PORTAL_PASS",
    help="Login password [env: PORTAL_PASS].",
)
@click.option("--subscriber", required=True, help="Subscriber / User ID to search.")
@click.option("--output-dir", default="./output", help="Directory for screenshots & JSON.")
@click.option("--headless/--no-headless", default=True, help="Run browser headless.")
@click.option("--timeout", default=30000, type=int, help="Default page timeout (ms).")
def fetch(
    portal: str,
    portal_url: str,
    login_user: str,
    login_pass: str,
    subscriber: str,
    output_dir: str,
    headless: bool,
    timeout: int,
) -> None:
    """Fetch subscriber details from a partner portal."""
    result = asyncio.run(
        _run_fetch(portal, portal_url, login_user, login_pass, subscriber, output_dir, headless, timeout)
    )
    output = result.model_dump_json(indent=2)
    click.echo(output)

    # Also write JSON to the run directory
    if result.screenshots:
        from pathlib import Path

        run_dir = Path(result.screenshots[0]).parent
        json_path = run_dir / "result.json"
        json_path.write_text(output)
        click.echo(f"\nJSON saved to: {json_path}", err=True)

    sys.exit(0 if result.success else 1)


@main.command()
@click.option("--port", default=8000, type=int, help="Port to run the web server on.")
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
def serve(port: int, host: str) -> None:
    """Launch the web UI."""
    import uvicorn

    from portal_fetcher.web.app import app  # noqa: F811

    click.echo(f"Starting Portal Fetcher V4 at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


@main.command()
@click.option("--portal-url", required=True, help="Portal login URL.")
@click.option(
    "--login-user",
    required=True,
    envvar="PORTAL_USER",
    help="Login username [env: PORTAL_USER].",
)
@click.option(
    "--login-pass",
    required=True,
    envvar="PORTAL_PASS",
    help="Login password [env: PORTAL_PASS].",
)
@click.option("--output-dir", default="./output", help="Directory for discovery output.")
@click.option("--headless/--no-headless", default=True, help="Run browser headless.")
@click.option("--timeout", default=30000, type=int, help="Default page timeout (ms).")
def discover(
    portal_url: str,
    login_user: str,
    login_pass: str,
    output_dir: str,
    headless: bool,
    timeout: int,
) -> None:
    """Discover DOM structure of a portal for creating new YAML configs."""
    result = asyncio.run(
        _run_discover(portal_url, login_user, login_pass, output_dir, headless, timeout)
    )
    click.echo(json.dumps(result, indent=2))


async def _run_discover(
    portal_url: str,
    login_user: str,
    login_pass: str,
    output_dir: str,
    headless: bool,
    timeout: int,
) -> dict:
    """Run DOM discovery on a portal."""
    from portal_fetcher.discovery import discover_portal

    screenshots = ScreenshotManager(output_dir)
    async with create_browser_page(headless=headless, timeout=timeout) as page:
        return await discover_portal(page, portal_url, login_user, login_pass, screenshots)


async def _run_fetch(
    portal: str,
    portal_url: str,
    login_user: str,
    login_pass: str,
    subscriber: str,
    output_dir: str,
    headless: bool,
    timeout: int,
    on_progress: Optional[Callable[[str], None]] = None,
    on_screencast_frame: Optional[Callable[[bytes], None]] = None,
) -> FetchResult:
    """Orchestrate browser launch -> adapter execution -> error handling.

    Smart connector detection:
    1. Try URL pattern match (instant, no browser needed)
    2. If no match, open the page and probe each connector's login selectors
    3. Use whichever connector's selectors are found on the page
    4. If none match, return error
    """
    from portal_fetcher.browser import start_screencast
    from portal_fetcher.selector_store import get_probe_selectors

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    screenshots = ScreenshotManager(output_dir)
    adapter_cls = None
    adapter_kwargs = {}

    # Step 1: If portal explicitly provided, use it directly
    if portal:
        progress(f"Using connector: {portal}")
        try:
            adapter_cls, adapter_kwargs = get_adapter(portal)
        except KeyError as exc:
            return FetchResult(
                success=False,
                portal=portal,
                subscriber=subscriber,
                failure_reason=FailureReason.UNEXPECTED_ERROR,
                error_message=str(exc),
            )

    # Step 2: Try URL pattern match (instant, no browser)
    if not adapter_cls:
        progress("Checking URL patterns...")
        try:
            portal, (adapter_cls, adapter_kwargs) = detect_and_get_adapter(portal_url)
            progress(f"URL matched connector: {portal}")
        except KeyError:
            progress("No URL pattern match — will probe page structure...")

    # Step 3: Launch browser (needed for probing or execution)
    progress("Launching browser...")

    try:
        async with create_browser_page(headless=headless, timeout=timeout) as page:
            # Start CDP screencast if callback provided
            cdp_session = None
            if on_screencast_frame:
                try:
                    cdp_session = await start_screencast(page, on_screencast_frame)
                    progress("Live view connected...")
                except Exception:
                    pass

            # Step 3b: If no connector yet, probe page structure
            if not adapter_cls:
                progress("Opening page to detect connector...")
                try:
                    await page.goto(portal_url, wait_until="domcontentloaded", timeout=timeout)
                except Exception:
                    pass  # Page may partially load — still try probing

                probes = get_probe_selectors()
                for probe_name, probe_selector in probes:
                    try:
                        el = await page.wait_for_selector(probe_selector, timeout=5000)
                        if el:
                            portal = probe_name
                            adapter_cls, adapter_kwargs = get_adapter(portal)
                            progress(f"Page structure matched: {portal}")
                            break
                    except Exception:
                        progress(f"  Tried {probe_name} — no match")
                        continue

                if not adapter_cls:
                    return FetchResult(
                        success=False,
                        portal="unknown",
                        subscriber=subscriber,
                        failure_reason=FailureReason.UNEXPECTED_ERROR,
                        error_message="No connector matched this portal. None of the available connectors' "
                        "login form selectors were found on the page.",
                        screenshots=screenshots.paths,
                    )

            # Step 4: Create adapter and execute
            adapter = adapter_cls(
                portal_url=portal_url,
                login_user=login_user,
                login_pass=login_pass,
                subscriber=subscriber,
                screenshots=screenshots,
                **adapter_kwargs,
            )

            progress(f"Executing flow: {portal}...")
            try:
                result = await adapter.execute(page)
            except PortalFetchError as exc:
                dom_snapshot_path = None
                if exc.reason == FailureReason.PAGE_STRUCTURE_CHANGED:
                    progress("Page structure changed — capturing DOM snapshot...")
                    dom_snapshot_path = await _capture_dom_snapshot(page, screenshots)
                result = FetchResult(
                    success=False,
                    portal=portal,
                    subscriber=subscriber,
                    failure_reason=exc.reason,
                    error_message=exc.message,
                    screenshots=screenshots.paths,
                    dom_snapshot_path=dom_snapshot_path,
                )
            except PwTimeout as exc:
                result = FetchResult(
                    success=False,
                    portal=portal,
                    subscriber=subscriber,
                    failure_reason=FailureReason.PORTAL_TIMEOUT,
                    error_message=f"Playwright timeout: {exc}",
                    screenshots=screenshots.paths,
                )
            except Exception as exc:
                result = FetchResult(
                    success=False,
                    portal=portal,
                    subscriber=subscriber,
                    failure_reason=FailureReason.UNEXPECTED_ERROR,
                    error_message=f"{type(exc).__name__}: {exc}",
                    screenshots=screenshots.paths,
                )
            finally:
                if cdp_session:
                    try:
                        await cdp_session.send("Page.stopScreencast")
                    except Exception:
                        pass
            return result
    except Exception as exc:
        return FetchResult(
            success=False,
            portal=portal or "unknown",
            subscriber=subscriber,
            failure_reason=FailureReason.UNEXPECTED_ERROR,
            error_message=f"Browser launch failed: {type(exc).__name__}: {exc}",
            screenshots=screenshots.paths,
        )


async def _capture_dom_snapshot(page, screenshots: ScreenshotManager) -> str | None:
    """Capture DOM structure to JSON while page is still alive."""
    try:
        from portal_fetcher.discovery import snapshot_page_structure

        snapshot = await snapshot_page_structure(page)
        snapshot_path = screenshots.run_dir / "dom_snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot, indent=2))
        return str(snapshot_path)
    except Exception:
        return None
