#!/usr/bin/env bash
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"
SCENARIO_DIR="pipeline/scenarios/amazon_last"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="pipeline/shared"
mkdir -p data/amazon_last/html data/amazon_last/screenshots data/amazon_last/verifications
node "$SCENARIO_DIR/preprocess_top_10.js" --snapshot "$SCENARIO_DIR/source/Amazon.com _ laptop.html" --output data/amazon_last
node "$SCENARIO_DIR/generate_variations.js" --snapshot data/amazon_last/top_10_products.html --output data/amazon_last/html
python3 "$SHARED/screenshot_generator.py" "$CONFIG"
python3 "$SHARED/coordinate_calculator.py" "$CONFIG"
python3 "$SHARED/verification_boxer.py" "$CONFIG"
echo "Done. Results in data/amazon_last/"
