# Amazon First

Target: first product (HP 14 Laptop, Overall Pick, $172.19) on an Amazon-style search results page.

## Source

- `source/Amazon.com _ laptop.html` – original snapshot.

## Config

- `config.json` – Python pipeline (paths: `data/amazon_first/`).
- `config_amazon.json` – JS generator (no `targetAsinSecond`; first product).

## Run (from repo root)

```bash
./pipeline/scenarios/amazon_first/run.sh
```

## Outputs

In `data/amazon_first/`: `top_10_products.html`, `html/*.html`, `screenshots/`, `coordinates.json`, `verifications/`.
