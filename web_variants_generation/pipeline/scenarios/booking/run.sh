#!/usr/bin/env bash
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO_ROOT"
SCENARIO_DIR="web_variants_generation/pipeline/scenarios/booking"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="web_variants_generation/pipeline/shared"
mkdir -p web_variants_generation/data/booking/html web_variants_generation/data/booking/screenshots web_variants_generation/data/booking/verifications

SNAPSHOT="$REPO_ROOT/$SCENARIO_DIR/source/top_10_hotels.html"
echo "Step 1: Generate variation HTML (first hotel + positions: header, banner, spotlight, sidebar)"
node "$SCENARIO_DIR/generate_variations.js" \
  --snapshot "$SNAPSHOT" \
  --output web_variants_generation/data/booking/html

echo "Step 2: Screenshots + coordinates (Booking-specific)"
python3 "$SCENARIO_DIR/html_to_screenshots_coords.py" \
  --input-dir web_variants_generation/data/booking/html \
  --output-dir web_variants_generation/data/booking

echo "Step 3: Verification images"
python3 "$SHARED/verification_boxer.py" "$CONFIG"

echo "Done. Results in web_variants_generation/data/booking/"
