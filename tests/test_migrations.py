from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_migrations_apply_to_empty_database(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration.db"
    monkeypatch.setenv("CONFIG_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{database_path.as_posix()}")
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}"))
    assert {
        "admin_user",
        "event",
        "integration_instance",
        "managed_library",
        "source_freshness",
        "inventory_policy",
        "sync_run",
        "media_identity",
        "media_lifecycle",
        "media_file_revision",
        "playback",
        "torrent",
        "torrent_tracker",
        "torrent_media_mapping",
        "request_record",
        "alembic_version",
    }.issubset(inspector.get_table_names())
    assert "monitored" in {column["name"] for column in inspector.get_columns("media_lifecycle")}
