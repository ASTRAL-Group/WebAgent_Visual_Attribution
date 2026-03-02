#!/usr/bin/env bash
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"
SCENARIO_DIR="pipeline/scenarios/booking"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="pipeline/shared"
mkdir -p data/booking/html data/booking/screenshots data/booking/verifications

SNAPSHOT="$SCENARIO_DIR/source/Booking.com：_Hotels in San Francisco.html"
echo "Step 1: Generate variation HTML (first hotel + positions: header, banner, spotlight, sidebar)"
node "$SCENARIO_DIR/generate_variations.js" \
  --snapshot "$SNAPSHOT" \
  --output data/booking/html

echo "Step 2: Screenshots + coordinates (Booking-specific)"
python3 "$SCENARIO_DIR/html_to_screenshots_coords.py" \
  --input-dir data/booking/html \
  --output-dir data/booking

echo "Step 3: Verification images"
python3 "$SHARED/verification_boxer.py" "$CONFIG"

echo "Done. Results in data/booking/"
