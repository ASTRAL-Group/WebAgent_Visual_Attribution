#!/usr/bin/env python3
"""
Screenshot generator: converts HTML variant files to PNG screenshots using Playwright.
Supports full-page and viewport screenshots with the same rendering as coordinate extraction.
Output: screenshots/ under the configured output_directory.
"""

import os
import sys
import json
import time
from pathlib import Path
from tqdm import tqdm
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional


class ScreenshotGenerator:
    """Generates PNG screenshots from HTML variant files."""

    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self.input_dir = Path(self.config["input_directory"])
        self.output_dir = Path(self.config["output_directory"])
        self.screenshots_dir = self.output_dir / self.config["output_structure"]["screenshots_dir"]
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._setup_logging()

        print("Screenshot generator initialized")
        print(f"  Input dir:  {self.input_dir}")
        print(f"  Output dir: {self.output_dir}")
        print(f"  Screenshots: {self.screenshots_dir}")

    def _load_config(self, config_path: str) -> Dict:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _setup_logging(self) -> None:
        log_file = self.output_dir / f"screenshot_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Log file: {log_file}")

    def generate_screenshot(self, html_file_path: str) -> Optional[str]:
        """Generate a PNG screenshot for one HTML file. Returns screenshot path or None."""
        html_path = Path(html_file_path)
        screenshot_name = f"{html_path.stem}.png"
        screenshot_path = self.screenshots_dir / screenshot_name

        self.logger.info(f"Generating screenshot: {html_path.name} -> {screenshot_name}")

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-web-security",
                        "--disable-features=VizDisplayCompositor",
                    ],
                )
                page = browser.new_page()
                page.goto(f"file://{html_path.absolute()}", timeout=60000)
                page.wait_for_load_state("networkidle", timeout=60000)
                time.sleep(self.config["screenshot_settings"]["wait_time"])

                page_size = page.evaluate(
                    """
                    () => {
                        return {
                            width: Math.max(
                                document.documentElement.scrollWidth,
                                document.body.scrollWidth,
                                document.documentElement.offsetWidth,
                                document.body.offsetWidth
                            ),
                            height: Math.max(
                                document.documentElement.scrollHeight,
                                document.body.scrollHeight,
                                document.documentElement.offsetHeight,
                                document.body.offsetHeight
                            )
                        };
                    }
                """
                )
                page_size["width"] = max(page_size["width"], 1280)
                page_size["height"] = max(page_size["height"], 1200)
                page.set_viewport_size({"width": page_size["width"], "height": page_size["height"]})
                time.sleep(2)

                self.screenshots_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(screenshot_path),
                    full_page=self.config["screenshot_settings"]["full_page"],
                    type="png",
                )
                browser.close()

                if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
                    from PIL import Image
                    with Image.open(screenshot_path) as img:
                        actual_width, actual_height = img.size
                    self.logger.info(f"Screenshot saved: {screenshot_path}")
                    self.logger.info(f"Page size: {page_size['width']}x{page_size['height']}, image: {actual_width}x{actual_height}")
                    return str(screenshot_path)
                else:
                    self.logger.error(f"Screenshot failed: {screenshot_path}")
                    return None
        except Exception as e:
            self.logger.error(f"Screenshot failed for {html_path.name}: {e}")
            return None

    def generate_all_screenshots(self) -> Dict[str, Any]:
        """Generate screenshots for all HTML files in input_directory. Returns result stats."""
        self.logger.info("Starting batch screenshot generation")
        html_files = list(self.input_dir.glob("*.html"))
        if not html_files:
            self.logger.error(f"No HTML files in {self.input_dir}")
            return {"success": False, "error": "No HTML files found"}

        self.logger.info(f"Found {len(html_files)} HTML files")
        results = {
            "total_files": len(html_files),
            "successful": 0,
            "failed": 0,
            "screenshots": [],
            "errors": [],
        }
        for html_file in tqdm(html_files, desc="Screenshots"):
            screenshot_path = self.generate_screenshot(str(html_file))
            if screenshot_path:
                results["successful"] += 1
                results["screenshots"].append(
                    {"html_file": html_file.name, "screenshot_file": Path(screenshot_path).name, "screenshot_path": screenshot_path}
                )
            else:
                results["failed"] += 1
                results["errors"].append({"html_file": html_file.name, "error": "Screenshot generation failed"})

        self._save_results(results)
        self.logger.info(f"Screenshots done: {results['successful']}/{results['total_files']} succeeded")
        return results

    def _save_results(self, results: Dict[str, Any]) -> None:
        results_file = self.output_dir / self.config["output_structure"]["results_file"]
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Results saved: {results_file}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python screenshot_generator.py <config.json>")
        sys.exit(1)
    config_path = sys.argv[1]
    if not Path(config_path).exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)
    generator = ScreenshotGenerator(config_path)
    results = generator.generate_all_screenshots()
    if results.get("successful", 0) > 0:
        print(f"Screenshots done: {results['successful']}/{results['total_files']} succeeded")
        sys.exit(0)
    print("Screenshot generation failed")
    sys.exit(1)


if __name__ == "__main__":
    main()
