# Configuration Reference

## Environment Variables

### Required

| Variable | Description | Example | Where to get it |
|---|---|---|---|
| `AZURE_TENANT_ID` | Azure Directory (tenant) ID for the **billing service principal** | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` | Azure Portal → Entra ID → App Registrations → `azure-billing-mcp-reader` → Overview → Directory (tenant) ID |
| `AZURE_CLIENT_ID` | Application (client) ID of the **billing service principal** | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` | Azure Portal → Entra ID → App Registrations → `azure-billing-mcp-reader` → Overview → Application (client) ID |
| `AZURE_CLIENT_SECRET` | Client secret for the billing service principal | (opaque string) | Created in SETUP.md Step 1.3. Value shown only once at creation. |

**Important:** These credentials are for the `azure-billing-mcp-reader` service principal only. Do not use the `gemini-enterprise-mcp` app credentials here — they serve a different purpose.

### Optional

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8080` | TCP port the Uvicorn server listens on. Cloud Run injects this automatically. |
| `LOG_LEVEL` | `INFO` | Python logging level. Valid values: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Use `DEBUG` for verbose output during troubleshooting. |

---

## How Credentials Are Loaded

The server uses `azure-identity`'s `DefaultAzureCredential`, which tries multiple credential sources in order:

1. **Environment variables** (`AZURE_TENANT_ID` + `AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET`) — used in Cloud Run
2. **Managed Identity** — used if running on a GCP/Azure compute resource with managed identity configured
3. **Azure CLI** (`az login`) — used for local development as an alternative to setting env vars
4. Other sources (Workload Identity, etc.) — not typically relevant here

For **Cloud Run production**, always use env vars (set via `--set-env-vars` and `--set-secrets` in `deploy_cloudrun.sh`). For **local development**, either set env vars in `.env` or run `az login`.

---

## GCP Secret Manager

In production, `AZURE_CLIENT_SECRET` is stored in GCP Secret Manager rather than as a plain environment variable. The deploy script injects it at runtime:

```bash
--set-secrets "AZURE_CLIENT_SECRET=Azure_secret_value:latest"
```

This means Cloud Run reads the secret from Secret Manager at container startup and exposes it as the `AZURE_CLIENT_SECRET` environment variable inside the container. The value is never stored in the Cloud Run service configuration in plain text.

### Secret Name

The GCP Secret Manager secret must be named exactly `Azure_secret_value` (as configured in `deploy_cloudrun.sh`). If you use a different name, update both the secret creation command and the `SECRET_NAME` variable in `deploy_cloudrun.sh`.

### Granting Access

The Cloud Run service uses the default compute service account. It must have the `secretmanager.secretAccessor` role on `Azure_secret_value`:

```bash
gcloud secrets add-iam-policy-binding Azure_secret_value \
  --project <your-gcp-project-id> \
  --member="serviceAccount:<project-number>-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## deploy_cloudrun.sh Variables

| Variable | Description | Notes |
|---|---|---|
| `GCP_PROJECT` | Your GCP project ID | Found in Google Cloud Console → Project Info |
| `GCP_REGION` | Cloud Run region | Default: `asia-south1`. Change to match your project's region. |
| `SERVICE_NAME` | Cloud Run service name | The URL-safe name for the deployed service. Can be anything meaningful. |
| `SECRET_NAME` | GCP Secret Manager secret name | Must exactly match the secret created in Step 4 of SETUP.md. Default: `Azure_secret_value`. |
| `COST_MGMT_TENANT_ID` | Azure tenant ID (same as `AZURE_TENANT_ID`) | Passed to Cloud Run as `AZURE_TENANT_ID` env var. |
| `COST_MGMT_CLIENT_ID` | Azure client ID (same as `AZURE_CLIENT_ID`) | Passed to Cloud Run as `AZURE_CLIENT_ID` env var. |

---

## Gemini Enterprise Configuration

These values are entered in the Gemini Enterprise data store form — they are not environment variables in this server.

| Field | Value | Notes |
|---|---|---|
| MCP Server URL | `https://<cloud-run-url>/mcp` | Printed by deploy script |
| Authorization URL | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/authorize` | Use the tenant ID of `gemini-enterprise-mcp` app |
| Token URL | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token` | Same tenant ID |
| Client ID | Application (client) ID of `gemini-enterprise-mcp` | From Step 3 of SETUP.md — NOT the billing service principal |
| Client Secret | Secret from `gemini-enterprise-mcp` | From Step 3.3 of SETUP.md — NOT the billing service principal secret |
| Scopes | `api://<client-id>/mcp.access offline_access` | Replace `<client-id>` with the `gemini-enterprise-mcp` Application (client) ID |
| Enable PKCE | Unchecked | Leave unchecked |

---

## Logging Configuration

Log output goes to stdout (captured by Cloud Run and available in Google Cloud Logging).

Log format:
```
2024-01-15 10:23:45,123 | INFO     | azure-billing-mcp - list_subscriptions called (caller token present: True)
```

To increase verbosity for debugging, set `LOG_LEVEL=DEBUG` (Cloud Run env var or `.env` locally). This will log Azure SDK internals including token acquisition steps.

To view logs from Cloud Run:
```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=<service-name>" \
  --project <your-gcp-project-id> \
  --limit 100 \
  --format "table(timestamp, textPayload)"
```
