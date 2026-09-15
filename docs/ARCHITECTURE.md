# Architecture

## Overview

The Azure Billing MCP Server is a single-process Python service. It uses the [FastMCP](https://github.com/jlowin/fastmcp) library to expose an MCP-compliant HTTP endpoint, and the [azure-identity](https://pypi.org/project/azure-identity/) library to authenticate to Azure as a service principal.

All logic lives in `server.py` — this is intentional. The service is small enough that a single well-structured file is cleaner than a package layout.

---

## Component Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ Google Cloud Run                                                      │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  Starlette ASGI application (FastMCP.http_app)                  │ │
│  │                                                                  │ │
│  │  ┌───────────────────────────────────────────────────────────┐  │ │
│  │  │  BearerTokenMiddleware                                     │  │ │
│  │  │  Extracts Authorization: Bearer <token> from every request │  │ │
│  │  │  Stores it in a ContextVar for per-request audit logging   │  │ │
│  │  └───────────────────────────┬───────────────────────────────┘  │ │
│  │                               │                                   │ │
│  │  ┌────────────────────────────▼──────────────────────────────┐  │ │
│  │  │  FastMCP (path="/mcp")                                     │  │ │
│  │  │  Handles MCP protocol (tool discovery, tool invocation)    │  │ │
│  │  │                                                            │  │ │
│  │  │  11 @mcp.tool decorated functions:                         │  │ │
│  │  │    list_subscriptions          get_billing_accounts        │  │ │
│  │  │    query_costs                 get_billing_periods         │  │ │
│  │  │    get_cost_by_service         get_invoices                │  │ │
│  │  │    get_cost_by_resource_group  get_usage_details           │  │ │
│  │  │    get_daily_cost_trend        get_budgets                 │  │ │
│  │  │    get_top_resources_by_cost                               │  │ │
│  │  └────────────────────────────┬──────────────────────────────┘  │ │
│  │                               │                                   │ │
│  │  ┌────────────────────────────▼──────────────────────────────┐  │ │
│  │  │  AzureCostClient                                           │  │ │
│  │  │  Manages token caching (60s refresh buffer)                │  │ │
│  │  │  Wraps httpx.Client for all Azure REST API calls           │  │ │
│  │  │  DefaultAzureCredential → service principal credentials    │  │ │
│  │  └──────────┬────────────────────────────────────────────────┘  │ │
│  └─────────────┼───────────────────────────────────────────────────┘ │
└────────────────┼─────────────────────────────────────────────────────┘
                 │ HTTPS (port 443)
       ┌─────────┴─────────┐
       │                   │
       ▼                   ▼
management.azure.com    login.microsoftonline.com
Azure Cost Management   Token endpoint
Azure Billing API       (DefaultAzureCredential)
```

---

## Components

### BearerTokenMiddleware

**File:** `server.py` — class `BearerTokenMiddleware`

A lightweight Starlette middleware that runs on every inbound HTTP request. It reads the `Authorization: Bearer <token>` header and stores the token value in a `ContextVar` (`current_token`). The middleware does not validate, decode, or forward the token. Its only purpose is to make the token available to tool functions for audit log entries.

**Why not validate the token?** Token validation is Cloud Run IAM's job. Cloud Run is deployed with `--no-allow-unauthenticated` — unauthenticated requests are rejected at the infrastructure level before they reach this code.

### AzureCostClient

**File:** `server.py` — class `AzureCostClient`

A wrapper around `httpx.Client` that handles all Azure REST API calls. Key behaviours:

- **Lazy credential init:** `DefaultAzureCredential` is only instantiated on the first token request, not at server startup. This avoids startup failures if credentials are loaded slightly late.
- **Token caching:** The Azure access token is cached in memory. It is refreshed 60 seconds before expiry (`expires_on - 60`).
- **Scope separation:** All Azure management API calls use the scope `https://management.azure.com/.default`. This is distinct from the scope of the inbound bearer token (`api://<client-id>/mcp.access`), which is why inbound tokens cannot be forwarded to Azure.
- **Error wrapping:** Azure API non-2xx responses and authentication failures raise `AzureCostClientError`, which the MCP tool functions catch and return as `{"error": "..."}` dicts.

### MCP Tools

**File:** `server.py` — functions decorated with `@mcp.tool(annotations=_R)`

All 11 tools follow the same pattern:
1. Validate and construct an Azure scope string
2. Call the appropriate `AzureCostClient` method
3. Parse the columnar response format (`columns` + `rows`) into a list of dicts
4. Round cost values to 2 decimal places
5. Return `{"rows": [...]}` or `{"error": "..."}` on failure

The `_R = ToolAnnotations(readOnlyHint=True)` annotation signals to MCP clients that these tools are safe to call without user confirmation.

### Response Parsing

The Azure Cost Management Query API returns results in a columnar format:
```json
{"properties": {"columns": [{"name": "Cost"}, {"name": "ServiceName"}], "rows": [[12.34, "Storage"]]}}
```

The `_parse()` function converts this to the more useful row-dict format:
```json
[{"Cost": 12.34, "ServiceName": "Storage"}]
```

---

## Azure API Endpoints Used

| API | Endpoint | Version |
|---|---|---|
| Subscriptions | `GET /subscriptions` | `2022-12-01` |
| Cost Management Query | `POST /subscriptions/{id}/providers/Microsoft.CostManagement/query` | `2023-11-01` |
| Billing Accounts | `GET /providers/Microsoft.Billing/billingAccounts` | `2020-05-01` |
| Billing Periods | `GET /subscriptions/{id}/providers/Microsoft.Billing/billingPeriods` | `2018-03-01-preview` |
| Invoices | `GET /providers/Microsoft.Billing/billingAccounts/{name}/invoices` | `2020-05-01` |
| Usage Details | `GET /subscriptions/{id}/providers/Microsoft.Consumption/usageDetails` | `2023-05-01` |
| Budgets | `GET /subscriptions/{id}/providers/Microsoft.Consumption/budgets` | `2023-05-01` |

All endpoints are under `https://management.azure.com`.

---

## Authentication Flow Detail

```
Azure service principal auth (every Azure API call):

AzureCostClient._get_token()
  └── DefaultAzureCredential.get_token("https://management.azure.com/.default")
        Tries, in order:
        1. AZURE_CLIENT_ID + AZURE_CLIENT_SECRET env vars  ← used in Cloud Run
        2. Managed Identity                                ← fallback if no env vars
        3. Azure CLI (az login)                            ← used for local dev
        4. (other credential providers disabled)

Gemini Enterprise OAuth (user login, handled externally):

GE user → Entra ID login screen (gemini-enterprise-mcp app registration)
         → Access token issued (audience: api://<gemini-enterprise-mcp-client-id>/mcp.access)
         → GE sends this token to Cloud Run as Authorization: Bearer header
         → Cloud Run IAM checks caller is gcp-sa-discoveryengine service account
         → BearerTokenMiddleware logs token presence
         → Token is NEVER used for Azure API calls
```

---

## Runtime Characteristics

- **Language:** Python 3.12
- **Framework:** FastMCP (ASGI), served by Uvicorn
- **Container:** `python:3.12-slim` — ~130 MB image
- **Concurrency:** Uvicorn handles concurrent requests; `AzureCostClient` uses a single `httpx.Client` (connection pool shared across requests). The token is cached in an instance variable (safe for single-process, single-replica deployment).
- **State:** No database, no persistent storage. The server is fully stateless except for the in-memory Azure access token cache.
- **Startup time:** Fast — no expensive initialization at startup. Azure credentials are loaded lazily on first tool call.
