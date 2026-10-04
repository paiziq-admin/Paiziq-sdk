#!/usr/bin/env bash
# CI deployment to the existing app. Retains all secrets, settings and volumes.
# The Azure Files SQLite database must have only one process writing it.
set -euo pipefail
: "${AZ_RESOURCE_GROUP:?AZ_RESOURCE_GROUP is required}"
: "${AZ_APP_NAME:?AZ_APP_NAME is required}"
: "${AZ_IMAGE:?AZ_IMAGE is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"
: "${GITHUB_RUN_ATTEMPT:?GITHUB_RUN_ATTEMPT is required}"

az containerapp show --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
  --only-show-errors --output none
revisions="$(az containerapp revision list --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
  --query '[?properties.active].name' --output tsv --only-show-errors)"
while IFS= read -r revision; do
  [ -z "$revision" ] && continue
  az containerapp revision deactivate --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
    --revision "$revision" --only-show-errors --output none
  replicas=1
  for _ in $(seq 1 60); do
    replicas="$(az containerapp replica list --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
      --revision "$revision" --query 'length(@)' --output tsv --only-show-errors)"
    [ "$replicas" = 0 ] && break
    sleep 2
  done
  if [ "$replicas" != 0 ]; then
    echo 'Old revision still has replicas; refusing concurrent SQLite writers.' >&2
    exit 1
  fi
done <<< "$revisions"

az containerapp update --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
  --image "$AZ_IMAGE" --revision-suffix "ci-${GITHUB_SHA:0:12}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}" \
  --min-replicas 1 --max-replicas 1 --only-show-errors --output none

# Default hostnames change when Terraform recreates the app. Discover the
# deployed endpoints rather than checking a hostname belonging to a deleted app.
fqdn="$(az containerapp show --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv --only-show-errors)"
if [ -z "$fqdn" ] || [ "$fqdn" = null ]; then
  echo 'Deployed backend has no public hostname; cannot run hosted smoke checks.' >&2
  exit 1
fi
origins="$(az containerapp show --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
  --query "properties.template.containers[0].env[?name=='PAIZIQ_CORS_ORIGINS'].value | [0]" \
  --output tsv --only-show-errors)"
export PAIZIQ_ENDPOINT="https://$fqdn"
export PAIZIQ_DASHBOARD_URL="${origins%%,*}"

for _ in $(seq 1 60); do
  if make ci-smoke; then
    exit 0
  fi
  sleep 5
done
echo 'Hosted backend smoke checks did not pass before the deadline.' >&2
exit 1
