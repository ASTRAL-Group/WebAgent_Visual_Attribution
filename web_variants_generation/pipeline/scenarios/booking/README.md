# Booking

Target: first hotel card (Holiday Inn San Francisco - Golden Gateway). Position variants: header, banner, spotlight, sidebar (merged from the legacy position-only scenario).

## Source

- `source/Booking.com：_Hotels in San Francisco.html`

## Run (from repo root)

```bash
./pipeline/scenarios/booking/run.sh
```

## Outputs

In `data/booking/`: `html/*.html`, `screenshots/`, `coordinates.json`, `verifications/`. Uses scenario-specific `html_to_screenshots_coords.py` for screenshot + coordinate extraction, then shared `verification_boxer.py`.
