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

LOGGER = logging.getLogger(__name__)


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


def _optional_path(prompt: str) -> Path | None:
    value = input(prompt).strip()
    return Path(value) if value else None


def collect_user_inputs() -> dict[str, Any]:
    print("\n=== AI Human Listing Tool ===")
    platform = input("Platform (amazon/myntra/flipkart/shopify): ").strip().lower()
    operation = input("Operation (new_listing/edit_listing/bulk_update): ").strip().lower()
    command = input(
        "Instruction command (e.g., 'List new product', 'Update price of SKU123 to 799'): "
    ).strip()

    requires_data = operation in {"new_listing", "bulk_update"}
    requires_images = operation == "new_listing"

    if requires_data:
        data_file = Path(input("Product data file path (.json/.csv): ").strip())
    else:
        data_file = _optional_path("Product data file path (.json/.csv) [optional for edit_listing]: ")

    if requires_images:
        images_folder = Path(input("Images folder path: ").strip())
    else:
        images_folder = _optional_path("Images folder path [optional for edit_listing]: ")

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

    llm = GeminiLLMEngine(api_key=settings.gemini_api_key, model=settings.gemini_model)
    workflow = llm.interpret_user_command(user_input["command"])
    operation = (workflow.get("operation") or user_input["operation"]).lower()

    products = load_product_data(
        user_input["data_file"],
        allow_empty=operation == "edit_listing",
    )

    # Allow edit flows to run from natural-language command even without file input.
    if operation == "edit_listing" and not products:
        sku = workflow.get("sku") or ""
        products = [{"sku": sku}] if sku else [{}]

    browser = BrowserEngine(llm=llm, session_dir=settings.sessions_dir, headless=settings.browser_headless)

    credential_manager = CredentialManager(store_path=settings.credentials_store)
    credentials = ensure_credentials(user_input["platform"], credential_manager)

    platform = get_platform(user_input["platform"], browser)
    context = await browser.start(platform.name.lower().replace(" ", "_"))

    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await platform.login(page, credentials)

        for product in products:
            sku = str(product.get("sku") or workflow.get("sku") or "").strip()
            if operation == "new_listing":
                if not sku:
                    raise ValueError("Each new listing product requires a valid SKU.")
                image_paths = get_product_image_paths(user_input["images_folder"], sku, required=True)
                await platform.create_listing(page, product, image_paths)
            elif operation == "bulk_update":
                if not sku:
                    raise ValueError("Each bulk_update record requires a valid SKU.")
                updates = workflow.get("updates", {})
                await platform.edit_listing(page, updates=updates, sku=sku)
            elif operation == "edit_listing":
                updates = workflow.get("updates", {})
                await platform.edit_listing(page, updates=updates, sku=sku)
            else:
                raise ValueError(f"Unsupported operation: {operation}")
    finally:
        await browser.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Automation failed: %s", exc)
        print(f"\n[ERROR] {exc}\nTip: For edit_listing you can leave product/images blank if command includes target SKU.")
        raise
