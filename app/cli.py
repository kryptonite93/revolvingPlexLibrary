from __future__ import annotations

import argparse

from app.services.backups import create_verified_backup
from app.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="revolving-plex")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup", help="Create and verify an online SQLite backup")
    args = parser.parse_args()

    settings = Settings()
    settings.prepare()
    if args.command == "backup":
        result = create_verified_backup(
            settings.sqlite_path,
            settings.backup_directory,
            settings.backup_retention_count,
        )
        print(f"Verified backup: {result.path} ({result.integrity_result})")
