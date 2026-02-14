from __future__ import annotations

import json
import logging
import re
import warnings
from pathlib import Path
from typing import Any

warnings.simplefilter("ignore", FutureWarning)

import google.generativeai as genai

LOGGER = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-1.5-flash-latest"
MODEL_CANDIDATES = (
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash",
    "gemini-1.5-pro-latest",
)


class GeminiLLMEngine:
    """Gemini API wrapper for visual UI interpretation and instruction planning."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        genai.configure(api_key=api_key)
        self._active_model_name = model
        self.model = genai.GenerativeModel(model_name=model)

    def _try_switch_to_supported_model(self) -> None:
        available = {m.name.split("/")[-1] for m in genai.list_models() if "generateContent" in m.supported_generation_methods}
        for candidate in MODEL_CANDIDATES:
            if candidate in available:
                self._active_model_name = candidate
                self.model = genai.GenerativeModel(model_name=candidate)
                LOGGER.info("Switched Gemini model to supported candidate: %s", candidate)
                return

    def _generate(self, contents: Any) -> str:
        try:
            response = self.model.generate_content(contents)
            return (response.text or "").strip()
        except Exception as error:
            LOGGER.warning("Primary Gemini request failed for model '%s': %s", self._active_model_name, error)
            try:
                self._try_switch_to_supported_model()
                response = self.model.generate_content(contents)
                return (response.text or "").strip()
            except Exception as retry_error:
                LOGGER.warning("Gemini retry with discovered model failed: %s", retry_error)
                raise RuntimeError("LLM request failed") from retry_error

    def analyze_screen_with_llm(self, screenshot_path: Path, instruction: str) -> dict[str, Any]:
        """Analyze current UI screenshot and return structured action JSON."""
        if not screenshot_path.exists():
            raise FileNotFoundError(f"Screenshot not found: {screenshot_path}")

        prompt = f"""
You are an expert e-commerce listing operator.
Given this webpage screenshot and instruction, return STRICT JSON only.
Instruction: {instruction}

Output JSON schema:
{{
  "actions": [
    {{
      "action": "click|type|scroll|hover|wait|upload|press|done",
      "target": "human-readable field/button name",
      "value": "optional value",
      "confidence": 0.0,
      "reason": "short reason"
    }}
  ],
  "screen_state": "short description",
  "risk": "none|captcha|2fa|error|popup"
}}

Rules:
- Always include at least one action.
- Use done action if task appears complete.
- If uncertain, include wait or scroll then re-check.
"""
        try:
            text = self._generate(
                [
                    prompt,
                    {"mime_type": "image/png", "data": screenshot_path.read_bytes()},
                ]
            )
            return json.loads(text)
        except json.JSONDecodeError:
            LOGGER.warning("LLM returned non-JSON response. Falling back to safe wait.")
        except Exception as error:
            LOGGER.warning("LLM screenshot analysis failed. Falling back to safe wait: %s", error)

        return {
            "actions": [
                {
                    "action": "wait",
                    "target": "page",
                    "value": "2",
                    "confidence": 0.3,
                    "reason": "Fallback due to LLM/API parsing failure",
                }
            ],
            "screen_state": "Unknown",
            "risk": "error",
        }

    def interpret_user_command(self, command: str) -> dict[str, Any]:
        prompt = f"""
Convert user instruction to listing workflow JSON.
Instruction: {command}
Return strict JSON:
{{
  "operation": "new_listing|edit_listing|bulk_update",
  "updates": {{"field": "value"}},
  "sku": "optional",
  "notes": "short text"
}}
"""
        try:
            text = self._generate(prompt)
            return json.loads(text)
        except Exception as error:
            LOGGER.warning("Command interpretation via LLM failed, using local fallback parser: %s", error)
            return self._fallback_command_parse(command)

    def _fallback_command_parse(self, command: str) -> dict[str, Any]:
        normalized = command.strip()
        lowered = normalized.lower()

        operation = "edit_listing"
        if "new" in lowered and "list" in lowered:
            operation = "new_listing"
        elif "bulk" in lowered:
            operation = "bulk_update"

        sku_match = re.search(r"\bsku\s*[:#-]?\s*([A-Za-z0-9_-]+)\b", normalized, flags=re.IGNORECASE)
        sku = sku_match.group(1) if sku_match else ""

        updates: dict[str, Any] = {}

        name_match = re.search(
            r"(?:update|change)\s+(?:the\s+)?(?:name|title).+?\bto\b\s+(.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if name_match:
            updates["title"] = name_match.group(1).strip().strip('"').strip("'")

        quoted_chunks = re.findall(r'"([^"]+)"', normalized)
        if len(quoted_chunks) >= 2 and "title" in updates:
            updates["target_title"] = quoted_chunks[0]
            updates["title"] = quoted_chunks[-1]

        return {
            "operation": operation,
            "updates": updates,
            "sku": sku,
            "notes": "Fallback parser used because LLM response was unavailable or invalid.",
        }
