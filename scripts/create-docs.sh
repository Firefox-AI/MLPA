#!/usr/bin/env bash
set -e
# set dir to the root of the project
cd "$(dirname "$0")/.."

OPENAPI_URL="http://localhost:8000/openapi.json"
OUTPUT="docs/index.html"
API_JSON="openapi.json"

# Optional: fetch the OpenAPI JSON first
curl -sSL "$OPENAPI_URL" -o "$API_JSON"

# If you have redoc-cli installed, bundle into a standalone HTML
# Ensure you have npm and redoc-cli installed: npm install -g redoc-cli
npx -y @redocly/cli@latest build-docs "$API_JSON" -o "$OUTPUT"

rm $API_JSON

echo "Generated $OUTPUT from $OPENAPI_URL"
