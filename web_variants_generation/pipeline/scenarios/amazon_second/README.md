# Amazon Second

Target: second product (HP 15.6" Laptop, 16GB DDR4, 1TB PCIe SSD) on an Amazon-style search results page.

## Source

- `source/Amazon.com _ laptop.html` – original snapshot used as input.

## Config

- `config.json` – used by the shared Python pipeline (screenshot, coordinate, verification). Paths point to `data/amazon_second/`.
- `config_amazon.json` – used by the JS generator (variation types, selectors, etc.). Default paths point to `data/amazon_second/` when run from repo root.

## Run (from repo root)

```bash
./pipeline/scenarios/amazon_second/run.sh
```

Or step by step:

1. **Preprocess** (optional if `data/amazon_second/top_10_products.html` already exists):
   ```bash
   node pipeline/scenarios/amazon_second/preprocess_top_10.js \
     --snapshot pipeline/scenarios/amazon_second/source/Amazon.com\ _\ laptop.html \
     --output data/amazon_second
   ```

2. **Generate variation HTML**:
   ```bash
   node pipeline/scenarios/amazon_second/generate_variations.js \
     --snapshot data/amazon_second/top_10_products.html \
     --output data/amazon_second/html
   ```

3. **Python pipeline** (screenshots, coordinates, verifications):
   ```bash
   python3 pipeline/shared/screenshot_generator.py pipeline/scenarios/amazon_second/config.json
   python3 pipeline/shared/coordinate_calculator.py pipeline/scenarios/amazon_second/config.json
   python3 pipeline/shared/verification_boxer.py pipeline/scenarios/amazon_second/config.json
   ```

## Outputs (in `data/amazon_second/`)

- `top_10_products.html` – preprocessed page (intermediate).
- `html/*.html` – variation HTML files.
- `screenshots/*.png` – one PNG per variation.
- `coordinates.json` – bounding box per variation.
- `verifications/*.png` – screenshots with target box drawn.

## Notes

- Requires Node.js (for `preprocess_top_10.js` and `generate_variations.js`) and Python 3 with `playwright`, `Pillow`, `tqdm`. Run `playwright install chromium` once.
- Paths in `config.json` are relative to the directory from which you run the Python scripts; run from repo root.
