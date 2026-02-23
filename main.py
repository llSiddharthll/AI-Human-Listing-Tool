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

IDENTIFIER_FIELDS = {"sku", "title", "name", "brand", "category", "target_title", "target_sku"}


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "app.log"),
            logging.StreamHandler(),
        ],
    )
    logging.getLogger("google_genai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


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
    if normalized in mapping:
        return mapping[normalized](browser)

    from platforms.common import LLMDrivenPlatform
    class GenericPlatform(LLMDrivenPlatform):
        @property
        def name(self) -> str:
            return platform_name.title()
        
        @property
        def login_url(self) -> str:
            return f"https://www.google.com/search?q={platform_name.replace(' ', '+')}+login"

    return GenericPlatform(browser)


async def agentic_input_collection(llm: GeminiLLMEngine) -> dict[str, Any]:
    print("\n=== AI Human Listing Tool (Agent Mode) ===")
    print("How can I help you today? (e.g., 'Update price for Face Wash to 599 on Amazon')")

    history: list[dict[str, str]] = []
    
    while True:
        user_msg = input("\nYou: ").strip()
        if not user_msg:
            continue
        
        history.append({"role": "user", "content": user_msg})
        
        print("Thinking...")
        state = await llm.analyze_conversation_state(history)
        
        if state.get("is_complete"):
            print(f"\nPlan Summary:")
            print(f" - Platform: {state.get('platform')}")
            print(f" - Operation: {state.get('operation')}")
            print(f" - Item: {state.get('sku') or state.get('product_name') or 'From file'}")
            
            confirm = input("\nDoes this look correct? (y/n): ").strip().lower()
            if confirm == 'y':
                return {
                    "platform": state.get("platform"),
                    "operation": state.get("operation"),
                    "command": user_msg, # Using the last message as the command for interpret_user_command
                    "data_file": Path(state.get("data_file_path")) if state.get("data_file_path") else None,
                    "images_folder": Path(state.get("images_folder_path")) if state.get("images_folder_path") else None,
                    "sku": state.get("sku"),
                    "product_name": state.get("product_name")
                }
            else:
                print("Okay, what should we change?")
                history.append({"role": "assistant", "content": "The user rejected the plan. I need to clarify the details. What should we change?"})
        else:
            assistant_msg = state.get("suggested_next_question", "Could you tell me more about what you'd like to do?")
            print(f"\nAI: {assistant_msg}")
            history.append({"role": "assistant", "content": assistant_msg})


def ensure_credentials(platform: str, manager: CredentialManager) -> dict[str, str]:
    try:
        return manager.get_credentials(platform)
    except KeyError:
        print(f"No encrypted credentials found for {platform}. You can provide them now, or leave blank if already logged in via persistent session.")
        username = sanitize_text(input("Username/email (press Enter to skip): "))
        if not username:
            return {}
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
    
    llm = GeminiLLMEngine(api_keys=settings.gemini_api_keys, model=settings.gemini_model)
    user_input = await agentic_input_collection(llm)

    platform_name = user_input["platform"]
    operation = user_input["operation"].strip().lower()
    data_file: Path | None = user_input["data_file"]
    images_folder: Path | None = user_input["images_folder"]

    products: list[dict[str, Any]] = []
    if data_file:
        products = load_product_data(data_file, strict=False)
    elif user_input.get("sku") or user_input.get("product_name"):
        products = [{
            "sku": user_input.get("sku") or "UNSPECIFIED",
            "title": user_input.get("product_name")
        }]

    if operation == "new_listing" and not products:
        raise ValueError("Product information is required for new listings.")

    # For edit/bulk — if no specific product, use the original command as a browse instruction
    # A human would just navigate the dashboard and find the product
    if operation in {"edit_listing", "bulk_update"} and not products:
        products = [{
            "sku": "BROWSE",
            "title": user_input.get("command", "Browse and find the product")
        }]

    async def cli_otp_callback() -> str:
        print("\n" + "="*40)
        print("🔐 2FA/OTP REQUIRED")
        print("Please check your email or phone for a login code.")
        print("="*40)
        otp = input("Enter OTP Code: ").strip()
        return otp

    browser = BrowserEngine(llm=llm, session_dir=settings.sessions_dir, headless=settings.browser_headless, otp_callback=cli_otp_callback)
    credential_manager = CredentialManager(store_path=settings.credentials_store)
    credentials = ensure_credentials(platform_name, credential_manager)

    workflow = await llm.interpret_user_command(user_input["command"])
    # workflow can override operation if it's more specific
    operation = str(workflow.get("operation") or operation).strip().lower()

    platform = get_platform(platform_name, browser)
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
                    # If we have a product name but no target_title in updates, use product name
                    if product.get("title") and "target_title" not in updates:
                        updates["target_title"] = product["title"]
                    
                    await platform.edit_listing(page, updates=updates, sku=sku)
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
