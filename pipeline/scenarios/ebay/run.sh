#!/usr/bin/env bash
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"
SCENARIO_DIR="pipeline/scenarios/ebay"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="pipeline/shared"
mkdir -p data/ebay/html data/ebay/screenshots data/ebay/verifications

echo "Step 1: Generate variation HTML (first product + position: banner, header, sidebar)"
node "$SCENARIO_DIR/generate_variations.js" \
  --snapshot "$SCENARIO_DIR/source/Earphones.html" \
  --output data/ebay/html

echo "Step 2: Screenshots"
python3 "$SHARED/screenshot_generator.py" "$CONFIG"

echo "Step 3: Coordinates (eBay-specific)"
python3 "$SCENARIO_DIR/coordinate_calculator.py" --input-dir data/ebay/html --output-dir data/ebay

echo "Step 4: Verification images"
python3 "$SHARED/verification_boxer.py" "$CONFIG"

echo "Done. Results in data/ebay/"
