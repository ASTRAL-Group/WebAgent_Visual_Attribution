#!/usr/bin/env bash
# Run full pipeline for amazon_second from repo root.
# Usage: from repo root, ./pipeline/scenarios/amazon_second/run.sh
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"
SCENARIO_DIR="pipeline/scenarios/amazon_second"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="pipeline/shared"
mkdir -p data/amazon_second/html data/amazon_second/screenshots data/amazon_second/verifications

echo "Step 1: Preprocess (source HTML -> top_10_products.html)"
node "$SCENARIO_DIR/preprocess_top_10.js" \
  --snapshot "$SCENARIO_DIR/source/Amazon.com _ laptop.html" \
  --output data/amazon_second

echo "Step 2: Generate variation HTML"
node "$SCENARIO_DIR/generate_variations.js" \
  --snapshot data/amazon_second/top_10_products.html \
  --output data/amazon_second/html

echo "Step 3: Screenshots"
python3 "$SHARED/screenshot_generator.py" "$CONFIG"

echo "Step 4: Coordinates"
python3 "$SHARED/coordinate_calculator.py" "$CONFIG"

echo "Step 5: Verification images"
python3 "$SHARED/verification_boxer.py" "$CONFIG"

echo "Done. Results in data/amazon_second/"
