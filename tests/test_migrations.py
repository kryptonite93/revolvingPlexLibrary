from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


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
        "requester_profile",
        "tracker_policy",
        "dry_run_proposal",
        "rollout_policy",
        "deletion_job",
        "manual_deletion_batch",
        "manual_deletion_item",
        "alembic_version",
    }.issubset(inspector.get_table_names())
    assert "monitored" in {column["name"] for column in inspector.get_columns("media_lifecycle")}
    assert "seeding_seconds" in {
        column["name"] for column in inspector.get_columns("torrent")
    }
    assert "selected" in {
        column["name"] for column in inspector.get_columns("tracker_policy")
    }
    assert "user_name" in {column["name"] for column in inspector.get_columns("playback")}


def test_tracker_selection_migration_preserves_existing_rules(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "existing-rules.db"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("CONFIG_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "0007_tracker_dry_run")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO tracker_policy (
                    id, normalized_domain, minimum_ratio, minimum_seed_seconds,
                    combination, grace_period_seconds, automatic_deletion_allowed,
                    created_at, updated_at
                ) VALUES (
                    'policy-1', 'tracker.example', 1.0, 864000,
                    'RATIO_OR_TIME', 43200, 0,
                    '2026-08-19 00:00:00', '2026-08-19 00:00:00'
                )"""
            )
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        selected = connection.scalar(
            text(
                "SELECT selected FROM tracker_policy "
                "WHERE normalized_domain = 'tracker.example'"
            )
        )
    assert selected == 1
