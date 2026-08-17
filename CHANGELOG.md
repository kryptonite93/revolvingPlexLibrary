# Changelog

## Unreleased

- Fixed Unraid startup by running the container as the standard appdata owner (`99:100`).
- Reduced the local administrator password minimum from 12 characters to 8.

## 0.1.0 - 2026-08-16

- Added the safety-first application foundation and Unraid deployment template.
- Added encrypted read-only integrations for Plex, Tautulli, Overseerr, Radarr, Sonarr, and qBittorrent.
- Added normalized inventory synchronization, source freshness, lifecycle retention decisions, and the media workbench.
- Kept Active Management unavailable while the application remains in Inventory Only rollout.
