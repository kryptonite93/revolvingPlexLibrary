# Changelog

## Unreleased

- Fixed Manual Management checking the operation coordinator only after attempting a database write, and replaced SQLite lock failures with a safe retry message.
- Separated Arr automation protection from explicit Manual Management, allowing Active Management on Protected Radarr and Sonarr instances while automated proposals remain blocked, and prevented expanded Settings controls from colliding at intermediate widths.
- Fixed Manual Management retries getting stranded when Radarr reports a duplicate import exclusion as HTTP 400 or 409 after deleting a movie.
- Changed Manual Management’s filtered selection into a select-all-except workflow with editable checkboxes and cross-page exclusions.
- Added Overseerr request date as a Manual Management sorting option.
- Added ascending and descending Manual Management sorting by name, last meaningful watch, release date, or size.
- Added selected storage totals beside the selected-item count in Manual Management, including all filtered pages.
- Added meaningful-watch filtering and watch evidence to Manual Management for movies and individual seasons.
- Added requester-based Manual Management across Radarr and Sonarr, with cross-instance provider matching, selectable season dropdowns, one-step batch confirmation, live safety revalidation, and optional Radarr import exclusions.
- Added Tautulli usernames to meaningful playback evidence and backfilled stored playback rows during synchronization.
- Fixed Plex reconciliation when a successful library refresh returns an empty response.
- Simplified movie execution to one confirmation checkbox and made every Radarr deletion create or verify an import exclusion so synchronized lists cannot silently restore it.
- Added restart-safe, explicitly approved movie deletion jobs for a Managed Radarr instance, with fresh live revalidation, Radarr-first ordering, optional mapped-torrent cleanup, and Plex/Overseerr reconciliation.
- Added staged global rollout controls and kept Sonarr execution unavailable while the non-4K Radarr manual workflow is validated.
- Removed stale media mappings when torrents disappear from qBittorrent and excluded absent torrents from current deletion evidence.
- Exposed every other title sharing a mapped torrent on lifecycle evidence pages and named those titles in blocked deletion-preview reasons.
- Added state filters to the dedicated Deletion Queue and made its calculation action amber only when the preview is out of date.
- Made Save actions neutral until their form changes, then amber while work remains unsaved.
- Made tracker minimum seed time a whole-day field with matching server validation.
- Moved each tracker rule's explicit save action into its header and made unsaved changes visible.
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
