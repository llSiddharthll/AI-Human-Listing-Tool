# AI-Human-Listing-Tool

Production-grade, LLM-driven multi-platform product listing automation using **Python + Playwright + Gemini Flash**.

## Features

- Multi-platform abstraction for:
  - Amazon Seller Central
  - Myntra Seller Panel
  - Flipkart Seller Hub
  - Shopify Admin
- Supports:
  - New listing creation
  - Existing listing edits (price, stock, description, etc.)
  - Bulk update workflows
- Gemini Flash-powered visual reasoning:
  - Screenshot interpretation
  - Dynamic action planning
  - Popup/CAPTCHA/2FA flow diagnosis
- Human-like browser automation:
  - Randomized delays
  - Character-by-character typing
  - Hover-before-click behavior
  - Natural scroll simulation
- Persistent login sessions with Playwright user profiles
- Encrypted credential storage via Fernet

## Project Structure

```text
/project-root
  /config
    credentials.py
    settings.py
  /images
  /product_data
    example_product.json
  /sessions
  /platforms
    base.py
    common.py
    amazon.py
    myntra.py
    flipkart.py
    shopify.py
  llm_engine.py
  browser_engine.py
  data_engine.py
  main.py
```

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Generate encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `.env` values:

- `GEMINI_API_KEY`
- `GEMINI_MODEL` (default: `gemini-3.5-flash`)
- `CREDENTIAL_ENCRYPTION_KEY`

## Input Data Requirements

Supported product file formats: JSON or CSV.

Mandatory fields:

- `title`
- `brand`
- `description`
- `price`
- `sku`
- `category`

Example JSON payload is provided in `product_data/example_product.json`.

## Image Folder Naming

Expected image layout:

```text
/images
  /product_sku_001
    main.jpg
    1.jpg
    2.jpg
```

The helper `store_images_with_proper_naming(product_id, source_paths)` can normalize image names.

## Run Workflow

```bash
python main.py
```

CLI prompts:

- Platform (`amazon`, `myntra`, `flipkart`, `shopify`)
- Operation (`new_listing`, `edit_listing`, `bulk_update`)
- Natural-language command
- Product data file path
- Images folder path

### Important behavior

- `new_listing`: requires product file and image folder.
- `bulk_update`: requires product file.
- `edit_listing`: product file and images are optional (you can drive purely from instruction text + SKU).

### Example command instructions

- `List new product`
- `Update price of SKU123 to 799`
- `Change stock to 50`
- `Edit description of product XYZ`

## Session Persistence and 2FA

- Uses Playwright persistent context (`sessions/<platform_name>/`) to reuse authenticated state.
- On first login, user may need to manually complete OTP/2FA.
- After successful authentication, next runs reuse saved browser state.

## Security

- API keys are loaded from environment.
- Credentials are encrypted at rest in `config/credentials.enc`.
- Session data and cookies are not printed to logs.

## Notes

- LLM output is parsed as strict JSON; markdown code fences are handled.
- The automation loop is adaptive and intended to tolerate moderate UI changes better than static scripts.
