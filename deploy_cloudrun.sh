#!/usr/bin/env bash
# Deploys the Azure Billing MCP server to Google Cloud Run.
#
# Access control: deployed WITHOUT --allow-unauthenticated. Only Gemini
# Enterprise's own Google service account is granted permission to invoke
# it (see the add-iam-policy-binding step below).
#
# Prerequisites:
#   - gcloud CLI installed and logged in (`gcloud auth login`)
#   - A GCP project selected (`gcloud config set project <project-id>`)
#   - APIs enabled:
#       gcloud services enable run.googleapis.com secretmanager.googleapis.com discoveryengine.googleapis.com
#
# Usage: fill in the variables below, then run: bash deploy_cloudrun.sh

set -euo pipefail

# --- Fill these in ---
GCP_REGION="us-central1"
SERVICE_NAME="azure-billing-mcp"
SECRET_NAME="cost-mgmt-client-secret"

# Azure Cost Management service principal identifiers (not secret --
# these are just IDs, like a username).
COST_MGMT_TENANT_ID="<tenant-id-of-billing-service-principal>"
COST_MGMT_CLIENT_ID="<client-id-of-billing-service-principal>"
# ---------------------

PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

# Create the secret with a placeholder if it doesn't exist yet. Edit the
# real value yourself afterward (console, or `gcloud secrets versions add`
# -- see CREDENTIALS.md). This script never touches the value once created.
if ! gcloud secrets describe "$SECRET_NAME" >/dev/null 2>&1; then
  printf '%s' 'REPLACE_ME' | gcloud secrets create "$SECRET_NAME" --data-file=-
  echo "Created secret '$SECRET_NAME' with a placeholder value."
  echo "Edit it with the real Cost Management client secret before relying on this deployment:"
  echo "  gcloud secrets versions add $SECRET_NAME --data-file=-"
fi

gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --region "$GCP_REGION" \
  --no-allow-unauthenticated \
  --set-env-vars "AZURE_TENANT_ID=$COST_MGMT_TENANT_ID,AZURE_CLIENT_ID=$COST_MGMT_CLIENT_ID" \
  --set-secrets "AZURE_CLIENT_SECRET=$SECRET_NAME:latest"

# Grant Gemini Enterprise's own Google service account permission to invoke
# this service -- this, not an app-level token check, is what restricts
# who can reach it.
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --region "$GCP_REGION" \
  --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region "$GCP_REGION" --format "value(status.url)")

echo ""
echo "Deployed. MCP Server URL for Gemini Enterprise: $SERVICE_URL/mcp"
