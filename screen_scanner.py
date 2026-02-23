"""
DOM-based screen scanner that performs fast actions WITHOUT calling the LLM.
Scans the actual page HTML to detect state and act directly.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from playwright.async_api import Page

LOGGER = logging.getLogger(__name__)


class ScreenScanner:
    """Scans the browser DOM directly to detect page state and perform fast actions."""

    @staticmethod
    async def detect_page_state(page: Page) -> dict[str, Any]:
        """Analyze URL and DOM to determine the current page type and available actions."""
        state = {
            "url": "",
            "title": "",
            "page_type": "unknown",
            "has_modal": False,
            "has_products": False,
            "product_count": 0,
            "actionable": False,
        }
        try:
            url = page.url.lower()
            state["url"] = url
        except Exception:
            return state

        try:
            state["title"] = await page.title()
        except Exception:
            pass

        try:
            modal_count = await page.locator("[role='dialog']:visible, .Polaris-Modal:visible, .modal:visible").count()
            state["has_modal"] = modal_count > 0
        except Exception:
            pass

        # Detect page type from URL
        if "login" in url or "signin" in url or "sign-in" in url:
            state["page_type"] = "login"
        elif "/products" in url and not re.search(r"/products/\d+", url):
            state["page_type"] = "product_list"
        elif re.search(r"/products/\d+", url):
            state["page_type"] = "product_detail"
        elif "/admin" in url or "dashboard" in url or "seller" in url:
            state["page_type"] = "dashboard"
        elif "/orders" in url:
            state["page_type"] = "orders"

        if state["page_type"] == "product_list":
            try:
                products = await page.locator("table tbody tr, [class*='ResourceItem'], [class*='IndexTable'] tbody tr, .product-item, [data-primary-link]").count()
                state["product_count"] = products
                state["has_products"] = products > 0
                state["actionable"] = True
            except Exception:
                pass

        return state

    @staticmethod
    async def dismiss_modals(page: Page) -> bool:
        """Try to close any open modals/popups/overlays fast. Returns True if anything was dismissed."""
        dismissed = False

        # Try pressing Escape first
        try:
            modal = page.locator("[role='dialog'], .Polaris-Modal, .modal").first
            if await modal.count() > 0:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(200)
                dismissed = True
                LOGGER.info("Scanner: Dismissed modal via Escape")
        except Exception:
            pass

        # Try clicking close/cancel buttons inside modals FORCEFULLY
        for selector in ["[role='dialog'] button[aria-label*='Close' i]", ".Polaris-Modal button:has-text('Close')",
                         ".Polaris-Modal button:has-text('Cancel')", "button[aria-label*='Close' i]",
                         ".modal .close", ".modal-close"]:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click(force=True, timeout=200)
                    dismissed = True
                    LOGGER.info("Scanner: Dismissed modal via '%s' (force)", selector)
                    break
            except Exception:
                continue

        return dismissed

    @staticmethod
    async def click_first_product(page: Page, force: bool = False) -> bool:
        """Click on the first/latest product in a product list. Returns True if clicked."""
        selectors = [
            # Shopify Polaris — try links first (most reliable)
            "table tbody tr:first-child a[href*='/products/']",
            "[class*='IndexTable'] tbody tr:first-child a[href*='/products/']",
            "table tbody tr:first-child a",
            "[data-primary-link]:first-child a",
            "[data-primary-link]:first-child",
            # Generic
            "table tbody tr:first-child td:nth-child(2)",
            "table tbody tr:first-child",
            ".product-item:first-child a",
        ]

        for selector in selectors:
            try:
                el = page.locator(selector).first
                if await el.count() > 0:
                    await el.click(force=True, timeout=500)
                    LOGGER.info("Scanner: Clicked first product via '%s' (force)", selector)
                    await page.wait_for_timeout(1500)
                    return True
            except Exception as e:
                LOGGER.debug("Selector '%s' failed: %s", selector, str(e)[:60])
                continue

        # Last resort: try navigating to first product link directly
        try:
            for link in await page.locator("a[href*='/products/']").all():
                href = await link.get_attribute("href")
                if href and "/inventory" not in href and "/gift_cards" not in href and "/transfers" not in href and "/collections" not in href and "/new" not in href:
                    if href.endswith("/products") or href.endswith("/products/"):
                        continue  # skip the main products list link
                    full_url = href if href.startswith("http") else f"https://admin.shopify.com{href}"
                    await page.goto(full_url, wait_until="domcontentloaded", timeout=10000)
                    LOGGER.info("Scanner: Navigated directly to first product: %s", full_url)
                    return True
        except Exception:
            pass

        return False

    @staticmethod
    async def find_and_fill_price(page: Page, price: str) -> bool:
        """Find a price input field and fill it with the given value fast."""
        # Common price field selectors
        selectors = [
            "input[name='price']",
            "input[name='Price']",
            "input[name*='price' i]",
            "input[id*='price' i]",
            "input[placeholder*='price' i]",
            "input[aria-label*='price' i]",
            "input[name*='Price']",
            "[class*='price'] input",
        ]

        for selector in selectors:
            try:
                # Require the element to be visible
                field = page.locator(f"{selector}:visible").first
                if await field.count() > 0:
                    await field.fill(price, timeout=800)
                    await page.keyboard.press("Tab") # trigger blur/validation
                    LOGGER.info("Scanner: Filled price '%s' via '%s'", price, selector)
                    return True
            except Exception:
                # If fill fails due to strict interactivity checks, fall back to force
                try:
                    field = page.locator(f"{selector}:visible").first
                    if await field.count() > 0:
                        await field.click(force=True, timeout=500)
                        await field.fill(price, force=True, timeout=500)
                        await page.keyboard.press("Tab")
                        LOGGER.info("Scanner: Click+Filled price '%s' via '%s' (force)", price, selector)
                        return True
                except Exception:
                    continue

        # Fallback: look for labels containing "Price" and find nearby inputs
        try:
            label = page.locator("label:has-text('Price'):visible").first
            if await label.count() > 0:
                label_for = await label.get_attribute("for")
                if label_for:
                    field = page.locator(f"#{label_for}:visible")
                    if await field.count() > 0:
                        await field.fill(price, timeout=800)
                        await page.keyboard.press("Tab")
                        LOGGER.info("Scanner: Filled price '%s' via label 'for'", price)
                        return True
        except Exception:
            pass

        return False

    @staticmethod
    async def click_save(page: Page) -> bool:
        """Find and click a Save/Publish button fast."""
        selectors = [
            "button:has-text('Save')",
            "button:has-text('Save product')",
            "button:has-text('Publish')",
            "button[type='submit']:has-text('Save')",
            "input[type='submit'][value*='Save']",
            "button:has-text('Update')",
            "button[aria-label*='Save' i]",
        ]

        # Wait a moment for save button to enable after blurring input
        await page.wait_for_timeout(500)

        for selector in selectors:
            try:
                # Ensure the save button is not disabled
                btn = page.locator(f"{selector}:visible:not([disabled])").first
                if await btn.count() > 0:
                    await btn.click(timeout=1000)
                    LOGGER.info("Scanner: Clicked save via '%s'", selector)
                    await page.wait_for_timeout(1000)
                    return True
            except Exception:
                # Try with force if normal click timeout fails due to overlay
                try:
                    btn = page.locator(f"{selector}:visible:not([disabled])").first
                    if await btn.count() > 0:
                        await btn.click(force=True, timeout=1000)
                        LOGGER.info("Scanner: Clicked save via '%s' (force)", selector)
                        await page.wait_for_timeout(1000)
                        return True
                except Exception:
                    continue

        return False

    @staticmethod
    async def navigate_to_products(page: Page) -> bool:
        """Navigate to the products page using known platform URL patterns."""
        url = page.url.lower()

        # Shopify
        if "shopify" in url or "myshopify" in url:
            base = re.match(r"(https://admin\.shopify\.com/store/[^/]+)", url)
            if base:
                products_url = f"{base.group(1)}/products"
                await page.goto(products_url, wait_until="domcontentloaded", timeout=15000)
                LOGGER.info("Scanner: Navigated to Shopify products: %s", products_url)
                return True

        # Amazon Seller Central
        if "sellercentral" in url:
            await page.goto("https://sellercentral.amazon.com/inventory", wait_until="domcontentloaded", timeout=15000)
            LOGGER.info("Scanner: Navigated to Amazon inventory")
            return True

        # Try clicking "Products" link as fallback
        try:
            link = page.get_by_role("link", name="Products").first
            if await link.count() > 0:
                await link.click(timeout=5000)
                LOGGER.info("Scanner: Clicked 'Products' link")
                await page.wait_for_timeout(1500)
                return True
        except Exception:
            pass

        return False

    @staticmethod
    async def extract_page_text_summary(page: Page) -> str:
        """Extract a short text summary of visible page content for LLM context."""
        try:
            # Get visible text from main content area
            text = await page.locator("main, [role='main'], #content, .content, body").first.inner_text(timeout=3000)
            # Limit to 500 chars to save tokens
            return text[:500].strip()
        except Exception:
            return ""
