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

1. Create virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

2. Configure environment:

```bash
cp .env.example .env
```

3. Generate credential encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste generated key into `.env` as `CREDENTIAL_ENCRYPTION_KEY`.

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

CLI will ask for:

- Platform (`amazon`, `myntra`, `flipkart`, `shopify`)
- Operation (`new_listing`, `edit_listing`, `bulk_update`)
- Natural-language command instruction
- Product data file path
- Images folder path

### Example command instructions

- `List new product`
- `Update price of SKU123 to 799`
- `Change stock to 50`
- `Edit description of product XYZ`

## Gemini Integration Example

`GeminiLLMEngine.analyze_screen_with_llm(...)` receives:

- Full-page screenshot
- Action instruction prompt

And returns structured JSON actions, for example:

```json
{
  "actions": [
    {
      "action": "type",
      "target": "Product Title field",
      "value": "Premium Cotton T-Shirt",
      "confidence": 0.94,
      "reason": "Title field is visible and empty"
    }
  ],
  "screen_state": "Create listing form",
  "risk": "none"
}
```

The browser engine executes this plan adaptively and loops until a `done` action is returned.

## Session Persistence and 2FA

- Uses Playwright persistent context (`sessions/<platform_name>/`) to reuse authenticated state.
- On first login, user may need to manually complete OTP/2FA.
- After successful authentication, next runs reuse saved browser state.

## Security

- API keys are loaded from environment (`GEMINI_API_KEY`).
- Credentials are encrypted at rest in `config/credentials.enc`.
- Session data and cookies are never printed to logs.

## Notes

- This tool is LLM-driven, so workflows adapt to UI changes better than static XPath scripts.
- You should review marketplace policies and legal constraints before automating production seller accounts.
