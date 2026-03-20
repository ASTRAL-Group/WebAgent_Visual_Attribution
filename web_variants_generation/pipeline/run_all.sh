#!/usr/bin/env bash
# Run full pipeline for all scenarios (or selected scenarios).
# Usage:
#   ./pipeline/run_all.sh
#   ./pipeline/run_all.sh --continue-on-error
#   ./pipeline/run_all.sh --scenarios "amazon_first booking npr"

set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_ROOT"

CONTINUE_ON_ERROR=false
SCENARIOS_RAW=""

usage() {
  echo "Usage: ./pipeline/run_all.sh [--continue-on-error] [--scenarios \"name1 name2 ...\"]"
  echo
  echo "Options:"
  echo "  --continue-on-error   Continue running remaining scenarios when one fails"
  echo "  --scenarios \"...\"     Run only specific scenario names (space-separated)"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --continue-on-error)
      CONTINUE_ON_ERROR=true
      shift
      ;;
    --scenarios)
      if [[ $# -lt 2 ]]; then
        echo "Error: --scenarios requires an argument."
        usage
        exit 1
      fi
      SCENARIOS_RAW="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -n "$SCENARIOS_RAW" ]]; then
  IFS=' ' read -r -a SCENARIOS <<< "$SCENARIOS_RAW"
else
  mapfile -t SCENARIOS < <(
    for d in pipeline/scenarios/*; do
      [[ -d "$d" ]] || continue
      [[ -f "$d/run.sh" ]] || continue
      basename "$d"
    done | sort
  )
fi

if [[ ${#SCENARIOS[@]} -eq 0 ]]; then
  echo "No scenarios found to run."
  exit 1
fi

FAILED=()

echo "Running scenarios: ${SCENARIOS[*]}"
for scenario in "${SCENARIOS[@]}"; do
  echo "=============================="
  echo "Scenario: $scenario"
  if bash pipeline/run.sh "$scenario"; then
    echo "Scenario succeeded: $scenario"
  else
    echo "Scenario failed: $scenario"
    FAILED+=("$scenario")
    if [[ "$CONTINUE_ON_ERROR" == false ]]; then
      echo "Stopping on first failure. Use --continue-on-error to keep going."
      exit 1
    fi
  fi
done

echo "=============================="
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "All scenarios completed successfully."
  exit 0
fi

echo "Completed with failures: ${FAILED[*]}"
exit 1
