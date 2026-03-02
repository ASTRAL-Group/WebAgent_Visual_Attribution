#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO_ROOT"

SCENARIO_DIR="web_variants_generation/pipeline/scenarios/npr"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="web_variants_generation/pipeline/shared"

mkdir -p web_variants_generation/data/npr/html web_variants_generation/data/npr/screenshots web_variants_generation/data/npr/verifications

echo "Step 1: Generate NPR HTML variants"
node "$SCENARIO_DIR/generate_variations.js" \
  --output web_variants_generation/data/npr/html

echo "Step 2: Screenshots"
python3 "$SHARED/screenshot_generator.py" "$CONFIG"

echo "Step 3: Coordinates"
python3 "$SHARED/coordinate_calculator.py" "$CONFIG"

echo "Step 4: Verification images"
python3 "$SHARED/verification_boxer.py" "$CONFIG"

echo "Done. Results in data/npr/"

