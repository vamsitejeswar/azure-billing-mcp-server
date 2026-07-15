# Azure Billing — Gemini Enterprise Setup

## MCP Server URL
```
https://<your-cloud-run-service-url>/mcp
```
(printed by `deploy_cloudrun.sh` after deploy)

## Name
```
Azure Billing
```

## MCP Server Description
```
Provides AI-powered access to Azure Cost Management for actual spend and cost
analysis: subscription discovery, flexible cost queries, cost by service,
cost by resource group, daily cost trends, and top resources by cost.
```

## MCP Agent Instructions
```
You have access to Azure Cost Management tools for querying actual Azure spend.
All tools are read-only. If the user doesn't specify a subscription, call
list_subscriptions first. Default timeframe is "MonthToDate" unless the user
asks for a different period.
```

---

## Access control model

This server does **not** validate an inbound OAuth token. Access is enforced
by **Cloud Run IAM**: deployed with `--no-allow-unauthenticated`, and only
Gemini Enterprise's own Google service account is granted permission to
invoke it (step below). No other caller can reach it.

Separately, every tool call always uses the server's **own** Azure Cost
Management service principal credentials (see CREDENTIALS.md) -- never
whatever token Gemini Enterprise forwards. The OAuth fields below exist to
satisfy Gemini Enterprise's setup screen and the user consent step, not to
authorize the Azure API call.

## Authentication (OAuth 2.0)

| Field | Value |
|---|---|
| **Authorization URL** | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/authorize` |
| **Token URL** | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token` |
| **Client ID** | From the `gemini-enterprise-mcp` Entra ID app registration |
| **Client Secret** | From that app registration's Certificates & secrets |
| **Scopes** | `api://<client-id>/mcp.access` |
| **PKCE** | Enabled |

## OAuth App Registration Steps

1. Entra ID → App registrations → New registration → `gemini-enterprise-mcp`
2. Expose an API → accept default Application ID URI → Add a scope `mcp.access`
3. Certificates & secrets → New client secret → copy immediately
4. API permissions → Add a permission → My APIs → this same app → Delegated → `mcp.access` → Add
5. Authentication → Add a platform → Web → Redirect URI: *(paste the callback URL Gemini Enterprise's screen shows)*
6. Note Tenant ID, Client ID, Client Secret for the table above

## Granting Cloud Run access

```bash
gcloud run services add-iam-policy-binding azure-billing-mcp \
  --project <your-gcp-project-id> \
  --region <your-region> \
  --member="serviceAccount:service-<project-number>@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

Find `<project-number>`: `gcloud projects describe <your-gcp-project-id> --format="value(projectNumber)"`

(`deploy_cloudrun.sh` already runs this for you -- this is here for reference/re-runs.)

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Failed to reload custom actions` | IAM policy not propagated yet | Wait 1-2 min, retry |
| `403 Forbidden` | Gemini's service account missing invoker role | Re-run the IAM grant above |
| Tool succeeds but no cost data | Service principal lacks the role | Grant "Cost Management Reader" -- see CREDENTIALS.md |
| `AADSTS...` during login | Redirect URI mismatch | Confirm Gemini Enterprise's exact callback URL matches Entra ID's Authentication tab |
