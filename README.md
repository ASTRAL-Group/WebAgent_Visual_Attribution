# WebAgent Visual Attribution

This repository contains pipelines for **web page variant generation** and **visual attribution evaluation**: generating varied web pages (HTML, screenshots, target coordinates) and evaluating how those variants influence model click behavior.

---

## Repository structure

| Directory | Purpose |
|-----------|--------|
| **`comprehensive_pipeline/`** | End-to-end evaluation pipeline: discovers variants, runs model inference (e.g. GLM-4V, Qwen, UI-TARS), compares predicted clicks to target coordinates, and produces reports. See [comprehensive_pipeline/README.md](comprehensive_pipeline/README.md) for setup, scenarios, and usage. |
| **`web_variants_generation/`** | Variant generation pipeline: produces HTML variants from source snapshots, takes screenshots, extracts target-element coordinates, and draws verification overlays. Outputs under `web_variants_generation/data/`. See [web_variants_generation/README.md](web_variants_generation/README.md) for setup and per-scenario instructions. |

Each part has its own README with detailed setup, options, and troubleshooting.

---

## Data directory (web variant outputs)

Generated outputs (HTML, screenshots, `coordinates.json`, verification images) go under **`web_variants_generation/data/`**.

- **You do not need to create this folder.** The repo includes an empty `web_variants_generation/data/` directory (with a `.gitkeep` placeholder). When you run a scenario, the pipeline creates the needed subfolders (e.g. `data/amazon_first/html`, `data/amazon_first/screenshots`) automatically.

If you prefer to create the output root yourself before the first run, you can:

```bash
mkdir -p web_variants_generation/data
```

This is optional; the pipeline will create it if missing.

---

## Quick start

### 1. Generate variants (HTML + screenshots + coordinates)

From the **repository root**:

```bash
bash web_variants_generation/pipeline/run.sh <scenario_name>
```

Examples: `amazon_first`, `amazon_second`, `booking`, `npr`, `expedia`, `ebay`.

Run all scenarios in one command:

```bash
bash web_variants_generation/pipeline/run_all.sh
```

Useful options:

```bash
# Keep running remaining scenarios even if one fails
bash web_variants_generation/pipeline/run_all.sh --continue-on-error

# Run only selected scenarios
bash web_variants_generation/pipeline/run_all.sh --scenarios "amazon_first booking npr"
```

**Requirements:** Python 3.8+, Node.js, Playwright. One-time setup:

```bash
# If node/npm is missing, install Node.js first (example with nvm):
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm install --lts && nvm use --lts

cd web_variants_generation
pip install -r requirements.txt
npm install
playwright install chromium
```

Optional (recommended) Python setup with `uv`:

```bash
# Install uv (if missing)
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"

cd web_variants_generation
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
npm install
uv run playwright install chromium
```

Results appear under `web_variants_generation/data/<scenario_name>/` (html, screenshots, coordinates, verifications). See [web_variants_generation/README.md](web_variants_generation/README.md) for scenario list and step-by-step flow.

### 2. Run comprehensive evaluation

For model inference, coordinate comparison, and reports, use the comprehensive pipeline. Setup and usage (including scenario names, model types, and output paths) are in [comprehensive_pipeline/README.md](comprehensive_pipeline/README.md).

---

## Summary

| Goal | Where to look |
|------|----------------|
| Generate page variants and coordinates | [web_variants_generation/README.md](web_variants_generation/README.md) |
| Evaluate model click behavior on variants | [comprehensive_pipeline/README.md](comprehensive_pipeline/README.md) |
| Data/output location | `web_variants_generation/data/` (auto-created by pipeline; contents gitignored) |
