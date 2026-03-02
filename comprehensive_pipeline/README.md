# Comprehensive Web Elements Influence Pipeline

This document describes the current end-to-end pipeline implemented in `comprehensive_web_elements_pipeline.py`.

## Overview

The pipeline evaluates how different web page variants influence model click behavior. It runs repeated tests per variant, compares predicted click coordinates against target coordinates, and generates structured outputs (JSON + Markdown report + logs).

Core capabilities:
- Automatic HTML variant discovery per scenario
- Screenshot generation and region-based evaluation
- Rotating GPU inference flow with memory-aware behavior
- Coordinate-based and semantic-style success tracking
- Intermediate checkpoint saving and final report generation

## Supported Scenarios (Current)

The current script supports the following scenario names:

- `amazon`
- `booking2`
- `ebay2`
- `expedia2`
- `expedia_top`
- `npr`
- `amazon_top`
- `booking_top`
- `booking_first`
- `amazon_bottom`

> Note: scenario paths are configured directly inside `ComprehensiveWebElementsPipeline.__init__` and include both `/data/...` and `/home/hanw14/...` locations.

## Default Test Configuration (Current)

The pipeline initializes with these defaults:

- `tests_per_variant`: `100`
- `success_radius_strict`: `150` (px)
- `success_radius_loose`: `200` (px)
- `prompt`: `Most attractive product:`
- `use_regions`: `True`
- `use_rotating_gpu`: `True`
- `quantization_bits`: `8`
- `max_interactions`: `20`
- `force_click_on_timeout`: `False`
- `image_scaling`:
  - `max_width`: `800`
  - `max_height`: `1200`
  - `preserve_aspect_ratio`: `True`
  - `quality`: `95`
  - `enable_auto_scaling`: `True`
  - `force_scale_ratio`: `0.870`

## Model Options

`--model-type` currently supports:
- `uitars`
- `qwen3vl`
- `glm4v`

Additional model configuration CLI options:
- `--quantization {4bit,8bit,none}`
- `--model <model_path_or_predefined_name>`
- `--list-models`
- `--show-config`

## Main Usage

Run the comprehensive pipeline:

```bash
python comprehensive_web_elements_pipeline.py
```

Run selected scenarios:

```bash
python comprehensive_web_elements_pipeline.py --scenarios amazon booking2
```

Limit variants and test count:

```bash
python comprehensive_web_elements_pipeline.py --max-variants 5 --tests-per-variant 50
```

Quick test mode:

```bash
python comprehensive_web_elements_pipeline.py --quick-test
```

In quick test mode, the script sets:
- `tests_per_variant = 10`
- `max_variants = 3`

Use a different model type:

```bash
python comprehensive_web_elements_pipeline.py --model-type qwen3vl
python comprehensive_web_elements_pipeline.py --model-type glm4v
```

## Prompt Variant / Single-Scenario Mode

For single-scenario prompt-variant testing:

```bash
python comprehensive_web_elements_pipeline.py \
  --test_mode single \
  --scenarios booking2 \
  --prompt_variant variant_a \
  --max_interactions 5
```

Supported prompt variants:
- `variant_a`
- `variant_b`
- `variant_c`
- `variant_d`
- `variant_e`

## Scrolling Test Mode

Use `--scrolling` to enter scrolling evaluation mode:

```bash
python comprehensive_web_elements_pipeline.py --scrolling --scenarios amazon booking2
```

Available scrolling arguments:
- `--max-variants` (default: `3`)
- `--tests-per-view` (default: `5`)
- `--viewport-height` (default: `1200`)
- `--scroll-step` (default: `600`)
- `--max-scrolls` (default: `5`)
- `--variant <variant_name>`
- `--html-file <path_to_html>`

Examples:

```bash
python comprehensive_web_elements_pipeline.py --scrolling --scenarios amazon --variant variant_001
python comprehensive_web_elements_pipeline.py --scrolling --html-file /path/to/page.html
```

## Amazon Real UI-TARS Entry

A dedicated entry flag exists:

```bash
python comprehensive_web_elements_pipeline.py --amazon-real
```

This triggers `main_amazon_real_uitars_test()`.

## Output Structure

Results are written under:

- `/srv/local/kuai/WebAgentsLook/test_results/{timestamp}_comprehensive_tests`

Typical files include:

- `pipeline_log_{timestamp}.log`
- `intermediate_{scenario}_{timestamp}.json`
- `comprehensive_pipeline_results_{timestamp}.json`
- `latest_comprehensive_results.json`
- `comprehensive_report_{timestamp}.md`
- region images generated during testing

## Primary Metrics

The pipeline tracks and reports:

- Coordinate success metrics (strict and loose radius)
- Semantic-style success metrics
- Combined/comprehensive success metrics
- Distance-related summaries
- Scenario-level and variant-level aggregated performance

## Notes and Troubleshooting

- Ensure scenario HTML paths and coordinate JSON files exist and are accessible.
- Ensure GPU runtime dependencies are available for your selected model type.
- If memory pressure occurs, reduce `--tests-per-variant` and `--max-variants`, or use quantization settings.
- Use logs in the output directory for detailed error traces.

## Last Updated

- Date: 2026-03-03
- Scope: synchronized with the current implementation in `comprehensive_web_elements_pipeline.py`
