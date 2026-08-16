# Revolving Plex Manager

Revolving Plex Manager is a safety-first control layer for a revolving Plex library. Phase 1 is defined by [PHASE-1-BUILD-SHEET.md](PHASE-1-BUILD-SHEET.md) and is being implemented one gated milestone at a time.

## Safety posture

- No media paths are mounted into the application container.
- New integrations default to disabled.
- Active Management defaults to disabled.
- Unknown, stale, or ambiguous state blocks deletion.
- No integration mutation adapters exist in Milestone 0.

## Local development

Python 3.12 or newer is required.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
export CONFIG_DIRECTORY="$PWD/config"
export APP_ENVIRONMENT="development"
export SECURE_COOKIES="false"
alembic upgrade head
uvicorn app.main:app --reload
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` and set environment variables with `$env:NAME = "value"`.

The application generates and persists a signing secret in the configuration directory when `APP_SECRET` is not supplied. Production mounts `/config` to local or cache-backed Unraid appdata.

## Database backup

```bash
revolving-plex backup
```

The command uses SQLite's online backup API, runs `PRAGMA integrity_check` on the result, and only then publishes the dated backup under `/config/backups`.

## Docker

```bash
docker build -t revolving-plex-manager .
docker run --rm -p 8787:8787 -v /path/to/appdata:/config revolving-plex-manager
```

The production deployment target is the Unraid template in `unraid/`.

## Unraid

The public template is hosted at:

```text
https://raw.githubusercontent.com/kryptonite93/revolvingPlexLibrary/main/unraid/revolving-plex-manager.xml
```

It pulls `ghcr.io/kryptonite93/revolving-plex-manager:latest` and exposes the administrative Web UI on port `8787`. The image is published automatically from the `main` branch.
