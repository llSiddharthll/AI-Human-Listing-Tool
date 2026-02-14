from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from llm_engine import GeminiLLMEngine

LOGGER = logging.getLogger(__name__)


class BrowserEngine:
    """Human-like Playwright wrapper with adaptive LLM-driven action execution."""

    def __init__(self, llm: GeminiLLMEngine, session_dir: Path, headless: bool = False) -> None:
        self.llm = llm
        self.session_dir = session_dir
        self.headless = headless
        self.playwright = None
        self.context: BrowserContext | None = None

    async def start(self, platform_name: str) -> BrowserContext:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = await async_playwright().start()
        user_data_dir = str(self.session_dir / platform_name)

        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=self.headless,
            viewport={"width": 1366, "height": 900},
            slow_mo=80,
            args=["--disable-blink-features=AutomationControlled"],
        )
        return self.context

    async def stop(self) -> None:
        if self.context:
            await self.context.close()
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

    async def natural_scroll(self, page: Page, steps: int = 4) -> None:
        for _ in range(steps):
            delta = random.randint(200, 650)
            await page.mouse.wheel(0, delta)
            await self.random_delay(0.4, 1.2)

    async def execute_llm_actions(self, page: Page, instruction: str, max_cycles: int = 12) -> None:
        for cycle in range(max_cycles):
            screenshot_path = Path("sessions") / f"llm_cycle_{cycle}.png"
            await page.screenshot(path=str(screenshot_path), full_page=True)
            decision = self.llm.analyze_screen_with_llm(screenshot_path, instruction)
            actions = decision.get("actions", [])
            risk = decision.get("risk", "none")

            if risk in {"captcha", "2fa"}:
                LOGGER.warning("Risk '%s' detected. Waiting for manual intervention.", risk)
                await self.random_delay(8, 12)

            done = False
            for action in actions:
                if await self._execute_action(page, action):
                    done = True
                    break
            if done:
                return

        raise RuntimeError("LLM action loop exhausted before completion.")

    async def _execute_action(self, page: Page, action: dict[str, Any]) -> bool:
        action_type = action.get("action", "wait")
        target = action.get("target", "")
        value = action.get("value", "")

        if action_type == "done":
            return True
        if action_type == "wait":
            await self.random_delay(1, 3)
            return False
        if action_type == "scroll":
            await self.natural_scroll(page)
            return False

        # Semantic targeting fallback: try label/placeholder/aria text similarity.
        locator = page.get_by_label(target)
        if await locator.count() == 0:
            locator = page.get_by_placeholder(target)
        if await locator.count() == 0:
            locator = page.get_by_role("button", name=target)
        if await locator.count() == 0:
            locator = page.get_by_text(target)
        if await locator.count() == 0:
            LOGGER.warning("Could not find target '%s' for action '%s'.", target, action_type)
            await self.random_delay(0.8, 1.6)
            return False

        if action_type == "click":
            await locator.first.hover()
            await self.random_delay(0.1, 0.5)
            await locator.first.click()
            await self.random_delay()
        elif action_type == "hover":
            await locator.first.hover()
            await self.random_delay()
        elif action_type == "type":
            await locator.first.click()
            for char in str(value):
                await page.keyboard.type(char, delay=random.randint(45, 170))
            await self.random_delay()
        elif action_type == "press":
            await page.keyboard.press(str(value or "Enter"))
            await self.random_delay()
        elif action_type == "upload":
            await locator.first.set_input_files(str(value))
            await self.random_delay(1.2, 2.6)

        return False
