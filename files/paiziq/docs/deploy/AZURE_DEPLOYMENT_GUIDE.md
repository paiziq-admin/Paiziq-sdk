# Paiziq Azure Deployment Guide

Deploy the Paiziq backend (ingest / control plane) to Azure, publish the
live-data dashboard, and run the Northstar demo so real SDK results appear in
the dashboard. Last updated 2026-10-04.

## 1. Scope and status

| Piece | State | Evidence |
| --- | --- | --- |
| Backend container package (`services/ingest/Dockerfile`, `entrypoint.sh`, `.dockerignore`) | Verified locally | `make docker-smoke` builds the image, starts it in production mode, and passes 5/5 smoke checks |
| Northstar demo runner (`scripts/northstar_demo.py`) | Completed against Azure on 2026-10-03 | Three persisted payments, matching decisions and SDK traces; one open review; demo read-only key verified; dashboard CORS allowed |
| Deployment lane tests (`tests/test_deploy_package.py`) | Passing | 19 tests under `make ingest-test` |
| Azure deploy script (`deploy/azure/deploy_backend.sh`) | Deployed on 2026-10-03 | Healthy East US 2 Container App; hosted smoke 5/5; trace, agent, SDK key and read key survive a revision restart |
| Dashboard main-branch deployment | Verified on 2026-10-03 | Automatic quality/E2E/deployment and contributor notification passed |

Nothing in this guide seeds the static dashboard. The dashboard reads live data
from the backend; the demo creates that data through the SDK.

### Completed Azure rollout — 2026-10-03

The initial rollout was verified on 2026-10-03. On 2026-10-04 the subscription
API reports `Warned`, while Container Apps returns `ManagedClusterSuspended`
and says the subscription is disabled. The app reports `Failed`, and its
health endpoint times out. The subscription Owner must resolve the warning
and restore the suspended compute; hosted deployment cannot currently be
verified. See [Azure subscription states](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/subscription-states).
API base URL:
`https://paiziq-ingest-dev.whiteforest-4bca54b1.eastus2.azurecontainerapps.io`.
[API documentation](https://paiziq-ingest-dev.whiteforest-4bca54b1.eastus2.azurecontainerapps.io/docs)
and [health check](https://paiziq-ingest-dev.whiteforest-4bca54b1.eastus2.azurecontainerapps.io/health).
Deployment from SDK `Dev` commit `2f4119a` reached these stages:

| Item | Actual state |
| --- | --- |
| Local validation | `make check`: 181 SDK tests, 145 backend tests, 3 examples; container smoke 5/5 on ARM64 and Linux AMD64 |
| Registry | `paiziqdevacr8406cce0` (Basic, Central US); admin and anonymous pull disabled |
| Uploaded image | `paiziqdevacr8406cce0.azurecr.io/paiziq-ingest:2f4119a-azure1` |
| Image manifest digest | `sha256:69d505b411bac64250e6f488cf907c9826b670d1069d6887b5bbd1d1b7866130` |
| Database storage | `paiziqdevdata8406cce02`, East US 2, Standard LRS; 5 GiB quota share `paiziq-ingest-data` |
| Failed environment | `paiziq-dev-env`, Central US; create failed with `AKSCapacityHeavyUsage`; deleted after Azure background cleanup |
| Replacement environment | `paiziq-dev-env-eastus2`, East US 2; `Succeeded` |
| Backend app | `paiziq-ingest-dev`; `Succeeded`, one healthy active revision, one replica, one worker; 0.5 vCPU / 1 GiB |
| Subscription policy | `FreeTrial_2014-09-01`, spending limit `On`; Azure enforces one Container Apps environment globally |
| Credentials | Bootstrap, Fernet, repository-only ACR pull, SDK developer and dashboard read-only keys saved in gitignored `deploy/azure/backend.env` with mode `0600`; no keys in this guide |
| Application records | Organization `org_63703cd5efff6f0db7b5` (`Paiziq`), environment `env_d0f615f491e11933fbed` (`dev`, sandbox), agent `agt_6c16810c442e0e12cedf` (`payment-agent-dev`) |
| Hosted verification | Smoke 5/5; managed SDK key trace ingest accepted; read key retrieves trace; restart preserves records and keys; Swagger/OpenAPI reachable |

ACR Tasks are disabled for this subscription (`TasksOperationsNotAllowed`), so
the image was built and tested locally for Linux AMD64, then pushed with a
short-lived Azure login token. The account has Contributor access and cannot
grant `AcrPull`; the registry credential has only content/metadata read access
to `paiziq-ingest`. The empty Central US storage account from the first attempt
was removed; the existing dashboard is unaffected.

The image is already uploaded. To redeploy it with the local configuration:

```bash
cd files/paiziq
set -a; source deploy/azure/backend.env; set +a
./deploy/azure/deploy_backend.sh --skip-build
```

The replacement uses Consumption with no additional Log Analytics workspace.
The failed Central US environment briefly blocked deployment because this
subscription allows only one environment globally. Cleanup completed and the
East US 2 create was accepted. Do not delete `paiziq-dev`: it contains the
deployed dashboard and prepared backend assets.

Connection settings in the local `backend.env`:

- `PAIZIQ_ENDPOINT`: live backend origin, not the static dashboard origin.
- `PAIZIQ_API_KEY`: managed `developer` key for SDK ingest/read.
- `PAIZIQ_READ_KEY`: managed `read_only` key for dashboard reads.
- `PAIZIQ_CONTROL_ENV_ID` / `PAIZIQ_CONTROL_AGENT_ID`: saved dev record IDs.

The bootstrap key remains in `PAIZIQ_INGEST_KEYS` for server administration.
Do not give it to the browser. No reviewer key was issued. The Northstar demo
subsequently created three synthetic payments and executed one with the SDK's
`MockGateway`; no real gateway was charged. The dashboard's live frontend was subsequently published by its main-branch CI
on 2026-10-03. The deployment-script fixes and rollout notes are
included with the regression tests in this change.
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

Verification on 2026-10-03: [SDK CI run](https://github.com/paiziq-admin/Paiziq-sdk/actions/runs/37175728093)
passed quality, package build and container smoke, then failed Azure login
with `No subscriptions found` because the identity has no role assignments.
The backend app was not changed by that run. The
[dashboard push run](https://github.com/paiziq-admin/Paiziq-Dashboard/actions/runs/37175628199)
passed quality, Chromium E2E and deployment. Both notification workflows
completed successfully and mentioned contributors in the
[SDK results thread](https://github.com/paiziq-admin/Paiziq-sdk/issues/2) and
[dashboard results thread](https://github.com/paiziq-admin/Paiziq-Dashboard/issues/3).

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

The default managed-identity path needs permission to grant `AcrPull` on the
registry. Subscription `Contributor` alone cannot create role assignments.
For a development deployment with that role, create an ACR token with only
`content/read` and `metadata/read` on the `paiziq-ingest` repository and set
both `AZ_ACR_PULL_USERNAME` and `AZ_ACR_PULL_PASSWORD` in the gitignored
`backend.env`. The deployment script stores the password as a Container Apps
registry secret. Do not enable the registry admin account or anonymous pull.
Use managed identity when role-assignment permission becomes available.

If `az acr build` returns `TasksOperationsNotAllowed`, build and test locally,
then push with your Azure login and deploy with `--skip-build`. On Apple
Silicon, select Linux AMD64 for Container Apps:

```bash
set -a; source deploy/azure/backend.env; set +a
export DOCKER_DEFAULT_PLATFORM=linux/amd64
make docker-smoke DOCKER_IMAGE="$AZ_ACR_NAME.azurecr.io/paiziq-ingest:$IMAGE_TAG" \
  PAIZIQ_CORS_ORIGINS="$PAIZIQ_CORS_ORIGINS"
az acr login --name "$AZ_ACR_NAME"
docker push "$AZ_ACR_NAME.azurecr.io/paiziq-ingest:$IMAGE_TAG"
./deploy/azure/deploy_backend.sh --skip-build
```

Set `IMAGE_TAG` explicitly in `backend.env` when using this fallback. The
registry remains private; cloud build permission is not required.

For a regional capacity error, keep the existing resource group and change
`AZ_LOCATION` to an available US region. Create both the Container Apps
environment and the database file share in that region. The resource group's
metadata location does not restrict the locations of its resources.

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

The Azure demo was run successfully on 2026-10-03 with `make northstar-demo`:

| Demo setting | Verified value |
| --- | --- |
| Organization | `e2e-org-062b161c` (`org_ba4d66545374463738f8`) |
| Environment | `e2e-sandbox`, `env_58c32600eb950cadd320` |
| Agent | `e2e-payment-agent`, `agt_31ccd5e693df1ee0d885` |
| Policy | `e2e-threshold-policy` v1 |
| Acme payment | `pay_001af9dc6d43f30e4140`: 49.99 USD, approved/executed by `MockGateway` |
| CloudHost payment | `pay_70ee4ad4bf3ffbe99514`: 180.00 USD, needs review; one open review |
| Shady payment | `pay_7b0badd0110ae81c4173`: 20.00 USD, rejected |
| Report | `.e2e/northstar-run.json` (no secrets) |
| Demo dashboard connection | `.e2e/northstar.env`, mode `0600`, gitignored; `PAIZIQ_READ_KEY` is the demo's own read-only key |

All three payments, persisted decisions, policy versions, and SDK traces were
independently read back from the hosted API with the demo key. The demo is a
one-time seed/run, not a continuously running server; its results remain in
the Azure database. Use the demo organization/environment when viewing them
after publishing the dashboard's live frontend. The existing `Paiziq` / `dev`
environment from backend setup is separate and does not contain these rows.

To run another fresh demo, use an admin credential because the runner creates
and publishes a policy and issues an API key:

```bash
cd files/paiziq
set -a; source deploy/azure/backend.env; set +a
export PAIZIQ_API_KEY="${PAIZIQ_INGEST_KEYS%%,*}"
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

The script deactivates existing revisions and waits until their replicas have
stopped before replacing the image. Expect
a short outage on redeployment. `nobrl` disables server byte-range locking;
it does not make SQLite safe for simultaneous writers in separate processes.
Keep one replica, one worker, and one active revision. Volume updates remove
the exported revision suffix so Azure can assign a fresh immutable revision.
The infrastructure script applies image, environment and volume together in
one template update; it does not start an image revision and then attach the
volume through a second swap.

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
- The Azure CLI rollout completed on 2026-10-03; see the live status in §1.
  Hosted smoke and restart-persistence checks passed. This is a dev deployment,
  not the managed multi-tenant production persistence/queue topology.
- The backend is deployed. Automatic redeployment uses the existing-app CI
  lane above; the original bootstrap script is retained for infrastructure
  setup and should be watched when used for a new environment.
- The dashboard was deployed by its main-branch CI on 2026-10-03; see the
  successful run linked in §1.
- SQLite on Azure Files is a development-tier arrangement: single replica, no
  horizontal scaling, and a short outage during revision swaps.
  The Phase 1 tracker still lists the Terraform/RDS path for production.
- `POST /v1/orgs` currently accepts any valid key (not admin-gated). The
  read-only dashboard key therefore can create organizations; it cannot mint
  keys, publish policies, or act on reviews. Tightening org creation is a
  follow-up, not part of this change.
- Costs: Basic ACR, Standard_LRS storage, a Consumption-plan Container App with
  `min=1` (always on, roughly a few tens of USD per month), and the existing
  Static Web App.

### Terraform environment recreation

The infrastructure lifecycle is managed in [Paiziq-Infra](https://github.com/paiziq-admin/Paiziq-Infra). Its manual workflows create or delete the entire dev/prod application group. Runtime deployment uses one SQLite writer; do not run SDK CI concurrently with a lifecycle workflow. After updating the backend, `make ci-deploy-azure` discovers its current hostname and first configured dashboard CORS origin directly from Azure. Hosted smoke checks therefore follow recreated resources rather than old default URLs. After dev recreation, run the infrastructure repository's `scripts/configure-app-repos.sh` locally to refresh the dashboard CI deployment token.
