#!/usr/bin/env python3
"""
Verification boxer: draws target bounding boxes on screenshots using coordinates.json.
Output: verifications/ under the configured output_directory.
"""

import os
import sys
import json
from pathlib import Path
from tqdm import tqdm
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow required: pip install Pillow")
    sys.exit(1)


class VerificationBoxer:
    """Draws verification boxes on screenshots from coordinates.json."""

    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self.output_dir = Path(self.config["output_directory"])
        self.screenshots_dir = self.output_dir / self.config["output_structure"]["screenshots_dir"]
        self.verifications_dir = self.output_dir / self.config["output_structure"]["verifications_dir"]
        self.coordinates_file = self.output_dir / self.config["output_structure"]["coordinates_file"]
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logging()

        print("Verification boxer initialized")
        print(f"  Screenshots:    {self.screenshots_dir}")
        print(f"  Verifications:  {self.verifications_dir}")
        print(f"  Coordinates:    {self.coordinates_file}")

    def _load_config(self, config_path: str) -> Dict:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _setup_logging(self) -> None:
        log_file = self.output_dir / f"verification_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Log file: {log_file}")

    def load_coordinates(self) -> Dict[str, Dict]:
        if not self.coordinates_file.exists():
            self.logger.error(f"Coordinates file not found: {self.coordinates_file}")
            return {}
        with open(self.coordinates_file, "r", encoding="utf-8") as f:
            coordinates = json.load(f)
        self.logger.info(f"Loaded {len(coordinates)} coordinate entries")
        return coordinates

    def draw_verification_box(
        self, screenshot_path: str, coordinates: Dict, html_filename: str
    ) -> Optional[str]:
        """Draw box on one screenshot. Returns verification image path or None."""
        screenshot_file = Path(screenshot_path)
        verification_name = f"verification_{screenshot_file.stem}.png"
        verification_path = self.verifications_dir / verification_name

        self.logger.info(f"Drawing box: {screenshot_file.name} -> {verification_name}")

        try:
            image = Image.open(screenshot_path)
            draw = ImageDraw.Draw(image)
            x = coordinates.get("x", 0)
            y = coordinates.get("y", 0)
            width = coordinates.get("width", 0)
            height = coordinates.get("height", 0)

            if width <= 0 or height <= 0:
                self.logger.warning(f"Invalid coordinates: {coordinates}")
                return None

            box_color = (255, 0, 0)
            box_width = 3
            draw.rectangle(
                [x, y, x + width, y + height],
                outline=box_color,
                width=box_width,
            )

            try:
                font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 16)
            except Exception:
                try:
                    font = ImageFont.truetype(
                        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16
                    )
                except Exception:
                    font = ImageFont.load_default()

            if "button_id" in coordinates or "button_text" in coordinates:
                label_text = f"Button ({width}x{height})"
            elif "asin" in coordinates:
                label_text = f"Product Card ({width}x{height})"
            else:
                label_text = f"Target ({width}x{height})"

            label_bbox = draw.textbbox((0, 0), label_text, font=font)
            label_width = label_bbox[2] - label_bbox[0]
            label_height = label_bbox[3] - label_bbox[1]
            label_x = x
            label_y = y - label_height - 5
            if label_y < 0:
                label_y = y + height + 5

            draw.rectangle(
                [
                    label_x - 2,
                    label_y - 2,
                    label_x + label_width + 2,
                    label_y + label_height + 2,
                ],
                fill=(255, 255, 255),
                outline=box_color,
                width=1,
            )
            draw.text((label_x, label_y), label_text, fill=box_color, font=font)

            self.verifications_dir.mkdir(parents=True, exist_ok=True)
            image.save(verification_path, "PNG")
            self.logger.info(f"Verification saved: {verification_path}")
            return str(verification_path)
        except Exception as e:
            self.logger.error(f"Verification failed for {screenshot_file.name}: {e}")
            return None

    def validate_box_coverage(self, coordinates: Dict) -> Dict[str, Any]:
        """Check if box size is in a reasonable range for a product card."""
        width = coordinates.get("width", 0)
        height = coordinates.get("height", 0)
        min_card_width, min_card_height = 300, 200
        max_card_width, max_card_height = 2000, 1000
        is_valid = (
            min_card_width <= width <= max_card_width
            and min_card_height <= height <= max_card_height
        )
        quality = "unknown"
        if is_valid:
            if width >= 1200 and height >= 500:
                quality = "excellent"
            elif width >= 800 and height >= 400:
                quality = "good"
            elif width >= 500 and height >= 300:
                quality = "acceptable"
            else:
                quality = "poor"
        else:
            quality = "poor"
        return {
            "is_valid_size": is_valid,
            "width": width,
            "height": height,
            "area": width * height,
            "coverage_quality": quality,
        }

    def generate_all_verifications(self) -> Dict[str, Any]:
        """Draw verification boxes for all screenshots that have coordinates. Returns result stats."""
        self.logger.info("Starting batch verification")
        coordinates_data = self.load_coordinates()
        if not coordinates_data:
            self.logger.error("No coordinates found")
            return {"success": False, "error": "No coordinates found"}

        screenshot_files = list(self.screenshots_dir.glob("*.png"))
        if not screenshot_files:
            self.logger.error(f"No screenshots in {self.screenshots_dir}")
            return {"success": False, "error": "No screenshots found"}

        self.logger.info(f"Found {len(screenshot_files)} screenshots")
        results = {
            "total_files": len(screenshot_files),
            "successful": 0,
            "failed": 0,
            "verifications": [],
            "validations": {},
            "errors": [],
        }

        for screenshot_file in tqdm(screenshot_files, desc="Verifications"):
            html_filename = screenshot_file.stem + ".html"
            # Support both key formats: "original.html" (Amazon) and "original" (Booking)
            coordinates = coordinates_data.get(html_filename) or coordinates_data.get(screenshot_file.stem)
            if not coordinates:
                self.logger.warning(f"No coordinates for {html_filename} or {screenshot_file.stem}")
                results["failed"] += 1
                results["errors"].append(
                    {"screenshot_file": screenshot_file.name, "error": "No coordinates found"}
                )
                continue

            validation = self.validate_box_coverage(coordinates)
            results["validations"][html_filename] = validation
            verification_path = self.draw_verification_box(
                str(screenshot_file), coordinates, html_filename
            )
            if verification_path:
                results["successful"] += 1
                results["verifications"].append(
                    {
                        "screenshot_file": screenshot_file.name,
                        "verification_file": Path(verification_path).name,
                        "verification_path": verification_path,
                        "coordinates": coordinates,
                        "validation": validation,
                    }
                )
            else:
                results["failed"] += 1
                results["errors"].append(
                    {"screenshot_file": screenshot_file.name, "error": "Verification failed"}
                )

        self._save_results(results)
        self._generate_validation_report(results)
        self.logger.info(f"Verifications done: {results['successful']}/{results['total_files']} succeeded")
        return results

    def _generate_validation_report(self, results: Dict[str, Any]) -> None:
        report_file = self.output_dir / self.config["output_structure"]["summary_file"]
        quality_stats = {}
        for validation in results["validations"].values():
            q = validation["coverage_quality"]
            quality_stats[q] = quality_stats.get(q, 0) + 1

        is_button = "target_button_selector" in self.config
        title = "# Button coordinate verification report" if is_button else "# Product card coordinate verification report"
        report_content = f"""{title}

## Stats
- Total: {results['total_files']}
- Success: {results['successful']}
- Failed: {results['failed']}
- Success rate: {results['successful']/results['total_files']*100:.1f}%

## Coverage quality
"""
        for quality, count in quality_stats.items():
            pct = count / results["total_files"] * 100
            report_content += f"- {quality}: {count} ({pct:.1f}%)\n"

        if is_button:
            report_content += """
## Quality
- **excellent**: box >= 300x200, full button
- **good**: box >= 200x100, most of button
- **acceptable**: box >= 100x50, basic area
- **poor**: box < 100x50

"""
        else:
            report_content += f"""
## Quality
- **excellent**: box >= 400x300, full card
- **good**: box >= 350x250, most of card
- **acceptable**: box >= 300x200, basic card
- **poor**: box < 300x200

## Target
- ASIN: {self.config.get('target_asin', 'N/A')}
- Name: {self.config.get('target_text', 'N/A')}

"""
        report_content += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report_content)
        self.logger.info(f"Report saved: {report_file}")

    def _save_results(self, results: Dict[str, Any]) -> None:
        results_file = self.output_dir / self.config["output_structure"]["results_file"]
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Results saved: {results_file}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python verification_boxer.py <config.json>")
        sys.exit(1)
    config_path = sys.argv[1]
    if not Path(config_path).exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)
    boxer = VerificationBoxer(config_path)
    results = boxer.generate_all_verifications()
    if results.get("successful", 0) > 0:
        print(f"Verifications done: {results['successful']}/{results['total_files']} succeeded")
        sys.exit(0)
    print("Verification failed")
    sys.exit(1)


if __name__ == "__main__":
    main()
