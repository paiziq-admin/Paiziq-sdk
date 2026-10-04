#!/usr/bin/env bash
# Deploy the Paiziq ingest / control-plane backend to Azure Container Apps.
#
# Topology: Azure Container Registry (image built in the cloud with
# `az acr build`, no local Docker needed) -> Container Apps environment ->
# one external HTTPS app with exactly one replica -> Azure Files share
# mounted at /data with `nobrl` so single-process SQLite works.
#
# Idempotent: every step is create-if-missing or update-in-place, so re-run
# it to ship a new image or change settings. Configuration comes from the
# environment; see backend.env.example.
#
#   set -a; source deploy/azure/backend.env; set +a
#   ./deploy/azure/deploy_backend.sh            # or: make deploy-azure
#
# Flags: --skip-build   reuse IMAGE_TAG already in the registry
#        --no-smoke     do not run scripts/smoke_backend.py afterwards
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SKIP_BUILD=0
RUN_SMOKE=1
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --no-smoke) RUN_SMOKE=0 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

# --- configuration ---------------------------------------------------------
: "${AZ_RESOURCE_GROUP:=paiziq-dev}"
: "${AZ_LOCATION:=centralus}"
: "${AZ_ACA_ENV:=paiziq-dev-env}"
: "${AZ_APP_NAME:=paiziq-ingest-dev}"
: "${AZ_FILE_SHARE:=paiziq-ingest-data}"
: "${AZ_STORAGE_LINK:=paiziq-data}"
: "${PAIZIQ_LOG_LEVEL:=INFO}"
: "${PAIZIQ_RATE_LIMIT_RPM:=240}"
: "${PAIZIQ_CORS_ORIGINS:=}"
: "${PAIZIQ_SECRETS_KEY:=}"
: "${PAIZIQ_REVIEW_SLA_MS:=}"
IMAGE_REPO="paiziq-ingest"
if [ -z "${IMAGE_TAG:-}" ]; then
  IMAGE_TAG="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%d%H%M%S)"
fi

fail() { echo "error: $*" >&2; exit 1; }
log()  { printf '\n==> %s\n' "$*"; }

[ -n "${AZ_ACR_NAME:-}" ] || fail "AZ_ACR_NAME is required (globally unique, lowercase alphanumeric)"
[ -n "${AZ_STORAGE_ACCOUNT:-}" ] || fail "AZ_STORAGE_ACCOUNT is required (globally unique, lowercase alphanumeric)"
[ -n "${PAIZIQ_INGEST_KEYS:-}" ] || fail "PAIZIQ_INGEST_KEYS is required (strong bootstrap admin key)"
case ",${PAIZIQ_INGEST_KEYS}," in
  *,dev-key,*) fail "PAIZIQ_INGEST_KEYS must not contain dev-key; production mode rejects it" ;;
esac
FIRST_KEY="${PAIZIQ_INGEST_KEYS%%,*}"
[ "${#FIRST_KEY}" -ge 24 ] || fail "the first PAIZIQ_INGEST_KEYS entry must be at least 24 characters"
FIRST_ORIGIN="${PAIZIQ_CORS_ORIGINS%%,*}"

command -v az >/dev/null 2>&1 || fail "Azure CLI (az) is not installed: https://learn.microsoft.com/cli/azure/install-azure-cli"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
az account show >/dev/null 2>&1 || fail "not logged in; run: az login --tenant 4a384267-1d1e-4008-b8ba-10d00c3b5f71"
if [ -n "${AZ_SUBSCRIPTION:-}" ]; then
  az account set --subscription "$AZ_SUBSCRIPTION"
fi
if ! az containerapp --help >/dev/null 2>&1; then
  log "Installing the containerapp CLI extension"
  az extension add --name containerapp --upgrade --only-show-errors
fi

log "Registering resource providers (no-op when already registered)"
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry Microsoft.Storage; do
  az provider register --namespace "$ns" --only-show-errors >/dev/null
done

log "Resource group ${AZ_RESOURCE_GROUP} in ${AZ_LOCATION}"
az group create --name "$AZ_RESOURCE_GROUP" --location "$AZ_LOCATION" --only-show-errors >/dev/null

# --- container registry and image -----------------------------------------
log "Container registry ${AZ_ACR_NAME}"
if ! az acr show --name "$AZ_ACR_NAME" --resource-group "$AZ_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
  az acr create --name "$AZ_ACR_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
    --location "$AZ_LOCATION" --sku Basic --only-show-errors >/dev/null
fi
ACR_SERVER="$(az acr show --name "$AZ_ACR_NAME" --resource-group "$AZ_RESOURCE_GROUP" --query loginServer -o tsv)"
IMAGE="${ACR_SERVER}/${IMAGE_REPO}:${IMAGE_TAG}"

if [ "$SKIP_BUILD" -eq 0 ]; then
  log "Building ${IMAGE} in Azure (context: ${PROJECT_ROOT})"
  az acr build --registry "$AZ_ACR_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
    --image "${IMAGE_REPO}:${IMAGE_TAG}" \
    --file "${PROJECT_ROOT}/services/ingest/Dockerfile" "$PROJECT_ROOT"
else
  log "Skipping build; using ${IMAGE}"
fi

# --- persistent storage ----------------------------------------------------
log "Storage account ${AZ_STORAGE_ACCOUNT} and file share ${AZ_FILE_SHARE}"
if ! az storage account show --name "$AZ_STORAGE_ACCOUNT" --resource-group "$AZ_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
  az storage account create --name "$AZ_STORAGE_ACCOUNT" --resource-group "$AZ_RESOURCE_GROUP" \
    --location "$AZ_LOCATION" --sku Standard_LRS --kind StorageV2 \
    --min-tls-version TLS1_2 --allow-blob-public-access false --only-show-errors >/dev/null
fi
STORAGE_KEY="$(az storage account keys list --account-name "$AZ_STORAGE_ACCOUNT" \
  --resource-group "$AZ_RESOURCE_GROUP" --query '[0].value' -o tsv)"
if ! az storage share-rm show --storage-account "$AZ_STORAGE_ACCOUNT" --resource-group "$AZ_RESOURCE_GROUP" \
    --name "$AZ_FILE_SHARE" --only-show-errors >/dev/null 2>&1; then
  az storage share-rm create --storage-account "$AZ_STORAGE_ACCOUNT" --resource-group "$AZ_RESOURCE_GROUP" \
    --name "$AZ_FILE_SHARE" --quota 5 --only-show-errors >/dev/null
fi

# --- container apps environment -------------------------------------------
log "Container Apps environment ${AZ_ACA_ENV}"
if ! az containerapp env show --name "$AZ_ACA_ENV" --resource-group "$AZ_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
  az containerapp env create --name "$AZ_ACA_ENV" --resource-group "$AZ_RESOURCE_GROUP" \
    --location "$AZ_LOCATION" --only-show-errors >/dev/null
fi
az containerapp env storage set --name "$AZ_ACA_ENV" --resource-group "$AZ_RESOURCE_GROUP" \
  --storage-name "$AZ_STORAGE_LINK" --storage-type AzureFile \
  --azure-file-account-name "$AZ_STORAGE_ACCOUNT" --azure-file-account-key "$STORAGE_KEY" \
  --azure-file-share-name "$AZ_FILE_SHARE" --access-mode ReadWrite --only-show-errors >/dev/null

# --- the app ---------------------------------------------------------------
SECRET_ARGS=(ingest-keys="$PAIZIQ_INGEST_KEYS")
ENV_ARGS=(
  PAIZIQ_ENV=production
  PAIZIQ_INGEST_KEYS=secretref:ingest-keys
  PAIZIQ_INGEST_DB=/data/paiziq.sqlite
  PAIZIQ_LOG_LEVEL="$PAIZIQ_LOG_LEVEL"
  PAIZIQ_RATE_LIMIT_RPM="$PAIZIQ_RATE_LIMIT_RPM"
  PORT=8800
)
[ -n "$PAIZIQ_CORS_ORIGINS" ] && ENV_ARGS+=(PAIZIQ_CORS_ORIGINS="$PAIZIQ_CORS_ORIGINS")
[ -n "$PAIZIQ_REVIEW_SLA_MS" ] && ENV_ARGS+=(PAIZIQ_REVIEW_SLA_MS="$PAIZIQ_REVIEW_SLA_MS")
if [ -n "$PAIZIQ_SECRETS_KEY" ]; then
  SECRET_ARGS+=(secrets-key="$PAIZIQ_SECRETS_KEY")
  ENV_ARGS+=(PAIZIQ_SECRETS_KEY=secretref:secrets-key)
fi

log "Container app ${AZ_APP_NAME}"
if ! az containerapp show --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
  az containerapp create --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
    --environment "$AZ_ACA_ENV" --image "$IMAGE" \
    --registry-server "$ACR_SERVER" --registry-identity system \
    --ingress external --target-port 8800 --transport auto \
    --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1.0Gi \
    --revision-suffix "r${IMAGE_TAG//[^a-z0-9]/}" \
    --secrets "${SECRET_ARGS[@]}" --env-vars "${ENV_ARGS[@]}" --only-show-errors >/dev/null
else
  az containerapp secret set --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
    --secrets "${SECRET_ARGS[@]}" --only-show-errors >/dev/null
  az containerapp update --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
    --image "$IMAGE" --min-replicas 1 --max-replicas 1 \
    --set-env-vars "${ENV_ARGS[@]}" --only-show-errors >/dev/null
fi

# --- mount the file share at /data with SQLite-safe options ---------------
# mountOptions is only settable through the full app spec, so export the
# current spec, patch it, and apply it. JSON is valid YAML for `--yaml`.
log "Mounting ${AZ_STORAGE_LINK} at /data (nobrl, uid/gid 10001)"
SPEC_FILE="$(mktemp -t paiziq-aca.XXXXXX.json)"
trap 'rm -f "$SPEC_FILE"' EXIT
az containerapp show --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" -o json > "$SPEC_FILE"
python3 - "$SPEC_FILE" "$AZ_STORAGE_LINK" <<'PY'
import json, sys
path, storage_link = sys.argv[1], sys.argv[2]
spec = json.load(open(path))
template = spec["properties"]["template"]
volume = {
    "name": "data",
    "storageType": "AzureFile",
    "storageName": storage_link,
    "mountOptions": "uid=10001,gid=10001,nobrl,mfsymlinks,cache=none",
}
template["volumes"] = [v for v in (template.get("volumes") or []) if v.get("name") != "data"] + [volume]
for container in template["containers"]:
    mounts = [m for m in (container.get("volumeMounts") or []) if m.get("volumeName") != "data"]
    mounts.append({"volumeName": "data", "mountPath": "/data"})
    container["volumeMounts"] = mounts
template["scale"] = {**(template.get("scale") or {}), "minReplicas": 1, "maxReplicas": 1}
json.dump(spec, open(path, "w"), indent=2)
PY
az containerapp update --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
  --yaml "$SPEC_FILE" --only-show-errors >/dev/null

# --- result ----------------------------------------------------------------
FQDN="$(az containerapp show --name "$AZ_APP_NAME" --resource-group "$AZ_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn -o tsv)"
BACKEND_URL="https://${FQDN}"
log "Backend URL: ${BACKEND_URL}"
echo "    image:   ${IMAGE}"
echo "    data:    ${AZ_STORAGE_ACCOUNT}/${AZ_FILE_SHARE} -> /data/paiziq.sqlite"
echo "    cors:    ${PAIZIQ_CORS_ORIGINS:-<none>}"
echo "    logs:    az containerapp logs show -n ${AZ_APP_NAME} -g ${AZ_RESOURCE_GROUP} --follow"

if [ "$RUN_SMOKE" -eq 1 ]; then
  log "Waiting for the new revision to answer /health"
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 5 "${BACKEND_URL}/health" >/dev/null 2>&1; then break; fi
    sleep 5
  done
  log "Smoke test"
  SMOKE_ARGS=(--endpoint "$BACKEND_URL")
  [ -n "$FIRST_ORIGIN" ] && SMOKE_ARGS+=(--origin "$FIRST_ORIGIN")
  PAIZIQ_API_KEY="$FIRST_KEY" python3 "${PROJECT_ROOT}/services/ingest/scripts/smoke_backend.py" "${SMOKE_ARGS[@]}"
fi

cat <<EOF

Next:
  export PAIZIQ_ENDPOINT='${BACKEND_URL}'
  export PAIZIQ_API_KEY='<the bootstrap admin key>'
  make northstar-demo PAIZIQ_DASHBOARD_URL='${FIRST_ORIGIN:-https://<dashboard>}'
EOF
