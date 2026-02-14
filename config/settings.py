from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(slots=True)
class Settings:
    """Application configuration loaded from environment variables."""

    gemini_api_key: str
    browser_headless: bool
    sessions_dir: Path
    logs_dir: Path
    credentials_store: Path

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required. Add it to your environment or .env file.")

        return cls(
            gemini_api_key=api_key,
            browser_headless=os.getenv("BROWSER_HEADLESS", "false").lower() == "true",
            sessions_dir=Path(os.getenv("SESSIONS_DIR", "sessions")),
            logs_dir=Path(os.getenv("LOGS_DIR", "logs")),
            credentials_store=Path(os.getenv("CREDENTIALS_STORE", "config/credentials.enc")),
        )
