#!/usr/bin/env bash
set -e

# ==============================================================================
# ContextPilot AI - Automated 1-Click Google Cloud Run Deployment Script
# Designed for Google Cloud Shell execution
# ==============================================================================

echo "============================================================"
echo "🚀 Starting Automated Google Cloud Run Deployment for ContextPilot AI"
echo "============================================================"

# 1. Detect or set Google Cloud Project
CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")

if [ -z "$CURRENT_PROJECT" ] || [ "$CURRENT_PROJECT" = "(unset)" ]; then
    echo "🔍 No active project selected. Searching available projects..."
    PROJECT_ID=$(gcloud projects list --format="value(projectId)" | head -n 1)
    
    if [ -z "$PROJECT_ID" ]; then
        echo "⚠️ No existing Google Cloud project found."
        PROJECT_ID="contextpilot-$RANDOM"
        echo "Creating new project: $PROJECT_ID..."
        gcloud projects create "$PROJECT_ID" --name="ContextPilot AI" || true
    fi
    
    echo "✅ Setting project to: $PROJECT_ID"
    gcloud config set project "$PROJECT_ID"
else
    PROJECT_ID="$CURRENT_PROJECT"
    echo "✅ Using active project: $PROJECT_ID"
fi

# 2. Enable necessary APIs (Cloud Run & Cloud Build)
echo "🔧 Enabling Google Cloud Run and Cloud Build APIs..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

# 3. Clone fresh repository
echo "📦 Downloading latest ContextPilot AI codebase..."
rm -rf context-g
git clone https://github.com/ananyapandey9895/context-g.git
cd context-g

# 4. Deploy directly to Google Cloud Run
echo "🚀 Building Docker container and deploying to Cloud Run (us-central1)..."
gcloud run deploy contextpilot \
    --source . \
    --region us-central1 \
    --allow-unauthenticated \
    --port 8080 \
    --set-env-vars "PORT=8080" \
    --quiet

# 5. Retrieve and display public service URL
SERVICE_URL=$(gcloud run services describe contextpilot --region us-central1 --format='value(status.url)')

echo ""
echo "============================================================"
echo "🎉 SUCCESS! ContextPilot AI is LIVE on Google Cloud Run!"
echo "🌐 Permanent Public URL:"
echo "   $SERVICE_URL"
echo "============================================================"
