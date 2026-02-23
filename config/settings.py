from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(slots=True)
class Settings:
    """Application configuration loaded from environment variables."""

    gemini_api_keys: list[str]
    gemini_model: str
    browser_headless: bool
    sessions_dir: Path
    logs_dir: Path
    credentials_store: Path

    @property
    def gemini_api_key(self) -> str:
        """Return the first API key (backward compatible)."""
        return self.gemini_api_keys[0]

    @classmethod
    def from_env(cls) -> "Settings":
        # Support comma-separated GEMINI_API_KEYS or single GEMINI_API_KEY
        keys_str = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
        if not keys_str:
            raise ValueError("GEMINI_API_KEYS or GEMINI_API_KEY is required. Add it to your environment or .env file.")
        
        api_keys = [k.strip() for k in keys_str.split(",") if k.strip()]

        return cls(
            gemini_api_keys=api_keys,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite"),
            browser_headless=os.getenv("BROWSER_HEADLESS", "false").lower() == "true",
            sessions_dir=Path(os.getenv("SESSIONS_DIR", "sessions")),
            logs_dir=Path(os.getenv("LOGS_DIR", "logs")),
            credentials_store=Path(os.getenv("CREDENTIALS_STORE", "config/credentials.enc")),
        )
