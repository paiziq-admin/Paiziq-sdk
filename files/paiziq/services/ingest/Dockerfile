# Paiziq ingest / control-plane service — production container image.
#
# The build context is the project root (files/paiziq), not this directory,
# because the service imports the local `paiziq` SDK package.
#
#   Build:  docker build -f services/ingest/Dockerfile -t paiziq-ingest .
#   Run:    docker run --rm -p 8800:8800 \
#             -e PAIZIQ_ENV=production \
#             -e PAIZIQ_INGEST_KEYS=<strong-bootstrap-key> \
#             -e PAIZIQ_CORS_ORIGINS=https://<dashboard-origin> \
#             -v paiziq-data:/data paiziq-ingest
#
# `make docker-build` and `make docker-smoke` wrap these commands.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Dependencies first so source edits do not invalidate the pip layer.
COPY sdk/pyproject.toml sdk/README.md sdk/
COPY sdk/src sdk/src
COPY services/ingest/requirements.txt services/ingest/requirements.txt
RUN pip3 install --no-cache-dir -r services/ingest/requirements.txt ./sdk

# The complete service: app, routers, stores, migrations, workers, helpers.
# Tests and caches are excluded by the root .dockerignore.
COPY services/ingest/ services/ingest/
RUN chmod +x services/ingest/entrypoint.sh

# Fixed UID/GID so a mounted volume (for example Azure Files with
# mountOptions uid=10001,gid=10001) is writable by the service user.
RUN groupadd --system --gid 10001 paiziq \
    && useradd --system --uid 10001 --gid 10001 --create-home paiziq \
    && mkdir -p /data \
    && chown -R paiziq:paiziq /data /srv

USER paiziq
WORKDIR /srv/services/ingest

# Persistent SQLite path; the entrypoint creates the parent directory.
ENV PAIZIQ_INGEST_DB=/data/paiziq.sqlite \
    PORT=8800

VOLUME ["/data"]
EXPOSE 8800

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import json,os,urllib.request;u=urllib.request.urlopen('http://127.0.0.1:%s/health'%os.environ.get('PORT','8800'),timeout=4);raise SystemExit(0 if json.load(u).get('status')=='ok' else 1)"

ENTRYPOINT ["/srv/services/ingest/entrypoint.sh"]
