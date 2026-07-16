# Azure Billing MCP Server — Complete Setup Guide

## Overview

This server exposes 11 Azure billing and cost management tools via the MCP protocol, deployed on Google Cloud Run and connected to Gemini Enterprise.

```
Gemini Enterprise
    │
    ├─► Entra ID (OAuth 2.0) ──► authenticates the GE user
    │
    └─► Cloud Run MCP Server ──► Azure Cost Management + Billing APIs
                                        │
                                        └─► Azure Service Principal (read-only)
```

---

## Part 1 — Azure: Create the Service Principal (Billing Reader)

This service principal is what the MCP server uses to query Azure billing data.

### 1.1 Create the service principal

```bash
az ad sp create-for-rbac \
  --name "azure-billing-mcp-reader" \
  --role "Cost Management Reader" \
  --scopes /subscriptions/<your-subscription-id>
```

Copy the output:
```json
{
  "tenant":   "→ AZURE_TENANT_ID",
  "appId":    "→ AZURE_CLIENT_ID",
  "password": "→ AZURE_CLIENT_SECRET"
}
```

### 1.2 Assign additional Billing role

Go to **Azure Portal → Subscriptions → your subscription → Access control (IAM) → Add role assignment**:

| Field | Value |
|---|---|
| Role | `Billing Reader` |
| Member | Search by Object ID of the service principal |

> To find the Object ID: **Entra ID → Enterprise Applications → search your app name → copy Object ID**

### 1.3 Required Azure roles for the service principal

| Role | Scope | Required for |
|---|---|---|
| `Cost Management Reader` | Subscription | All cost query tools |
| `Billing Reader` | Subscription | Billing periods, invoices, usage details |

---

## Part 2 — Azure: Create the Entra ID App Registration (GE OAuth)

This is a **separate** app from the service principal. It handles Gemini Enterprise's OAuth login screen.

### 2.1 Create the App Registration

1. Go to **Azure Portal → Entra ID → App Registrations → New Registration**
2. Fill in:
   - **Name**: `gemini-enterprise-mcp` (or any name)
   - **Supported account types**: `Accounts in this organizational directory only`
   - **Redirect URI**: leave blank for now
3. Click **Register**
4. Copy the **Application (client) ID** and **Directory (tenant) ID**

### 2.2 Add Redirect URIs

Go to **Authentication → Add a platform → Web**, add both:
```
https://vertexaisearch.cloud.google.com/console/oauth/default_oauth.html
https://vertexaisearch.cloud.google.com/oauth-redirect
```

Under **Implicit grant and hybrid flows**, leave both checkboxes **unchecked**.

Click **Save**.

### 2.3 Create a Client Secret

Go to **Certificates & Secrets → New client secret**:
- Description: `gemini-enterprise`
- Expires: 12 months (or as required)

Copy the **Value** immediately — it won't be shown again.

### 2.4 Expose an API (custom scope)

Go to **Expose an API**:

1. Click **Set** next to Application ID URI → accept the default `api://<client-id>` → Save
2. Click **Add a scope**:

| Field | Value |
|---|---|
| Scope name | `mcp.access` |
| Who can consent | `Admins and users` |
| Admin consent display name | `Access the Azure billing MCP tools` |
| Admin consent description | `Allows Gemini Enterprise to call the Azure billing MCP server` |
| State | `Enabled` |

### 2.5 Add API Permissions

Go to **API Permissions → Add a permission**:

| API | Permission | Type |
|---|---|---|
| Microsoft Graph | `User.Read` | Delegated |
| Azure Service Management | `user_impersonation` | Delegated |

Then click **Grant admin consent for `<your-tenant>`** → Yes.

### 2.6 Update the Manifest

Go to **Manifest**, find `requestedAccessTokenVersion` and change:
```json
"requestedAccessTokenVersion": null  →  "requestedAccessTokenVersion": 2
```
Click **Save**.

---

## Part 3 — GCP: Store Secrets and Deploy to Cloud Run

### 3.1 Store the Azure client secret in Secret Manager

```bash
printf '%s' 'YOUR_AZURE_CLIENT_SECRET' | gcloud secrets create Azure_secret_value \
  --project <your-gcp-project-id> --data-file=-
```

Grant Cloud Run access to the secret:
```bash
gcloud secrets add-iam-policy-binding Azure_secret_value \
  --project <your-gcp-project-id> \
  --member="serviceAccount:<project-number>-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### 3.2 Enable required GCP APIs

```bash
gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  discoveryengine.googleapis.com \
  --project <your-gcp-project-id>
```

### 3.3 Fill in deploy_cloudrun.sh

Open `deploy_cloudrun.sh` and update:
```bash
GCP_REGION="asia-south1"
SERVICE_NAME="verse-azure-billing-mcp"
SECRET_NAME="Azure_secret_value"
GCP_PROJECT="<your-gcp-project-id>"
COST_MGMT_TENANT_ID="<azure-tenant-id>"
COST_MGMT_CLIENT_ID="<azure-client-id>"
```

### 3.4 Deploy

```bash
bash deploy_cloudrun.sh
```

This will:
1. Build the Docker image via Cloud Build
2. Deploy to Cloud Run with `--no-allow-unauthenticated`
3. Inject `AZURE_TENANT_ID` and `AZURE_CLIENT_ID` as env vars
4. Mount `AZURE_CLIENT_SECRET` from Secret Manager
5. Grant Gemini Enterprise's service account `roles/run.invoker`

At the end it prints:
```
Deployed. MCP Server URL for Gemini Enterprise: https://<service>-<hash>-el.a.run.app/mcp
```

---

## Part 4 — Gemini Enterprise: Connect the MCP Server

### 4.1 Create a new Data Store

Go to **Gemini Enterprise → Data Stores → Create Data Store → Custom MCP Server**

### 4.2 Fill in Authentication Settings

| Field | Value |
|---|---|
| MCP Server URL | `https://<your-cloud-run-url>/mcp` |
| Authorization URL | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/authorize` |
| Token URL | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token` |
| Client ID | App Registration's Application (client) ID |
| Client Secret | Client secret value from Step 2.3 |
| Scopes | `api://<client-id>/mcp.access offline_access` |
| Enable PKCE | unchecked |

> **Important:** The Client Secret here is the Entra ID App Registration secret (Part 2), NOT the Azure service principal secret (Part 1).

### 4.3 Complete OAuth login

GE will redirect to Microsoft login. Sign in with an account that has admin access to the tenant. Admin consent will be requested for the `mcp.access` scope — approve it.

### 4.4 Reload actions

Once connected, click **Reload custom actions**. All 11 tools should appear as `Enabled`.

---

## Part 5 — Available Tools

### Cost Management Tools

| Tool | Description |
|---|---|
| `list_subscriptions` | Lists all Azure subscriptions accessible to the service principal |
| `query_costs` | General-purpose cost query with full control over timeframe, granularity, grouping |
| `get_cost_by_service` | Cost broken down by Azure service (VM, Storage, etc.) |
| `get_cost_by_resource_group` | Cost broken down by resource group |
| `get_daily_cost_trend` | Day-by-day cost time series |
| `get_top_resources_by_cost` | Top N most expensive individual resources |

### Billing API Tools

| Tool | Description |
|---|---|
| `get_billing_accounts` | Lists all billing accounts (needed for invoice queries) |
| `get_billing_periods` | Lists recent billing periods for a subscription |
| `get_invoices` | Lists invoices for a billing account |
| `get_usage_details` | Detailed usage records for a subscription by date range |
| `get_budgets` | Lists configured budgets and current spend vs limit |

---

## Part 6 — Subscription Type Requirements

The Cost Management and Billing APIs require a **commercial** Azure subscription:

| Subscription Type | Supported |
|---|---|
| Pay-As-You-Go (WebDirect) | ✅ |
| Enterprise Agreement (EA) | ✅ |
| Microsoft Customer Agreement (MCA) | ✅ |
| Free Trial | ❌ |
| Visual Studio / Dev/Test | ❌ |
| CSP / Sponsored | ❌ |

To upgrade a Free Trial: **Azure Portal → Subscriptions → Upgrade**.

---

## Part 7 — Redeployment (Credential Update)

When Azure credentials change:

1. Update the secret in Secret Manager:
```bash
printf '%s' 'NEW_CLIENT_SECRET' | gcloud secrets versions add Azure_secret_value \
  --project <your-gcp-project-id> --data-file=-
```

2. Update `deploy_cloudrun.sh` with new Tenant ID / Client ID if changed.

3. Redeploy:
```bash
bash deploy_cloudrun.sh
```

No changes to Gemini Enterprise are needed unless the MCP Server URL changes.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Failed to obtain refresh token` | Wrong client secret in GE form | Re-enter the correct secret from Step 2.3 |
| `Invalid tenant ID` | Placeholder credentials in deploy script | Fill in real `COST_MGMT_TENANT_ID` and `COST_MGMT_CLIENT_ID` |
| `Permission denied on secret` | Cloud Run SA lacks Secret Manager access | Re-run the `add-iam-policy-binding` command in Step 3.1 |
| `doesn't have valid WebDirect/AIRS offer type` | Free Trial subscription | Upgrade to Pay-As-You-Go |
| `Failed to load actions` in GE | Server not starting (check Cloud Run logs) | Usually a secret permission issue — re-grant SA access |
| `AuthorizationFailed` on billing APIs | Missing Billing Reader role | Assign `Billing Reader` role to the service principal (Step 1.2) |
