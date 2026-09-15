# Troubleshooting Guide

## Quick Diagnostics Checklist

Before diving into specific errors, verify these basics:

- [ ] Cloud Run service shows **Active** status in GCP Console
- [ ] `Azure_secret_value` exists in GCP Secret Manager
- [ ] Compute service account has `secretmanager.secretAccessor` on `Azure_secret_value`
- [ ] `AZURE_TENANT_ID` and `AZURE_CLIENT_ID` are set correctly in Cloud Run env vars
- [ ] Service principal has `Cost Management Reader` role on the Azure subscription
- [ ] Azure subscription is a paid commercial type (not Free Trial)

---

## Gemini Enterprise Errors

### `Failed to obtain refresh token`

**Symptom:** Gemini Enterprise shows this error when you try to connect or re-authenticate.

**Likely cause:** Wrong client secret entered in the GE data store form.

**Diagnose:** The GE form uses the `gemini-enterprise-mcp` app's secret (Step 3.3 of SETUP.md). The billing service principal's secret (Step 1.3) is a different value. These are commonly mixed up.

**Fix:**
1. In Azure Portal → Entra ID → App Registrations → `gemini-enterprise-mcp`
2. Certificates & Secrets — confirm which secret value you used
3. In Gemini Enterprise → Data Stores → your data store → Edit → update Client Secret to the `gemini-enterprise-mcp` secret

---

### `Failed to load actions` / Tools Not Appearing

**Symptom:** Gemini Enterprise connects but shows no tools, or an error when loading actions.

**Diagnose — step 1: check Cloud Run is running**
```bash
gcloud run services describe <service-name> --region <region> --format "value(status.conditions)"
```
Look for `Ready: True`.

**Diagnose — step 2: check Cloud Run logs for startup errors**
```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=<service-name> AND severity>=ERROR" \
  --project <your-gcp-project-id> --limit 50
```

**Common startup error:** `Permission denied on secret` — the compute service account cannot read `Azure_secret_value`. Fix:
```bash
gcloud secrets add-iam-policy-binding Azure_secret_value \
  --project <your-gcp-project-id> \
  --member="serviceAccount:<project-number>-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```
Then redeploy: `bash deploy_cloudrun.sh`

---

### `403 Forbidden` When GE Calls the Server

**Symptom:** Gemini Enterprise gets a 403 response from the Cloud Run endpoint.

**Likely cause:** GE's service account is missing the `run.invoker` role.

**Fix:** Re-run the IAM grant step from `deploy_cloudrun.sh`:
```bash
PROJECT_NUMBER=$(gcloud projects describe <your-gcp-project-id> --format="value(projectNumber)")
gcloud run services add-iam-policy-binding <service-name> \
  --region <region> \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

---

## Azure API Errors

### `doesn't have valid WebDirect/AIRS offer type`

**Symptom:** Returned by `query_costs` or other cost tools.

**Likely cause:** The Azure subscription is a Free Trial, Visual Studio, or other non-commercial subscription type.

**Fix:** Upgrade the subscription. In Azure Portal → Subscriptions → select subscription → Upgrade. See README.md — Subscription Type Requirement for supported types.

---

### `AuthorizationFailed` on Billing Tools

**Symptom:** `get_billing_periods`, `get_invoices`, or `get_billing_accounts` return `AuthorizationFailed`.

**Likely cause:** The billing service principal has `Cost Management Reader` but is missing `Billing Reader`.

**Fix:**
1. Azure Portal → Subscriptions → select subscription → Access control (IAM)
2. Add role assignment: `Billing Reader` → assign to the billing service principal using its **Object ID** (from Entra ID → Enterprise Applications → `azure-billing-mcp-reader` → Object ID)

---

### Tools Return Empty `[]` / `{}` Results

**Symptom:** Tools return `{"rows": []}` or empty lists with no error.

**Likely cause:** Service principal has no access to the subscription, or is assigned the role on the wrong identity.

**Diagnose:** Confirm the role assignment target. The role must be assigned to the **Object ID** from the **Enterprise Applications** page, not the Application (client) ID from App Registrations. These are different values.

1. Azure Portal → Entra ID → Enterprise Applications (not App Registrations)
2. Search for `azure-billing-mcp-reader`
3. Copy the **Object ID** from this page
4. Azure Portal → Subscriptions → Access control (IAM) → Role assignments
5. Confirm the service principal appears there with the correct Object ID

---

### `Failed to authenticate to Azure. Check AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET`

**Symptom:** Tool calls return this error message.

**Likely causes:**
- `AZURE_CLIENT_SECRET` has expired (Azure secrets have a finite validity period, typically 12 months)
- `AZURE_CLIENT_SECRET` was not set correctly in GCP Secret Manager
- `AZURE_TENANT_ID` or `AZURE_CLIENT_ID` contain placeholder values

**Diagnose:**
```bash
# Check what env vars Cloud Run sees
gcloud run services describe <service-name> --region <region> --format "value(spec.template.spec.containers[0].env)"

# Verify the secret has a value
gcloud secrets versions access latest --secret="Azure_secret_value" --project <your-gcp-project-id> | wc -c
```
(The second command returns the number of characters in the secret — should be non-zero.)

**Fix:**
- If secret expired: create a new secret in Azure Portal and update it in Secret Manager
- If placeholder values: fill in real values in `deploy_cloudrun.sh` and redeploy
- If Secret Manager value is wrong: update it and redeploy

---

### `Invalid tenant ID` on Startup

**Symptom:** Cloud Run logs show `Invalid tenant ID` at startup.

**Likely cause:** The `COST_MGMT_TENANT_ID` variable in `deploy_cloudrun.sh` was not updated from its placeholder value.

**Fix:** Open `deploy_cloudrun.sh`, set `COST_MGMT_TENANT_ID` to your actual Azure tenant ID, and run `bash deploy_cloudrun.sh` again.

---

## Local Development Errors

### `ModuleNotFoundError`

**Fix:** Ensure you activated the virtual environment and installed dependencies:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### `DefaultAzureCredential` Fails Locally

**Fix (option 1):** Add credentials to `.env`:
```bash
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
```

**Fix (option 2):** Use Azure CLI credentials:
```bash
az login
python server.py
```

### Port Already in Use

**Fix:** Either kill the process on port 8080, or override the port:
```bash
PORT=8081 python server.py
```

---

## Reading Cloud Run Logs

```bash
# Recent logs (all severities)
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=<service-name>" \
  --project <your-gcp-project-id> \
  --limit 100 \
  --format "table(timestamp, severity, textPayload)"

# Errors only
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=<service-name> AND severity>=ERROR" \
  --project <your-gcp-project-id> \
  --limit 50

# Or view in the Cloud Console:
# GCP Console → Cloud Run → <service-name> → Logs
```

Enable `LOG_LEVEL=DEBUG` in Cloud Run env vars for verbose output during troubleshooting. Remember to revert to `INFO` afterwards.
