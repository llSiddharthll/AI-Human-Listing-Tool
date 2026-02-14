from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet


class CredentialManager:
    """Encrypts and decrypts platform credentials using Fernet."""

    def __init__(self, store_path: Path) -> None:
        self.store_path = store_path
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        key = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
        if not key:
            raise ValueError(
                "CREDENTIAL_ENCRYPTION_KEY is required. Generate one via python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        self.fernet = Fernet(key.encode())

    def save_credentials(self, platform: str, credentials: dict[str, str]) -> None:
        existing = self.load_all()
        existing[platform] = credentials
        payload = json.dumps(existing).encode()
        encrypted = self.fernet.encrypt(payload)
        self.store_path.write_bytes(base64.b64encode(encrypted))

    def get_credentials(self, platform: str) -> dict[str, str]:
        all_credentials = self.load_all()
        if platform not in all_credentials:
            raise KeyError(f"No credentials saved for platform '{platform}'.")
        return all_credentials[platform]

    def load_all(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {}
        encrypted = base64.b64decode(self.store_path.read_bytes())
        decrypted = self.fernet.decrypt(encrypted)
        return json.loads(decrypted.decode())
