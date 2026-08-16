from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class BackupResult:
    path: Path
    integrity_result: str


def create_verified_backup(
    source: Path,
    destination_directory: Path,
    retention_count: int = 14,
) -> BackupResult:
    if not source.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {source}")

    destination_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    final_path = destination_directory / f"revolving_plex-{stamp}.db"
    temporary_path = destination_directory / f".{final_path.name}.partial"

    if temporary_path.exists():
        temporary_path.unlink()

    with (
        closing(sqlite3.connect(source)) as source_db,
        closing(sqlite3.connect(temporary_path)) as backup_db,
    ):
        source_db.backup(backup_db)

    with closing(
        sqlite3.connect(f"file:{temporary_path.as_posix()}?mode=ro", uri=True)
    ) as check_db:
        cursor = check_db.execute("PRAGMA integrity_check")
        try:
            result = str(cursor.fetchone()[0])
        finally:
            cursor.close()
    if result.lower() != "ok":
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"Backup integrity check failed: {result}")

    temporary_path.replace(final_path)
    backups = sorted(destination_directory.glob("revolving_plex-*.db"), reverse=True)
    for obsolete in backups[retention_count:]:
        obsolete.unlink()
    return BackupResult(path=final_path, integrity_result=result)
