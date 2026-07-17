# Azure Billing MCP Server — Setup Guide

## How It Works

```
Gemini Enterprise (AI Assistant)
        │
        ├──► Microsoft Entra ID ──► verifies who is logging in (OAuth)
        │
        └──► Cloud Run MCP Server ──► reads Azure billing & cost data
                                              │
                                              └──► Azure (read-only service account)
```

---

## What You Need Before Starting

- Access to **Azure Portal** (portal.azure.com) with admin rights
- Access to **Google Cloud Console** (console.cloud.google.com)
- Access to **Gemini Enterprise** console

---

## Step 1 — Create a Service Account in Azure (for billing data)

This is the account the MCP server uses to read Azure billing data. It only has read access.

### 1.1 Go to Microsoft Entra ID → App Registrations

1. Open [portal.azure.com](https://portal.azure.com)
2. Search for **"Entra ID"** in the top search bar
3. Click **App registrations** in the left menu
4. Click **+ New registration**
5. Fill in:
   - **Name**: `azure-billing-mcp-reader`
   - **Supported account types**: `Accounts in this organizational directory only`
6. Click **Register**

### 1.2 Note down these values

After registration, on the Overview page copy:

| Value | Where to find it |
|---|---|
| `AZURE_TENANT_ID` | Directory (tenant) ID |
| `AZURE_CLIENT_ID` | Application (client) ID |

### 1.3 Create a Client Secret

1. Click **Certificates & Secrets** in the left menu
2. Click **+ New client secret**
3. Description: `mcp-server-secret`
4. Expires: `12 months`
5. Click **Add**
6. **Copy the Value immediately** — it disappears after you leave the page

This is your `AZURE_CLIENT_SECRET`.

### 1.4 Find the Object ID (needed for role assignment)

1. Click **Overview** in the left menu
2. At the top, click the link that says **"Managed application in local directory"**
3. On the Enterprise Application page, copy the **Object ID**

> This Object ID is different from the Application (client) ID. You need this for Step 2.

---

## Step 2 — Give the Service Account Access to Billing Data

### 2.1 Assign Cost Management Reader role

1. In Azure Portal, search for **"Subscriptions"**
2. Click on your subscription
3. Click **Access control (IAM)** in the left menu
4. Click **+ Add** → **Add role assignment**
5. In the **Role** tab, search for `Cost Management Reader` → select it → click **Next**
6. In the **Members** tab, click **+ Select members**
7. Paste the **Object ID** from Step 1.4 into the search box
8. Select the service account → click **Select**
9. Click **Review + assign** → **Assign**

### 2.2 Assign Billing Reader role (for invoices and billing periods)

Repeat the same steps above but choose the role `Billing Reader` instead.

---

## Step 3 — Create the Entra ID OAuth App (for Gemini Enterprise login)

This is a **separate** app from Step 1. This one handles the login screen that appears when Gemini Enterprise connects to the MCP server.

### 3.1 Create a new App Registration

1. Go to **Entra ID → App Registrations → + New Registration**
2. Fill in:
   - **Name**: `gemini-enterprise-mcp`
   - **Supported account types**: `Accounts in this organizational directory only`
3. Click **Register**

### 3.2 Add Redirect URIs

1. Click **Authentication** in the left menu
2. Click **+ Add a platform** → choose **Web**
3. Add these two redirect URIs one by one:
   ```
   https://vertexaisearch.cloud.google.com/console/oauth/default_oauth.html
   https://vertexaisearch.cloud.google.com/oauth-redirect
   ```
4. Leave both checkboxes under **Implicit grant** unchecked
5. Click **Save**

### 3.3 Create a Client Secret

1. Click **Certificates & Secrets → + New client secret**
2. Description: `gemini-enterprise`
3. Expires: `12 months`
4. Click **Add** and **copy the Value immediately**

> This secret goes into the Gemini Enterprise form — do NOT mix it up with the secret from Step 1.3.

### 3.4 Expose the API (create a custom scope)

1. Click **Expose an API** in the left menu
2. Next to **Application ID URI**, click **Set** → accept the default → **Save**
3. Click **+ Add a scope**:

| Field | Value |
|---|---|
| Scope name | `mcp.access` |
| Who can consent | `Admins and users` |
| Admin consent display name | `Access the Azure billing MCP tools` |
| Admin consent description | `Allows Gemini Enterprise to call the Azure billing MCP server` |
| State | `Enabled` |

4. Click **Add scope**

### 3.5 Add API Permissions

1. Click **API Permissions → + Add a permission**
2. Add the following:

| API | Permission | Type |
|---|---|---|
| Microsoft Graph | `User.Read` | Delegated |
| Azure Service Management | `user_impersonation` | Delegated |

3. Click **Grant admin consent for `<your tenant name>`** → **Yes**

All permissions should show a green ✅ **Granted** status.

### 3.6 Update the Manifest

1. Click **Manifest** in the left menu
2. Find the line:
   ```json
   "requestedAccessTokenVersion": null
   ```
3. Change it to:
   ```json
   "requestedAccessTokenVersion": 2
   ```
4. Click **Save**

---

## Step 4 — Store the Secret in GCP Secret Manager

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **Secret Manager**
2. Click **+ Create Secret**
3. Name: `Azure_secret_value`
4. Secret value: paste the `AZURE_CLIENT_SECRET` from Step 1.3
5. Click **Create secret**

### 4.1 Grant Cloud Run access to the secret

Run this command in Cloud Shell (or terminal with gcloud):

```bash
gcloud secrets add-iam-policy-binding Azure_secret_value \
  --project <your-gcp-project-id> \
  --member="serviceAccount:<project-number>-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

> Find your project number in **Cloud Console → Home → Project info → Project number**

---

## Step 5 — Deploy to Cloud Run

### 5.1 Fill in deploy_cloudrun.sh

Open the file and update these lines:

```bash
GCP_REGION="asia-south1"
SERVICE_NAME="verse-azure-billing-mcp"
SECRET_NAME="Azure_secret_value"
GCP_PROJECT="<your-gcp-project-id>"
COST_MGMT_TENANT_ID="<AZURE_TENANT_ID from Step 1.2>"
COST_MGMT_CLIENT_ID="<AZURE_CLIENT_ID from Step 1.2>"
```

### 5.2 Run the deploy script

```bash
bash deploy_cloudrun.sh
```

At the end it prints the MCP Server URL:
```
Deployed. MCP Server URL for Gemini Enterprise: https://verse-azure-billing-mcp-xxxx.asia-south1.run.app/mcp
```

**Save this URL** — you need it in Step 6.

---

## Step 6 — Connect to Gemini Enterprise

### 6.1 Create a new Data Store

1. Go to **Gemini Enterprise Console**
2. Click **Data Stores → Create Data Store**
3. Choose **Custom MCP Server**

### 6.2 Fill in Authentication Settings

Use the values collected across the steps above:

| Field | Value |
|---|---|
| **MCP Server URL** | The URL from Step 5.2 ending in `/mcp` |
| **Authorization URL** | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/authorize` |
| **Token URL** | `https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token` |
| **Client ID** | App (client) ID of the `gemini-enterprise-mcp` app from Step 3 |
| **Client Secret** | Secret value from Step 3.3 |
| **Scopes** | `api://<client-id-from-step-3>/mcp.access offline_access` |
| **Enable PKCE** | Leave unchecked |

> **Common mistake:** Do NOT use the secret from Step 1.3 here. Use the secret from Step 3.3.

### 6.3 Complete OAuth login

Gemini Enterprise will open a Microsoft login window. Sign in with your Azure admin account and approve the permission request.

### 6.4 Reload actions

Once connected, click **Reload custom actions**. You should see all 11 tools with ✅ Enabled status.

---

## Available Tools (11 total)

### Cost Management
| Tool | What it does |
|---|---|
| `list_subscriptions` | Lists all Azure subscriptions |
| `query_costs` | Custom cost query (timeframe, grouping, filters) |
| `get_cost_by_service` | Cost breakdown by Azure service (VM, Storage, etc.) |
| `get_cost_by_resource_group` | Cost breakdown by resource group |
| `get_daily_cost_trend` | Day-by-day cost time series |
| `get_top_resources_by_cost` | Top N most expensive resources |

### Billing API
| Tool | What it does |
|---|---|
| `get_billing_accounts` | Lists billing accounts |
| `get_billing_periods` | Lists billing periods history |
| `get_invoices` | Lists invoices for a billing account |
| `get_usage_details` | Detailed usage records by date range |
| `get_budgets` | Budget limits and current spend |

---

## Subscription Type Requirement

The billing and cost APIs only work with **paid commercial** Azure subscriptions:

| ✅ Works | ❌ Does NOT work |
|---|---|
| Pay-As-You-Go | Free Trial |
| Enterprise Agreement (EA) | Visual Studio / Dev/Test |
| Microsoft Customer Agreement (MCA) | CSP / Sponsored |

To upgrade: **Azure Portal → Subscriptions → select your subscription → Upgrade**

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Failed to obtain refresh token` | Wrong Client Secret in GE form | Use the secret from Step 3.3, not Step 1.3 |
| `Failed to load actions` in GE | Cloud Run server not starting | Check Secret Manager permission (Step 4.1) |
| `doesn't have valid WebDirect/AIRS offer type` | Free Trial subscription | Upgrade to Pay-As-You-Go |
| `Invalid tenant ID` on startup | Placeholder in deploy script | Fill in real Tenant ID and Client ID in deploy_cloudrun.sh |
| `Permission denied on secret` | Cloud Run can't read the secret | Re-run the `add-iam-policy-binding` command from Step 4.1 |
| `AuthorizationFailed` on billing tools | Missing Billing Reader role | Assign `Billing Reader` role in Azure Portal (Step 2.2) |
| Tools return empty results `[]` | Service principal has no subscription access | Assign roles in Step 2 using the correct Object ID |


❯ 9e4fd41f-6b39-4a5d-b9c0-19dc460c893e

Let me assign the role using the REST API.

  Ran 1 shell command

The service principal can't assign its own roles — it needs an Azure admin to do it. But I got the Object ID from the error. Here are the exact portal steps:

Object ID of the service principal: 3acf5006-50c4-4d82-9c72-542b74abf476

---
Do this in Azure Portal:

1. Go to portal.azure.com → Subscriptions → 9e4fd41f-6b39-4a5d-b9c0-19dc460c893e
2. Click Access control (IAM) → Add → Add role assignment
3. Role tab → search Cost Management Reader → select it → Next
4. Members tab → Select members → paste this Object ID:
3acf5006-50c4-4d82-9c72-542b74abf476
5. Review + assign