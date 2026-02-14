from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

MANDATORY_FIELDS = {"title", "brand", "description", "price", "sku", "category"}
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def load_product_data(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        raise FileNotFoundError(f"Product data file does not exist: {file_path}")

    if file_path.suffix.lower() == ".json":
        data = json.loads(file_path.read_text())
        products = data if isinstance(data, list) else [data]
    elif file_path.suffix.lower() == ".csv":
        with file_path.open("r", newline="", encoding="utf-8") as handle:
            products = list(csv.DictReader(handle))
    else:
        raise ValueError("Only JSON or CSV product data files are supported.")

    for product in products:
        missing = MANDATORY_FIELDS.difference(product.keys())
        if missing:
            raise ValueError(f"Product {product} missing mandatory fields: {sorted(missing)}")
    return products


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
