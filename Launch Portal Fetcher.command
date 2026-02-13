#!/bin/bash
# Double-click this file to launch Portal Fetcher Web UI
# It will open automatically in your default browser.

cd "$(dirname "$0")"
source .venv/bin/activate

PORT=8000
echo "Starting Portal Fetcher on http://127.0.0.1:$PORT ..."
echo "Press Ctrl+C to stop."
echo ""

# Open browser after a short delay
(sleep 2 && open "http://127.0.0.1:$PORT") &

portal-fetcher serve --port $PORT
