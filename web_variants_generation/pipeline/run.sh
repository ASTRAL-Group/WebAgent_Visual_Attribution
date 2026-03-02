#!/usr/bin/env bash
# Run full pipeline for a scenario: generate HTML -> screenshots -> coordinates -> verifications.
# Usage: ./run.sh <scenario_name>
# Example: ./run.sh amazon_second
# Requires: from repo root, data/ and pipeline/scenarios/<name>/ exist; config paths use data/<name>/.

set -e
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_ROOT"
SCENARIO="${1:-}"
if [ -z "$SCENARIO" ]; then
  echo "Usage: ./pipeline/run.sh <scenario_name>"
  echo "Example: ./pipeline/run.sh amazon_second"
  exit 1
fi
SCENARIO_DIR="pipeline/scenarios/$SCENARIO"
CONFIG="$SCENARIO_DIR/config.json"
if [ ! -f "$CONFIG" ]; then
  echo "Config not found: $CONFIG"
  exit 1
fi
echo "Running pipeline for scenario: $SCENARIO"
if [ -f "$SCENARIO_DIR/run.sh" ]; then
  bash "$SCENARIO_DIR/run.sh"
else
  echo "No $SCENARIO_DIR/run.sh; run JS generator then:"
  echo "  python pipeline/shared/screenshot_generator.py $CONFIG"
  echo "  python pipeline/shared/coordinate_calculator.py $CONFIG"
  echo "  python pipeline/shared/verification_boxer.py $CONFIG"
  exit 1
fi
