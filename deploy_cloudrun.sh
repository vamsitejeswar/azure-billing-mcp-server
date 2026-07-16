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
GCP_REGION="asia-south1"
SERVICE_NAME="verse-azure-billing-mcp"
SECRET_NAME="Azure_secret_value"
GCP_PROJECT="gemini-project-n1"

# Azure Cost Management service principal identifiers (not secret --
# these are just IDs, like a username).
COST_MGMT_TENANT_ID="67289332-b388-45bf-9ee0-72164a055698"
COST_MGMT_CLIENT_ID="30bfa59f-b87d-4313-9363-61e51b9b1b5f"
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
