#!/bin/sh
# Container entrypoint for the Paiziq ingest service.
#
# - Creates the parent directory of PAIZIQ_INGEST_DB (the app does not).
# - Runs exactly one Uvicorn worker: the SQLite control-plane stores and the
#   background webhook worker are single-process by design.
# - Honours PORT so platforms that inject it (Container Apps, Cloud Run) work.
# - Trusts proxy headers so request.client reflects the real caller behind
#   the platform ingress (used for rate limiting of unauthenticated calls).
set -eu

: "${PAIZIQ_INGEST_DB:=/data/paiziq.sqlite}"
: "${PORT:=8800}"
export PAIZIQ_INGEST_DB PORT

case "$PAIZIQ_INGEST_DB" in
  :memory:) ;;
  *) mkdir -p "$(dirname "$PAIZIQ_INGEST_DB")" ;;
esac

cd "$(dirname "$0")"

exec python3 -m uvicorn app:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  "$@"
