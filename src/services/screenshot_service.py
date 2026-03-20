"""Screenshot service for capturing webpage previews.

Supports two methods:
- pageshot: External PageShot API (free, no key needed, fast)
- playwright: Internal Playwright-based capture (privacy-safe, no external calls)

The captured screenshots can be uploaded as preview images to edu-sharing nodes.
"""

import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class ScreenshotResult:
    """Result of a screenshot capture."""

    def __init__(
        self,
        image_bytes: bytes,
        format: str = "png",
        width: int = 0,
        height: int = 0,
        capture_time_ms: int = 0,
        method: str = "pageshot",
        url: str = "",
    ):
        self.image_bytes = image_bytes
        self.format = format
        self.width = width
        self.height = height
        self.capture_time_ms = capture_time_ms
        self.method = method
        self.url = url
        self.size_bytes = len(image_bytes)

    @property
    def mimetype(self) -> str:
        return f"image/{self.format}"

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "url": self.url,
            "format": self.format,
            "mimetype": self.mimetype,
            "width": self.width,
            "height": self.height,
            "size_bytes": self.size_bytes,
            "capture_time_ms": self.capture_time_ms,
        }


class ScreenshotService:
    """Service for capturing screenshots of webpages."""

    def __init__(self, settings=None):
        if settings is None:
            from ..config import get_settings

            settings = get_settings()

        self.default_method = settings.screenshot_method
        self.default_width = settings.screenshot_width
        self.default_height = settings.screenshot_height
        self.default_format = settings.screenshot_format
        self.block_ads = settings.screenshot_block_ads
        self.full_page = settings.screenshot_full_page
        self.delay = settings.screenshot_delay
        self.pageshot_url = settings.pageshot_api_url

    async def capture(
        self,
        url: str,
        method: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        format: Optional[str] = None,
        full_page: Optional[bool] = None,
        delay: Optional[int] = None,
    ) -> ScreenshotResult:
        """
        Capture a screenshot of a URL.

        Args:
            url: Webpage URL to capture
            method: 'pageshot' or 'playwright' (default from config)
            width: Viewport width (default from config)
            height: Viewport height (default from config)
            format: Image format 'png', 'jpeg', 'webp' (default from config)
            full_page: Capture full scrollable page (default from config)
            delay: Wait before capture in ms (default from config)

        Returns:
            ScreenshotResult with image bytes and metadata
        """
        method = method or self.default_method
        width = width or self.default_width
        height = height or self.default_height
        format = format or self.default_format
        full_page = full_page if full_page is not None else self.full_page
        delay = delay if delay is not None else self.delay

        if method == "playwright":
            return await self._capture_playwright(
                url, width, height, format, full_page, delay
            )
        else:
            return await self._capture_pageshot(
                url, width, height, format, full_page, delay
            )

    async def _capture_pageshot(
        self,
        url: str,
        width: int,
        height: int,
        format: str,
        full_page: bool,
        delay: int,
    ) -> ScreenshotResult:
        """Capture screenshot using external PageShot API."""
        start = time.monotonic()
        logger.info(f"📸 PageShot: capturing {url} ({width}x{height})")

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                self.pageshot_url,
                json={
                    "url": url,
                    "width": width,
                    "height": height,
                    "full_page": full_page,
                    "format": format,
                    "block_ads": self.block_ads,
                    "delay": delay,
                },
            )

            if response.status_code != 200:
                error_text = response.text[:500]
                logger.error(f"❌ PageShot error {response.status_code}: {error_text}")
                raise ScreenshotError(
                    f"PageShot API returned {response.status_code}: {error_text}"
                )

            capture_time = int((time.monotonic() - start) * 1000)
            image_bytes = response.content

            # Try to get capture time from header
            header_time = response.headers.get("X-Screenshot-Time", "")
            if header_time:
                logger.info(f"✅ PageShot: {len(image_bytes)} bytes ({header_time})")
            else:
                logger.info(f"✅ PageShot: {len(image_bytes)} bytes ({capture_time}ms)")

            return ScreenshotResult(
                image_bytes=image_bytes,
                format=format,
                width=width,
                height=height,
                capture_time_ms=capture_time,
                method="pageshot",
                url=url,
            )

    async def _capture_playwright(
        self,
        url: str,
        width: int,
        height: int,
        format: str,
        full_page: bool,
        delay: int,
    ) -> ScreenshotResult:
        """Capture screenshot using internal Playwright (privacy-safe)."""
        start = time.monotonic()
        logger.info(f"📸 Playwright: capturing {url} ({width}x{height})")

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ScreenshotError(
                "Playwright is not installed. Install with: pip install playwright && playwright install chromium"
            )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                )
                page = await context.new_page()

                # Navigate and wait for content
                await page.goto(url, wait_until="networkidle", timeout=30000)

                # Additional delay if configured
                if delay > 0:
                    await page.wait_for_timeout(delay)

                # Capture screenshot
                screenshot_options = {
                    "type": format if format in ("png", "jpeg") else "png",
                    "full_page": full_page,
                }
                if format == "jpeg":
                    screenshot_options["quality"] = 85

                image_bytes = await page.screenshot(**screenshot_options)

                capture_time = int((time.monotonic() - start) * 1000)
                logger.info(
                    f"✅ Playwright: {len(image_bytes)} bytes ({capture_time}ms)"
                )

                return ScreenshotResult(
                    image_bytes=image_bytes,
                    format=format,
                    width=width,
                    height=height,
                    capture_time_ms=capture_time,
                    method="playwright",
                    url=url,
                )
            finally:
                await browser.close()

    async def capture_and_upload_preview(
        self,
        url: str,
        node_id: str,
        repository: str = "staging",
        auth_header: str = "",
        method: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Capture screenshot and upload as preview to edu-sharing node.

        Designed to run as async task in parallel with metadata upload.

        Args:
            url: Webpage URL to capture
            node_id: edu-sharing node ID
            repository: 'staging' or 'prod'
            auth_header: Basic Auth header for edu-sharing
            method: Screenshot method override
            width: Viewport width override
            height: Viewport height override

        Returns:
            Dict with success status and details
        """
        try:
            # 1. Capture screenshot
            result = await self.capture(url, method=method, width=width, height=height)

            # 2. Upload to edu-sharing
            from .repository_service import _get_repository_configs

            config = _get_repository_configs().get(repository)
            if not config:
                return {
                    "success": False,
                    "error": f"Unknown repository: {repository}",
                    "screenshot": result.to_dict(),
                }

            base_url = config["base_url"]
            upload_url = (
                f"{base_url}/rest/node/v1/nodes/-home-/{node_id}/preview"
                f"?mimetype={result.mimetype}&createVersion=true"
            )

            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    upload_url,
                    headers={"Authorization": auth_header},
                    files={
                        "image": ("screenshot.png", result.image_bytes, result.mimetype)
                    },
                )

                if response.status_code in (200, 201, 204):
                    logger.info(f"✅ Preview uploaded to node {node_id}")
                    return {
                        "success": True,
                        "node_id": node_id,
                        "screenshot": result.to_dict(),
                    }
                else:
                    error = response.text[:500]
                    logger.error(
                        f"❌ Preview upload failed: {response.status_code} {error}"
                    )
                    return {
                        "success": False,
                        "error": f"Upload failed: {response.status_code}",
                        "screenshot": result.to_dict(),
                    }

        except Exception as e:
            logger.error(f"❌ Screenshot+upload failed: {type(e).__name__}: {e}")
            return {
                "success": False,
                "error": f"{type(e).__name__}: {e}",
            }


class ScreenshotError(Exception):
    """Raised when screenshot capture fails."""

    pass


# Singleton
_screenshot_service: Optional[ScreenshotService] = None


def get_screenshot_service() -> ScreenshotService:
    """Get screenshot service singleton."""
    global _screenshot_service
    if _screenshot_service is None:
        _screenshot_service = ScreenshotService()
    return _screenshot_service
