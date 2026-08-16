#!/bin/sh
set -eu

alembic upgrade head
exec uvicorn app.main:app --host "${LISTEN_HOST:-0.0.0.0}" --port "${LISTEN_PORT:-8787}" --proxy-headers

