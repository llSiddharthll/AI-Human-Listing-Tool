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
            f"Log into {self.name}. FIRST: Check if the screen shows a dashboard, product list, or 'Log Out' button "
            "indicating you are ALREADY logged in. If so, return 'done' immediately. "
            f"Otherwise, locate the login fields and enter: email/username={credentials.get('username', '')}, "
            "password=<provided securely>. IMPORTANT: Find and check any 'Trust this device', 'Keep me signed in' "
            "or 'Stay signed in' boxes to ensure future sessions are persisted. AVOID typing into search or filter boxes."
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

    async def edit_listing(self, page: Page, updates: dict[str, Any], sku: str = "UNSPECIFIED") -> None:
        target_title = updates.get("target_title")
        
        if sku and sku != "UNSPECIFIED":
            listing_reference = f"SKU {sku}"
        elif target_title:
            listing_reference = f"product with name/title '{target_title}'"
        else:
            listing_reference = "the listing identified by the provided command context"
            
        instruction = (
            f"Find {listing_reference} on {self.name} and apply updates: {updates}. "
            "If searching by name, ensure you pick the most relevant match. "
            "Handle popups, layout changes, and validations, and save the changes."
        )
        await self.browser.execute_llm_actions(page, instruction)

    async def upload_images(self, page: Page, image_paths: list[Path]) -> None:
        for image_path in image_paths:
            instruction = f"Upload this product image on {self.name}: {image_path}"
            await self.browser.execute_llm_actions(page, instruction)

    async def save_listing(self, page: Page) -> None:
        await self.browser.execute_llm_actions(page, f"Click save/publish on {self.name} and verify success message.")
