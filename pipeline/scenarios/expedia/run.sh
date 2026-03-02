#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

SCENARIO_DIR="pipeline/scenarios/expedia"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="pipeline/shared"

mkdir -p data/expedia/html data/expedia/screenshots data/expedia/verifications

SNAPSHOT="$SCENARIO_DIR/source/Montage Big Sky Hotel Search Results.html"

echo "Step 1: Generate Expedia HTML variants (style + position/order/size/clarity)"
node "$SCENARIO_DIR/generate_variations.js" \
  --snapshot "$SNAPSHOT" \
  --output data/expedia/html

echo "Step 2: Screenshots"
python3 "$SHARED/screenshot_generator.py" "$CONFIG"

echo "Step 3: Coordinates"
python3 "$SHARED/coordinate_calculator.py" "$CONFIG"

echo "Step 4: Verification images"
python3 "$SHARED/verification_boxer.py" "$CONFIG"

echo "Done. Results in data/expedia/"

