from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

MANDATORY_FIELDS = {"title", "brand", "description", "price", "sku", "category"}
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def load_product_data(file_path: Path, *, strict: bool = True) -> list[dict[str, Any]]:
    if not file_path.exists():
        raise FileNotFoundError(f"Product data file does not exist: {file_path}")

    if not file_path.suffix:
        raise ValueError("Product data file must have a .json or .csv extension.")

    if file_path.suffix.lower() == ".json":
        data = json.loads(file_path.read_text())
        products = data if isinstance(data, list) else [data]
    elif file_path.suffix.lower() == ".csv":
        with file_path.open("r", newline="", encoding="utf-8") as handle:
            products = list(csv.DictReader(handle))
    else:
        raise ValueError("Only JSON or CSV product data files are supported.")

    valid_products: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    for index, product in enumerate(products, start=1):
        if not isinstance(product, dict):
            validation_errors.append(f"Row {index}: product entry is not a JSON object/CSV row.")
            continue

        missing = MANDATORY_FIELDS.difference(product.keys())
        if missing:
            validation_errors.append(f"Row {index}: missing mandatory fields {sorted(missing)}")
            continue

        valid_products.append(product)

    if validation_errors and strict:
        error_preview = "; ".join(validation_errors[:5])
        raise ValueError(f"Product validation failed: {error_preview}")

    if not valid_products:
        raise ValueError("No valid products found in the provided data file.")

    return valid_products


def get_product_image_paths(images_root: Path, sku: str) -> list[Path]:
    product_folder = images_root / sku
    if not product_folder.exists():
        raise FileNotFoundError(f"Image folder not found for SKU {sku}: {product_folder}")

    image_paths = sorted(
        [
            path
            for path in product_folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        ]
    )
    if not image_paths:
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
