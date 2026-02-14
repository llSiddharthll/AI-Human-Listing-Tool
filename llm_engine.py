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

DEFAULT_MODEL = "gemini-2.5-flash"
MODEL_CANDIDATES = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-exp",
    "gemini-1.5-flash-latest",
    "gemini-1.5-pro-latest",
)


class GeminiLLMEngine:
    """Gemini API wrapper for visual UI interpretation and instruction planning."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        genai.configure(api_key=api_key)
        self._active_model_name = model
        self.model = genai.GenerativeModel(model_name=model)

    @staticmethod
    def _extract_json_payload(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return stripped

        fenced = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", stripped, flags=re.DOTALL)
        if fenced:
            return fenced.group(1).strip()

        start = min((idx for idx in [stripped.find("{"), stripped.find("[")] if idx != -1), default=-1)
        if start == -1:
            return stripped

        candidate = stripped[start:]
        end_obj = candidate.rfind("}")
        end_arr = candidate.rfind("]")
        end = max(end_obj, end_arr)
        return candidate[: end + 1] if end != -1 else candidate

    def _discover_supported_models(self) -> list[str]:
        try:
            names = [
                model.name.split("/")[-1]
                for model in genai.list_models()
                if "generateContent" in getattr(model, "supported_generation_methods", [])
            ]
            return list(dict.fromkeys(names))
        except Exception as error:
            LOGGER.warning("Could not list Gemini models: %s", error)
            return []

    def _try_switch_to_supported_model(self) -> bool:
        available = self._discover_supported_models()
        ordered = [
            candidate for candidate in MODEL_CANDIDATES if candidate in available and candidate != self._active_model_name
        ]
        ordered.extend([name for name in available if name not in ordered and name != self._active_model_name])

        for candidate in ordered:
            try:
                self._active_model_name = candidate
                self.model = genai.GenerativeModel(model_name=candidate)
                LOGGER.info("Switched Gemini model to supported candidate: %s", candidate)
                return True
            except Exception:
                continue
        return False

    def _generate(self, contents: Any) -> str:
        try:
            response = self.model.generate_content(contents)
            return (response.text or "").strip()
        except Exception as error:
            LOGGER.warning("Primary Gemini request failed for model '%s': %s", self._active_model_name, error)

        if not self._try_switch_to_supported_model():
            raise RuntimeError("LLM request failed and no fallback model could be selected.")

        try:
            response = self.model.generate_content(contents)
            return (response.text or "").strip()
        except Exception as retry_error:
            LOGGER.warning("Gemini retry with fallback model '%s' failed: %s", self._active_model_name, retry_error)
            raise RuntimeError("LLM request failed") from retry_error

    def analyze_screen_with_llm(self, screenshot_path: Path, instruction: str) -> dict[str, Any]:
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
"""
        try:
            text = self._generate([prompt, {"mime_type": "image/png", "data": screenshot_path.read_bytes()}])
            return json.loads(self._extract_json_payload(text))
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
  "filters": {{"title": "optional", "category": "optional", "brand": "optional"}},
  "notes": "short text"
}}
"""
        try:
            text = self._generate(prompt)
            parsed = json.loads(self._extract_json_payload(text))
            if not isinstance(parsed, dict):
                raise ValueError("Workflow response was not an object.")
            return parsed
        except Exception as error:
            LOGGER.warning("Command interpretation via LLM failed, using local fallback parser: %s", error)
            return self._fallback_command_parse(command)

    def _fallback_command_parse(self, command: str) -> dict[str, Any]:
        normalized = command.strip()
        lowered = normalized.lower()

        operation = "edit_listing"
        if "new" in lowered and "list" in lowered:
            operation = "new_listing"
        elif "bulk" in lowered or "all " in lowered or " all" in lowered:
            operation = "bulk_update"

        sku_match = re.search(r"\bsku\s*[:#-]?\s*([A-Za-z0-9_-]+)\b", normalized, flags=re.IGNORECASE)
        sku = sku_match.group(1) if sku_match else ""

        updates: dict[str, Any] = {}
        filters: dict[str, str] = {}

        quoted_chunks = re.findall(r'"([^"]+)"', normalized)

        name_match = re.search(
            r"(?:update|change)\s+(?:the\s+)?(?:name|title).+?\bto\b\s+(.+)$",
            normalized,
            flags=re.IGNORECASE,
        )
        if name_match:
            updates["title"] = name_match.group(1).strip().strip('"').strip("'")

        price_match = re.search(r"\b(?:price|prices?)\b.*?\bto\b\s*(\d+(?:\.\d+)?)", lowered)
        if price_match:
            value = price_match.group(1)
            updates["price"] = float(value) if "." in value else int(value)

        category_match = re.search(r"\b(?:of|for|in)\s+([a-zA-Z][a-zA-Z\s]+?)(?:\s+from\s+the\s+file|\s+to\s+|$)", lowered)
        if category_match and any(token in lowered for token in {"price", "prices", "change", "update"}):
            filters["category"] = category_match.group(1).strip()

        if len(quoted_chunks) >= 2 and "title" in updates:
            filters["title"] = quoted_chunks[0]
            updates["title"] = quoted_chunks[-1]
        elif len(quoted_chunks) == 1 and "title" not in updates:
            filters["title"] = quoted_chunks[0]

        return {
            "operation": operation,
            "updates": updates,
            "sku": sku,
            "filters": filters,
            "notes": "Fallback parser used because LLM response was unavailable or invalid.",
        }
