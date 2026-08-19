# Changelog

## Unreleased

- Treated retained Arr media without a current qBittorrent mapping as tracker-not-applicable instead of permanently blocking cleanup review, and added the total eligible storage to the deletion preview.
- Replaced the automatically expanded tracker list with a searchable Add trackers picker and a compact selected-rule view.
- Added per-tracker ratio/seed-time policies and a persisted, fail-closed read-only deletion preview.
- Preserved each season's actual meaningful-watch date while applying forward TV retention resets separately.
- Labeled Sonarr season zero as Specials and excluded missing seasons from series retention summaries.
- Separated current Arr files from clearly labeled, collapsed historical revisions on media detail pages.
- Consolidated connection, retention, and freshness configuration under a Settings page at the bottom of the navigation.
- Simplified qBittorrent authentication to API keys for version 5.2 and newer.
- Added reversible manual protection for selected media or every lifecycle matching the current Media filters.
- Added compact text protection indicators to collapsed television-series rows.
- Versioned static asset URLs so container updates cannot reuse incompatible cached styles or scripts.
- Prevented missing media and pre-import television playback from producing removal-review deadlines.
- Named the integrations whose stale data is blocking a lifecycle decision.
- Grouped television seasons by series and added searchable, paginated Media filters.
- Clarified missing and unmonitored media states throughout the lifecycle workbench.
- Clarified that Tautulli synchronization reports newly imported playback rows.
- Rendered Web UI timestamps in the timezone configured by the Unraid template.
- Fixed Radarr inventory by scoping movie-file requests and paginating complete Arr history.
- Reduced SQLite contention by completing remote inventory fetches before opening write transactions.
- Clarified the management-mode confirmation copy.
- Added integration editing and typed-confirmation local removal without sending delete commands to connected services.
- Fixed Unraid startup by running the container as the standard appdata owner (`99:100`).
- Reduced the local administrator password minimum from 12 characters to 8.

## 0.1.0 - 2026-08-16

- Added the safety-first application foundation and Unraid deployment template.
- Added encrypted read-only integrations for Plex, Tautulli, Overseerr, Radarr, Sonarr, and qBittorrent.
- Added normalized inventory synchronization, source freshness, lifecycle retention decisions, and the media workbench.
- Kept Active Management unavailable while the application remains in Inventory Only rollout.
