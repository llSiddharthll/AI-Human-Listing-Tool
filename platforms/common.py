from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Page

from platforms.base import PlatformBase


class LLMDrivenPlatform(PlatformBase):
    """Shared LLM-guided behavior for all platforms, with platform-specific URLs/prompts."""

    async def login(self, page: Page, credentials: dict[str, str]) -> None:
        await page.goto(self.login_url, wait_until="domcontentloaded")
        instruction = (
            f"Log into {self.name} using these credentials: email/username={credentials.get('username', '')}, "
            "password=<provided in secure manager>. If OTP/2FA appears, wait for human and continue."
        )
        await self.browser.execute_llm_actions(page, instruction)

    async def create_listing(self, page: Page, product: dict[str, Any], image_paths: list[Path]) -> None:
        instruction = (
            f"Create a new product listing on {self.name} with product payload: {product}. "
            "Select the best matching category and fill all mandatory fields naturally."
        )
        await self.browser.execute_llm_actions(page, instruction)
        await self.upload_images(page, image_paths)
        await self.save_listing(page)

    async def edit_listing(self, page: Page, updates: dict[str, Any], sku: str) -> None:
        listing_reference = (
            f"SKU {sku}" if sku and sku != "UNSPECIFIED" else "the listing identified by the provided command context"
        )
        instruction = (
            f"Find {listing_reference} on {self.name} and apply updates: {updates}. "
            "Handle popups, layout changes, and validations."
        )
        await self.browser.execute_llm_actions(page, instruction)
        await self.save_listing(page)

    async def upload_images(self, page: Page, image_paths: list[Path]) -> None:
        for image_path in image_paths:
            instruction = f"Upload this product image on {self.name}: {image_path}"
            await self.browser.execute_llm_actions(page, instruction)

    async def save_listing(self, page: Page) -> None:
        await self.browser.execute_llm_actions(page, f"Click save/publish on {self.name} and verify success message.")
