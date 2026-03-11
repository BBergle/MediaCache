#!/usr/bin/env bash
# Download pinned versions of vendored JS libraries.
# Run this script when you want to bump versions, then commit the results.
#
# Usage: bash tools/update_vendor_libs.sh

set -euo pipefail

# --- Version pins (edit these to update) ---
HTMX_VERSION="1.9.10"
LUCIDE_VERSION="0.576.0"
# -------------------------------------------

VENDOR_DIR="$(cd "$(dirname "$0")/../web/static/js/vendor" && pwd)"
mkdir -p "$VENDOR_DIR"

echo "Downloading vendored libraries to $VENDOR_DIR"
echo ""

echo "  htmx.org@${HTMX_VERSION} (core)..."
curl -sL "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.min.js" -o "$VENDOR_DIR/htmx.min.js"

echo "  htmx.org@${HTMX_VERSION} (ws extension)..."
curl -sL "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/ext/ws.js" -o "$VENDOR_DIR/htmx-ws.js"

echo "  lucide@${LUCIDE_VERSION}..."
curl -sL "https://unpkg.com/lucide@${LUCIDE_VERSION}/dist/umd/lucide.min.js" -o "$VENDOR_DIR/lucide.min.js"

echo ""
echo "Done. File sizes:"
wc -c "$VENDOR_DIR"/*.js | tail -4
echo ""
echo "Versions: htmx=${HTMX_VERSION}, lucide=${LUCIDE_VERSION}"
echo "Commit these files when ready."
