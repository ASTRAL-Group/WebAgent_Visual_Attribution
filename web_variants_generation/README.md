# WebAgent Visual Attribution

Pipeline for generating web page variants (HTML), screenshots, and target-element coordinates for visual attribution evaluation.

## Structure

- **`web_variants_generation/pipeline/`** – Code and source inputs (everything needed to run the generator).
  - **`web_variants_generation/pipeline/shared/`** – Shared Python scripts: screenshot generation, coordinate extraction, verification overlay.
  - **`web_variants_generation/pipeline/scenarios/<name>/`** – Per-scenario: source HTML, `config.json`, and JS variation generator.
- **`web_variants_generation/data/`** – Generated outputs (database): variation HTML, screenshots, `coordinates.json`, verifications. Populated by running the pipeline; optionally gitignored.

## Setup

1. **Python 3.8+** with pip.
2. **Node.js** (for running the JS variation generators).
3. Install Python dependencies, Node dependencies, and Playwright browsers:

```bash
# If node/npm is missing, install Node.js first (example with nvm):
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm install --lts && nvm use --lts

pip install -r requirements.txt
npm install
playwright install chromium
```

Optional (recommended) Python setup with `uv`:

```bash
# Install uv (if missing)
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"

uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
npm install
uv run playwright install chromium
```

## Running a scenario

Each scenario has its own folder under `web_variants_generation/pipeline/scenarios/<name>/` with:

- `source/` – Original HTML snapshot(s).
- `config.json` – Paths and target settings (paths point to `data/<name>/`).
- `generate_variations.js` – Produces variation HTML into `data/<name>/html/`.
- `README.md` – Scenario-specific instructions.

**Typical flow:**

1. Generate variation HTML (from the scenario directory or repo root):
   ```bash
   cd web_variants_generation/pipeline/scenarios/<name> && node generate_variations.js
   ```
   Output: `web_variants_generation/data/<name>/html/*.html`.

2. Run the shared pipeline (screenshots → coordinates → verification images) from repo root:
   ```bash
   python web_variants_generation/pipeline/shared/screenshot_generator.py web_variants_generation/pipeline/scenarios/<name>/config.json
   python web_variants_generation/pipeline/shared/coordinate_calculator.py web_variants_generation/pipeline/scenarios/<name>/config.json
   python web_variants_generation/pipeline/shared/verification_boxer.py web_variants_generation/pipeline/scenarios/<name>/config.json
   ```
   Or use the scenario’s `run.sh` if provided.

3. Results appear under `web_variants_generation/data/<name>/`: `screenshots/`, `coordinates.json`, `verifications/`.

Config paths in each scenario’s `config.json` still use `data/<name>/` relative to the `web_variants_generation` folder so that generated artifacts stay in one place.

## Run all scenarios in one command

From the repository root:

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

## Scenarios

See `web_variants_generation/pipeline/scenarios/<name>/README.md` for each scenario’s source, target element, and any special notes.
