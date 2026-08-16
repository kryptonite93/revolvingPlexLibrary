from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.security.credentials import CredentialCipher


def test_credentials_round_trip_without_plaintext() -> None:
    cipher = CredentialCipher(Fernet.generate_key())
    encrypted = cipher.encrypt({"api_key": "top-secret"})
    assert "top-secret" not in encrypted
    assert cipher.decrypt(encrypted) == {"api_key": "top-secret"}


def test_credentials_reject_wrong_key() -> None:
    token = CredentialCipher(Fernet.generate_key()).encrypt({"api_key": "top-secret"})
    with pytest.raises(ValueError, match="cannot be decrypted"):
        CredentialCipher(Fernet.generate_key()).decrypt(token)
