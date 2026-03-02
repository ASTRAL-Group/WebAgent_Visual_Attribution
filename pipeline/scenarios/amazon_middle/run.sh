#!/usr/bin/env bash
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"
SCENARIO_DIR="pipeline/scenarios/amazon_middle"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="pipeline/shared"
mkdir -p data/amazon_middle/html data/amazon_middle/screenshots data/amazon_middle/verifications
node "$SCENARIO_DIR/preprocess_top_10.js" --snapshot "$SCENARIO_DIR/source/Amazon.com _ laptop.html" --output data/amazon_middle
node "$SCENARIO_DIR/generate_variations.js" --snapshot data/amazon_middle/top_10_products.html --output data/amazon_middle/html
python3 "$SHARED/screenshot_generator.py" "$CONFIG"
python3 "$SHARED/coordinate_calculator.py" "$CONFIG"
python3 "$SHARED/verification_boxer.py" "$CONFIG"
echo "Done. Results in data/amazon_middle/"
