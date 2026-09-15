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
GCP_REGION="asia-south1"               # Change if your Cloud Run region differs
SERVICE_NAME="azure-billing-mcp"       # Cloud Run service name (client-specific)
SECRET_NAME="Azure_secret_value"       # Must match the secret name in GCP Secret Manager (Step 4 of SETUP.md)
GCP_PROJECT="<your-gcp-project-id>"   # Your GCP project ID

# Azure Cost Management service principal identifiers.
# These are the AZURE_TENANT_ID and AZURE_CLIENT_ID from Step 1.2 of SETUP.md.
# They are identifiers (like a username), not secrets — the actual secret is in GCP Secret Manager above.
COST_MGMT_TENANT_ID="<your-azure-tenant-id>"
COST_MGMT_CLIENT_ID="<your-azure-client-id>"
# ---------------------

PROJECT_ID="${GCP_PROJECT}"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
IMAGE="asia-south1-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/$SERVICE_NAME"

# Build and push the container image via Cloud Build
gcloud builds submit \
  --tag "$IMAGE" \
  --project "$PROJECT_ID" \
  .

# Deploy from the built container image
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
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
