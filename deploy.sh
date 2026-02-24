#!/bin/bash
# Deploy script for policy-irr-analyzer
# Run on server: bash deploy.sh
set -e

cd "$(dirname "$0")"

echo "Pulling latest code..."
git pull origin main

echo "Rebuilding and restarting containers..."
docker compose down
docker compose up -d --build

echo "Waiting for health check..."
sleep 5
if curl -sf http://localhost:5000/health > /dev/null 2>&1; then
    echo "Deploy successful! App is healthy."
else
    echo "Warning: Health check failed. Check logs with: docker compose logs app"
fi
