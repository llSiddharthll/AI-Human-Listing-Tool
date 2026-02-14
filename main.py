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


def load_products_for_operation(operation: str, data_file: Path | None) -> list[dict[str, Any]]:
    if not data_file:
        return []

    try:
        if operation == "new_listing":
            return load_product_data(data_file, strict=False)

        # Edit/bulk updates can work with partial data rows (title/category/price etc.)
        raw_rows = load_product_data(data_file, strict=False, required_fields=set())
        cleaned_rows: list[dict[str, Any]] = []
        for row in raw_rows:
            if any(str(value).strip() for value in row.values()):
                cleaned_rows.append(row)
        if not cleaned_rows:
            raise ValueError("Provided data file has no usable rows.")
        return cleaned_rows
    except Exception as error:
        LOGGER.warning("Could not load product data from '%s': %s", data_file, error)
        return []


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def build_edit_tasks(products: list[dict[str, Any]], workflow: dict[str, Any]) -> list[dict[str, Any]]:
    workflow_updates = _safe_dict(workflow.get("updates"))
    workflow_filters = _safe_dict(workflow.get("filters"))
    workflow_sku = str(workflow.get("sku") or "").strip()

    if not products:
        contextual_updates = dict(workflow_updates)
        for key, value in workflow_filters.items():
            if value:
                contextual_updates[f"target_{key}"] = value
        return [
            {
                "sku": workflow_sku or "UNSPECIFIED",
                "updates": contextual_updates,
            }
        ]

    tasks: list[dict[str, Any]] = []
    for row in products:
        if not isinstance(row, dict):
            continue

        sku = str(row.get("sku") or workflow_sku or "").strip() or "UNSPECIFIED"
        row_filters: dict[str, Any] = {}

        if row.get("title"):
            row_filters["target_title"] = str(row["title"])
        elif row.get("name"):
            row_filters["target_title"] = str(row["name"])

        if row.get("category"):
            row_filters["target_category"] = str(row["category"])
        if row.get("brand"):
            row_filters["target_brand"] = str(row["brand"])

        for key, value in workflow_filters.items():
            if value and f"target_{key}" not in row_filters:
                row_filters[f"target_{key}"] = value

        row_updates = {
            key: value
            for key, value in row.items()
            if key not in IDENTIFIER_FIELDS and value not in {None, ""}
        }
        combined_updates = {**row_filters, **row_updates, **workflow_updates}

        if not combined_updates:
            combined_updates = dict(workflow_updates)
            if row_filters:
                combined_updates.update(row_filters)

        tasks.append({"sku": sku, "updates": combined_updates})

    return tasks or [{"sku": workflow_sku or "UNSPECIFIED", "updates": workflow_updates}]


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
