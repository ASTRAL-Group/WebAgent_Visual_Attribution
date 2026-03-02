# WebAgent Visual Attribution

Repository for web page variant generation and visual attribution evaluation pipelines.

## Contents

- **`comprehensive_pipeline/`** – End-to-end pipeline for evaluating how web page variants influence model click behavior (screenshot generation, coordinate comparison, inference, reports). See [comprehensive_pipeline/README.md](comprehensive_pipeline/README.md).

- **`web_variants_generation/`** – Pipeline for generating web page variants (HTML), screenshots, and target-element coordinates used as inputs for attribution. Run from repo root; outputs go under `web_variants_generation/data/`. See [web_variants_generation/README.md](web_variants_generation/README.md).

## Quick start

- **Generate variants (HTML + screenshots + coordinates):**  
  From repo root: `bash web_variants_generation/pipeline/run.sh <scenario_name>` (e.g. `amazon_second`, `booking`, `npr`).  
  Requirements: Python 3.8+, Node.js, and Playwright; see `web_variants_generation/README.md` for setup.

- **Run comprehensive evaluation:**  
  See `comprehensive_pipeline/README.md` for setup and usage.
