# Azure Billing MCP Server

MCP server for Azure spend, cost analysis, and billing via the Azure Cost Management and Billing APIs. Deployed to Google Cloud Run and connected to Gemini Enterprise.

---

## Architecture

```
User in Gemini Enterprise
        │
        ▼
Gemini Enterprise ──► Entra ID OAuth (login.microsoftonline.com)
        │  receives access token (scope: api://<client-id>/mcp.access)
        ▼
MCP Server on Cloud Run  (access gated by Cloud Run IAM)
  BearerTokenMiddleware captures the token for audit logging only —
  it is NOT forwarded to Azure (wrong audience for management.azure.com)
        │
        ▼
  Server's own service principal ──► Azure Cost Management + Billing APIs
        │
        ▼
Response back to Gemini Enterprise ──► User
```

> The Entra ID app used for GE login and the service principal used for billing are **two separate identities**. The incoming token is logged for visibility but never used to call Azure.

---

## Files

```
server.py            # FastMCP app, Azure client, all 11 tools, entrypoint
requirements.txt
Dockerfile
deploy_cloudrun.sh   # build image, deploy to Cloud Run, grant IAM
.env.example         # for local development
SETUP.md             # complete step-by-step setup guide for clients
```

---

## Tools (11 total)

### Cost Management
| Tool | Description |
|---|---|
| `list_subscriptions` | List all accessible Azure subscriptions |
| `query_costs` | General-purpose cost query (timeframe, granularity, grouping) |
| `get_cost_by_service` | Cost breakdown by Azure service |
| `get_cost_by_resource_group` | Cost breakdown by resource group |
| `get_daily_cost_trend` | Day-by-day cost time series |
| `get_top_resources_by_cost` | Top N most expensive resources |

### Billing API
| Tool | Description |
|---|---|
| `get_billing_accounts` | List billing accounts |
| `get_billing_periods` | List billing periods history |
| `get_invoices` | List invoices for a billing account |
| `get_usage_details` | Detailed usage records by date range |
| `get_budgets` | Budget limits and current spend vs limit |

All tools are **read-only** (`readOnlyHint=True`).

---

## Two Separate Credentials

| Credential | Purpose | Used by |
|---|---|---|
| **Azure service principal** (`AZURE_CLIENT_ID`) | Queries Azure billing APIs | The MCP server itself |
| **Entra ID app registration** (`gemini-enterprise-mcp`) | OAuth login screen for GE | Gemini Enterprise |

Do NOT mix these up — they serve different roles.

---

## Gemini Enterprise Setup

### MCP Server URL
```
https://<your-cloud-run-url>/mcp
```
Printed by `deploy_cloudrun.sh` after deploy.

### Authentication Settings (fill in GE form)

| Field | Value |
|---|---|
| **MCP Server URL** | `https://<cloud-run-url>/mcp` |
| **Authorization URL** | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/authorize` |
| **Token URL** | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token` |
| **Client ID** | Application (client) ID of `gemini-enterprise-mcp` app |
| **Client Secret** | Secret from `gemini-enterprise-mcp` app — NOT the service principal secret |
| **Scopes** | `api://<client-id>/mcp.access offline_access` |
| **Enable PKCE** | Leave unchecked |

### MCP Agent Instructions (paste into GE)
```
You have access to Azure Cost Management and Billing tools for querying actual
Azure spend, invoices, budgets, and usage. All tools are read-only.
If the user doesn't specify a subscription, call list_subscriptions first.
For invoice queries, call get_billing_accounts first.
Default timeframe is "MonthToDate" unless the user asks for a different period.
```

### Required Redirect URIs (add in Entra ID App Registration → Authentication)
```
https://vertexaisearch.cloud.google.com/console/oauth/default_oauth.html
https://vertexaisearch.cloud.google.com/oauth-redirect
```

---

## Local Development

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
python server.py        # runs at http://0.0.0.0:8080/mcp
```

---

## Deploy to Cloud Run

```bash
# 1. Fill in deploy_cloudrun.sh variables:
#    GCP_PROJECT, COST_MGMT_TENANT_ID, COST_MGMT_CLIENT_ID

# 2. Store the Azure client secret in Secret Manager:
printf '%s' 'YOUR_SECRET' | gcloud secrets create Azure_secret_value \
  --project <your-gcp-project-id> --data-file=-

# 3. Deploy:
bash deploy_cloudrun.sh
```

> For the full step-by-step client setup guide including Azure Portal screenshots, see **SETUP.md**.

---

## Subscription Type Requirement

The Cost Management and Billing APIs require a **paid commercial** Azure subscription:

| ✅ Works | ❌ Does NOT work |
|---|---|
| Pay-As-You-Go | Free Trial |
| Enterprise Agreement (EA) | Visual Studio / Dev/Test |
| Microsoft Customer Agreement (MCA) | CSP / Sponsored |

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Failed to obtain refresh token` | Wrong Client Secret in GE form | Use the `gemini-enterprise-mcp` secret, not the service principal secret |
| `Failed to load actions` in GE | Cloud Run server not starting | Re-grant Secret Manager permission to the compute service account |
| `doesn't have valid WebDirect/AIRS offer type` | Free Trial subscription | Upgrade to Pay-As-You-Go |
| `Invalid tenant ID` on startup | Placeholder credentials in deploy script | Fill in real `COST_MGMT_TENANT_ID` and `COST_MGMT_CLIENT_ID` |
| `Permission denied on secret` | Cloud Run SA lacks Secret Manager access | Run `gcloud secrets add-iam-policy-binding` for the compute SA |
| `403 Forbidden` | GE service account missing invoker role | Re-run the IAM grant in `deploy_cloudrun.sh` |
| `AuthorizationFailed` on billing tools | Missing Billing Reader role | Assign `Billing Reader` role to the service principal in Azure Portal |
| Tools return empty `[]` | Service principal has no subscription access | Assign `Cost Management Reader` role using the service principal's Object ID |
