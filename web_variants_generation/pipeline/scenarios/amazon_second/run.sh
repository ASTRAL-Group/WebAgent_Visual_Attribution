#!/usr/bin/env bash
# Run full pipeline for amazon_second from repo root.
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO_ROOT"
SCENARIO_DIR="web_variants_generation/pipeline/scenarios/amazon_second"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="web_variants_generation/pipeline/shared"
mkdir -p web_variants_generation/data/amazon_second/html web_variants_generation/data/amazon_second/screenshots web_variants_generation/data/amazon_second/verifications

echo "Step 1: Generate variation HTML (source: top_10_products.html)"
node "$SCENARIO_DIR/generate_variations.js" \
  --snapshot "$REPO_ROOT/$SCENARIO_DIR/source/top_10_products.html" \
  --output web_variants_generation/data/amazon_second/html

echo "Step 2: Screenshots"
python3 "$SHARED/screenshot_generator.py" "$CONFIG"

echo "Step 3: Coordinates"
python3 "$SHARED/coordinate_calculator.py" "$CONFIG"

echo "Step 4: Verification images"
python3 "$SHARED/verification_boxer.py" "$CONFIG"

echo "Done. Results in web_variants_generation/data/amazon_second/"
