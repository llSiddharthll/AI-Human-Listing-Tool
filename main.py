from __future__ import annotations

import asyncio
import getpass
import json
import logging
import re
from datetime import datetime, timezone
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
IDENTIFIER_FIELDS = {"sku", "title", "name", "category", "brand"}


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


def sanitize_text(value: str) -> str:
    return "".join(char for char in value if char.isprintable()).strip()


def append_cache_event(cache_file: Path, event_type: str, payload: dict[str, Any]) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "payload": payload,
    }
    with cache_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


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
    platform = sanitize_text(input("Platform (amazon/myntra/flipkart/shopify): "))
    operation = sanitize_text(input("Operation (new_listing/edit_listing/bulk_update): "))
    command = sanitize_text(
        input("Instruction command (e.g., 'List new product', 'Update price of SKU123 to 799'): ")
    )
    data_file_input = sanitize_text(input("Product data file path (.json/.csv): "))
    images_folder_input = sanitize_text(input("Images folder path: "))

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
        username = sanitize_text(input("Username/email: "))
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

        raw_rows = load_product_data(data_file, strict=False, required_fields=set())
        cleaned_rows = [row for row in raw_rows if any(str(value).strip() for value in row.values())]
        if not cleaned_rows:
            raise ValueError("Provided data file has no usable rows.")
        return cleaned_rows
    except Exception as error:
        LOGGER.warning("Could not load product data from '%s': %s", data_file, error)
        return []


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _needs_clarification(workflow: dict[str, Any], products: list[dict[str, Any]], operation: str) -> bool:
    if operation not in {"edit_listing", "bulk_update"}:
        return False
    if products:
        return False

    updates = _safe_dict(workflow.get("updates"))
    filters = _safe_dict(workflow.get("filters"))
    sku = str(workflow.get("sku") or "").strip()
    return not sku and not updates and not filters


def enrich_workflow_from_followup(workflow: dict[str, Any]) -> dict[str, Any]:
    print("\n😄 Quick helper mode: no SKU? No stress. I can target by name/category/brand.")
    identifier = sanitize_text(
        input("Give SKU or product name or category (example: SKU123 / Rose Gold Ring / rings): ")
    )
    field_name = sanitize_text(input("Which field to change? (title/price/description/stock/etc): ")).lower()
    field_value = sanitize_text(input("New value for that field: "))

    merged = dict(workflow)
    merged_updates = _safe_dict(workflow.get("updates"))
    merged_filters = _safe_dict(workflow.get("filters"))

    if field_name and field_value:
        merged_updates[field_name] = field_value

    if identifier:
        sku_like = re.match(r"^[A-Za-z]{1,6}[A-Za-z0-9_-]*\d+[A-Za-z0-9_-]*$", identifier)
        if sku_like:
            merged["sku"] = identifier
        elif any(token in identifier.lower() for token in {"ring", "shirt", "shoe", "watch", "dress"}):
            merged_filters.setdefault("category", identifier)
        else:
            merged_filters.setdefault("title", identifier)

    merged["updates"] = merged_updates
    merged["filters"] = merged_filters
    merged.setdefault("operation", "edit_listing")
    merged.setdefault("notes", "User follow-up clarification applied.")
    return merged


def build_edit_tasks(products: list[dict[str, Any]], workflow: dict[str, Any]) -> list[dict[str, Any]]:
    workflow_updates = _safe_dict(workflow.get("updates"))
    workflow_filters = _safe_dict(workflow.get("filters"))
    workflow_sku = str(workflow.get("sku") or "").strip()

    if not products:
        contextual_updates = dict(workflow_updates)
        for key, value in workflow_filters.items():
            if value:
                contextual_updates[f"target_{key}"] = value
        return [{"sku": workflow_sku or "UNSPECIFIED", "updates": contextual_updates}]

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
            key: value for key, value in row.items() if key not in IDENTIFIER_FIELDS and value not in {None, ""}
        }
        combined_updates = {**row_filters, **row_updates, **workflow_updates}
        if not combined_updates:
            combined_updates = {**row_filters, **workflow_updates}

        tasks.append({"sku": sku, "updates": combined_updates})

    return tasks or [{"sku": workflow_sku or "UNSPECIFIED", "updates": workflow_updates}]


async def run() -> None:
    settings = Settings.from_env()
    setup_logging(settings.logs_dir)
    cache_file = settings.sessions_dir / "user_action_cache" / "actions.jsonl"

    user_input = collect_user_inputs()
    append_cache_event(cache_file, "user_input", {
        "platform": user_input["platform"],
        "operation": user_input["operation"],
        "command": user_input["command"],
        "data_file": str(user_input["data_file"] or ""),
    })

    if not user_input["platform"]:
        raise ValueError("Platform is required.")

    if not user_input["operation"]:
        raise ValueError("Operation is required.")

    operation = user_input["operation"].strip().lower()
    data_file: Path | None = user_input["data_file"]
    images_folder: Path | None = user_input["images_folder"]

    if operation == "new_listing" and not data_file:
        raise ValueError("Product data file is required for new listings.")

    products = load_products_for_operation(operation, data_file)

    llm = GeminiLLMEngine(api_key=settings.gemini_api_key)
    browser = BrowserEngine(llm=llm, session_dir=settings.sessions_dir, headless=settings.browser_headless)

    credential_manager = CredentialManager(store_path=settings.credentials_store)
    credentials = ensure_credentials(user_input["platform"], credential_manager)

    workflow = llm.interpret_user_command(user_input["command"])
    operation = str(workflow.get("operation") or operation).strip().lower()

    if _needs_clarification(workflow, products, operation):
        workflow = enrich_workflow_from_followup(workflow)

    append_cache_event(cache_file, "workflow", {
        "operation": operation,
        "workflow": workflow,
        "products_loaded": len(products),
    })

    if operation in {"edit_listing", "bulk_update"}:
        tasks = build_edit_tasks(products, workflow)
    else:
        tasks = [{"sku": str(product.get("sku") or "UNSPECIFIED"), "product": product} for product in products]

    append_cache_event(cache_file, "task_plan", {"operation": operation, "task_count": len(tasks)})

    platform = get_platform(user_input["platform"], browser)
    context = await browser.start(platform.name.lower().replace(" ", "_"))

    try:
        page = context.pages[0] if context.pages else await context.new_page()
        await platform.login(page, credentials)

        for task in tasks:
            sku = str(task.get("sku") or "UNSPECIFIED")
            try:
                image_paths: list[Path] = []
                if images_folder and sku != "UNSPECIFIED":
                    try:
                        image_paths = get_product_image_paths(images_folder, sku)
                    except Exception as image_error:
                        LOGGER.warning("Image loading failed for sku=%s: %s", sku, image_error)

                if operation == "new_listing":
                    product = task.get("product")
                    if not isinstance(product, dict):
                        raise ValueError("Missing product payload for new listing task.")
                    await platform.create_listing(page, product, image_paths)
                elif operation in {"edit_listing", "bulk_update"}:
                    updates = _safe_dict(task.get("updates"))
                    await platform.edit_listing(page, updates=updates, sku=sku)
                else:
                    raise ValueError(f"Unsupported operation: {operation}")
                append_cache_event(cache_file, "task_result", {"sku": sku, "status": "success"})
            except Exception as task_error:
                append_cache_event(cache_file, "task_result", {"sku": sku, "status": "failed", "error": str(task_error)})
                LOGGER.exception("Failed processing task for sku=%s", sku)
                print(f"Warning: failed processing task {sku}: {task_error}")
    finally:
        await browser.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as error:
        logging.exception("Fatal error while executing listing workflow.")
        print(f"Error: {error}")
