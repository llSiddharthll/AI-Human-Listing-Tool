from __future__ import annotations

import asyncio
import getpass
import logging
from pathlib import Path
from typing import Any

from browser_engine import BrowserEngine
from config.credentials import CredentialManager
from config.settings import Settings
from data_engine import get_product_image_paths, load_product_data
from llm_engine import GeminiLLMEngine
from platforms.amazon import AmazonPlatform
from platforms.base import PlatformBase
from platforms.flipkart import FlipkartPlatform
from platforms.myntra import MyntraPlatform
from platforms.shopify import ShopifyPlatform


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "automation.log"),
            logging.StreamHandler(),
        ],
    )


def get_platform(platform_name: str, browser: BrowserEngine) -> PlatformBase:
    normalized = platform_name.strip().lower()
    mapping: dict[str, type[PlatformBase]] = {
        "amazon": AmazonPlatform,
        "myntra": MyntraPlatform,
        "flipkart": FlipkartPlatform,
        "shopify": ShopifyPlatform,
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported platform '{platform_name}'. Choose from: {', '.join(mapping)}")
    return mapping[normalized](browser)


def collect_user_inputs() -> dict[str, Any]:
    print("\n=== AI Human Listing Tool ===")
    platform = input("Platform (amazon/myntra/flipkart/shopify): ").strip()
    operation = input("Operation (new_listing/edit_listing/bulk_update): ").strip()
    command = input(
        "Instruction command (e.g., 'List new product', 'Update price of SKU123 to 799'): "
    ).strip()
    data_file = Path(input("Product data file path (.json/.csv): ").strip())
    images_folder = Path(input("Images folder path: ").strip())

    return {
        "platform": platform,
        "operation": operation,
        "command": command,
        "data_file": data_file,
        "images_folder": images_folder,
    }


def ensure_credentials(platform: str, manager: CredentialManager) -> dict[str, str]:
    try:
        return manager.get_credentials(platform)
    except KeyError:
        print(f"No encrypted credentials found for {platform}. Please provide them once.")
        username = input("Username/email: ").strip()
        password = getpass.getpass("Password (hidden): ").strip()
        credentials = {"username": username, "password": password}
        manager.save_credentials(platform, credentials)
        return credentials


async def run() -> None:
    settings = Settings.from_env()
    setup_logging(settings.logs_dir)

    user_input = collect_user_inputs()
    products = load_product_data(user_input["data_file"])

    llm = GeminiLLMEngine(api_key=settings.gemini_api_key, model="gemini-1.5-flash")
    browser = BrowserEngine(llm=llm, session_dir=settings.sessions_dir, headless=settings.browser_headless)

    credential_manager = CredentialManager(store_path=settings.credentials_store)
    credentials = ensure_credentials(user_input["platform"], credential_manager)

    workflow = llm.interpret_user_command(user_input["command"])
    operation = workflow.get("operation") or user_input["operation"]

    platform = get_platform(user_input["platform"], browser)
    context = await browser.start(platform.name.lower().replace(" ", "_"))

    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await platform.login(page, credentials)

        for product in products:
            sku = product["sku"]
            image_paths = get_product_image_paths(user_input["images_folder"], sku)
            if operation == "new_listing":
                await platform.create_listing(page, product, image_paths)
            elif operation in {"edit_listing", "bulk_update"}:
                updates = workflow.get("updates", {})
                await platform.edit_listing(page, updates=updates, sku=workflow.get("sku") or sku)
            else:
                raise ValueError(f"Unsupported operation: {operation}")
    finally:
        await browser.stop()


if __name__ == "__main__":
    asyncio.run(run())
