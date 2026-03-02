# eBay

Target: first product card on eBay search (Earphones). Includes position variants: banner, header, sidebar (merged from former ebay_position).

## Source

- `source/Earphones.html` – original snapshot.

## Run (from repo root)

```bash
./pipeline/scenarios/ebay/run.sh
```

## Outputs

In `data/ebay/`: `html/*.html`, `screenshots/`, `coordinates.json`, `verifications/`. This scenario uses the shared screenshot and verification scripts and a scenario-specific `coordinate_calculator.py` for eBay DOM.
