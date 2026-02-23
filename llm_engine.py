from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from google import genai
from google.genai import types

LOGGER = logging.getLogger(__name__)

# Best cost-efficient model for high-volume listing tasks
DEFAULT_MODEL = "gemini-2.5-flash-lite"

# Fallback ordered by efficiency (cheapest first)
MODEL_CANDIDATES = (
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
)

# Free text-only providers from webscout (no API key, no quota, unlimited)
FREE_TEXT_PROVIDERS = ("Blackbox", "LLMChat")


def _get_free_provider():
    """Get a working free text provider from webscout. Returns None if none work."""
    try:
        import webscout
        for name in FREE_TEXT_PROVIDERS:
            try:
                cls = getattr(webscout, name)
                provider = cls()
                LOGGER.info("Free provider '%s' initialized", name)
                return provider
            except Exception:
                continue
    except ImportError:
        LOGGER.debug("webscout not installed — free providers unavailable")
    return None


class GeminiLLMEngine:
    """Gemini API wrapper with multi-key rotation + free webscout fallback."""

    def __init__(self, api_keys: list[str] | str, model: str = DEFAULT_MODEL) -> None:
        if isinstance(api_keys, str):
            self._api_keys = [api_keys]
        else:
            self._api_keys = list(api_keys)
        
        self._current_key_index = 0
        self.client = genai.Client(api_key=self._api_keys[0])
        self._model = model
        self._exhausted_models: set[str] = set()
        self._exhausted_keys: set[int] = set()
        self._exhausted_combos: set[tuple[int, str]] = set()  # Persistent (key, model) tracking
        self._combos_exhausted_at: float = 0.0  # When all combos were exhausted
        self._free_provider = _get_free_provider()

        LOGGER.info("LLM Engine: %d API key(s), model: %s, free fallback: %s",
            len(self._api_keys), model, type(self._free_provider).__name__ if self._free_provider else "None")

    async def _free_generate(self, prompt: str) -> str | None:
        """Generate text using free webscout provider (no quota limits). Text-only, no images."""
        if not self._free_provider:
            self._free_provider = _get_free_provider()
        if not self._free_provider:
            return None
        try:
            response = await asyncio.to_thread(self._free_provider.chat, prompt)
            if response:
                LOGGER.info("Free provider responded (%d chars)", len(response))
                return response.strip()
        except Exception as e:
            LOGGER.warning("Free provider failed: %s", str(e)[:80])
            # Try next free provider
            self._free_provider = _get_free_provider()
        return None

    def _switch_key(self) -> bool:
        """Switch to the next available API key. Returns True if switched."""
        for i in range(len(self._api_keys)):
            idx = (self._current_key_index + 1 + i) % len(self._api_keys)
            if idx not in self._exhausted_keys:
                self._current_key_index = idx
                self.client = genai.Client(api_key=self._api_keys[idx])
                LOGGER.info("Switched to API key #%d/%d", idx + 1, len(self._api_keys))
                return True
        return False

    async def _generate(self, contents: list | str, system_instruction: str | None = None) -> str:
        """Core generation with instant key+model rotation. Fails fast when all exhausted."""
        total_combos = len(self._api_keys) * len(MODEL_CANDIDATES)

        # If all combos were exhausted recently, check if cooldown has passed
        if len(self._exhausted_combos) >= total_combos:
            elapsed = time.monotonic() - self._combos_exhausted_at
            if elapsed < 60:  # 60s cooldown before retrying
                raise RuntimeError(f"All {total_combos} combos exhausted ({60 - elapsed:.0f}s until retry)")
            else:
                LOGGER.info("Cooldown passed. Resetting exhausted combos.")
                self._exhausted_combos.clear()
                self._exhausted_models.clear()
                self._exhausted_keys.clear()
                self._model = MODEL_CANDIDATES[0]
                self._current_key_index = 0
                self.client = genai.Client(api_key=self._api_keys[0])

        # Skip combos we already know are dead
        current_combo = (self._current_key_index, self._model)
        if current_combo in self._exhausted_combos:
            # Find first available combo
            found = False
            for m in MODEL_CANDIDATES:
                for ki in range(len(self._api_keys)):
                    if (ki, m) not in self._exhausted_combos:
                        self._current_key_index = ki
                        self.client = genai.Client(api_key=self._api_keys[ki])
                        self._model = m
                        found = True
                        break
                if found:
                    break
            if not found:
                self._combos_exhausted_at = time.monotonic()
                raise RuntimeError(f"All {total_combos} combos exhausted.")

        # Try remaining combos (no wait between failures — speed is key)
        while len(self._exhausted_combos) < total_combos:
            try:
                config = None
                if system_instruction:
                    config = types.GenerateContentConfig(system_instruction=system_instruction)

                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self._model,
                    contents=contents,
                    config=config,
                )
                # Success — reset tracking
                self._exhausted_combos.clear()
                self._exhausted_models.clear()
                self._exhausted_keys.clear()
                return (response.text or "").strip()

            except Exception as error:
                error_str = str(error)
                is_quota = "429" in error_str or "quota" in error_str.lower() or "RESOURCE_EXHAUSTED" in error_str

                if not is_quota:
                    raise RuntimeError(f"LLM error: {error}") from error

                # Mark this combo as dead
                self._exhausted_combos.add((self._current_key_index, self._model))
                self._exhausted_models.add(self._model)
                self._exhausted_keys.add(self._current_key_index)
                LOGGER.warning("Quota: key#%d/%s (%d/%d dead)",
                    self._current_key_index + 1, self._model,
                    len(self._exhausted_combos), total_combos)

                # Find next live combo instantly (no sleep)
                found = False
                for m in MODEL_CANDIDATES:
                    for ki in range(len(self._api_keys)):
                        if (ki, m) not in self._exhausted_combos:
                            self._current_key_index = ki
                            self.client = genai.Client(api_key=self._api_keys[ki])
                            self._model = m
                            found = True
                            break
                    if found:
                        break

        # All dead
        self._combos_exhausted_at = time.monotonic()
        raise RuntimeError(f"All {total_combos} combos exhausted.")

    async def analyze_screen_with_llm(self, screenshot_bytes: bytes, instruction: str) -> dict[str, Any]:
        if not screenshot_bytes:
            raise ValueError("Screenshot bytes cannot be empty")

        prompt = f"""Analyze this webpage screenshot for the instruction below.

RULES:
1. If goal ALREADY ACHIEVED, return "action":"done".
2. Don't type credentials into search/filter/chat boxes.
3. During login, check "Trust this device"/"Stay logged in" boxes.
4. If screen is BLANK/WHITE, return "action":"wait".
5. To scroll, use "action":"scroll", "value":"down"/"up".
6. If clicking a button/link doesn't work, try using "action":"navigate" with "value":"direct URL" to go to pages like /admin/products, /products, etc.
7. Use SHORT, EXACT text you see on the screen for targets (e.g., "Products" not "Products Navigation Link").
8. For Shopify: Products page is at /admin/products. Use navigate if clicking fails.

Instruction: {instruction}

Return ONLY valid JSON:
{{"actions":[{{"action":"click|type|scroll|hover|wait|upload|press|navigate|select|done","target":"exact text on screen","value":"optional","confidence":0.0,"reason":"short"}}],"screen_state":"description","risk":"none|captcha|2fa|error|popup"}}"""

        try:
            image_part = types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png")
            text = await self._generate(
                contents=[prompt, image_part],
                system_instruction="You are an expert e-commerce listing operator. Return strict JSON only, no markdown."
            )
            text = text.strip()
            if text.startswith("```json"): text = text[7:]
            elif text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            text = text.strip()

            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
            return json.loads(text)
        except (json.JSONDecodeError, AttributeError):
            LOGGER.warning("LLM returned non-JSON. Snippet: %s", str(text)[:150])
        except Exception as error:
            LOGGER.warning("LLM screenshot analysis failed: %s", error)

        return {
            "actions": [
                {
                    "action": "wait",
                    "target": "page",
                    "value": "2",
                    "confidence": 0.3,
                    "reason": "Fallback due to LLM/API failure",
                }
            ],
            "screen_state": "Unknown",
            "risk": "error",
        }

    async def interpret_user_command(self, command: str) -> dict[str, Any]:
        prompt = f"""Convert to listing workflow JSON.
Instruction: {command}
Return strict JSON:
{{"operation":"new_listing|edit_listing|bulk_update","updates":{{"field":"value"}},"sku":"optional","filters":{{"title":"optional","category":"optional","brand":"optional"}},"notes":"short"}}"""
        try:
            text = await self._generate(prompt)
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group(0))
            return json.loads(text)
        except Exception as error:
            LOGGER.warning("Gemini command interpretation failed: %s. Trying free provider...", error)
            # Try free provider
            free_text = await self._free_generate(prompt)
            if free_text:
                try:
                    if free_text.startswith("```"): free_text = re.sub(r"```\w*\n?", "", free_text).rstrip("`").strip()
                    json_match = re.search(r"\{.*\}", free_text, re.DOTALL)
                    if json_match:
                        return json.loads(json_match.group(0))
                except Exception:
                    pass
            return self._fallback_command_parse(command)

    async def analyze_conversation_state(self, history: list[dict[str, str]]) -> dict[str, Any]:
        """Analyzes chat history. Tries: Gemini -> Free provider -> Heuristic fallback."""
        
        history_text = "\n".join([f"{h['role']}: {h['content']}" for h in history])
        prompt = f"""You are a smart listing assistant. Analyze this conversation and figure out what the user wants.

Think like a HUMAN assistant, not a rigid form. The user does NOT need to provide an exact SKU or product name.
They can describe the product in ANY way, and you should accept it. Examples of VALID product references:
- "the latest product" => product_name = "latest product (browse to find)"
- "most recent upload" => product_name = "most recent upload (browse to find)"
- "first item on the list" => product_name = "first item on the list (browse to find)"
- "the red shoes I added yesterday" => product_name = "red shoes added yesterday (browse to find)"
- "all products in electronics" => this is a bulk_update
- "no name, just find it" => product_name = "browse and identify (browse to find)"

If the user says they don't have an SKU/name, DON'T keep asking. Mark is_complete=true and set product_name to a descriptive browse instruction.

Conversation:
{history_text}

Return ONLY JSON:
{{"platform":"string|null","operation":"new_listing|edit_listing|bulk_update|null","sku":"string|null","product_name":"string|null","data_file_path":"string|null","images_folder_path":"string|null","is_complete":true|false,"missing_info_reason":"what is missing","suggested_next_question":"question to ask user"}}"""

        # Strategy 1: Try Gemini (if quota available)
        if len(self._exhausted_models) < len(MODEL_CANDIDATES) or len(self._exhausted_keys) < len(self._api_keys):
            try:
                text = await self._generate(prompt)
                json_match = re.search(r"\{.*\}", text, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(0))
                return json.loads(text)
            except Exception as error:
                LOGGER.warning("Gemini conversation analysis failed: %s", error)

        # Strategy 2: Try free webscout provider (unlimited, no quota)
        LOGGER.info("Trying free provider for conversation analysis...")
        free_text = await self._free_generate(prompt)
        if free_text:
            try:
                if free_text.startswith("```"): free_text = re.sub(r"```\w*\n?", "", free_text).rstrip("`").strip()
                json_match = re.search(r"\{.*\}", free_text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(0))
                    LOGGER.info("Free provider gave valid analysis: platform=%s, complete=%s",
                        result.get("platform"), result.get("is_complete"))
                    return result
            except Exception as e:
                LOGGER.warning("Free provider returned non-JSON: %s", str(e)[:80])

        # Strategy 3: Local heuristic fallback (offline, instant)
        LOGGER.info("Using heuristic fallback for conversation analysis")
        return self._fallback_conversation_analysis(history)

    def _fallback_conversation_analysis(self, history: list[dict[str, str]]) -> dict[str, Any]:
        """Local heuristic to detect platform/operation when LLM is down."""
        full_text = " ".join([h["content"].lower() for h in history])
        last_msg = history[-1]["content"].lower() if history else ""

        # Only reset on explicit restart commands, NOT on "no name" or "no sku" type answers
        reset_words = ["restart", "start over", "clear", "reset"]
        if any(w in last_msg for w in reset_words):
            return {
                "platform": None, "operation": None, "sku": None, "product_name": None,
                "is_complete": False, "suggested_next_question": "Let's start over. What platform and product should we work with?"
            }

        platform = None
        platforms = ["shopify", "amazon", "flipkart", "myntra", "meesho", "ajio", "nykaa", "ebay", "etsy"]
        for p in platforms:
            if p in full_text:
                platform = p
                if p in last_msg:
                    platform = p
                    break

        operation = "edit_listing"
        if any(w in full_text for w in ["new", "create", "add"]):
            operation = "new_listing"
        elif any(w in full_text for w in ["bulk", "batch", "multiple"]):
            operation = "bulk_update"

        sku_match = re.search(r"\bsku\s*[:#-]?\s*([A-Za-z0-9_-]+)\b", full_text)
        sku = sku_match.group(1) if sku_match else None

        product_name = None
        name_match = re.search(r'edit (?:the )?price of ["\']?([^"\']+)["\']?', full_text)
        if not name_match:
            name_match = re.search(r'(?:for|update) ["\']?([^"\']+)["\']?', full_text)

        if name_match:
            product_name = name_match.group(1).strip()
            product_name = re.split(r'\s+to\s+|\s+on\s+', product_name)[0]

        # Detect vague/descriptive product references — a human would just browse and find it
        vague_descriptors = [
            "latest", "most recent", "last uploaded", "newest", "first",
            "top", "any", "random", "all product", "everything",
            "no name", "no sku", "don't have", "dont have", "don't know",
            "just find", "browse", "search for it", "look for it",
        ]
        has_vague_ref = any(desc in full_text for desc in vague_descriptors)

        if has_vague_ref and not product_name:
            # Extract a human-readable description from the original user message
            first_user_msg = next((h["content"] for h in history if h["role"] == "user"), "")
            product_name = f"{first_user_msg.strip()} (browse to find)"

        is_complete = platform is not None and (sku is not None or product_name is not None)

        # If we have a platform and the user's intent is clear (there's an action verb), just proceed
        if platform and not is_complete:
            action_verbs = ["update", "change", "edit", "set", "modify", "delete", "remove"]
            has_action = any(v in full_text for v in action_verbs)
            if has_action:
                first_user_msg = next((h["content"] for h in history if h["role"] == "user"), "")
                product_name = f"{first_user_msg.strip()} (browse to find)"
                is_complete = True

        msg = "Running in offline mode due to API limits. "
        if not platform:
            msg += "Which platform (Shopify, Amazon, etc.)?"
        elif is_complete:
            msg = "Got it! I'll browse the platform and find the right product. Ready to proceed."
        else:
            msg += "Can you describe what you'd like to do? I can browse and find products for you."

        return {
            "platform": platform,
            "operation": operation,
            "sku": sku,
            "product_name": product_name,
            "is_complete": is_complete,
            "suggested_next_question": msg
        }

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
