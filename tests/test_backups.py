from __future__ import annotations

import sqlite3

from app.services.backups import create_verified_backup


def test_creates_verified_sqlite_backup(tmp_path) -> None:
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as database:
        database.execute("CREATE TABLE example (value TEXT NOT NULL)")
        database.execute("INSERT INTO example VALUES ('preserved')")
        database.commit()

    result = create_verified_backup(source, tmp_path / "backups", retention_count=3)
    assert result.path.is_file()
    assert result.integrity_result == "ok"
    with sqlite3.connect(result.path) as backup:
        assert backup.execute("SELECT value FROM example").fetchone()[0] == "preserved"
