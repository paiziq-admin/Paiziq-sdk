# Paiziq Azure Deployment Guide

Deploy the Paiziq backend (ingest / control plane) to Azure, publish the
live-data dashboard, and run the Northstar demo so real SDK results appear in
the dashboard. Last updated 2026-10-03.

## 1. Scope and status

| Piece | State | Evidence |
| --- | --- | --- |
| Backend container package (`services/ingest/Dockerfile`, `entrypoint.sh`, `.dockerignore`) | Verified locally | `make docker-smoke` builds the image, starts it in production mode, and passes 5/5 smoke checks |
| Northstar demo runner (`scripts/northstar_demo.py`) | Verified locally | Run against the production-mode container: approved / needs_review / rejected, read-only key issued, CORS preflight allowed |
| Deployment lane tests (`tests/test_deploy_package.py`) | Passing | 14 tests under `make ingest-test` |
| Azure backend | Online | `https://paiziq-ingest-dev.whiteforest-4bca54b1.eastus2.azurecontainerapps.io`; East US 2, existing persistent volume |
| Dashboard publish from the `dev` branch | Documented, **not yet performed** | Needs a push to GitHub and a manual workflow dispatch |

Nothing in this guide seeds the static dashboard. The dashboard reads live data
from the backend; the demo creates that data through the SDK.

### Automatic main-branch CI

The repository-root `.github/workflows/ci.yml` is **SDK CI and Azure deployment**.
The workflows under `files/paiziq/.github/` are historical project templates;
GitHub only discovers workflows at the repository root.

Every push or merge to `main` runs `make check build` with Python 3.12. Ruff is
pinned to 0.4.10 in CI to match the validated lint rules. Pull requests run the
quality gate and build without accessing deployment credentials. Manual runs
deploy only when selected on `main`.

After quality passes, CI builds and smoke-tests the Linux container, pushes
`paiziqdevacr8406cce0.azurecr.io/paiziq-ingest:<full commit SHA>`, and runs
`make ci-deploy-azure`. This uses `deploy_existing_backend.sh` rather than the
full infrastructure bootstrap script: it retains existing secrets, registry
credentials, environment variables and the Azure Files mount. Old active
revisions are deactivated and must have zero replicas before the update.
Expect a short outage; SQLite remains a single-process development setup.
Hosted health, invalid/missing-key rejection and CORS smoke checks must pass
for a successful run. CI does not store a backend API key and does not perform
the positive authenticated login probe; the container smoke lane checks that
probe with an ephemeral local-only key.

GitHub repository configuration:

| Setting | Type | Value / purpose |
| --- | --- | --- |
| `AZURE_CLIENT_ID` | Variable | `58867675-6e0c-475f-a09b-34e93833900c` |
| `AZURE_TENANT_ID` | Variable | `4a384267-1d1e-4008-b8ba-10d00c3b5f71` |
| `AZURE_SUBSCRIPTION_ID` | Variable | `8406cce0-3a67-4d8e-b536-965b930989af` |
| `CI_NOTIFICATION_ISSUE` | Variable | Number of the CI results issue |
| `CI_NOTIFICATION_USERS` | Variable | Fallback collaborator usernames, space-separated |

The managed identity `paiziq-github-deploy` trusts only GitHub OIDC subject
`repo:paiziq-admin/Paiziq-sdk:ref:refs/heads/main`. No Azure client secret or
cached personal login is stored in GitHub. An Azure Owner / User Access
Administrator must grant these roles to principal
`171abb64-1aad-4bec-b59f-aa935eca4c1a` before backend CI can deploy:

```bash
az role assignment create \
  --assignee-object-id 171abb64-1aad-4bec-b59f-aa935eca4c1a \
  --assignee-principal-type ServicePrincipal --role Contributor \
  --scope /subscriptions/8406cce0-3a67-4d8e-b536-965b930989af/resourceGroups/paiziq-dev/providers/Microsoft.App/containerApps/paiziq-ingest-dev
az role assignment create \
  --assignee-object-id 171abb64-1aad-4bec-b59f-aa935eca4c1a \
  --assignee-principal-type ServicePrincipal --role AcrPush \
  --scope /subscriptions/8406cce0-3a67-4d8e-b536-965b930989af/resourceGroups/paiziq-dev/providers/Microsoft.ContainerRegistry/registries/paiziqdevacr8406cce0
```

Subscription Contributor cannot create role assignments. Until those grants
exist, backend quality/build checks run, but Azure deployment fails. After the
Owner grants access, rerun the failed workflow from Actions.

`notify-ci.yml` reports every completed CI run in a dedicated issue, including
success, failure and cancellation, and mentions contributors/collaborators.
Contributor discovery is live; if collaborator enumeration is denied, the
configured fallback list is used. Update that list when repository access
changes. GitHub email/inbox delivery remains subject to users' notification
preferences. The dashboard has equivalent main deployment and notifications;
its existing Static Web Apps deployment-token secret is retained.

## 2. Topology

```
Browser ── https ──> Azure Static Web App (paiziq-dashboard-dev)
   │                    serves the Vite build from the dashboard `dev` branch
   │
   └── https + Bearer key ──> Azure Container Apps: paiziq-ingest-dev (1 replica)
                                FastAPI + Uvicorn (1 worker) on :8800
                                SQLite at /data/paiziq.sqlite
                                   └── Azure Files share, mounted with nobrl
SDK (payment agent) ── https + Bearer key ──> same backend
```

Why these choices:

- The backend holds one SQLite connection guarded by a lock and runs a webhook
  worker thread in-process. It must run as **one process with one Uvicorn
  worker and one replica**. The entrypoint and the deploy script enforce this.
- Azure App Service's persistent `/home` is a CIFS mount that Microsoft
  documents as incompatible with SQLite locking. Azure Container Apps with an
  Azure Files volume mounted using `nobrl` avoids that failure mode.
- `az acr build` builds the image inside Azure, so no local Docker daemon is
  required to deploy (Docker is only needed for `make docker-smoke`).

## 3. Prerequisites

Tools on the operator machine:

- Azure CLI 2.60+ (`az`) with the `containerapp` extension (the script
  installs it if missing) — https://learn.microsoft.com/cli/azure/install-azure-cli
- `python3`, `bash`, `curl`, `git`
- Optional: Docker, for the local `make docker-smoke` rehearsal

Existing Azure facts (from the integration guide in `~/Downloads`):

| Item | Value |
| --- | --- |
| Subscription | `8406cce0-3a67-4d8e-b536-965b930989af` |
| Tenant | `4a384267-1d1e-4008-b8ba-10d00c3b5f71` |
| Resource group | `paiziq-dev` (Central US) |
| Static Web App | `paiziq-dashboard-dev` |
| Dashboard URL | `https://brave-river-0a6dd1310.5.azurestaticapps.net` |
| GitHub secret | `AZURE_STATIC_WEB_APPS_API_TOKEN` already configured on the dashboard repo |

Sign in once:

```bash
az login --tenant 4a384267-1d1e-4008-b8ba-10d00c3b5f71
az account set --subscription 8406cce0-3a67-4d8e-b536-965b930989af
```

Repository layout used below (run `make` commands from `files/paiziq`):

```
files/paiziq/
  Makefile                         docker-build, docker-smoke, smoke, northstar-demo, deploy-azure
  .dockerignore
  deploy/azure/deploy_backend.sh   idempotent Azure Container Apps deployment
  deploy/azure/backend.env.example configuration template
  services/ingest/Dockerfile       build context is files/paiziq
  services/ingest/entrypoint.sh    mkdir for the DB, exec uvicorn --workers 1
  services/ingest/scripts/smoke_backend.py
  services/ingest/scripts/northstar_demo.py
  services/ingest/tests/test_deploy_package.py
```

## 4. Step 1 — Rehearse the package locally

```bash
cd files/paiziq
make docker-smoke PAIZIQ_CORS_ORIGINS=https://brave-river-0a6dd1310.5.azurestaticapps.net
```

This builds `paiziq-ingest:local`, starts it with `PAIZIQ_ENV=production`, a
random bootstrap key, and the dashboard origin, then runs
`scripts/smoke_backend.py`. Expected output:

```
PASS  health                HTTP 200 {"status": "ok"}
PASS  login_probe           HTTP 200 success envelope; agents total=0
PASS  wrong_key_rejected    HTTP 403 for an unknown key
PASS  missing_key_rejected  HTTP 401 without Authorization
PASS  cors_preflight        origin https://brave-river-0a6dd1310.5.azurestaticapps.net allowed
```

`login_probe` is the exact request the dashboard login screen makes
(`GET /v1/agents?limit=1`), so a green smoke test means the dashboard will be
able to sign in to this backend.

Image facts: `python:3.12-slim`, non-root user `paiziq` (uid/gid 10001), SDK
installed from `sdk/`, `/data` volume, port 8800, Docker `HEALTHCHECK` on
`/health`. Build it alone with `make docker-build` (context is `files/paiziq`).

## 5. Step 2 — Deploy the backend to Azure Container Apps

### 5.1 Configure

```bash
cp deploy/azure/backend.env.example deploy/azure/backend.env   # keep out of git
```

Edit `deploy/azure/backend.env`:

| Variable | Set to |
| --- | --- |
| `AZ_ACR_NAME` | globally unique registry name, lowercase alphanumeric (e.g. `paiziqdevacr01`) |
| `AZ_STORAGE_ACCOUNT` | globally unique storage account, 3–24 lowercase alphanumeric |
| `PAIZIQ_INGEST_KEYS` | a strong bootstrap admin key: `python3 -c "import secrets; print('pzq_admin_' + secrets.token_urlsafe(32))"` |
| `PAIZIQ_CORS_ORIGINS` | `https://brave-river-0a6dd1310.5.azurestaticapps.net` (add a custom domain later, comma-separated, no trailing slash) |
| `PAIZIQ_SECRETS_KEY` | optional Fernet key so stored webhook secrets survive restarts |

The script refuses `dev-key` and keys shorter than 24 characters; the service
itself refuses to start in production with `dev-key` or an in-memory DB.

### 5.2 Run

```bash
set -a; source deploy/azure/backend.env; set +a
make deploy-azure
```

What `deploy/azure/deploy_backend.sh` does, in order (every step is
create-if-missing or update-in-place, so re-running is safe):

1. Registers the `Microsoft.App`, `Microsoft.OperationalInsights`,
   `Microsoft.ContainerRegistry`, `Microsoft.Storage` providers and ensures the
   resource group exists.
2. Creates the Basic-tier ACR and runs `az acr build` with
   `services/ingest/Dockerfile` and `files/paiziq` as context. The tag is the
   short git SHA unless `IMAGE_TAG` is set. `--skip-build` reuses a tag.
3. Creates a `Standard_LRS` storage account (TLS 1.2, no public blob access)
   and a 5 GiB file share, then links it to the Container Apps environment as
   `AzureFile` storage named `paiziq-data`.
4. Creates the Container Apps environment and the app with external HTTPS
   ingress on 8800, `min=max=1` replicas, 0.5 vCPU / 1 GiB, system-assigned
   identity pulling from ACR, and Container Apps **secrets** for the bootstrap
   key (`secretref:ingest-keys`) and the optional Fernet key. Environment:
   `PAIZIQ_ENV=production`, `PAIZIQ_INGEST_DB=/data/paiziq.sqlite`,
   `PAIZIQ_CORS_ORIGINS`, `PAIZIQ_LOG_LEVEL`, `PAIZIQ_RATE_LIMIT_RPM`.
5. Exports the app spec, adds the volume
   (`mountOptions: uid=10001,gid=10001,nobrl,mfsymlinks,cache=none`) and the
   `/data` mount, and applies it with `az containerapp update --yaml`.
6. Prints the FQDN, waits for `/health`, and runs the smoke test with the
   bootstrap key and the first CORS origin. `--no-smoke` skips that.

Record the output:

```
==> Backend URL: https://paiziq-ingest-dev.<hash>.centralus.azurecontainerapps.io
```

Keep the bootstrap key in your password manager. It is an admin credential and
should never be typed into a browser; the demo issues a separate read-only key
for the dashboard.

### 5.3 Verify independently

```bash
export PAIZIQ_ENDPOINT='https://<backend fqdn>'
export PAIZIQ_API_KEY='<bootstrap admin key>'
make smoke PAIZIQ_DASHBOARD_URL=https://brave-river-0a6dd1310.5.azurestaticapps.net
curl -s "$PAIZIQ_ENDPOINT/health"
```

Logs and revisions:

```bash
az containerapp logs show -n paiziq-ingest-dev -g paiziq-dev --follow
az containerapp revision list -n paiziq-ingest-dev -g paiziq-dev -o table
```

## 6. Step 3 — Publish the live-data dashboard

The dashboard repository (`Paiziq-Dashboard`) has two relevant branches:

- `dev` (`541aab9 New: API integration`): the live-data dashboard with the
  login screen (Backend URL + API key, kept in `sessionStorage`), the API
  client, and the fixture/service E2E lanes. **This is what must be published.**
- `origin/main`: contains only the Azure publishing files that `dev` lacks —
  `.github/workflows/deploy-dashboard.yml` (manual `workflow_dispatch`,
  Node 24, `npm ci && npm run build`, uploads `dist/` to the Static Web App
  with `production_branch: ${{ github.ref_name }}`) and
  `public/staticwebapp.config.json` (SPA fallback to `/index.html`).

No `VITE_*` build variables are needed: the backend URL is entered at login.

### 6.1 Bring the workflow into `dev`

```bash
cd ../Paiziq-Dashboard
git status                      # commit or stash the uncommitted E2E work first
git fetch origin
git checkout dev
git merge origin/main           # adds the workflow, SWA config, README section
npm ci && npm run check         # lint + unit tests, must be green
npm run build                   # confirms the Vite build
git push origin dev
```

If the merge reports conflicts they will be in `README.md` only; keep both
sections.

### 6.2 Dispatch the deployment

1. GitHub → `paiziq-admin/Paiziq-Dashboard` → **Actions** →
   **Deploy dashboard to Azure** → **Run workflow**.
2. Pick branch **`dev`** and run. The job builds `dist/` and uploads it with
   the stored `AZURE_STATIC_WEB_APPS_API_TOKEN`; it finishes in a few minutes.
3. The workflow uses `production_branch: ${{ github.ref_name }}`, so
   dispatching from `dev` publishes to the production slot of
   `paiziq-dashboard-dev`. Do not dispatch from `main`; that would publish the
   mock-only build.

Or from a machine with the GitHub CLI:

```bash
gh workflow run deploy-dashboard.yml --ref dev --repo paiziq-admin/Paiziq-Dashboard
gh run watch --repo paiziq-admin/Paiziq-Dashboard
```

### 6.3 Verify the publish

- Open `https://brave-river-0a6dd1310.5.azurestaticapps.net/login`. The
  "Sign in to Paiziq" form with **Backend URL** and **API key** fields must
  appear; the mock-only build has no login route.
- Deep links such as `/payments` must load directly (SPA fallback).
- `curl -sI https://brave-river-0a6dd1310.5.azurestaticapps.net/login` returns
  `200` with `content-type: text/html`.

### 6.4 CORS

The backend only answers browsers whose `Origin` is in `PAIZIQ_CORS_ORIGINS`.
The deploy script sets it from `backend.env`; the smoke test's `cors_preflight`
row and the demo's "CORS preflight" line both confirm it. To add a custom
domain later:

```bash
az containerapp update -n paiziq-ingest-dev -g paiziq-dev \
  --set-env-vars PAIZIQ_CORS_ORIGINS="https://brave-river-0a6dd1310.5.azurestaticapps.net,https://dashboard.example.com"
```

## 7. Step 4 — Run the Northstar demo

"Northstar" is not defined anywhere in either repository. This guide uses the
deterministic payment-agent scenario shipped with the E2E suite
(`services/ingest/tests/e2e_support/scenario.py`), driven by the real
`paiziq` SDK through `SimulatedPaymentAgent`. If a different demo is meant,
swap the scenario module; the runner, key issuance, and report are unchanged.

| Transaction | Merchant | Amount | Expected verdict | Expected state |
| --- | --- | --- | --- | --- |
| t1 | acme corp | 49.99 USD | approved | executed |
| t2 | cloudhost inc | 180.00 USD | needs_review | needs_review (opens a human review) |
| t3 | shady llc | 20.00 USD | rejected | rejected (blocklisted merchant) |

### 7.1 Run it against the deployed backend

```bash
cd files/paiziq
export PAIZIQ_ENDPOINT='https://<backend fqdn>'
export PAIZIQ_API_KEY='<bootstrap admin key>'
make northstar-demo PAIZIQ_DASHBOARD_URL=https://brave-river-0a6dd1310.5.azurestaticapps.net
```

The runner:

1. Checks `/health` and refuses to continue if the endpoint answers with HTML
   (a Static Web App URL pasted by mistake) or anything but `{"status":"ok"}`.
2. Creates a fresh organization (`e2e-org-<suffix>`), a `sandbox` environment
   named `e2e-sandbox`, the agent `e2e-payment-agent`, and publishes
   `e2e-threshold-policy` v1 through the SDK and control-plane API.
3. Runs the three payments through the SDK; the SDK's local verdicts are
   compared with the control plane's persisted decisions.
4. Issues a **read-only** managed key (`scope=read`, `role=read_only`) scoped
   to the new environment, verifies it with the dashboard's login probe, and
   prints it once. Pass `--no-dashboard-key` to skip this.
5. Sends a CORS preflight from the dashboard origin and reports the result.
6. Writes `.e2e/northstar-run.json` (ids, verdicts, payment ids, CORS result;
   never any secret).

Example output:

```
Northstar demo complete
  backend:      https://paiziq-ingest-dev.<hash>.centralus.azurecontainerapps.io
  organization: e2e-org-255a9982  (org_...)
  environment:  e2e-sandbox / sandbox  (env_...)
  agent:        e2e-payment-agent  (agt_...)
  policy:       e2e-threshold-policy v1

  merchant        amount  verdict      state        payment
  acme corp        49.99  approved     executed     pay_...
  cloudhost inc   180.00  needs_review needs_review pay_...
  shady llc        20.00  rejected     rejected     pay_...

  CORS preflight from https://brave-river-0a6dd1310.5.azurestaticapps.net: allowed

Next steps
  1. Open https://brave-river-0a6dd1310.5.azurestaticapps.net/login
  2. Backend URL: https://...
  3. API key (read-only, shown once): pzq_sandbox_...
  ...
```

### 7.2 See the results in the dashboard

1. Open `<dashboard>/login`, paste the Backend URL and the read-only key, and
   click **Connect dashboard**. The dashboard verifies the key with
   `GET /v1/agents?limit=1` and stores both values in `sessionStorage` for the
   tab.
2. The first organization is selected automatically; choose
   `e2e-org-<suffix>` and the `e2e-sandbox` environment in the environment
   selector (also under Settings). Keep the time range at **Last 24 hours**.
3. **Payments** (Live Payment Feed): three rows — acme corp approved/executed,
   cloudhost inc needs_review, shady llc rejected. Open the cloudhost inc
   payment for the decision reasons and threshold flag.
4. **Reviews** (Human Review Queue): one open review for the 180.00 USD
   payment. Approving or rejecting it there requires a key with the
   `reviewer` role; the read-only key can only look.
5. **Agents**, **Policies**, **Audit**: the demo agent, `e2e-threshold-policy`
   v1, and the audit entries written by the run.

Each run creates a new organization, so repeated demos stay isolated; pick the
newest `e2e-org-*` when several exist.

## 8. Operate

### Ship a new backend version

```bash
set -a; source deploy/azure/backend.env; set +a
make deploy-azure                # builds :<git sha>, updates the app, re-runs smoke
```

Container Apps creates a new revision; the old one is deactivated once the
new one is healthy. During that overlap two processes may briefly share the
SQLite file — accepted for the development tier because `nobrl` plus SQLite's
own locking serialises writers, but avoid deploying while the demo is writing.

### Rotate the bootstrap key

```bash
NEW_KEY="$(python3 -c "import secrets; print('pzq_admin_' + secrets.token_urlsafe(32))")"
az containerapp secret set -n paiziq-ingest-dev -g paiziq-dev --secrets ingest-keys="$NEW_KEY"
az containerapp revision restart -n paiziq-ingest-dev -g paiziq-dev \
  --revision "$(az containerapp revision list -n paiziq-ingest-dev -g paiziq-dev --query '[0].name' -o tsv)"
```

Managed keys (`POST /v1/api-keys`) are stored hashed in SQLite and are not
affected. Revoke a dashboard key with `DELETE /v1/api-keys/{id}` using an admin
key.

### Back up and restore the database

```bash
az storage file download --account-name "$AZ_STORAGE_ACCOUNT" --share-name paiziq-ingest-data \
  --path paiziq.sqlite --dest ./paiziq-backup-$(date -u +%Y%m%dT%H%M%SZ).sqlite
```

Restore by scaling to zero, uploading the file with `az storage file upload`,
and scaling back to one replica. Snapshots of the share
(`az storage share snapshot`) are a cheaper daily option.

### Roll back

```bash
az containerapp revision list -n paiziq-ingest-dev -g paiziq-dev -o table
az containerapp revision activate -n paiziq-ingest-dev -g paiziq-dev --revision <previous>
az containerapp ingress traffic set -n paiziq-ingest-dev -g paiziq-dev --revision-weight <previous>=100
```

Or `IMAGE_TAG=<previous sha> ./deploy/azure/deploy_backend.sh --skip-build`.

## 9. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `health` smoke check reports HTML / "static site" | `PAIZIQ_ENDPOINT` points at the dashboard, not the backend | use the Container Apps FQDN |
| `login_probe` is `403` | wrong or revoked key | re-check `PAIZIQ_API_KEY`; the smoke test never prints it |
| `cors_preflight` fails / dashboard shows "Live data is unavailable" | origin missing from `PAIZIQ_CORS_ORIGINS` | add the exact origin (scheme + host, no path, no trailing slash) and redeploy |
| Container restarts with `PAIZIQ_INGEST_KEYS must not use dev-key` or `:memory:` | production guard tripped | set a real key / `PAIZIQ_INGEST_DB=/data/paiziq.sqlite` |
| `sqlite3.OperationalError: database is locked` or `disk I/O error` | share mounted without `nobrl`, or more than one replica | re-run the deploy script (it re-applies mount options and `min=max=1`) |
| `az acr build` fails with `Dockerfile not found` | wrong build context | the context must be `files/paiziq`, the file `services/ingest/Dockerfile` |
| Dashboard deep links return 404 | SWA config missing from the published branch | merge `origin/main` into `dev` (adds `public/staticwebapp.config.json`) and redeploy |
| Northstar demo: `DemoError: health check ...` | backend unreachable or still starting | wait for `/health`, check `az containerapp logs show` |
| Demo rows missing in the dashboard | different org/env or time range selected | pick the newest `e2e-org-*`, `e2e-sandbox`, Last 24 hours |

## 10. Assumptions and caveats

- **Northstar demo** = the deterministic three-payment scenario from the E2E
  suite run by the real SDK (see §7). Confirm or replace the scenario.
- The backend is deployed. Automatic redeployment uses the existing-app CI
  lane above; the original bootstrap script is retained for infrastructure
  setup and should be watched when used for a new environment.
- The dashboard merge/dispatch (§6) is documented, not performed; it touches a
  GitHub repository and should be done by an operator with push rights.
- SQLite on Azure Files is a development-tier arrangement: single replica, no
  horizontal scaling, and a short dual-process window during revision swaps.
  The Phase 1 tracker still lists the Terraform/RDS path for production.
- `POST /v1/orgs` currently accepts any valid key (not admin-gated). The
  read-only dashboard key therefore can create organizations; it cannot mint
  keys, publish policies, or act on reviews. Tightening org creation is a
  follow-up, not part of this change.
- Costs: Basic ACR, Standard_LRS storage, a Consumption-plan Container App with
  `min=1` (always on, roughly a few tens of USD per month), and the existing
  Static Web App.
