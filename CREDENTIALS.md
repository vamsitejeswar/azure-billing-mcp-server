# Azure Cost Management credentials

Separate from the Entra ID app used for Gemini Enterprise's OAuth screen --
this is the service principal (with "Cost Management Reader" role) the
server uses to actually query Azure.

## 0. First time: create the secret with a placeholder

```bash
printf '%s' 'REPLACE_ME' | gcloud secrets create cost-mgmt-client-secret \
  --project <your-gcp-project-id> --data-file=-
```

`deploy_cloudrun.sh` does this automatically if the secret doesn't exist yet.

## 1. Create the service principal (once)

```bash
az ad sp create-for-rbac --name "azure-billing-mcp-reader" \
  --role "Cost Management Reader" \
  --scopes /subscriptions/<your-subscription-id>
```

Copy `tenant` → `COST_MGMT_TENANT_ID`, `appId` → `COST_MGMT_CLIENT_ID` in
`deploy_cloudrun.sh`, and `password` → the real secret value (step 2).

## 2. Set the real secret value

```bash
printf '%s' 'REAL_CLIENT_SECRET' | gcloud secrets versions add cost-mgmt-client-secret \
  --project <your-gcp-project-id> --data-file=-
```

Or edit it directly in the console: Secret Manager → `cost-mgmt-client-secret` → New version.

No redeploy needed -- Cloud Run reads the `:latest` version on next request/restart.

## 3. Local testing

```bash
cp .env.example .env
# fill in AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET
python server.py
```

## Notes

- Client ID and Tenant ID are plain identifiers (not secret) -- fine as
  regular env vars in `deploy_cloudrun.sh`.
- Old secret versions aren't deleted automatically:
  `gcloud secrets versions list cost-mgmt-client-secret --project <your-gcp-project-id>`
- Rotating the secret doesn't require re-granting IAM (`run.invoker`) --
  that's unaffected by credential changes.
