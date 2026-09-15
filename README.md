# Azure Billing MCP Server

An MCP (Model Context Protocol) server that provides read-only access to Azure Cost Management and Billing APIs. It runs on Google Cloud Run and integrates with Gemini Enterprise as a custom AI connector, allowing users to query live Azure billing data through natural language.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [MCP Tools](#mcp-tools)
5. [Local Development](#local-development)
6. [Deployment](#deployment)
7. [Environment Variables](#environment-variables)
8. [Network Requirements](#network-requirements)
9. [Security](#security)
10. [Subscription Type Requirement](#subscription-type-requirement)
11. [Gemini Enterprise Configuration](#gemini-enterprise-configuration)
12. [Troubleshooting](#troubleshooting)
13. [Maintenance](#maintenance)
14. [Known Limitations](#known-limitations)

---

## Overview

### What It Does

This server exposes 11 read-only MCP tools that allow Gemini Enterprise (or any MCP-compatible AI client) to query live Azure billing data — cost breakdowns by service, resource group, or individual resource; daily spend trends; invoices; budgets; and detailed usage records.

### Key Features

- **11 read-only tools** covering Azure Cost Management and Billing APIs
- **Deployed on Google Cloud Run** — serverless, auto-scaling, no infrastructure to manage
- **OAuth 2.0 via Microsoft Entra ID** — users log in with their corporate Microsoft account
- **Audit logging** — every request logs the caller's bearer token for traceability
- **Server-side service principal** — all Azure calls use a fixed service account with least-privilege read-only access

### Who Uses It

End users interact through Gemini Enterprise's AI chat interface. They ask natural-language questions about Azure spending (e.g. "What were our top 5 most expensive resources last month?"). The AI invokes the appropriate MCP tool and returns structured, readable data.

---

## Architecture

```
User (Gemini Enterprise chat)
       │
       ▼
Gemini Enterprise ──► Microsoft Entra ID OAuth
       │              (login.microsoftonline.com)
       │              Issues token scoped to: api://<client-id>/mcp.access
       ▼
Cloud Run MCP Server (IAM-gated: --no-allow-unauthenticated)
  ┌─────────────────────────────────────────────┐
  │  BearerTokenMiddleware                       │
  │  Captures bearer token for AUDIT LOG ONLY   │
  │  (token is NOT forwarded to Azure APIs)      │
  ├─────────────────────────────────────────────┤
  │  FastMCP → 11 MCP Tools                     │
  ├─────────────────────────────────────────────┤
  │  AzureCostClient                            │
  │  DefaultAzureCredential → service principal │
  └─────────────────────────────────────────────┘
       │
       ├──► Azure Cost Management API  (cost queries, usage)
       └──► Azure Billing API          (invoices, periods, budgets)
```

### Two Separate Azure Identities

The system uses two completely separate Azure identities. This is intentional and important:

| Identity | Purpose | Credentials used |
|---|---|---|
| **Billing service principal** (`azure-billing-mcp-reader`) | Queries Azure Cost Management + Billing APIs | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` in Cloud Run |
| **Entra ID OAuth app** (`gemini-enterprise-mcp`) | OAuth login screen for Gemini Enterprise users | Configured in Gemini Enterprise's data store form |

The bearer token that GE forwards with every request is scoped to `api://<client-id>/mcp.access` — this is the correct audience for **this server**, but the **wrong** audience for `management.azure.com`. It cannot be forwarded to Azure. All Azure API calls always use the server's own service principal credentials, regardless of who the caller is.

### Request Flow

1. User sends a billing question in Gemini Enterprise
2. GE authenticates with Entra ID and receives an OAuth token
3. GE sends the MCP request to Cloud Run with `Authorization: Bearer <token>`
4. Cloud Run IAM verifies the caller is GE's own service account (`gcp-sa-discoveryengine`)
5. `BearerTokenMiddleware` captures the token and logs its presence (audit only)
6. The MCP tool calls `AzureCostClient`, which authenticates to Azure using `DefaultAzureCredential` and the server's service principal
7. Azure responds; the server returns structured JSON to GE

For detailed setup instructions, see [SETUP.md](SETUP.md).

---

## Project Structure

```
azure-billing-mcp-server/
├── README.md                   # This file — main entry point
├── SETUP.md                    # Step-by-step Azure + GCP + Gemini Enterprise setup guide
│
├── docs/
│   ├── ARCHITECTURE.md         # Detailed architecture and component documentation
│   ├── CONFIGURATION.md        # All environment variables and configuration reference
│   ├── TROUBLESHOOTING.md      # Common errors, symptoms, and fixes
│   └── MAINTENANCE.md          # Ongoing maintenance procedures
│
├── server.py                   # All application code (MCP server, Azure client, 11 tools)
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Container image definition
├── deploy_cloudrun.sh          # One-command deployment to Google Cloud Run
├── .env.example                # Local development environment variable template
├── pyrightconfig.json          # Pyright/Pylance type-checker config (development only)
└── Azure_Setup_Guide.docx      # Azure setup reference document
```

### Key Files

| File | Purpose |
|---|---|
| `server.py` | **All application logic.** Contains `AzureCostClient` (Azure API wrapper), `BearerTokenMiddleware` (audit logging), and all 11 MCP tool definitions. Single-file by design — no package structure needed for a service this size. |
| `deploy_cloudrun.sh` | Builds the Docker image via Cloud Build, deploys to Cloud Run, grants IAM to Gemini Enterprise's service account. Fill in the variables at the top before running. |
| `Dockerfile` | Uses `python:3.12-slim`. Installs dependencies from `requirements.txt` then runs `server.py`. |
| `SETUP.md` | Complete step-by-step setup guide covering Azure Portal configuration, GCP Secret Manager, Cloud Run deployment, and Gemini Enterprise connection. Intended for engineers doing the initial setup. |
| `.env.example` | Template for local development credentials. Copy to `.env` and fill in values. Not used on Cloud Run. |

---

## MCP Tools

All tools are **read-only** (`readOnlyHint=True`). The server never writes to Azure.

### Cost Management Tools

| Tool | Parameters | Description |
|---|---|---|
| `list_subscriptions` | — | List all Azure subscriptions the service principal can access. Call this first if the user hasn't specified a subscription. |
| `query_costs` | `subscription_id`, `resource_group?`, `timeframe`, `granularity`, `group_by?`, `start_date?`, `end_date?`, `cost_type` | General-purpose cost query. Full control over grouping (e.g. `["ServiceName"]`, `["ResourceId"]`) and time range. |
| `get_cost_by_service` | `subscription_id`, `resource_group?`, `timeframe`, `start_date?`, `end_date?` | Cost breakdown by Azure service (VM, Storage, Functions, etc.), sorted highest first. |
| `get_cost_by_resource_group` | `subscription_id`, `timeframe`, `start_date?`, `end_date?` | Cost breakdown by resource group, sorted highest first. |
| `get_daily_cost_trend` | `subscription_id`, `resource_group?`, `timeframe`, `start_date?`, `end_date?` | Day-by-day cost time series sorted by date. |
| `get_top_resources_by_cost` | `subscription_id`, `resource_group?`, `timeframe`, `start_date?`, `end_date?`, `top_n` | Top N most expensive individual resources (default: 10). |

### Billing API Tools

| Tool | Parameters | Description |
|---|---|---|
| `get_billing_accounts` | — | List billing accounts accessible to the service principal. Call this first for invoice queries. |
| `get_billing_periods` | `subscription_id`, `top?` | List recent billing periods, most recent first (default: 12). |
| `get_invoices` | `billing_account_name`, `top?` | List invoices for a billing account (default: 12). |
| `get_usage_details` | `subscription_id`, `start_date`, `end_date`, `top?` | Detailed line-item usage records for a date range (default: 100 records). |
| `get_budgets` | `subscription_id` | Budget limits and current spend vs limit for a subscription. |

**Valid `timeframe` values:** `MonthToDate`, `BillingMonthToDate`, `TheLastMonth`, `TheLastBillingMonth`, `WeekToDate`, `Custom`

When `timeframe="Custom"`, both `start_date` and `end_date` are required in `YYYY-MM-DD` format.

---

## Local Development

```bash
# 1. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure credentials
cp .env.example .env
# Edit .env — fill in AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET

# 4. Start the server
python server.py
# Server starts at http://0.0.0.0:8080/mcp
```

**Alternative: Azure CLI authentication (no service principal needed locally)**
```bash
az login
python server.py    # DefaultAzureCredential picks up az login credentials automatically
```

The MCP endpoint is `http://localhost:8080/mcp`. You can connect any MCP-compatible client to this URL for local testing.

---

## Deployment

See [SETUP.md](SETUP.md) for complete step-by-step instructions. The high-level sequence is:

### Prerequisites

- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- GCP project selected (`gcloud config set project <project-id>`)
- Required GCP APIs enabled:
  ```bash
  gcloud services enable run.googleapis.com secretmanager.googleapis.com discoveryengine.googleapis.com
  ```

### Steps

1. **Azure** — Create the billing service principal (`azure-billing-mcp-reader`), assign `Cost Management Reader` and `Billing Reader` roles (SETUP.md Steps 1–2)
2. **Azure** — Create the OAuth app registration (`gemini-enterprise-mcp`) with the custom `mcp.access` scope (SETUP.md Step 3)
3. **GCP** — Store `AZURE_CLIENT_SECRET` in GCP Secret Manager as `Azure_secret_value` (SETUP.md Step 4)
4. **GCP** — Fill in `deploy_cloudrun.sh` variables and run `bash deploy_cloudrun.sh` (SETUP.md Step 5)
5. **Gemini Enterprise** — Create a Custom MCP data store pointing to the Cloud Run URL (SETUP.md Step 6)

### Deploy Script Variables

Edit the top section of `deploy_cloudrun.sh`:

```bash
GCP_PROJECT="<your-gcp-project-id>"
GCP_REGION="asia-south1"                    # change if needed
SERVICE_NAME="<your-service-name>"
SECRET_NAME="Azure_secret_value"            # must match the secret in GCP Secret Manager
COST_MGMT_TENANT_ID="<your-azure-tenant-id>"
COST_MGMT_CLIENT_ID="<your-azure-client-id>"
```

Then run:
```bash
bash deploy_cloudrun.sh
```

The script outputs the MCP Server URL on completion. Save this URL — it is needed for Gemini Enterprise configuration.

### Verifying Deployment

After deployment:
1. Check Cloud Run console — service status should be **Active**
2. In Gemini Enterprise, click **Reload custom actions** — all 11 tools should appear with a green checkmark

---

## Environment Variables

See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for full reference.

| Variable | Required | Default | Purpose | Where to get it |
|---|---|---|---|---|
| `AZURE_TENANT_ID` | Yes | — | Azure tenant ID for the billing service principal | Entra ID → App Registrations → Overview → Directory (tenant) ID |
| `AZURE_CLIENT_ID` | Yes | — | Application (client) ID of the billing service principal | Entra ID → App Registrations → Overview → Application (client) ID |
| `AZURE_CLIENT_SECRET` | Yes | — | Client secret for the billing service principal | Created in SETUP.md Step 1.3. In production, injected from GCP Secret Manager. |
| `PORT` | No | `8080` | HTTP port the server listens on | N/A |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | N/A |

On Cloud Run: `AZURE_TENANT_ID` and `AZURE_CLIENT_ID` are passed as `--set-env-vars`; `AZURE_CLIENT_SECRET` is injected via `--set-secrets` from GCP Secret Manager. The `.env` file is for local development only.

---

## Network Requirements

The server makes **outbound HTTPS (port 443)** calls to these endpoints:

| Endpoint | Purpose |
|---|---|
| `login.microsoftonline.com` | Azure token acquisition via `DefaultAzureCredential` |
| `management.azure.com` | Azure Cost Management API and Billing API |

**Inbound:** Cloud Run handles TLS termination. The container listens on port `8080` internally. Access is restricted by Cloud Run IAM (`--no-allow-unauthenticated`) — no additional firewall rules are required.

No VPC peering, IP allowlisting, custom DNS, or proxy configuration is required for a standard Cloud Run deployment.

---

## Security

### Access Control Model

- Cloud Run is deployed **without public access** (`--no-allow-unauthenticated`)
- Only Gemini Enterprise's own Google service account (`service-<project-number>@gcp-sa-discoveryengine.iam.gserviceaccount.com`) is granted `roles/run.invoker`
- The server does **not** perform JWT validation on incoming bearer tokens — token validation is Cloud Run IAM's responsibility
- All Azure API calls use the server's own service principal credentials — callers cannot escalate or override which Azure identity is used

### Secrets Management

| Secret | Production location | Notes |
|---|---|---|
| `AZURE_CLIENT_SECRET` | GCP Secret Manager (`Azure_secret_value`) | Injected at deploy time via `--set-secrets`. Never stored in environment variables directly or in code. |
| Entra ID OAuth app secret (`gemini-enterprise-mcp`) | Gemini Enterprise data store configuration | Used only by GE for the login flow — this server never sees it. |

### Required Azure Roles

The billing service principal requires:

| Role | Scope | Required for |
|---|---|---|
| `Cost Management Reader` | Subscription | All cost query and usage tools |
| `Billing Reader` | Subscription | `get_billing_periods`, `get_invoices`, `get_billing_accounts` |

The service principal has **no write permissions** anywhere.

---

## Subscription Type Requirement

Azure Cost Management and Billing APIs only work with **paid commercial** subscriptions:

| Works | Does NOT work |
|---|---|
| Pay-As-You-Go | Free Trial |
| Enterprise Agreement (EA) | Visual Studio / Dev/Test |
| Microsoft Customer Agreement (MCA) | CSP / Sponsored |

To upgrade: Azure Portal → Subscriptions → select subscription → Upgrade.

---

## Gemini Enterprise Configuration

After deploying to Cloud Run, configure Gemini Enterprise:

### Data Store Settings

In Gemini Enterprise Console → Data Stores → Create Data Store → Custom MCP Server:

| Field | Value |
|---|---|
| **MCP Server URL** | `https://<cloud-run-url>/mcp` (printed by deploy script) |
| **Authorization URL** | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/authorize` |
| **Token URL** | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token` |
| **Client ID** | Application (client) ID of the `gemini-enterprise-mcp` app (Step 3 of SETUP.md) |
| **Client Secret** | Secret from `gemini-enterprise-mcp` app (Step 3.3) — NOT the billing service principal secret |
| **Scopes** | `api://<client-id-of-gemini-enterprise-mcp>/mcp.access offline_access` |
| **Enable PKCE** | Leave unchecked |

### Required Redirect URIs (add in Entra ID App Registration → Authentication)

```
https://vertexaisearch.cloud.google.com/console/oauth/default_oauth.html
https://vertexaisearch.cloud.google.com/oauth-redirect
```

### MCP Agent Instructions

Paste this into the Gemini Enterprise agent instructions field:

```
You have access to Azure Cost Management and Billing tools for querying actual
Azure spend, invoices, budgets, and usage. All tools are read-only.
If the user doesn't specify a subscription, call list_subscriptions first.
For invoice queries, call get_billing_accounts first.
Default timeframe is "MonthToDate" unless the user asks for a different period.
```

---

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for the full guide.

| Symptom | Likely Cause | Fix |
|---|---|---|
| `Failed to obtain refresh token` in GE | Wrong client secret in GE form | Use the `gemini-enterprise-mcp` app secret (Step 3.3 of SETUP.md), **not** the billing service principal secret |
| `Failed to load actions` in GE | Cloud Run not starting | Check GCP Secret Manager IAM — the compute service account must have `secretmanager.secretAccessor` |
| `doesn't have valid WebDirect/AIRS offer type` | Free Trial Azure subscription | Upgrade to Pay-As-You-Go |
| `AuthorizationFailed` on billing tools | Missing `Billing Reader` role | Assign `Billing Reader` to the service principal in Azure Portal (SETUP.md Step 2.2) |
| Tools return empty `[]` | Service principal lacks subscription access | Assign roles using the service principal's **Object ID** (from Enterprise Applications), not the Application ID |
| `403 Forbidden` on Cloud Run | GE service account missing invoker role | Re-run the IAM grant step in `deploy_cloudrun.sh` |
| `Invalid tenant ID` on startup | Placeholder values left in deploy script | Fill in real `COST_MGMT_TENANT_ID` and `COST_MGMT_CLIENT_ID` in `deploy_cloudrun.sh` |
| `Permission denied on secret` | Cloud Run cannot read `Azure_secret_value` | Run the `gcloud secrets add-iam-policy-binding` command from SETUP.md Step 4.1 |

---

## Maintenance

See [docs/MAINTENANCE.md](docs/MAINTENANCE.md) for full procedures.

**Deploying a new version:**
```bash
bash deploy_cloudrun.sh    # rebuild image and redeploy
```

**Rotating the Azure client secret:**
1. Create a new secret in Azure Portal (Entra ID → App Registrations → `azure-billing-mcp-reader` → Certificates & Secrets)
2. Update the value in GCP Secret Manager: `Azure_secret_value`
3. Redeploy: `bash deploy_cloudrun.sh`

**Viewing logs:**
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=azure-billing-mcp" \
  --project <your-gcp-project-id> --limit 100 --format "table(timestamp, textPayload)"
```

---

## Known Limitations

- **Subscription type**: Cost Management APIs do not work on Free Trial, Visual Studio, CSP, or sponsored subscriptions.
- **Pagination**: `get_usage_details` defaults to 100 records (`top` parameter). Large date ranges may require multiple calls or a higher `top` value.
- **No per-user Azure RBAC**: All users share the service principal's access level. The bearer token is captured for audit logging only — there is no per-caller Azure identity switching.
- **Secret expiry**: The `AZURE_CLIENT_SECRET` has a finite validity (default: 12 months). When it expires, the server will stop authenticating to Azure. Set a calendar reminder to rotate it before expiry.
- **Single region**: The deploy script defaults to `asia-south1`. Multi-region deployments require manual configuration.
- **Read-only**: No write operations are supported. The server cannot create budgets, modify resources, or take any action in Azure.
