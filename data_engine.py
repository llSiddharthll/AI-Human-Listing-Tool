from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

MANDATORY_FIELDS = {"title", "brand", "description", "price", "sku", "category"}
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def load_product_data(file_path: Path | None, allow_empty: bool = False) -> list[dict[str, Any]]:
    """Load products from JSON/CSV.

    If ``allow_empty`` is True and ``file_path`` is missing/empty, return an empty list.
    """
    if file_path is None:
        if allow_empty:
            return []
        raise ValueError("Product data file path is required for this operation.")

    raw_path = str(file_path).strip()
    if not raw_path:
        if allow_empty:
            return []
        raise ValueError("Product data file path is required for this operation.")

    normalized = Path(raw_path)
    if not normalized.exists():
        raise FileNotFoundError(f"Product data file does not exist: {normalized}")

    if normalized.suffix.lower() == ".json":
        data = json.loads(normalized.read_text(encoding="utf-8"))
        products = data if isinstance(data, list) else [data]
    elif normalized.suffix.lower() == ".csv":
        with normalized.open("r", newline="", encoding="utf-8") as handle:
            products = list(csv.DictReader(handle))
    else:
        raise ValueError("Only JSON or CSV product data files are supported.")

    for product in products:
        missing = MANDATORY_FIELDS.difference(product.keys())
        if missing:
            raise ValueError(f"Product {product} missing mandatory fields: {sorted(missing)}")
    return products


def get_product_image_paths(images_root: Path | None, sku: str, required: bool = True) -> list[Path]:
    if images_root is None or not str(images_root).strip():
        if required:
            raise ValueError("Images folder path is required for this operation.")
        return []

    product_folder = images_root / sku
    if not product_folder.exists():
        if required:
            raise FileNotFoundError(f"Image folder not found for SKU {sku}: {product_folder}")
        return []

    image_paths = sorted(
        [
            path
            for path in product_folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        ]
    )
    if not image_paths and required:
        raise ValueError(f"No valid images found for SKU {sku} in {product_folder}")

    return image_paths


def store_images_with_proper_naming(product_id: str, source_paths: list[Path], images_root: Path = Path("images")) -> list[Path]:
    target_dir = images_root / product_id
    target_dir.mkdir(parents=True, exist_ok=True)

    stored_paths: list[Path] = []
    for index, source in enumerate(source_paths):
        if source.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue

        target_name = "main" + source.suffix.lower() if index == 0 else f"{index}" + source.suffix.lower()
        target_path = target_dir / target_name
        target_path.write_bytes(source.read_bytes())
        stored_paths.append(target_path)

    if not stored_paths:
        raise ValueError("No supported image files were stored.")

    return stored_paths
