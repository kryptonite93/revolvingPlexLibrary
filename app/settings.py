from __future__ import annotations

import os
import secrets
from functools import cached_property
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Revolving Plex Manager"
    app_environment: str = "production"
    app_secret: str | None = None
    config_directory: Path = Path("./config")
    database_url: str | None = None
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=8787, ge=1, le=65535)
    timezone: str = Field(
        default="America/Toronto",
        validation_alias=AliasChoices("TIMEZONE", "TZ"),
    )
    secure_cookies: bool = True
    session_max_age_seconds: int = Field(default=43_200, ge=900, le=604_800)
    login_attempt_limit: int = Field(default=5, ge=1, le=50)
    login_attempt_window_seconds: int = Field(default=900, ge=60, le=86_400)
    backup_retention_count: int = Field(default=14, ge=1, le=365)
    scheduler_poll_seconds: int = Field(default=60, ge=10, le=3600)

    def prepare(self) -> None:
        self.config_directory.mkdir(parents=True, exist_ok=True)
        self.backup_directory.mkdir(parents=True, exist_ok=True)

    @cached_property
    def sqlite_path(self) -> Path:
        return self.config_directory / "revolving_plex.db"

    @cached_property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+pysqlite:///{self.sqlite_path.as_posix()}"

    @cached_property
    def backup_directory(self) -> Path:
        return self.config_directory / "backups"

    @cached_property
    def signing_secret(self) -> str:
        if self.app_secret:
            return self.app_secret

        secret_path = self.config_directory / "app-secret.key"
        if secret_path.exists():
            return secret_path.read_text(encoding="utf-8").strip()

        self.config_directory.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(48)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(secret_path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
        return value

    @cached_property
    def credential_encryption_key(self) -> bytes:
        key_path = self.config_directory / "credential-encryption.key"
        if key_path.exists():
            return key_path.read_bytes().strip()

        self.config_directory.mkdir(parents=True, exist_ok=True)
        from cryptography.fernet import Fernet

        value = Fernet.generate_key()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(key_path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
        return value
