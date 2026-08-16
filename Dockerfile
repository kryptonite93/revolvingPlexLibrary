FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.source="https://github.com/kryptonite93/revolvingPlexLibrary" \
      org.opencontainers.image.description="Safety-first lifecycle management for Plex libraries"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_DIRECTORY=/config \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=8787 \
    APP_ENVIRONMENT=production \
    SECURE_COOKIES=true

WORKDIR /app

RUN groupadd --gid 1000 rpm \
    && useradd --uid 1000 --gid 1000 --create-home rpm \
    && mkdir -p /config /app \
    && chown -R rpm:rpm /config /app

COPY --chown=rpm:rpm pyproject.toml README.md alembic.ini ./
COPY --chown=rpm:rpm app ./app
COPY --chown=rpm:rpm migrations ./migrations
COPY --chown=rpm:rpm docker/entrypoint.sh /usr/local/bin/revolving-plex-entrypoint

RUN python -m pip install --no-cache-dir . \
    && chmod +x /usr/local/bin/revolving-plex-entrypoint

USER rpm
EXPOSE 8787
VOLUME ["/config"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3)" || exit 1

ENTRYPOINT ["/usr/local/bin/revolving-plex-entrypoint"]
