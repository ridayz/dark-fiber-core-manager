#!/usr/bin/env bash
# Run the dark fiber core manager on http://localhost:4173
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-4173}"
export PORT
exec python3 src/server/app.py
