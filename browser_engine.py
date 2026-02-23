from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from llm_engine import GeminiLLMEngine
from screen_scanner import ScreenScanner

LOGGER = logging.getLogger(__name__)


class BrowserEngine:
    """Human-like Playwright wrapper with adaptive LLM-driven action execution."""

    def __init__(self, llm: GeminiLLMEngine, session_dir: Path, headless: bool = False, otp_callback: Any = None) -> None:
        self.llm = llm
        self.session_dir = session_dir
        self.headless = headless
        self.otp_callback = otp_callback
        self.playwright = None
        self.context: BrowserContext | None = None

    async def start(self, platform_name: str) -> BrowserContext:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = await async_playwright().start()
        user_data_dir = self.session_dir / f"{platform_name}_profile"
        user_data_dir.mkdir(parents=True, exist_ok=True)

        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=self.headless,
            slow_mo=80,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1366, "height": 900}
        )
        return self.context

    async def stop(self) -> None:
        if self.context:
            await self.context.close()
        if hasattr(self, 'browser_instance') and self.browser_instance:
            await self.browser_instance.close()
        if self.playwright:
            await self.playwright.stop()

    async def random_delay(self, min_seconds: float = 0.5, max_seconds: float = 2.5) -> None:
        await asyncio.sleep(random.uniform(min_seconds, max_seconds))

    async def human_type(self, page: Page, selector: str, text: str) -> None:
        await page.click(selector)
        for char in text:
            await page.keyboard.type(char, delay=random.randint(40, 180))
        await self.random_delay()

    async def human_hover_click(self, page: Page, selector: str) -> None:
        locator = page.locator(selector).first
        await locator.hover()
        await self.random_delay(0.2, 0.9)
        await locator.click()
        await self.random_delay()

    async def natural_scroll(self, page: Page, steps: int = 4, direction: str = "down") -> None:
        for _ in range(steps):
            delta = random.randint(200, 650)
            if direction == "up":
                delta = -delta
            await page.mouse.wheel(0, delta)
            await self.random_delay(0.4, 1.2)

    @staticmethod
    def _extract_price_from_instruction(instruction: str) -> str | None:
        """Extract a price value from the instruction text."""
        import re
        # Match "price": 1510 or price: 1510 or price to 1510
        match = re.search(r'(?:price|cost|amount)[\'\"]?\s*(?:to|=|:)?\s*[\$₹€£]?\s*(\d+(?:\.\d{1,2})?)', instruction, re.IGNORECASE)
        if match:
            return match.group(1)
        # Also try standalone numbers at end: "... to 1510"
        match = re.search(r'\bto\s+(\d+(?:\.\d{1,2})?)\s*$', instruction, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    async def execute_llm_actions(self, page: Page, instruction: str, max_cycles: int = 25) -> None:
        scanner = ScreenScanner()
        last_screen_state = ""
        stuck_count = 0
        MAX_STUCK = 3
        failed_targets: list[str] = []
        price = self._extract_price_from_instruction(instruction)
        modal_dismiss_attempts = 0
        scroll_attempts = 0
        is_login_task = "login" in instruction.lower() or "log in" in instruction.lower() or "sign in" in instruction.lower()

        for cycle in range(max_cycles):
            # Wait for page to settle
            await self.random_delay(0.5, 1.5)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass

            # ── PHASE 1: Fast DOM-based scanning (NO LLM calls) ──
            page_state = await scanner.detect_page_state(page)
            page_type = page_state['page_type']
            LOGGER.info("Cycle %d/%d | Scanner: type=%s products=%d modal=%s | URL: %s",
                cycle + 1, max_cycles, page_type, page_state['product_count'],
                page_state['has_modal'], page.url)

            # If this is a login task and we're already on products/dashboard, we're logged in!
            if is_login_task and page_type in ('product_list', 'product_detail', 'dashboard', 'orders'):
                LOGGER.info("Scanner: Already logged in (on %s page). Skipping login.", page_type)
                return

            # Handle modals — but only try 2 times, then just proceed with force clicks
            if page_state['has_modal'] and modal_dismiss_attempts < 2:
                modal_dismiss_attempts += 1
                dismissed = await scanner.dismiss_modals(page)
                if dismissed:
                    await self.random_delay(0.5, 1.0)
                    continue
            elif page_state['has_modal'] and modal_dismiss_attempts >= 2:
                LOGGER.info("Scanner: Modal persists after %d attempts. Proceeding anyway with force clicks.", modal_dismiss_attempts)
                # Don't continue — fall through to product clicking

            # Dashboard -> Navigate to products
            if page_type == 'dashboard':
                navigated = await scanner.navigate_to_products(page)
                if navigated:
                    modal_dismiss_attempts = 0
                    await self.random_delay(0.5, 1.0)
                    continue

            # Product list -> Click first product (even with modal, use force)
            if page_type == 'product_list' and page_state['has_products']:
                use_force = page_state['has_modal']
                clicked = await scanner.click_first_product(page, force=use_force)
                if clicked:
                    modal_dismiss_attempts = 0
                    await self.random_delay(0.5, 1.0)
                    continue

            # Product detail -> Fill price and save
            if page_type == 'product_detail' and price:
                # Dismiss modal first if present, then try filling
                if page_state['has_modal']:
                    await scanner.dismiss_modals(page)
                    await page.wait_for_timeout(300)
                
                filled = await scanner.find_and_fill_price(page, price)
                if filled:
                    await self.random_delay(0.5, 1.0)
                    saved = await scanner.click_save(page)
                    if saved:
                        LOGGER.info("Scanner: Price updated to %s and saved! Done.", price)
                        return
                    else:
                        LOGGER.warning("Scanner: Filled price but failed to find save button.")
                        return  # Mission accomplished mostly
                
                if scroll_attempts < 4:
                    LOGGER.info("Scanner: Price field not found yet. Scrolling down...")
                    await page.mouse.wheel(0, 600)
                    await self.random_delay(0.5, 1.0)
                    scroll_attempts += 1
                    continue
                else:
                    LOGGER.info("Scanner: Scrolled 4 times but no price field found. Falling back to LLM.")

            # ── PHASE 2: LLM fallback (only if Gemini is available) ──
            if len(self.llm._exhausted_combos) >= len(self.llm._api_keys) * 4:
                stuck_count += 1
                if stuck_count >= MAX_STUCK:
                    LOGGER.error("Scanner completely stuck and all Gemini combos exhausted. Aborting.")
                    return
                LOGGER.info("All Gemini combos exhausted. Scanner-only mode (no LLM calls) [%d/%d stuck limits].", stuck_count, MAX_STUCK)
                await self.random_delay(1.0, 2.0)
                continue

            LOGGER.info("Scanner couldn't handle this page. Falling back to LLM...")

            screenshot_bytes = await page.screenshot(full_page=False)

            enhanced_instruction = instruction
            if failed_targets:
                recent_failures = failed_targets[-5:]
                enhanced_instruction += f"\n\nIMPORTANT: These targets could NOT be found: {recent_failures}. Try different targets or use 'navigate' action."

            decision = await self.llm.analyze_screen_with_llm(screenshot_bytes, enhanced_instruction)
            screen_state = decision.get("screen_state", "Unknown")
            LOGGER.info("  LLM screen: %s", screen_state)
            
            actions = decision.get("actions", [])
            risk = decision.get("risk", "none")

            # Log all returned actions
            for i, act in enumerate(actions):
                LOGGER.info("  Action[%d]: %s -> target='%s' value='%s' reason='%s'",
                    i, act.get("action"), act.get("target", ""), act.get("value", ""), act.get("reason", ""))

            # Stuck detection
            if screen_state == last_screen_state:
                stuck_count += 1
                if stuck_count >= MAX_STUCK:
                    LOGGER.warning("Stuck for %d cycles on '%s'. Trying to break out.", stuck_count, screen_state)
                    # Try scrolling or navigating differently
                    instruction += " (CRITICAL: You are STUCK. The previous actions did NOT change the page. Try: 1) scroll down to find elements, 2) use a completely different navigation approach, 3) look at the page URL and suggest navigating to a direct URL path like /admin/products or /products if applicable.)"
                    stuck_count = 0
                    failed_targets.clear()
            else:
                stuck_count = 0
                failed_targets.clear()  # New screen = reset failures
            last_screen_state = screen_state

            if risk in {"captcha", "2fa"}:
                LOGGER.warning("Risk '%s' detected.", risk)
                if risk == "2fa" and self.otp_callback:
                    LOGGER.info("Attempting to get OTP via callback...")
                    otp_code = await self.otp_callback()
                    if otp_code:
                        instruction += f" (Note: The user provided OTP/2FA code is {otp_code}. Locate the OTP input field and enter this value.)"
                        LOGGER.info("OTP received and added to instructions.")
                    else:
                        LOGGER.warning("No OTP received from callback.")
                else:
                    LOGGER.warning("Waiting for manual intervention (10s)...")
                    await self.random_delay(10, 15)

            done = False
            any_action_succeeded = False
            for action in actions:
                result = await self._execute_action(page, action)
                if result is True:
                    done = True
                    break
                elif result is None:
                    # Action target not found - track it
                    target = action.get("target", "")
                    if target and target not in failed_targets:
                        failed_targets.append(target)
                else:
                    # Action executed (result is False = not done yet but action went through)
                    any_action_succeeded = True
                    
            if done:
                return

            # If no actions succeeded at all, add extra wait to avoid burning quota
            if not any_action_succeeded:
                LOGGER.info("No actions succeeded this cycle. Extra wait before retry.")
                await self.random_delay(3.0, 5.0)

        raise RuntimeError("LLM action loop exhausted before completion.")

    async def _execute_action(self, page: Page, action: dict[str, Any]) -> bool:
        action_type = action.get("action", "wait")
        target = action.get("target", "")
        value = action.get("value", "")

        if action_type == "done":
            LOGGER.info("Action 'done': %s", action.get("reason", "Goal achieved"))
            return True
        if action_type == "wait":
            LOGGER.info("Action 'wait': %s", action.get("reason", "No reason provided"))
            await self.random_delay(1, 3)
            return False
        if action_type == "scroll":
            direction = str(value).lower() if str(value).lower() == "up" else "down"
            LOGGER.info("Action 'scroll': direction=%s", direction)
            await self.natural_scroll(page, direction=direction)
            return False
        if action_type == "navigate":
            url = str(value or target)
            LOGGER.info("Action 'navigate': going to %s", url)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                await self.random_delay(1.5, 3.0)
            except Exception as e:
                LOGGER.warning("Navigation to '%s' failed: %s", url, e)
            return False
        if action_type == "select":
            LOGGER.info("Action 'select': target='%s' value='%s'", target, value)
            try:
                await page.select_option(target, value)
                await self.random_delay()
            except Exception:
                LOGGER.warning("Select on '%s' failed, trying by label.", target)
            return False

        # Semantic targeting fallback: try label/placeholder/aria text similarity.
        locator = page.get_by_label(target)
        if await locator.count() == 0:
            locator = page.get_by_placeholder(target)
        if await locator.count() == 0:
            locator = page.get_by_role("button", name=target)
        if await locator.count() == 0:
            locator = page.get_by_role("link", name=target)
        if await locator.count() == 0:
            locator = page.get_by_text(target)
        if await locator.count() == 0:
            locator = page.get_by_title(target)
        if await locator.count() == 0:
            locator = page.get_by_alt_text(target)
        if await locator.count() == 0:
            # Last ditch effort: ONLY try as a selector if it's alphanumeric/basic CSS
            # Avoid targets with spaces/parentheses/etc which are definitely not simple selectors
            import re
            is_probably_selector = re.match(r"^[a-zA-Z0-9.#-_ >+]+$", target)
            if is_probably_selector:
                try:
                    temp_locator = page.locator(target)
                    if await temp_locator.count() > 0:
                        locator = temp_locator
                except Exception:
                    pass

        if await locator.count() == 0:
            LOGGER.warning("Could not find target '%s' for action '%s'.", target, action_type)
            return None  # None = target not found (tracked for feedback)

        # Ensure visible
        try:
            await locator.first.scroll_into_view_if_needed()
            await locator.first.wait_for(state="visible", timeout=3000)
        except Exception:
            LOGGER.warning("Target '%s' not visible for action '%s'.", target, action_type)
            return None  # None = target not visible (tracked for feedback)

        try:
            if action_type == "click":
                try:
                    await locator.first.hover(timeout=5000)
                    await self.random_delay(0.1, 0.5)
                    await locator.first.click(timeout=5000)
                except Exception:
                    # Likely a modal/overlay blocking — try Escape first, then force click
                    LOGGER.info("Click blocked (overlay?). Pressing Escape and retrying with force...")
                    await page.keyboard.press("Escape")
                    await self.random_delay(0.5, 1.0)
                    try:
                        await locator.first.click(force=True, timeout=5000)
                    except Exception:
                        LOGGER.warning("Force click also failed for '%s'. Skipping.", target)
                        return None
                await self.random_delay()
            elif action_type == "hover":
                await locator.first.hover(timeout=5000)
                await self.random_delay()
            elif action_type == "type":
                await locator.first.click(timeout=5000)
                for char in str(value):
                    await page.keyboard.type(char, delay=random.randint(45, 170))
                await self.random_delay()
            elif action_type == "press":
                await page.keyboard.press(str(value or "Enter"))
                await self.random_delay()
            elif action_type == "upload":
                await locator.first.set_input_files(str(value))
                await self.random_delay(1.2, 2.6)
        except Exception as e:
            LOGGER.warning("Action '%s' on '%s' failed: %s. Continuing...", action_type, target, str(e)[:100])
            return None

        return False
