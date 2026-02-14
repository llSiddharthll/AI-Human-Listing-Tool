from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import google.generativeai as genai

LOGGER = logging.getLogger(__name__)


class GeminiLLMEngine:
    """Gemini Flash wrapper for visual UI interpretation and instruction planning."""

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash") -> None:
        genai.configure(api_key=api_key)
        self.model_name = model
        self.model = genai.GenerativeModel(model_name=model)

    def _parse_json_text(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(cleaned)

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
        response = self.model.generate_content(
            [
                prompt,
                {"mime_type": "image/png", "data": screenshot_path.read_bytes()},
            ]
        )

        try:
            return self._parse_json_text(response.text)
        except json.JSONDecodeError:
            LOGGER.warning("LLM returned non-JSON response. Falling back to safe wait.")
            return {
                "actions": [
                    {
                        "action": "wait",
                        "target": "page",
                        "value": "2",
                        "confidence": 0.3,
                        "reason": "Fallback due to non-JSON response",
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
        response = self.model.generate_content(prompt)
        try:
            parsed = self._parse_json_text(response.text)
            if not isinstance(parsed, dict):
                raise json.JSONDecodeError("Not object", response.text, 0)
            return parsed
        except json.JSONDecodeError:
            return {
                "operation": "edit_listing",
                "updates": {},
                "sku": "",
                "notes": f"Could not parse command exactly: {command}",
            }
