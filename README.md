# WebAgent Visual Attribution

Pipeline for generating web page variants (HTML), screenshots, and target-element coordinates for visual attribution evaluation.

## Structure

- **`pipeline/`** – Code and source inputs (everything needed to run the generator).
  - **`pipeline/shared/`** – Shared Python scripts: screenshot generation, coordinate extraction, verification overlay.
  - **`pipeline/scenarios/<name>/`** – Per-scenario: source HTML, `config.json`, and JS variation generator.
- **`data/`** – Generated outputs (database): variation HTML, screenshots, `coordinates.json`, verifications. Populated by running the pipeline; optionally gitignored.

## Setup

1. **Python 3.8+** with pip.
2. **Node.js** (for running the JS variation generators).
3. Install Python dependencies and Playwright browsers:

```bash
pip install -r requirements.txt
playwright install chromium
```

## Running a scenario

Each scenario has its own folder under `pipeline/scenarios/<name>/` with:

- `source/` – Original HTML snapshot(s).
- `config.json` – Paths and target settings (paths point to `data/<name>/`).
- `generate_variations.js` – Produces variation HTML into `data/<name>/html/`.
- `README.md` – Scenario-specific instructions.

**Typical flow:**

1. Generate variation HTML (from the scenario directory or repo root):
   ```bash
   cd pipeline/scenarios/<name> && node generate_variations.js
   ```
   Output: `data/<name>/html/*.html`.

2. Run the shared pipeline (screenshots → coordinates → verification images):
   ```bash
   python pipeline/shared/screenshot_generator.py pipeline/scenarios/<name>/config.json
   python pipeline/shared/coordinate_calculator.py pipeline/scenarios/<name>/config.json
   python pipeline/shared/verification_boxer.py pipeline/scenarios/<name>/config.json
   ```
   Or use the scenario’s `run.sh` if provided.

3. Results appear under `data/<name>/`: `screenshots/`, `coordinates.json`, `verifications/`.

Config paths in each scenario’s `config.json` use `data/<name>/` for input and output so that generated artifacts stay in one place.

## Scenarios

See `pipeline/scenarios/<name>/README.md` for each scenario’s source, target element, and any special notes.
