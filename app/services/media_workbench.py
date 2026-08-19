from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil

from app.persistence.models import IntegrationInstance, MediaIdentity, MediaLifecycle


@dataclass(frozen=True)
class WorkbenchRow:
    lifecycle: MediaLifecycle
    identity: MediaIdentity
    integration: IntegrationInstance
    torrent_count: int


@dataclass
class WorkbenchEntry:
    kind: str
    title: str
    integration: IntegrationInstance
    rows: list[WorkbenchRow]

    @property
    def active_count(self) -> int:
        return sum(row.lifecycle.state == "ACTIVE" for row in self.rows)

    @property
    def missing_count(self) -> int:
        return sum(row.lifecycle.state == "MISSING" for row in self.rows)

    @property
    def protected_count(self) -> int:
        return sum(
            row.lifecycle.protection_state == "PROTECTED" for row in self.rows
        )

    @property
    def fully_protected(self) -> bool:
        return bool(self.rows) and self.protected_count == len(self.rows)

    @property
    def next_deadline(self) -> datetime | None:
        deadlines = [
            row.lifecycle.retention_deadline
            for row in self.rows
            if row.lifecycle.retention_deadline is not None
        ]
        return min(deadlines) if deadlines else None

    @property
    def latest_import(self) -> datetime | None:
        imports = [
            row.lifecycle.first_imported_at
            for row in self.rows
            if row.lifecycle.first_imported_at is not None
        ]
        return max(imports) if imports else None


@dataclass(frozen=True)
class WorkbenchPage:
    entries: list[WorkbenchEntry]
    page: int
    page_count: int
    total_entries: int
    total_lifecycles: int


def _series_title(identity: MediaIdentity) -> str:
    marker = " · Season "
    return identity.canonical_title.rsplit(marker, 1)[0]


def _timestamp(value: datetime | None, *, missing: float) -> float:
    if value is None:
        return missing
    aware = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.timestamp()


def build_workbench_page(
    rows: list[WorkbenchRow],
    *,
    sort: str,
    page: int,
    page_size: int = 50,
) -> WorkbenchPage:
    entries: list[WorkbenchEntry] = []
    series_groups: dict[tuple[str, int], WorkbenchEntry] = {}
    for row in rows:
        if row.identity.media_type == "SEASON":
            key = (row.integration.id, row.lifecycle.arr_item_id)
            entry = series_groups.get(key)
            if entry is None:
                entry = WorkbenchEntry(
                    kind="SERIES",
                    title=_series_title(row.identity),
                    integration=row.integration,
                    rows=[],
                )
                series_groups[key] = entry
                entries.append(entry)
            entry.rows.append(row)
        else:
            entries.append(
                WorkbenchEntry(
                    kind="MOVIE",
                    title=row.identity.canonical_title,
                    integration=row.integration,
                    rows=[row],
                )
            )

    for entry in series_groups.values():
        entry.rows.sort(key=lambda row: row.identity.season_number or 0)

    if sort == "title":
        entries.sort(key=lambda entry: (entry.title.casefold(), entry.integration.name.casefold()))
    elif sort == "imported_newest":
        entries.sort(
            key=lambda entry: (
                entry.latest_import is None,
                -_timestamp(entry.latest_import, missing=0),
                entry.title.casefold(),
            )
        )
    elif sort == "retention_latest":
        entries.sort(
            key=lambda entry: (
                entry.next_deadline is None,
                -_timestamp(entry.next_deadline, missing=0),
                entry.title.casefold(),
            )
        )
    else:
        entries.sort(
            key=lambda entry: (
                _timestamp(entry.next_deadline, missing=float("inf")),
                entry.title.casefold(),
            )
        )

    total_entries = len(entries)
    page_count = max(1, ceil(total_entries / page_size))
    current_page = min(max(page, 1), page_count)
    start = (current_page - 1) * page_size
    return WorkbenchPage(
        entries=entries[start : start + page_size],
        page=current_page,
        page_count=page_count,
        total_entries=total_entries,
        total_lifecycles=len(rows),
    )
