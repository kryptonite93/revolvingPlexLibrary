# Revolving Plex Manager — Phase 1 Build Sheet

Status: implementation specification  
Audience: Codex and human maintainers  
Target environment: Docker on Unraid  
Primary goal: safely operate a playback-aware, seed-aware revolving Plex library without replacing Plex, Seerr, Sonarr, Radarr, or the torrent client.

Locked implementation target: Phase 1 integrates with Overseerr 1.34.0. The request-system boundary must remain provider-neutral so Seerr can be supported later without changing domain policy. References to Seerr below describe the request-system role and future compatibility; the concrete Phase 1 adapter and Web UI label are Overseerr.

## 1. How to use this document

This document is the source of truth for Phase 1. Build in the milestone order in section 24 and do not enable destructive behavior before its rollout gate is satisfied.

When an implementation detail is unclear:

1. Preserve data.
2. Prefer read-only behavior.
3. Block deletion and show a human-readable reason.
4. Record the decision and source data in the event history.
5. Do not infer that an integration is safe to manage merely because its credentials work.

## 2. Product summary

The application coordinates existing media services through their APIs:

```text
Plex users
   ├── request through Seerr
   └── watch through Plex
                    │
                    ▼
                 Tautulli
                    │
                    ▼
        Revolving Plex Manager
        ├── inventory and lifecycle state
        ├── retention calculations
        ├── user and tag protections
        ├── tracker requirement checks
        ├── deletion scheduling
        ├── Seerr re-request reconciliation
        └── immutable audit/event history
             │          │          │
             ▼          ▼          ▼
          Radarr      Sonarr    Torrent client
             └──────── Plex libraries ────────┘
```

Phase 1 does not stream content. Plex remains the only playback client and Plex-visible media remains in the normal Arr library paths.

## 3. Locked product decisions

The following decisions are part of Phase 1:

- This is a new purpose-built management application, not a Torrin fork.
- Plex remains the user-facing playback system.
- Seerr remains the request system.
- Tautulli is the authoritative playback-history source.
- Radarr and Sonarr remain responsible for importing and deleting library media.
- The torrent client remains responsible for torrent state and torrent-side data deletion.
- The manager never directly deletes media files in Phase 1.
- Production uses embedded SQLite in Phase 1. The database lives on the persistent local `/config` volume; external PostgreSQL may be added later without changing domain behavior.
- Historical records are retained after media is deleted.
- Meaningful source payloads are stored after secrets are sanitized.
- Metrics must be rebuildable from event history.
- Existing shared media is protected by a one-time legacy bootstrap.
- The personal 4K Radarr instance is protected at the instance level, with an Arr tag as a visible second safeguard.
- Unknown or ambiguous state blocks deletion.
- Initial rollout is read-only, followed by dry-run, manual approval, automatic movies, and finally automatic television.

## 4. Confirmed deployment and control decisions

1. **Integration Enable is an app-level connector switch, not an Arr media tag.** Every integration instance has an `enabled` boolean. When it is off, that integration is completely inactive except for an administrator-triggered connection test.
2. **Active Management is a second, independent per-instance switch.** An enabled integration with `active_management_enabled=false` continues health checks, inventory synchronization, history collection, retention calculations, and dry-run proposals, but cannot perform destructive media or torrent actions.
3. **Management mode remains a separate role.** Managed, protected, and ignored describe what the instance is allowed to do; Enable controls whether the connector runs; Active Management controls whether a managed instance may perform destructive actions.
4. **Movie-first rollout uses Active Management.** Shared Radarr and Sonarr may both be enabled and fully inventoried while Active Management is enabled for shared Radarr first and remains disabled for Sonarr.
5. **qBittorrent is the first complete torrent adapter and runs as an external container.** The core interface must permit a later Deluge adapter without changing domain logic.
6. **SQLite is the Phase 1 production database.** It is embedded in the manager process, stored under `/config`, configured for WAL mode and foreign-key enforcement, and protected by app-managed verified backups. The `/config` path must use local/cache-backed storage rather than SMB, NFS, or another network filesystem.
7. **Default timezone is `America/Toronto`, configurable during setup.** Store timestamps in UTC and render them in the configured timezone.

## 5. Scope

### 5.1 Required in Phase 1

- Local administrator authentication.
- Setup/onboarding wizard.
- Seerr connection and Arr instance discovery.
- Manual Arr credential fallback.
- Multiple Radarr and Sonarr instances.
- Independent `enabled` switch on every integration instance.
- Independent `active_management_enabled` switch on every Arr and torrent-client instance.
- Independent management mode on every Arr instance.
- Connection health and last-success state for every integration.
- Explicit Plex library selection.
- Radarr movie inventory.
- Sonarr series, season, episode-file, and monitoring inventory.
- Tautulli playback history import and incremental synchronization.
- Current Plex session check immediately before deletion.
- Seerr request and requester mapping.
- Torrent inventory, tracker policy evaluation, and media mapping.
- Movie- and season-level retention calculations.
- Quality-upgrade continuity.
- Legacy and protected bootstrap behavior.
- Dry-run proposals, manual approval, and automatic execution modes.
- Arr-mediated library deletion.
- Torrent removal and torrent-side data deletion.
- Plex refresh and Seerr re-request reconciliation.
- Immutable event history and deletion audit snapshots.
- Human-readable decision explanations.
- Database backup and restore instructions.
- Docker deployment suitable for Unraid.
- SQLite persistence/backup configuration and external qBittorrent connection configuration.

### 5.2 Explicitly out of scope

- Stremio or direct HTTP streaming.
- Replacing Plex, Seerr, Sonarr, Radarr, Tautulli, or the torrent client.
- Direct file deletion or modification.
- Media-directory mounts in the application container.
- SSD-to-HDD promotion or demotion.
- Transcoding.
- Automatic torrent acquisition.
- TimescaleDB.
- A public multi-user application.
- Mobile apps.
- Managing the personal 4K library destructively.
- Deluge implementation unless explicitly promoted into Phase 1 before coding begins.

## 6. Safety invariants

These rules must be enforced in domain logic, not only in the UI:

1. A disabled integration instance performs no scheduled work and cannot participate in an execution job.
2. An enabled Arr instance with Active Management disabled performs read-only synchronization and dry-run evaluation but cannot delete files, change monitoring state, remove a series/movie, alter Seerr request state, or initiate torrent removal.
3. A torrent-client instance with Active Management disabled cannot remove a torrent or its data, regardless of the associated Arr instance state.
4. A protected/read-only Arr instance can never produce an executable deletion job.
5. An ignored instance is excluded from routine inventory and lifecycle evaluation.
6. Automatic deletion is impossible until both the per-instance Active Management switch and global rollout mode permit it.
7. The manager never deletes a path directly.
8. Library media is deleted through Radarr or Sonarr first.
9. Torrent-side data is removed only after the applicable Arr deletion succeeds.
10. Active Plex playback blocks deletion.
11. `retention-protected` blocks deletion.
12. `retention-legacy` blocks deletion until explicitly released.
13. Incomplete or unknown tracker requirements block torrent and media deletion.
14. A missing media-to-torrent mapping blocks automatic deletion unless an explicit, separately audited no-torrent policy is later added.
15. Integration failures, stale data, conflicting mappings, or ambiguous IDs fail closed.
16. A quality upgrade cannot reset a title’s original import date, watch history, protection, or existing retention deadline.
17. Deleting operational media never deletes its historical database records.
18. A re-request must not be prevented by an Arr import exclusion.
19. Credentials, tokens, cookies, passkeys, and secret URLs cannot appear in logs, events, API responses, or browser state.

## 7. Integration-instance state model

Every integration instance has an Enable switch. Every Arr and torrent-client instance also has an independent Active Management switch. Arr instances additionally have a management mode.

### 7.1 Enable switch

```text
enabled = false
    Credentials and configuration are retained.
    Manual connection test is allowed.
    Scheduled synchronization does not run.
    Inventory and history collection do not run.
    Retention evaluation does not run.
    Bootstrap and metadata actions do not run.
    No deletion job can be approved or executed.

enabled = true
    The connector performs the work allowed by its other controls.
```

Changing `enabled` must:

- require an authenticated administrator;
- create an immutable `integration.enabled_changed` event;
- record old value, new value, actor, timestamp, and reason if supplied;
- cancel or block queued executable jobs for the instance when switched off;
- never resume old deletion approvals automatically when switched back on;
- require a fresh synchronization and eligibility recalculation after re-enabling.

The UI must show the enable state in the integration list, dashboard, media list, and deletion-job detail. A connection test is the only permitted interaction with a disabled connector.

### 7.2 Active Management switch

```text
active_management_enabled = false
    Continue health checks and scheduled read-only synchronization.
    Continue collecting history and torrent statistics.
    Continue retention, protection, mapping, and tracker calculations.
    Create clearly marked dry-run proposals.
    Do not delete or unmonitor media in Arr.
    Do not remove a movie or series from Arr.
    Do not remove a torrent or delete torrent-side data.
    Do not perform any other scheduled torrent-client mutation.
    Do not mutate Plex or Seerr as part of a deletion workflow.

active_management_enabled = true
    Destructive actions may occur only when management_mode and the global
    rollout mode also permit them and every safety predicate passes.
```

Changing Active Management must:

- require an authenticated administrator and an explicit confirmation when enabling;
- create an immutable `integration.active_management_changed` event;
- record old value, new value, actor, timestamp, and reason if supplied;
- immediately block queued destructive steps when switched off;
- invalidate existing approvals when switched either direction;
- require a fresh read-only synchronization and revalidation before any destructive action after enabling.

The switch gates destructive lifecycle management. Explicit administrator-confirmed setup metadata actions—creating/applying retention tags and releasing legacy tags—are allowed while Active Management is off because they do not delete or modify files. They must always show a preview, never run merely because the switch changed, and remain impossible when the integration itself is disabled.

### 7.3 Management mode

```text
MANAGED
    Inventory, retention calculations, proposals, and permitted writes.

PROTECTED
    Inventory and analytics only. No retention-driven Arr or torrent mutation.

IGNORED
    No scheduled inventory or lifecycle management. Retain historical records.
```

### 7.4 Execution matrix

| Enabled | Active Management | Mode | Inventory | Proposals | Destructive actions |
|---|---|---|---:|---:|---:|
| No | Any | Any | No | No | Never |
| Yes | No | Managed | Yes | Dry-run only | Never |
| Yes | Yes | Managed | Yes | Yes | Governed by rollout mode |
| Yes | Any | Protected | Yes | Protected/read-only | Never |
| Yes | Any | Ignored | No | No | Never |

For the torrent client, `active_management_enabled=false` still permits torrent inventory and tracker evaluation but blocks `remove_torrent` and any other state-changing operation.

Recommended initial configuration:

```text
Shared Radarr        enabled=true  active_management=false  mode=MANAGED
Sonarr               enabled=true  active_management=false  mode=MANAGED
Personal 4K Radarr   enabled=true  active_management=false  mode=PROTECTED
qBittorrent          enabled=true  active_management=false
```

After the read-only and dry-run gates pass, enable Active Management for shared Radarr and qBittorrent. Leave Sonarr Active Management off until television is deliberately introduced.

## 8. Global rollout mode

Global rollout mode is separate from both per-instance switches:

```text
INVENTORY_ONLY
    Synchronize and calculate state. Do not create deletion proposals.

DRY_RUN
    Create proposed deletion jobs. Perform no external writes.

APPROVAL_REQUIRED
    An administrator may approve and execute individual jobs.

AUTOMATIC
    Eligible jobs may execute inside the configured deletion window.
```

Changing to a more permissive mode requires confirmation and an audit event. Changing to a less permissive mode takes effect immediately. Active Management disabled always forces that instance to behave as dry-run even when the global mode is `APPROVAL_REQUIRED` or `AUTOMATIC`.

No instance may skip rollout stages when Active Management is first enabled. It must already have a successful full sync and current dry-run evaluation before destructive actions become possible.

## 9. Recommended technical stack

```text
Language:             Python
Web/API:              FastAPI
Validation/settings:  Pydantic
Database:             SQLite (embedded, WAL mode)
ORM/migrations:       SQLAlchemy 2 + Alembic
HTTP client:          httpx
Scheduler:            APScheduler or an equivalent persistent scheduler
Admin UI:             server-rendered Jinja + HTMX
Authentication:       local admin account + Argon2id password hashing
Testing:              pytest + integration fixtures/fake servers
Packaging:            single application container with an Unraid template
```

Use a layered/modular monolith. Integration adapters must not contain retention policy logic, and UI routes must not contain deletion workflow logic.

Suggested source layout:

```text
app/
├── api/
├── auth/
├── domain/
│   ├── media/
│   ├── retention/
│   ├── protection/
│   ├── trackers/
│   └── deletion/
├── integrations/
│   ├── plex/
│   ├── tautulli/
│   ├── seerr/
│   ├── radarr/
│   ├── sonarr/
│   └── torrents/
│       ├── base.py
│       └── qbittorrent.py
├── persistence/
├── scheduler/
├── services/
├── templates/
└── static/
tests/
├── unit/
├── integration/
├── contract/
└── end_to_end/
```

## 10. Deployment model

The application container needs only persistent configuration storage and API network access. It does not mount media paths.

All manager runtime dependencies—web UI, API, scheduler, migrations, SQLite, backup orchestration, and required language libraries—ship with the manager image. Existing media services and qBittorrent remain external.

```text
/config
├── generated encryption material if not supplied externally
├── revolving_plex.db and SQLite WAL files
├── exports/backups
└── non-secret application logs
```

Required application configuration:

- Configuration directory, mounted persistently at `/config` in production.
- Application secret/encryption key supplied outside the database.
- Web listen address and port.
- Timezone.
- Optional initial administrator bootstrap values.

All integration credentials are entered through setup or discovered through Seerr, encrypted at rest, and never returned to the browser after submission.

Provide:

- documented local-development commands;
- a production Dockerfile;
- an Unraid Community Applications template;
- health endpoint;
- database migration entrypoint;
- documented Unraid container variables and network requirements;
- graceful shutdown that prevents half-executed deletion workflows.

### 10.1 SQLite packaging decision

Phase 1 uses SQLite to match the normal one-container Unraid experience of Sonarr, Radarr, and Tautulli. SQLite runs in-process; no database server or supervisor is bundled.

Production requirements:

- store `revolving_plex.db` on the persistent `/config` volume;
- require `/config` to use a local or cache-backed filesystem, never SMB, NFS, or another network filesystem;
- enable WAL mode, foreign keys, and a bounded busy timeout on every connection;
- serialize destructive workflow execution and use database locks/unique constraints to prevent duplicate jobs;
- run schema migrations before the web process becomes ready;
- create scheduled online backups in `/config/backups` using SQLite’s backup API;
- run `PRAGMA integrity_check` against every produced backup before marking it successful;
- retain multiple dated backups and expose the most recent verified result in the UI;
- document an offline restore procedure that works when the application cannot start;
- never place the database only in the writable container layer.

The persistence boundary remains isolated behind repositories and SQLAlchemy models so a future external PostgreSQL option can be introduced without changing domain policies or integration adapters.

## 11. Integration contracts

### 11.1 Seerr

Use Seerr for:

- discovering configured Radarr and Sonarr instances from administrator settings;
- requester and request history;
- current availability/request state;
- post-deletion reconciliation;
- verifying that deleted media or seasons are requestable again.

Onboarding initially requests:

```text
Seerr internal URL
Seerr API key
```

Attempt discovery through the installed Seerr administrator settings endpoints for Radarr and Sonarr. Display all discovered servers and require the administrator to assign `enabled` and `management_mode`. Retain manual Arr URL/API-key entry as a fallback.

Do not assume all Seerr versions expose identical write endpoints. During implementation, inspect the OpenAPI document exposed by the owner’s installed Seerr version and isolate version-specific behavior in the adapter.

### 11.2 Plex

Use Plex for:

- explicit library selection;
- media identifier/path reconciliation where needed;
- current active-session query immediately before deletion;
- library refresh after deletion;
- confirmation that removed media is no longer available.

Do not rely exclusively on cached playback state. The pre-delete active-session check is mandatory.

### 11.3 Tautulli

Use Tautulli for:

- complete historical playback import;
- incremental playback history synchronization;
- Plex user identity;
- duration played, progress, and watch completion;
- library and media mapping data.

Suggested intervals:

```text
Incremental playback history: every 15 minutes
Full reconciliation:          once daily
```

Deduplicate by stable Tautulli history ID. Store the sanitized raw source payload so future metrics can be rebuilt.

### 11.4 Radarr

Use Radarr for:

- movie and movie-file inventory;
- history/import events;
- tags;
- file metadata and paths;
- movie deletion with files;
- removal without creating an import exclusion.

Stable identity is `(arr_instance_id, radarr_movie_id)` plus TMDb ID for cross-system reconciliation.

### 11.5 Sonarr

Use Sonarr for:

- series, season, episode, and episode-file inventory;
- history/import events;
- monitoring state;
- series-level tags;
- episode-file deletion;
- season monitoring updates;
- final series removal.

Stable identity is `(arr_instance_id, sonarr_series_id, season_number)` plus TVDb ID for cross-system reconciliation.

### 11.6 Torrent client

Define a client-neutral adapter:

```text
TorrentClient
├── health_check()
├── list_torrents()
├── get_torrent(hash)
├── get_trackers(hash)
├── get_files(hash)
├── get_ratio(hash)
├── get_seed_time(hash)
├── get_added_time(hash)
├── add_tag(hash, tag)
└── remove_torrent(hash, delete_data=true)
```

Implement qBittorrent first. It is an external prerequisite installed separately—normally from Unraid Community Apps—and is not bundled into the manager image. Domain services receive normalized torrent objects and must not depend on qBittorrent response shapes.

## 12. Core data model

Use UUID primary keys internally unless a table is naturally keyed by a source event ID. Preserve all source IDs separately.

### 12.1 `integration_instance`

```text
id
kind                         plex | tautulli | seerr | radarr | sonarr | qbittorrent
name
base_url
encrypted_credentials
enabled
active_management_enabled    nullable; used by radarr | sonarr | qbittorrent
management_mode              MANAGED | PROTECTED | IGNORED
connection_status
last_health_check_at
last_success_at
last_error_code
last_error_summary
discovered_via               SEERR | MANUAL
external_instance_id
created_at
updated_at
```

For every integration, `enabled` acts as a connector switch. For Arr and torrent-client integrations, `active_management_enabled` is the destructive-action gate. If a required shared integration is disabled or unhealthy, deletion fails closed globally.

### 12.2 `managed_library`

```text
id
plex_integration_id
plex_library_key
name
media_type                   MOVIE | SHOW
enabled
created_at
updated_at
```

Only explicitly selected and enabled Plex libraries are in scope.

### 12.3 `media_identity`

Represents a title across lifecycles.

```text
id
media_type                   MOVIE | SEASON
tmdb_id
tvdb_id
series_tmdb_id
series_tvdb_id
season_number
canonical_title
created_at
```

### 12.4 `media_lifecycle`

Represents one continuous presence in the managed library.

```text
id
media_identity_id
arr_instance_id
arr_media_id
plex_rating_key
library_id
status                       ACTIVE | DELETED | MISSING | ERROR
first_imported_at
deleted_at
previous_lifecycle_id
last_meaningful_watch_at
retention_until
watched
protection_state
protection_source
legacy
legacy_reason
legacy_applied_at
current_path
current_size_bytes
last_synced_at
version
```

When deleted media returns, create a new lifecycle linked to the previous lifecycle. Never reactivate the deleted row.

### 12.5 `media_file_revision`

Tracks imports and upgrades without resetting lifecycle state.

```text
id
media_lifecycle_id
arr_file_id
path
size_bytes
quality
imported_at
replaced_at
source_torrent_hash
source_payload
```

### 12.6 `playback`

```text
id
tautulli_history_id          unique
media_lifecycle_id
plex_user_id
plex_user_name
started_at
stopped_at
duration_played_seconds
media_duration_seconds
completion_percent
watched_scrobble
meaningful
source_payload
recorded_at
```

### 12.7 `torrent`

```text
id
torrent_client_id
info_hash
name
added_at
completed_at
ratio
seed_duration_seconds
save_path
status
requirement_status
last_synced_at
source_payload
unique(torrent_client_id, info_hash)
```

### 12.8 `torrent_tracker`

```text
id
torrent_id
normalized_domain
announce_url_redacted
status
last_message_sanitized
last_synced_at
```

### 12.9 `torrent_media_mapping`

```text
id
torrent_id
media_lifecycle_id
mapping_method
mapping_confidence
matched_files
created_at
confirmed_by_admin_at
```

One torrent may map to multiple seasons. Ambiguous automatic mappings cannot be used for automatic deletion.

### 12.10 `seerr_request`

```text
id
seerr_request_id
media_identity_id
media_lifecycle_id           nullable
requester_external_id
requester_name
requested_at
request_status
is_4k
source_payload
last_synced_at
```

### 12.11 `tracker_policy`

```text
id
normalized_domain            unique
minimum_ratio
minimum_seed_seconds
combination                  RATIO_ONLY | TIME_ONLY | OR | AND | NEVER_REMOVE
grace_period_seconds
automatic_deletion_allowed
created_at
updated_at
```

### 12.12 `deletion_job`

```text
id
media_lifecycle_id
arr_instance_id
state
rollout_mode_at_creation
eligibility_snapshot
eligible_reason
blocked_reason_code
blocked_reason_text
scheduled_at
approved_at
approved_by
started_at
completed_at
arr_result
torrent_result
plex_result
seerr_result
error_code
error_detail_sanitized
correlation_id
created_at
updated_at
```

### 12.13 `event`

Append-only and immutable through the application API.

```text
id
occurred_at
recorded_at
source
event_type
entity_type
entity_id
actor_type
actor_id
correlation_id
idempotency_key
previous_values             JSONB
new_values                  JSONB
reason
source_payload              JSONB, sanitized
schema_version
```

Create indexes for time, type, entity, correlation, and idempotency lookups. Event payload schema changes require a version increment. Never update or delete an event to represent a correction; append a correction event.

### 12.14 `system_setting` and `bootstrap_run`

Store global rollout mode, configured retention values, synchronization cursors, and the one-time legacy cutover state. Secret values do not belong in `system_setting` unless encrypted.

## 13. Event-history requirements

Record, at minimum:

- integration configured, enabled, disabled, Active Management changed, failed, and recovered;
- full and incremental sync started/completed/failed;
- media discovered, imported, upgraded, missing, restored, and deleted;
- request discovered or changed;
- playback imported and meaningful-watch registered;
- retention deadline changed;
- protection added, removed, or changed;
- Arr tag observed, added, or removed;
- torrent discovered, mapped, tracker-updated, requirement-met, and removed;
- deletion evaluated, blocked, proposed, approved, started, completed, reconciled, or failed;
- Plex refresh requested and confirmed;
- Seerr reconciliation completed or failed;
- bootstrap previewed, started, completed, partially failed, or rolled forward;
- administrator setting or manual override changed.

Each multi-step workflow uses one correlation ID. Store raw source payloads only after a central redaction function removes known secret fields and URL credentials/passkeys. Add automated tests proving secrets cannot enter event JSON or logs.

Historical data is retained indefinitely by default. Future aggregate tables are caches; the event stream remains authoritative.

## 14. Retention policy

All durations are configurable. Defaults:

### 14.1 Meaningful playback

Playback is meaningful when any condition is true:

```text
duration played >= 10 minutes
OR completion >= 10 percent
OR Plex/Tautulli marks the item watched/scrobbled
```

Brief starts do not reset retention.

### 14.2 Movies

```text
Never watched:
    retention_until = first_imported_at + 16 weeks

Watched:
    retention_until = last_meaningful_watch_at + 8 weeks
```

Every meaningful playback by any user resets the watched timer.

### 14.3 Television

Retention is season-level. When an episode in Season N is meaningfully watched:

- reset Season N;
- reset every currently imported season greater than N;
- do not reset earlier seasons;
- do not pre-create/reset a future unimported season.

A newly imported future season receives its own initial 16-week timer.

### 14.4 Quality upgrades

Preserve:

- lifecycle ID;
- first import timestamp;
- last meaningful watch;
- retention deadline;
- protection state;
- legacy state;
- request history.

Update file revision, path, size, quality, and torrent association.

### 14.5 Policy precedence

Strongest result wins:

```text
Instance disabled/protected
    > manual permanent pin
    > retention-protected Arr tag
    > retention-legacy protection
    > protected requester policy
    > manual extension
    > library default retention
```

No weaker policy may shorten a stronger protection silently.

## 15. Legacy and protected bootstrap

Bootstrap is explicit, previewable, resumable, idempotent, and normally runs once per Arr instance.

### 15.1 Cutover

When an enabled managed instance is initialized, record:

```text
management_cutover_at
legacy_bootstrap_completed
legacy_bootstrap_cutoff
```

Content imported after the cutoff is not legacy.

### 15.2 Shared Radarr

- Create `retention-legacy` if missing.
- Apply it to every movie present at cutover.
- Mirror the legacy state internally.
- New movies after cutover use normal retention.

### 15.3 Sonarr

Because Sonarr tags are series-level while retention is season-level:

- mark every season present at cutover as legacy internally;
- add `retention-legacy` to every series containing at least one legacy season;
- retain the series tag until no season in the series is legacy;
- support releasing one season, one series, selected seasons, or all legacy seasons;
- disabling the Sonarr instance prevents bootstrap from starting or continuing.

### 15.4 Personal 4K Radarr

- Configure instance as `PROTECTED`.
- Create/apply `retention-protected` only through an explicitly confirmed bootstrap action.
- Inventory for history/analytics when enabled.
- Never produce executable deletion jobs.

### 15.5 Re-run behavior

Do not run bootstrap on container restart or upgrade. A manual re-run must first display an exact diff and require confirmation. Partial failures are resumed from recorded per-item results; do not duplicate completed work.

## 16. Tracker policy engine

Policies are configured by normalized tracker domain.

Supported combinations:

```text
RATIO_ONLY
TIME_ONLY
RATIO_OR_TIME
RATIO_AND_TIME
NEVER_REMOVE
```

Example:

```text
Domain:             tracker.example
Minimum ratio:      1.0
Minimum seed time:  10 days
Combination:        OR
Grace period:       12 hours
Auto deletion:      allowed
```

Unknown tracker default: block deletion and display an administrator warning.

Human-readable results are required:

```text
Blocked — ratio 0.61 / required 1.00
Blocked — seeded 6d 4h / required 10d
Eligible — seed-time requirement met
Blocked — no policy exists for tracker.example
Protected — tracker policy is NEVER_REMOVE
```

Use the torrent client’s actual statistics. Store evaluation inputs in the deletion eligibility snapshot.

## 17. Torrent-to-media and multi-season behavior

Retention applies to media lifecycles. Tracker obligations apply to torrents.

The mapping engine should consider Arr history download IDs, torrent hashes, file names, paths, sizes, and imported file relationships. Every mapping receives a method and confidence score. Low-confidence mappings require administrator confirmation.

For a multi-season torrent, support:

```text
REMOVE_ON_FIRST_ASSOCIATED_DELETION
    After an eligible season is deleted through Sonarr, remove the whole torrent
    and torrent-side data. Other imported seasons remain in the Sonarr/Plex
    library through their existing hard links or independent copies.

KEEP_UNTIL_ALL_ASSOCIATED_MEDIA_EXPIRE
    Keep the torrent until all mapped media lifecycles are deleted.
```

Default: `REMOVE_ON_FIRST_ASSOCIATED_DELETION`.

No inode, checksum, or hard-link-count validation is required in the deletion safety path. If Arr imported the file into the Plex-visible library, removing the torrent-side directory entry does not remove the Arr-side hard link; if Arr copied it, the library copy is independent.

Still require the applicable Arr deletion to succeed before removing torrent data.

## 18. Eligibility decision

An item is executable only when every applicable predicate passes:

```text
arr_instance.enabled
AND arr_instance.active_management_enabled
AND arr_instance.management_mode == MANAGED
AND torrent_client.enabled
AND torrent_client.active_management_enabled
AND managed Plex library is enabled
AND rollout mode permits the action
AND lifecycle status == ACTIVE
AND retention_until < now
AND no active Plex session uses the item
AND no permanent/manual/legacy/requester protection applies
AND torrent mapping is sufficiently confident
AND every applicable tracker policy permits removal
AND integration data is fresh
AND no conflicting deletion job exists
AND no relevant integration is unhealthy
```

The evaluator returns structured predicates and one primary readable explanation. Evaluation must be deterministic and testable with a supplied timestamp.

## 19. Deletion workflows

All workflows are idempotent state machines. Persist state before and after each external side effect so a restart can safely reconcile rather than repeat blindly.

### 19.1 Movie

```text
1. Acquire lifecycle/job lock.
2. Confirm the Radarr instance and torrent client are enabled, Active Management is enabled on both, and Radarr is managed.
3. Refresh Radarr, Tautulli, Plex-session, and torrent state.
4. Re-evaluate all eligibility predicates.
5. Persist immutable eligibility snapshot.
6. Delete movie and library files through shared Radarr.
7. Do not create an import exclusion.
8. Confirm Radarr deletion.
9. Remove mapped torrent and torrent-side data.
10. Trigger Plex library refresh.
11. Wait/poll within bounded limits for Plex absence.
12. Trigger or observe Seerr synchronization.
13. Verify the movie is requestable again.
14. Mark lifecycle deleted and job complete.
15. Preserve every historical record.
```

If step 6 fails, do not touch the torrent. If a later step fails, keep the job in a reconciliation/error state with exact completed steps.

### 19.2 One television season while others remain

```text
1. Acquire series/season/job locks.
2. Confirm the Sonarr instance and torrent client are enabled, Active Management is enabled on both, and Sonarr is managed.
3. Refresh Sonarr, Tautulli, Plex-session, and torrent state.
4. Re-evaluate eligibility.
5. Delete the season’s episode files through Sonarr.
6. Set that season unmonitored.
7. Confirm the season has no episode files.
8. Remove torrent according to multi-season cleanup mode.
9. Keep the Sonarr series while any other season files remain.
10. Refresh Plex and Seerr.
11. Verify the season is requestable again.
12. Mark the season lifecycle deleted.
```

### 19.3 Final season on disk

After confirming there are no other season files and no active import/download for the series:

- remove the series through Sonarr with files;
- do not create an import exclusion;
- remove associated torrents;
- refresh Plex and Seerr;
- verify re-requestability;
- retain all historical series and season records.

### 19.4 Mid-workflow disable

If an administrator disables an integration or switches off Active Management while a job is executing:

- finish only the currently in-flight external request if it cannot be cancelled safely;
- do not begin another destructive step;
- persist `PAUSED_INSTANCE_DISABLED`, `PAUSED_ACTIVE_MANAGEMENT_DISABLED`, or `RECONCILE_REQUIRED` as applicable;
- require administrator review before continuation.

## 20. Seerr re-request reconciliation

Deletion is incomplete until requestability is checked.

Expected end state:

```text
Media absent from managed Arr instance
Media absent from selected Plex library
Torrent removed when policy permits
Seerr availability updated
No stale fulfilled/approved request prevents a new request
Audit history preserved
```

If Seerr still treats deleted content as fulfilled or already requested:

1. Use a supported endpoint from the installed Seerr OpenAPI schema to clear or update stale state.
2. Trigger another synchronization.
3. Recheck requestability.
4. Set `RECONCILE_REQUIRED` if it remains blocked.

Never invent or hard-code unsupported Seerr database writes.

## 21. Admin interface

The UI should be functional and clear rather than graph-heavy in Phase 1.

### 21.1 Setup wizard

1. Create administrator.
2. Configure timezone.
3. Enter Seerr URL and key.
4. Discover Arr instances.
5. Assign a clear name, `enabled`, `active_management_enabled`, and `management_mode` to each applicable instance.
6. Enter missing Arr credentials manually if needed.
7. Configure Plex, Tautulli, and torrent client.
8. Select Plex libraries.
9. Test connections.
10. Preview bootstrap changes.
11. Start in `INVENTORY_ONLY`.

### 21.2 Dashboard

Show:

- global rollout mode;
- enabled/disabled and Active Management state for every applicable instance;
- integration health and data freshness;
- managed movies and seasons;
- legacy and protected counts;
- eligible and tracker-blocked counts;
- pending approvals;
- reconciliation failures;
- recent actions.

### 21.3 Integrations page

Each integration card shows:

- name and kind;
- enabled switch;
- Active Management switch for Arr and torrent-client instances;
- management mode for Arr instances;
- connection health;
- last successful sync;
- latest sanitized error;
- Test Connection;
- Manual Read-Only Preview;
- Sync Now when enabled;
- exact warning before disabling an integration or Active Management with queued jobs.

### 21.4 Media page

Filters:

```text
Instance
Movie / television
Active / deleted
Never watched / watched
Eligible
Legacy
Protected
Tracker blocked
Pending deletion
Error/reconciliation required
```

Each row/detail shows title, instance, import date, last meaningful watch, deadline, requester, protection, torrent mapping, tracker progress, decision explanation, and lifecycle history.

### 21.5 Legacy manager

Support preview and release actions for:

- selected movies;
- selected seasons;
- an entire series;
- imports before/after a chosen date;
- all items in one instance.

Every batch action displays exact affected counts and creates individual item events plus one correlation-level batch event.

### 21.6 Deletion queue

Display current inputs, all passed/failed predicates, estimated logical media size, mapped torrents, expected external operations, and approval history. Dry-run records must be visually distinct from executable jobs.

### 21.7 Audit log

Filter by time, event, entity, instance, actor, and correlation ID. Raw payload views are admin-only and already sanitized.

## 22. Scheduler and freshness

Suggested defaults:

```text
Health checks:                  every 5 minutes
Tautulli incremental sync:     every 15 minutes
Torrent state sync:            every 15 minutes
Arr inventory sync:            every hour
Seerr request sync:            every hour
Eligibility recalculation:     every 6 hours
Automatic execution window:    once nightly, configurable
Full reconciliation:           once daily
```

Only enabled integrations receive scheduled synchronization, except lightweight health checks for shared dependencies if explicitly configured. Disabled Arr instances do not receive scheduled work.

Define freshness thresholds per source. Deletion evaluation fails closed if required data is older than its threshold. Jobs use database locks and unique active-job constraints to prevent duplicate execution.

## 23. Job and lifecycle states

Use separate lifecycle, eligibility, and job states rather than overloading one field.

Suggested deletion-job states:

```text
PROPOSED_DRY_RUN
PENDING_APPROVAL
APPROVED
SCHEDULED
REVALIDATING
DELETING_FROM_ARR
REMOVING_TORRENT
REFRESHING_PLEX
RECONCILING_SEERR
COMPLETED
BLOCKED
PAUSED_INSTANCE_DISABLED
PAUSED_ACTIVE_MANAGEMENT_DISABLED
RECONCILE_REQUIRED
FAILED_RETRYABLE
FAILED_FINAL
CANCELLED
```

Every blocked/failed state has a stable machine-readable reason code and a human-readable message.

## 24. Build milestones and gates

### Milestone 0 — Repository and foundations

Build:

- application skeleton and module boundaries;
- configuration and secret handling;
- SQLite persistence layout and connection safety settings;
- SQLite schema and migrations;
- local admin authentication;
- append-only event writer;
- centralized payload redaction;
- Docker development environment;
- unit-test and integration-test harness.

Gate:

- migrations apply to an empty database;
- the SQLite database survives manager replacement and has a tested, integrity-checked online backup path;
- secrets are redacted in tests;
- event rows cannot be edited through application services;
- authentication and CSRF protection work.

### Milestone 1 — Setup and integration registry

Build:

- Seerr onboarding;
- Arr discovery and manual fallback;
- per-instance enable switch;
- per-instance Active Management switch for Arr and qBittorrent;
- per-instance management mode;
- Plex, Tautulli, and qBittorrent configuration;
- health checks;
- explicit Plex library selection.

Gate:

- all connections can be tested independently;
- disabling an instance stops scheduled jobs and blocks mutations;
- disabling Active Management preserves synchronization and dry-run proposals while blocking destructive calls;
- protected and ignored modes behave according to the matrix;
- credentials never return to the browser.

### Milestone 2 — Read-only inventory

Build:

- Radarr and Sonarr inventory;
- Tautulli historical/incremental import;
- Seerr request mapping;
- torrent inventory;
- media lifecycles and file revisions;
- media-to-torrent mapping;
- retention and protection calculation;
- inventory dashboard and media details.

Gate:

Every managed item shows a defensible:

- original import date;
- last meaningful watch;
- retention deadline;
- protection state;
- torrent and tracker mapping;
- source-data freshness;
- readable current decision.

No deletion adapter method is callable from a web or scheduler path.

### Milestone 3 — Legacy bootstrap

Build preview, idempotent execution, partial-failure recovery, and release tools.

Gate:

- existing shared Radarr movies receive legacy protection;
- existing Sonarr seasons are internally legacy and series tags mirror that state;
- personal Radarr is instance-protected and visibly tagged when approved;
- restart does not rerun bootstrap;
- disabling an instance prevents bootstrap writes.

### Milestone 4 — Dry-run engine

Build:

- tracker policy editor and evaluator;
- deterministic eligibility snapshots;
- movie/season proposals;
- TV forward-reset behavior;
- protected requester policies;
- multi-season mapping behavior;
- estimated space reporting;
- simulated reconciliation outcomes.

Gate:

- run for a meaningful observation period;
- compare proposed results against Tautulli, Arr, Plex, Seerr, and qBittorrent manually;
- no external mutation calls occur;
- every proposal is explainable from stored inputs.

### Milestone 5 — Manual approval execution

Build the persisted deletion state machines, retries, Plex refresh, and Seerr reconciliation.

Gate:

- administrator must approve each job;
- revalidation occurs immediately before deletion;
- disabling the instance blocks execution;
- test media completes the full workflow;
- partial failures resume or reconcile safely;
- all steps share a correlation ID.

### Milestone 6 — Automatic shared movies

Enable Active Management and automatic execution for the shared Radarr instance and qBittorrent only after explicit owner confirmation. Sonarr remains enabled for read-only inventory with Active Management disabled.

Gate:

- sustained manual-approval operation has no unexplained deletions;
- backups are working;
- health failures block deletion;
- alerts expose failed/reconciliation jobs.

### Milestone 7 — Television enablement

When the owner is ready:

1. Enable the Sonarr connector if it was completely disabled.
2. Run and verify a full read-only inventory with Active Management disabled.
3. Complete/verify legacy bootstrap.
4. Review television dry-run proposals.
5. Enable Sonarr Active Management and move to manual approval.
6. Enable automatic television only after explicit confirmation.

Gate:

- forward season resets are verified;
- earlier seasons can expire independently;
- final-series removal is verified;
- multi-season torrent cleanup is verified;
- season re-requestability is verified.

### Milestone 8 — Production hardening

Build:

- database backup/restore workflow;
- configuration export excluding secrets;
- failed-job notifications;
- retry/backoff policy;
- migration/upgrade documentation;
- Unraid template and deployment guide;
- operational runbook.

## 25. Required acceptance tests

### 25.1 Instance enablement

- A configured disabled integration permits connection testing but performs no scheduled inventory or history collection.
- A disabled instance cannot create, approve, or execute a deletion job.
- Disabling an instance cancels or blocks queued jobs.
- Re-enabling requires a fresh sync and does not revive previous approvals.
- An enabled managed Arr instance with Active Management disabled continues inventory, history, retention calculation, and dry-run proposals.
- Active Management disabled prevents Arr deletion, monitoring changes, movie/series removal, Seerr deletion reconciliation writes, and torrent removal.
- A qBittorrent instance with Active Management disabled continues torrent/tracker synchronization but cannot call any state-changing torrent-client operation.
- Disabling Active Management invalidates queued approvals and stops before the next destructive workflow step.
- Enabling Active Management requires a current full sync and fresh revalidation.
- Shared Radarr can actively manage movies while Sonarr remains enabled in read-only/dry-run mode.
- An enabled protected Radarr is inventoried but never produces an executable job.
- Enable and Active Management changes appear in immutable history.

### 25.2 Movies

- Never-watched movie expires after 16 weeks.
- Watched movie expires eight weeks after latest meaningful playback.
- Five-second playback does not reset retention.
- Meaningful replay resets retention.
- Quality upgrade does not reset lifecycle dates or protection.
- Legacy or protected movie is never queued.
- Unknown/incomplete tracker policy blocks deletion.
- Active Plex session blocks deletion.
- Arr deletion happens before torrent removal.
- Failed Arr deletion leaves the torrent untouched.
- Deleted movie becomes requestable again.
- Re-requested movie creates a new linked lifecycle.

### 25.3 Television

- Watching Season 3 resets Seasons 3 and all currently imported later seasons.
- Watching Season 3 does not reset Seasons 1–2.
- A future Season 7 receives a fresh initial timer.
- Expired season files are deleted and the season is unmonitored.
- Earlier season can expire while later seasons remain.
- Series remains while another season has files.
- Final season deletion removes the series without an import exclusion.
- Multi-season torrent follows the configured cleanup mode.
- Deleted season becomes requestable again.
- Releasing one legacy season preserves other legacy seasons and keeps/removes the series tag correctly.

### 25.4 Events and data

- Re-running a sync does not duplicate source events or playbacks.
- Every workflow step shares a correlation ID.
- Source payload redaction removes API keys, cookies, tokens, passwords, and URL passkeys.
- Deleted lifecycle, playback, request, torrent history, and events remain queryable.
- Aggregate metrics can be rebuilt from events.
- A correction appends an event rather than editing history.

### 25.5 Failure and concurrency

- Two schedulers cannot execute the same deletion.
- Stale source data blocks deletion.
- Integration failure during revalidation blocks deletion.
- Process restart at every workflow step resumes safely.
- Instance disable during a workflow stops before the next destructive step.
- Plex refresh timeout or Seerr mismatch results in `RECONCILE_REQUIRED`, not a false success.

## 26. Observability and operations

Provide structured logs with correlation IDs, but never raw secrets. Expose:

- application health;
- database readiness;
- enabled integration health;
- scheduler last-run/next-run;
- sync cursor and freshness;
- job counts by state;
- redacted recent errors.

Phase 1 need not ship rich graphs, but its event and normalized data must support future graphs for imports, deletions, bytes added/removed, watches, request-to-watch time, never-watched media, re-requests, tracker blocking, and lifecycle length.

## 27. Security requirements

- Bind to the configured interface; document reverse-proxy/TLS expectations.
- Use secure session cookies and CSRF protection.
- Rate-limit login attempts.
- Hash administrator passwords with Argon2id.
- Encrypt integration credentials with a key supplied outside the database.
- Never send saved credential values back to the browser.
- Redact secrets in HTTP traces, exception text, events, and logs.
- Use least-privilege API access where integrations support it.
- Require confirmation for bootstrap, rollout-mode escalation, batch legacy release, and destructive execution.
- Do not expose the torrent-client interface publicly.

## 28. Definition of Phase 1 complete

Phase 1 is complete when:

1. The application runs reproducibly as a single Docker container with SQLite persisted under `/config`.
2. All required integrations can be configured and health-checked.
3. Every integration has a reliable Enable switch; every Arr and torrent-client integration has an independent Active Management switch; every Arr instance has a management mode.
4. Shared movies can be actively managed while television continues complete read-only synchronization and dry-run evaluation.
5. Existing content is protected by the correct one-time bootstrap.
6. Inventory, playback, requests, torrent state, retention, and protections reconcile correctly.
7. Dry-run and manual approval have been observed successfully.
8. Automatic shared-movie deletion can operate safely.
9. Television can be enabled later through the documented rollout gates.
10. Deletions occur only through Arr followed by torrent-client cleanup.
11. Plex and Seerr converge to a requestable post-deletion state.
12. Complete sanitized history remains available for future analytics.
13. Required tests pass and backup/restore is documented and verified.

## 29. Implementation rules for Codex

- Read this document before changing behavior.
- Build one milestone at a time and report which gate is satisfied.
- Do not silently weaken a safety invariant to make a test pass.
- Keep external API shapes inside adapters.
- Use normalized domain objects in policy and workflow code.
- Use deterministic clocks in retention tests.
- Add contract fixtures for supported integration versions.
- Make all synchronization and workflow operations idempotent.
- Preserve user-owned configuration and database data across upgrades.
- Do not enable destructive modes in sample or production configuration.
- Default every newly discovered integration to `enabled=false` until the administrator explicitly enables it.
- Default `active_management_enabled=false` for every Arr and torrent-client integration.
- Never couple the Enable and Active Management switches: enabling a connector must not enable destructive behavior.
- Require a successful full sync and current dry-run evaluation before Active Management can be enabled.
- When uncertain, block the action, store the reason, and surface it in the UI.
