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


def collect_user_inputs() -> dict[str, Any]:
    print("\n=== AI Human Listing Tool ===")
    platform = input("Platform (amazon/myntra/flipkart/shopify): ").strip()
    operation = input("Operation (new_listing/edit_listing/bulk_update): ").strip()
    command = input(
        "Instruction command (e.g., 'List new product', 'Update price of SKU123 to 799'): "
    ).strip()
    data_file_input = input("Product data file path (.json/.csv): ").strip()
    images_folder_input = input("Images folder path: ").strip()

    return {
        "platform": platform,
        "operation": operation,
        "command": command,
        "data_file": Path(data_file_input) if data_file_input else None,
        "images_folder": Path(images_folder_input) if images_folder_input else None,
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

    if not user_input["platform"]:
        raise ValueError("Platform is required.")

    if not user_input["operation"]:
        raise ValueError("Operation is required.")

    operation = user_input["operation"].strip().lower()
    data_file: Path | None = user_input["data_file"]
    images_folder: Path | None = user_input["images_folder"]

    if operation == "new_listing" and not data_file:
        raise ValueError("Product data file is required for new listings.")

    products: list[dict[str, Any]] = []
    if data_file:
        products = load_product_data(data_file, strict=False)

    if not user_input["platform"]:
        raise ValueError("Platform is required.")

    if not user_input["operation"]:
        raise ValueError("Operation is required.")

    operation = user_input["operation"].strip().lower()
    data_file: Path | None = user_input["data_file"]
    images_folder: Path | None = user_input["images_folder"]

    if operation == "new_listing" and not data_file:
        raise ValueError("Product data file is required for new listings.")

    products: list[dict[str, Any]] = []
    if data_file:
        products = load_product_data(data_file, strict=False)

    llm = GeminiLLMEngine(api_key=settings.gemini_api_key)
    browser = BrowserEngine(llm=llm, session_dir=settings.sessions_dir, headless=settings.browser_headless)

    credential_manager = CredentialManager(store_path=settings.credentials_store)
    credentials = ensure_credentials(user_input["platform"], credential_manager)

    workflow = llm.interpret_user_command(user_input["command"])
    operation = str(workflow.get("operation") or operation).strip().lower()

    if operation in {"edit_listing", "bulk_update"} and not products:
        inferred_sku = workflow.get("sku")
        if inferred_sku:
            products = [{"sku": inferred_sku}]
        else:
            raise ValueError(
                "No product data provided and no SKU could be inferred from instruction. "
                "Provide a product file or include SKU in command."
            )

    platform = get_platform(user_input["platform"], browser)
    context = await browser.start(platform.name.lower().replace(" ", "_"))

    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await platform.login(page, credentials)

        for product in products:
            sku = product["sku"]
            try:
                image_paths: list[Path] = []
                if images_folder:
                    try:
                        image_paths = get_product_image_paths(images_folder, sku)
                    except Exception as image_error:
                        LOGGER.warning("Image loading failed for sku=%s: %s", sku, image_error)

                if operation == "new_listing":
                    await platform.create_listing(page, product, image_paths)
                elif operation in {"edit_listing", "bulk_update"}:
                    updates = workflow.get("updates", {})
                    await platform.edit_listing(page, updates=updates, sku=workflow.get("sku") or sku)
                else:
                    raise ValueError(f"Unsupported operation: {operation}")
            except Exception as product_error:
                LOGGER.exception("Failed processing sku=%s", sku)
                print(f"Warning: failed processing SKU {sku}: {product_error}")
    finally:
        await browser.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as error:
        logging.exception("Fatal error while executing listing workflow.")
        print(f"Error: {error}")
