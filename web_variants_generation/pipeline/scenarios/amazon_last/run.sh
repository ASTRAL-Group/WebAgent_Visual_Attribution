#!/usr/bin/env bash
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO_ROOT"
SCENARIO_DIR="web_variants_generation/pipeline/scenarios/amazon_last"
CONFIG="$REPO_ROOT/$SCENARIO_DIR/config.json"
SHARED="web_variants_generation/pipeline/shared"
mkdir -p web_variants_generation/data/amazon_last/html web_variants_generation/data/amazon_last/screenshots web_variants_generation/data/amazon_last/verifications
node "$SCENARIO_DIR/preprocess_top_10.js" --snapshot "$SCENARIO_DIR/source/Amazon.com _ laptop.html" --output web_variants_generation/data/amazon_last
node "$SCENARIO_DIR/generate_variations.js" --snapshot web_variants_generation/data/amazon_last/top_10_products.html --output web_variants_generation/data/amazon_last/html
python3 "$SHARED/screenshot_generator.py" "$CONFIG"
python3 "$SHARED/coordinate_calculator.py" "$CONFIG"
python3 "$SHARED/verification_boxer.py" "$CONFIG"
echo "Done. Results in web_variants_generation/data/amazon_last/"
