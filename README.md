# azure-billing-mcp-server

MCP server for actual Azure spend / cost analysis via the Azure Cost
Management Query API. Deployed to Google Cloud Run for use with Gemini
Enterprise.

## Architecture

```
User in Gemini Enterprise
        |
        v
Gemini Enterprise -> Entra ID OAuth (login.microsoftonline.com)
        |  receives access token (scope: api://<client-id>/mcp.access)
        v
MCP Server (Cloud Run, access gated by IAM -- see GEMINI_SETUP.md)
  BearerTokenMiddleware captures the token for audit logging only --
  it is NOT forwarded to Azure (wrong audience for management.azure.com)
        |
        v
  Server's own service principal credentials -> Azure Cost Management API
        |
        v
Response back to Gemini -> User
```

This is a deliberate difference from a same-app OAuth setup (where the
forwarded token would be directly reusable against the downstream API):
here the Entra app used for login and the service principal used for
billing data are two separate identities, so the incoming token is logged
for visibility but never used to call Azure. See `GEMINI_SETUP.md` for the
full reasoning.

## Files

```
server.py           # everything: FastMCP app, Azure Cost Management client,
                     # all 6 tools, and the entrypoint -- one file on purpose
requirements.txt
Dockerfile
deploy_cloudrun.sh   # deploy + IAM grant + placeholder secret creation
.env.example         # for local runs
CREDENTIALS.md       # Azure Cost Management service principal setup/rotation
GEMINI_SETUP.md       # OAuth fields, IAM grant, troubleshooting for Gemini Enterprise
```

## Tools

`list_subscriptions`, `query_costs`, `get_cost_by_service`,
`get_cost_by_resource_group`, `get_daily_cost_trend`, `get_top_resources_by_cost`

## Two separate credentials -- don't confuse them

- **Entra ID app (`gemini-enterprise-mcp`)** -- only for Gemini Enterprise's
  OAuth consent screen. See `GEMINI_SETUP.md`.
- **Azure Cost Management service principal** -- what this server actually
  uses to query Azure. See `CREDENTIALS.md`.

Who's allowed to *call* this server is Cloud Run IAM, not an in-app token
check -- see `GEMINI_SETUP.md` for why.

## Local development

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the three AZURE_* values
python server.py        # http://0.0.0.0:8000/mcp
```

## Deploy

```bash
gcloud auth login
gcloud config set project <your-gcp-project-id>
gcloud services enable run.googleapis.com secretmanager.googleapis.com discoveryengine.googleapis.com
# edit the identifiers at the top of deploy_cloudrun.sh, then:
bash deploy_cloudrun.sh
```

Then follow `GEMINI_SETUP.md` for the Entra ID app registration and the
values to paste into Gemini Enterprise's data store form.
