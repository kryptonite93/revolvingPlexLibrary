from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class CredentialCipher:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, values: dict[str, str]) -> str:
        serialized = json.dumps(values, separators=(",", ":"), sort_keys=True).encode()
        return self._fernet.encrypt(serialized).decode()

    def decrypt(self, token: str) -> dict[str, str]:
        try:
            value: Any = json.loads(self._fernet.decrypt(token.encode()))
        except (InvalidToken, json.JSONDecodeError) as error:
            raise ValueError("Stored credentials cannot be decrypted") from error
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise ValueError("Stored credentials have an invalid format")
        return value
