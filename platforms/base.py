from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from browser_engine import BrowserEngine


class PlatformBase(ABC):
    """Abstract interface for each marketplace/admin panel."""

    name: str
    login_url: str

    def __init__(self, browser: BrowserEngine) -> None:
        self.browser = browser

    @abstractmethod
    async def login(self, page: Page, credentials: dict[str, str]) -> None:
        ...

    @abstractmethod
    async def create_listing(self, page: Page, product: dict[str, Any], image_paths: list[Path]) -> None:
        ...

    @abstractmethod
    async def edit_listing(self, page: Page, updates: dict[str, Any], sku: str) -> None:
        ...

    @abstractmethod
    async def upload_images(self, page: Page, image_paths: list[Path]) -> None:
        ...

    @abstractmethod
    async def save_listing(self, page: Page) -> None:
        ...
