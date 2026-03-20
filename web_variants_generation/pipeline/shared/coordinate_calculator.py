#!/usr/bin/env python3
"""
Coordinate calculator: extracts target element bounding boxes from HTML using Playwright.
Supports multiple detection strategies (ASIN selector, card container, text matching, etc.)
and avoids 1x1 pixel detections. Output: coordinates.json under the configured output_directory.
"""

import os
import sys
import json
import time
from pathlib import Path
from tqdm import tqdm
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

class CoordinateCalculator:
    """Extracts target element coordinates from HTML variants."""

    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self.input_dir = Path(self.config["input_directory"])
        self.output_dir = Path(self.config["output_directory"])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.target_asin = self.config.get("target_asin") or ""
        self.target_text = self.config.get("target_text") or ""
        self.second_product_index = self.config.get("second_product_index")

        self._setup_logging()
        print("Coordinate calculator initialized")
        print(f"  Input dir:  {self.input_dir}")
        print(f"  Output dir: {self.output_dir}")
        print(f"  Target ASIN: {self.target_asin or '(by name)'}")
        print(f"  Target text: {(self.target_text[:50] + '...') if len(self.target_text) > 50 else self.target_text}")
        if self.second_product_index is not None:
            print(f"  Using match index: {self.second_product_index + 1} (second_product_index={self.second_product_index})")

    def _load_config(self, config_path: str) -> Dict:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _setup_logging(self) -> None:
        log_file = self.output_dir / f"coordinate_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Log file: {log_file}")
    
    def extract_html_coordinates(self, html_file_path: str) -> Optional[Dict]:
        """Extract target element coordinates from HTML (Playwright, same rendering as screenshots)."""
        self.logger.info(f"Extracting coordinates from: {Path(html_file_path).name}")
        
        try:
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor'
                    ]
                )
                
                page = browser.new_page()
                
                page.goto(f"file://{Path(html_file_path).absolute()}", timeout=60000)
                page.wait_for_load_state("networkidle", timeout=60000)
                time.sleep(self.config["screenshot_settings"]["wait_time"])

                page_size = page.evaluate("""
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
                """)
                page_size["width"] = max(page_size["width"], 1280)
                page_size["height"] = max(page_size["height"], 1200)
                page.set_viewport_size({"width": page_size["width"], "height": page_size["height"]})
                time.sleep(2)

                coordinates = self.detect_target_element(page, html_file_path)
                browser.close()

                if coordinates:
                    self.logger.info(f"Coordinates extracted: {coordinates}")
                    return coordinates
                self.logger.warning(f"Target element not found: {Path(html_file_path).name}")
                return None
        except Exception as e:
            self.logger.error(f"Coordinate extraction failed for {Path(html_file_path).name}: {e}")
            return None
    
    def detect_target_element(self, page, html_file_path: str) -> Optional[Dict]:
        """Multi-stage detection to avoid 1x1 pixel results; handles position/order variants."""
        strategies = self.config["coordinate_extraction"]["detection_strategies"]
        coordinate_cfg = self.config.get("coordinate_extraction", {})
        min_size = coordinate_cfg.get("min_size_filter", {"width": 200, "height": 150})
        html_filename = Path(html_file_path).name
        is_position_variant = "position_" in html_filename
        is_order_variant = html_filename.startswith("order_")

        if is_order_variant:
            coordinates = self.detect_by_order_variant(page, html_filename)
            if coordinates and self.validate_coordinates(coordinates, min_size):
                self.logger.info("order_variant strategy succeeded")
                return coordinates

        if self.second_product_index is not None:
            coordinates = self.detect_by_second_product_name(page)
            if coordinates and self.validate_coordinates(coordinates, min_size):
                self.logger.info("second_product name match succeeded")
                return coordinates

        if self.target_asin:
            coordinates = self.detect_by_asin(page, is_position_variant)
            if coordinates and self.validate_coordinates(coordinates, min_size):
                self.logger.info("target_asin (ASIN selector) succeeded")
                return coordinates

        coordinates = self.detect_by_product_name(page)
        if coordinates and self.validate_coordinates(coordinates, min_size):
            self.logger.info("Product name match succeeded")
            return coordinates

        # Generic text-based fallback for non-Amazon scenarios (e.g., NPR/Expedia)
        coordinates = self.detect_by_target_text_generic(page, is_position_variant, html_filename)
        if coordinates and self.validate_coordinates(coordinates, min_size):
            self.logger.info("target_text_generic succeeded")
            return coordinates

        for strategy in strategies:
            self.logger.info(f"Trying strategy: {strategy}")
            if strategy == "asin_selector":
                coordinates = self.detect_by_asin(page, is_position_variant)
            elif strategy == "card_container":
                coordinates = self.detect_by_card_container(page, is_position_variant)
            elif strategy == "parent_container":
                coordinates = self.detect_by_parent_container(page)
            elif strategy == "text_matching":
                coordinates = self.detect_by_text_matching(page)
            elif strategy == "placement_container":
                coordinates = self.detect_by_placement_container(page, html_filename)
            elif strategy == "card_size_variant":
                coordinates = self.detect_by_card_size_variant(page, html_filename)
            else:
                continue
            if coordinates and self.validate_coordinates(coordinates, min_size):
                self.logger.info(f"Strategy {strategy} succeeded")
                return coordinates
            self.logger.info(f"Strategy {strategy} failed or invalid coordinates")
        return None

    def detect_by_target_text_generic(self, page, is_position_variant: bool, html_filename: str) -> Optional[Dict]:
        """
        Generic detector for scenarios that do not expose Amazon-style identifiers.
        Finds elements containing target_text and scores nearby containers by card-like shape/classes.
        """
        if not self.target_text:
            return None
        try:
            result = page.evaluate("""
                ({ targetText, isPositionVariant }) => {
                    const text = (targetText || '').trim().toLowerCase();
                    if (!text) return null;

                    const placementIds = new Set([
                        'webarena-placement-header',
                        'webarena-placement-sidebar',
                        'webarena-placement-floating',
                        'webarena-placement-spotlight',
                        'webarena-placement-banner',
                        'npr-placement-header',
                        'npr-placement-banner'
                    ]);

                    const cardKeywords = [
                        'card', 'listing', 'property', 'hotel', 'article', 'bucketwrap',
                        'promocard', 'story', 'result', 'uitk', 'content'
                    ];

                    const allNodes = Array.from(document.querySelectorAll('*'));
                    const textNodes = allNodes.filter(el => {
                        const content = (el.textContent || '').trim().toLowerCase();
                        if (!content) return false;
                        if (!content.includes(text)) return false;
                        const tag = el.tagName.toLowerCase();
                        return tag !== 'html' && tag !== 'body';
                    });

                    if (!textNodes.length) return null;

                    let best = null;
                    let bestScore = -1;

                    const scoreNode = (node) => {
                        if (!node) return { score: -1, rect: null, inPlacement: false };
                        const rect = node.getBoundingClientRect();
                        if (!rect || rect.width <= 0 || rect.height <= 0) {
                            return { score: -1, rect: null, inPlacement: false };
                        }

                        // Ignore full-page wrappers and tiny fragments
                        if (rect.width > 1800 || rect.height > 3000 || rect.width < 80 || rect.height < 60) {
                            return { score: -1, rect: null, inPlacement: false };
                        }

                        const cls = (node.className || '').toString().toLowerCase();
                        const id = (node.id || '').toLowerCase();
                        const tag = node.tagName.toLowerCase();
                        let score = 0;

                        if (['article', 'section', 'li', 'div'].includes(tag)) score += 8;
                        if (cardKeywords.some(k => cls.includes(k) || id.includes(k))) score += 20;

                        // Prefer moderate "card-like" dimensions
                        if (rect.width >= 200 && rect.width <= 1200) score += 20;
                        if (rect.height >= 120 && rect.height <= 900) score += 20;

                        if (node.querySelector && node.querySelector('img')) score += 10;
                        if (node.querySelectorAll && node.querySelectorAll('a').length > 0) score += 5;

                        let cur = node;
                        let inPlacement = false;
                        while (cur && cur !== document.body) {
                            if (placementIds.has(cur.id)) {
                                inPlacement = true;
                                break;
                            }
                            cur = cur.parentElement;
                        }
                        if (inPlacement) score += 10;
                        if (isPositionVariant && inPlacement) score += 20;

                        return { score, rect, inPlacement };
                    };

                    for (const textNode of textNodes) {
                        let cur = textNode;
                        for (let depth = 0; depth < 8 && cur; depth++) {
                            const { score, rect } = scoreNode(cur);
                            if (score > bestScore) {
                                bestScore = score;
                                best = rect
                                    ? {
                                        x: Math.round(rect.left),
                                        y: Math.round(rect.top),
                                        width: Math.round(rect.width),
                                        height: Math.round(rect.height)
                                    }
                                    : null;
                            }
                            cur = cur.parentElement;
                        }
                    }

                    return best;
                }
            """, {"targetText": self.target_text, "isPositionVariant": is_position_variant})

            if not result:
                return None

            return {
                "x": int(result["x"]),
                "y": int(result["y"]),
                "width": int(result["width"]),
                "height": int(result["height"]),
                "method": "target_text_generic",
                "text_match": self.target_text[:80],
                "variant": html_filename,
            }
        except Exception as e:
            self.logger.error(f"target_text_generic failed: {e}")
            return None

    def detect_by_order_variant(self, page, html_filename: str) -> Optional[Dict]:
        """Handle order_first/middle/last variants: select card at given index by text or ASIN."""
        try:
            candidates = []
            container_nodes = page.query_selector_all('.puis-card-container, .s-result-item, .sg-col-20-of-24')
            for node in container_nodes:
                try:
                    text_content = (node.inner_text() or '').strip()
                except Exception:
                    text_content = ''
                if self.target_text.lower() in text_content.lower():
                    candidates.append(node)

            if not candidates:
                asin_selectors = [
                    f'.s-result-item[data-asin="{self.target_asin}"]',
                    f'.puis-card-container[data-asin="{self.target_asin}"]',
                    f'div[data-asin="{self.target_asin}"]',
                ]
                for selector in asin_selectors:
                    nodes = page.query_selector_all(selector)
                    if nodes:
                        candidates.extend(nodes)

            if not candidates:
                return None

            if "order_first" in html_filename:
                idx = 0
            elif "order_middle" in html_filename:
                idx = len(candidates) // 2
            elif "order_last" in html_filename:
                idx = len(candidates) - 1
            else:
                idx = 0

            element = candidates[idx]
            bbox = element.bounding_box()
            if not bbox:
                return None

            return {
                "x": int(bbox['x']),
                "y": int(bbox['y']),
                "width": int(bbox['width']),
                "height": int(bbox['height']),
                "method": "order_variant",
                "asin": self.target_asin,
                "index": idx,
            }
        except Exception as e:
            self.logger.error(f"order_variant detection failed: {e}")
        return None
    
    def detect_by_second_product_name(self, page) -> Optional[Dict]:
        """Find the Nth (second_product_index) item matching markers HP 15.6, 16GB DDR4 RAM, 1TB PCIe SSD."""
        try:
            markers = ["HP 15.6", "16GB DDR4 RAM", "1TB PCIe SSD"]
            all_items = page.query_selector_all("[data-asin]")
            self.logger.info(f"second_product: found {len(all_items)} items, selecting index {self.second_product_index + 1}")
            matches = []
            for item in all_items:
                try:
                    item_text = (item.inner_text() or '').strip()
                    if all(m in item_text for m in markers):
                        matches.append(item)
                except Exception:
                    continue
            if len(matches) <= self.second_product_index:
                self.logger.warning(f"second_product: only {len(matches)} matches, need index {self.second_product_index}")
                return None
            item = matches[self.second_product_index]
            card_container = item.evaluate_handle("""
                el => {
                    let current = el;
                    let maxDepth = 10;
                    while (current && maxDepth > 0) {
                        const className = current.getAttribute('class') || '';
                        const tagName = current.tagName.toLowerCase();
                        if (className.includes('puis-card-container') || className.includes('s-result-item') ||
                            className.includes('sg-col-20-of-24') || (tagName === 'div' && current.getAttribute('data-asin'))) {
                            const rect = current.getBoundingClientRect();
                            if (rect.width >= 200 && rect.width <= 1000 && rect.height >= 150) return current;
                        }
                        if (current.getAttribute('data-asin')) {
                            const rect = current.getBoundingClientRect();
                            if (rect.width >= 200 && rect.width <= 1000 && rect.height >= 150) return current;
                        }
                        current = current.parentElement;
                        maxDepth--;
                    }
                    return el;
                }
            """)
            if not card_container:
                return None
            bbox = card_container.bounding_box()
            if not bbox or bbox.get('width', 0) < 200 or bbox.get('height', 0) < 150:
                return None
            asin = item.get_attribute('data-asin') or ''
            return {
                "x": int(bbox['x']),
                "y": int(bbox['y']),
                "width": int(bbox['width']),
                "height": int(bbox['height']),
                "method": "second_product_name",
                "match_type": "HP 15.6 + 16GB DDR4 + 1TB PCIe SSD (index %d)" % self.second_product_index,
                "asin": asin,
            }
        except Exception as e:
            self.logger.error(f"second_product detection failed: {e}")
        return None
    
    def detect_by_product_name(self, page) -> Optional[Dict]:
        """Detect by product name: first try Overall Pick + $172.19, then HP 14 Laptop + Intel Celeron."""
        try:
            all_items = page.query_selector_all("[data-asin]")
            self.logger.info(f"Found {len(all_items)} items, matching by product name")
            for item in all_items:
                try:
                    item_text = item.inner_text().strip()
                    if 'Overall Pick' in item_text and '$172.19' in item_text:
                        card_container = item.evaluate_handle("""
                            el => {
                                let current = el;
                                let maxDepth = 10;
                                while (current && maxDepth > 0) {
                                    const className = current.getAttribute('class') || '';
                                    const tagName = current.tagName.toLowerCase();
                                    if (className.includes('puis-card-container') || 
                                        className.includes('s-result-item') ||
                                        className.includes('sg-col-20-of-24') ||
                                        (tagName === 'div' && current.getAttribute('data-asin'))) {
                                        
                                        const rect = current.getBoundingClientRect();
                                        if (rect.width >= 200 && rect.width <= 1000 && rect.height >= 150) {
                                            return current;
                                        }
                                    }
                                    if (current.getAttribute('data-asin')) {
                                        const rect = current.getBoundingClientRect();
                                        if (rect.width >= 200 && rect.width <= 1000 && rect.height >= 150) {
                                            return current;
                                        }
                                    }
                                    
                                    current = current.parentElement;
                                    maxDepth--;
                                }
                                return el;
                            }
                        """)
                        
                        if card_container:
                            bbox = card_container.bounding_box()
                            if bbox and bbox['width'] >= 200 and bbox['height'] >= 150:
                                coordinates = {
                                    "x": int(bbox['x']),
                                    "y": int(bbox['y']),
                                    "width": int(bbox['width']),
                                    "height": int(bbox['height']),
                                    "method": "product_name",
                                    "match_type": "Overall Pick + $172.19"
                                }
                                self.logger.info(f"Matched Overall Pick + $172.19: {coordinates}")
                                return coordinates
                except Exception as e:
                    self.logger.debug(f"Item check error: {e}")
                    continue
            
            self.logger.info("Overall Pick not found, trying HP 14 Laptop + Intel Celeron...")
            for item in all_items:
                try:
                    item_text = item.inner_text().strip()
                    if 'HP 14 Laptop' in item_text and 'Intel Celeron' in item_text:
                        card_container = item.evaluate_handle("""
                            el => {
                                let current = el;
                                let maxDepth = 10;
                                while (current && maxDepth > 0) {
                                    const className = current.getAttribute('class') || '';
                                    const tagName = current.tagName.toLowerCase();
                                    if (className.includes('puis-card-container') || 
                                        className.includes('s-result-item') ||
                                        className.includes('sg-col-20-of-24') ||
                                        (tagName === 'div' && current.getAttribute('data-asin'))) {
                                        
                                        const rect = current.getBoundingClientRect();
                                        if (rect.width >= 200 && rect.width <= 1000 && rect.height >= 150) {
                                            return current;
                                        }
                                    }
                                    if (current.getAttribute('data-asin')) {
                                        const rect = current.getBoundingClientRect();
                                        if (rect.width >= 200 && rect.width <= 1000 && rect.height >= 150) {
                                            return current;
                                        }
                                    }
                                    
                                    current = current.parentElement;
                                    maxDepth--;
                                }
                                return el;
                            }
                        """)
                        
                        if card_container:
                            bbox = card_container.bounding_box()
                            if bbox and bbox['width'] >= 200 and bbox['height'] >= 150:
                                coordinates = {
                                    "x": int(bbox['x']),
                                    "y": int(bbox['y']),
                                    "width": int(bbox['width']),
                                    "height": int(bbox['height']),
                                    "method": "product_name",
                                    "match_type": "HP 14 Laptop + Intel Celeron"
                                }
                                self.logger.info(f"Matched by product name: {coordinates}")
                                return coordinates
                except Exception as e:
                    self.logger.debug(f"Item check error: {e}")
                    continue
            
            return None
            
        except Exception as e:
            self.logger.error(f"Product name detection failed: {e}")
            return None
    
    def detect_by_asin(self, page, is_position_variant: bool = False) -> Optional[Dict]:
        """Detect by ASIN selector."""
        if not self.target_asin:
            return None
        try:
            precise_selectors = [
                f'div[data-asin="{self.target_asin}"]',
                f'.s-result-item[data-asin="{self.target_asin}"]',
                f'.puis-card-container[data-asin="{self.target_asin}"]',
                f'[data-asin="{self.target_asin}"]',
            ]
            
            if is_position_variant:
                placement_selectors = [
                    f"#webarena-placement-header [data-asin=\"{self.target_asin}\"]",
                    f"#webarena-placement-sidebar [data-asin=\"{self.target_asin}\"]",
                    f"#webarena-placement-floating [data-asin=\"{self.target_asin}\"]",
                    f"#webarena-placement-spotlight [data-asin=\"{self.target_asin}\"]",
                    f"#webarena-placement-banner [data-asin=\"{self.target_asin}\"]",
                ]
                selectors = placement_selectors + precise_selectors
            else:
                selectors = precise_selectors
            
            for selector in selectors:
                self.logger.info(f"Trying ASIN selector: {selector}")
                elements = page.query_selector_all(selector)
                
                for element in elements:
                    asin_attr = element.get_attribute("data-asin")
                    if asin_attr != self.target_asin:
                        continue
                    
                    tag_name = element.evaluate("el => el.tagName.toLowerCase()")
                    class_name = element.get_attribute("class") or ""
                    is_card_container = (
                        'puis-card-container' in class_name or
                        's-result-item' in class_name or
                        tag_name == 'div'
                    )
                    
                    if not is_card_container:
                        parent_container = element.evaluate_handle("""
                            el => {
                                let current = el.parentElement;
                                while (current) {
                                    const asin = current.getAttribute('data-asin');
                                    const className = current.getAttribute('class') || '';
                                    if (asin === arguments[0] && 
                                        (className.includes('puis-card-container') || 
                                         className.includes('s-result-item') ||
                                         current.tagName.toLowerCase() === 'div')) {
                                        return current;
                                    }
                                    current = current.parentElement;
                                }
                                return el;
                            }
                        """, self.target_asin)
                        
                        if parent_container:
                            element = parent_container
                    
                    bbox = element.bounding_box()
                    if bbox and bbox["width"] > 0 and bbox["height"] > 0:
                        if bbox["width"] < 50 or bbox["height"] < 50:
                            continue
                        
                        coordinates = {
                            "x": int(bbox['x']),
                            "y": int(bbox['y']),
                            "width": int(bbox['width']),
                            "height": int(bbox['height']),
                            "method": "asin_selector",
                            "selector": selector,
                            "asin": asin_attr
                        }
                        self.logger.info(f"ASIN selector succeeded: {coordinates}")
                        return coordinates
            
            return None
            
        except Exception as e:
            self.logger.error(f"ASIN selector failed: {e}")
            return None
    
    def detect_by_card_container(self, page, is_position_variant: bool = False) -> Optional[Dict]:
        """Detect by card container (optionally inside placement for position variants)."""
        try:
            if is_position_variant:
                placement_containers = page.query_selector_all("#webarena-placement-header, #webarena-placement-sidebar, #webarena-placement-floating, #webarena-placement-spotlight, #webarena-placement-banner")
                
                for placement in placement_containers:
                    card_containers = placement.query_selector_all('.puis-card-container, .s-result-item, .sg-col-20-of-24')
                    
                    for container in card_containers:
                        asin_attr = container.get_attribute('data-asin')
                        if asin_attr == self.target_asin:
                            bbox = container.bounding_box()
                            if bbox:
                                coordinates = {
                                    "x": int(bbox['x']),
                                    "y": int(bbox['y']),
                                    "width": int(bbox['width']),
                                    "height": int(bbox['height']),
                                    "method": "card_container",
                                    "asin": asin_attr,
                                    "placement": placement.get_attribute('id')
                                }
                                self.logger.info(f"Card container (placement) succeeded: {coordinates}")
                                return coordinates
            
            card_containers = page.query_selector_all(".puis-card-container, .s-result-item, .sg-col-20-of-24")
            for container in card_containers:
                asin_attr = container.get_attribute("data-asin")
                if asin_attr == self.target_asin:
                    bbox = container.bounding_box()
                    if bbox:
                        coordinates = {
                            "x": int(bbox['x']),
                            "y": int(bbox['y']),
                            "width": int(bbox['width']),
                            "height": int(bbox['height']),
                            "method": "card_container",
                            "asin": asin_attr
                        }
                        self.logger.info(f"Card container succeeded: {coordinates}")
                        return coordinates
            
            return None
            
        except Exception as e:
            self.logger.error(f"Card container detection failed: {e}")
            return None
    
    def detect_by_parent_container(self, page) -> Optional[Dict]:
        """Detect by parent container (within product card)."""
        try:
            card_containers = page.query_selector_all(f'[data-asin="{self.target_asin}"]')
            for container in card_containers:
                text_elements = container.query_selector_all("h2, h2 span, h2 a, .a-text-normal")
                for element in text_elements:
                    try:
                        text_content = element.inner_text().strip()
                        if text_content and self.target_text.lower() in text_content.lower():
                            bbox = container.bounding_box()
                            if bbox:
                                width = bbox["width"]
                                height = bbox["height"]
                                if width >= 200 and height >= 150 and width < 1000 and height < 2000:
                                    coordinates = {
                                        "x": int(bbox['x']),
                                        "y": int(bbox['y']),
                                        "width": int(bbox['width']),
                                        "height": int(bbox['height']),
                                        "method": "parent_container",
                                        "text_match": text_content[:50],
                                        "asin": self.target_asin
                                    }
                                    self.logger.info(f"Parent container succeeded: {coordinates}")
                                    return coordinates
                    except Exception:
                        continue
            
            text_elements = page.query_selector_all("h2, h2 span, h2 a")
            for element in text_elements:
                try:
                    text_content = element.inner_text().strip()
                    if text_content and self.target_text.lower() in text_content.lower():
                        parent_with_asin = element.evaluate_handle("""
                            el => {
                                let current = el.parentElement;
                                let maxDepth = 10;
                                while (current && current !== document.body && maxDepth > 0) {
                                    const asin = current.getAttribute('data-asin');
                                    if (asin === arguments[0]) {
                                        return current;
                                    }
                                    current = current.parentElement;
                                    maxDepth--;
                                }
                                return null;
                            }
                        """, self.target_asin)
                        
                        if parent_with_asin:
                            bbox = parent_with_asin.bounding_box()
                            if bbox:
                                width = bbox["width"]
                                height = bbox["height"]
                                if width >= 200 and height >= 150 and width < 1000 and height < 2000:
                                    if width < 1200 and height < 8000:
                                        coordinates = {
                                            "x": int(bbox['x']),
                                            "y": int(bbox['y']),
                                            "width": int(bbox['width']),
                                            "height": int(bbox['height']),
                                            "method": "parent_container",
                                            "text_match": text_content[:50],
                                            "asin": self.target_asin
                                        }
                                        self.logger.info(f"Parent container (traverse up) succeeded: {coordinates}")
                                        return coordinates
                except Exception:
                    continue
            
            return None
            
        except Exception as e:
            self.logger.error(f"Parent container detection failed: {e}")
            return None
    
    def detect_by_text_matching(self, page) -> Optional[Dict]:
        """Detect by text matching (within product card)."""
        try:
            card_containers = page.query_selector_all(f'[data-asin="{self.target_asin}"]')
            for container in card_containers:
                text_elements = container.query_selector_all("h2, h2 span, h2 a, .a-text-normal, span.a-text-normal")
                
                for element in text_elements:
                    try:
                        text_content = element.inner_text().strip()
                        if text_content and self.target_text.lower() in text_content.lower():
                            bbox = element.bounding_box()
                            if bbox:
                                viewport_size = page.viewport_size
                                if viewport_size and bbox["width"] > viewport_size["width"] * 0.9:
                                    self.logger.warning(f"Skipping full-page match: {bbox['width']}x{bbox['height']}")
                                    continue
                                if bbox["width"] < 200 or bbox["height"] < 50:
                                    parent_card = container
                                    parent_bbox = parent_card.bounding_box()
                                    if parent_bbox and parent_bbox['width'] >= 200 and parent_bbox['height'] >= 150:
                                        coordinates = {
                                            "x": int(parent_bbox['x']),
                                            "y": int(parent_bbox['y']),
                                            "width": int(parent_bbox['width']),
                                            "height": int(parent_bbox['height']),
                                            "method": "text_matching",
                                            "text_match": text_content[:50],
                                            "asin": self.target_asin
                                        }
                                        self.logger.info(f"Text matching (container) succeeded: {coordinates}")
                                        return coordinates
                                else:
                                    coordinates = {
                                        "x": int(bbox['x']),
                                        "y": int(bbox['y']),
                                        "width": int(bbox['width']),
                                        "height": int(bbox['height']),
                                        "method": "text_matching",
                                        "text_match": text_content[:50],
                                        "asin": self.target_asin
                                    }
                                    self.logger.info(f"Text matching succeeded: {coordinates}")
                                    return coordinates
                    except Exception:
                        continue
            
            text_elements = page.query_selector_all("h2, h2 span, h2 a")
            
            for element in text_elements:
                try:
                    text_content = element.inner_text().strip()
                    if text_content and self.target_text.lower() in text_content.lower():
                        parent_card = element.evaluate_handle("""
                            el => {
                                let current = el.parentElement;
                                let maxDepth = 15;
                                while (current && current !== document.body && maxDepth > 0) {
                                    const className = current.getAttribute('class') || '';
                                    const tagName = current.tagName.toLowerCase();
                                    if (className.includes('puis-card-container') || 
                                        className.includes('s-result-item') ||
                                        className.includes('sg-col-20-of-24') ||
                                        (tagName === 'div' && current.getAttribute('data-asin'))) {
                                        
                                        const rect = current.getBoundingClientRect();
                                        if (rect.width >= 200 && rect.width <= 1000 && rect.height >= 150) {
                                            return current;
                                        }
                                    }
                                    current = current.parentElement;
                                    maxDepth--;
                                }
                                return null;
                            }
                        """)
                        
                        if parent_card:
                            bbox = parent_card.bounding_box()
                            if bbox:
                                viewport_size = page.viewport_size
                                if viewport_size and bbox["width"] > viewport_size["width"] * 0.9:
                                    continue
                                if bbox["width"] >= 200 and bbox["height"] >= 150 and bbox["width"] < 1000 and bbox["height"] < 2000:
                                    coordinates = {
                                        "x": int(bbox['x']),
                                        "y": int(bbox['y']),
                                        "width": int(bbox['width']),
                                        "height": int(bbox['height']),
                                        "method": "text_matching",
                                        "text_match": text_content[:50],
                                        "asin": self.target_asin
                                    }
                                    self.logger.info(f"Text matching (parent) succeeded: {coordinates}")
                                    return coordinates
                except Exception as e:
                    self.logger.debug(f"Text match exception: {e}")
                    continue
            
            return None
            
        except Exception as e:
            self.logger.error(f"Text matching failed: {e}")
            return None
    
    def detect_by_placement_container(self, page, html_filename: str) -> Optional[Dict]:
        """Detect by placement container (for position variants)."""
        try:
            placement_id = None
            if "position_header" in html_filename:
                placement_id = "webarena-placement-header"
            elif "position_sidebar" in html_filename:
                placement_id = "webarena-placement-sidebar"
            elif "position_floating" in html_filename:
                placement_id = "webarena-placement-floating"
            elif "position_spotlight" in html_filename:
                placement_id = "webarena-placement-spotlight"
            elif "position_banner" in html_filename:
                placement_id = "webarena-placement-banner"
            
            if not placement_id:
                return None
            
            self.logger.info(f"Looking for placement container: {placement_id}")
            placement_element = page.query_selector(f"#{placement_id}")
            if not placement_element:
                self.logger.warning(f"Placement container not found: {placement_id}")
                return None
            target_element = placement_element.query_selector(f'div[data-asin="{self.target_asin}"]')
            if not target_element:
                target_element = placement_element.query_selector("*")
                for element in placement_element.query_selector_all('*'):
                    text_content = element.inner_text().strip()
                    if self.target_text.lower() in text_content.lower():
                        target_element = element
                        break
            
            if target_element:
                bbox = target_element.bounding_box()
                if bbox:
                    coordinates = {
                        "x": int(bbox['x']),
                        "y": int(bbox['y']),
                        "width": int(bbox['width']),
                        "height": int(bbox['height']),
                        "method": "placement_container",
                        "placement_id": placement_id,
                        "asin": self.target_asin
                    }
                    self.logger.info(f"Placement container succeeded: {coordinates}")
                    return coordinates
            
            return None
            
        except Exception as e:
            self.logger.error(f"Placement container detection failed: {e}")
            return None
    
    def detect_by_card_size_variant(self, page, html_filename: str) -> Optional[Dict]:
        """Detect by card_size variant (scale-transformed product card)."""
        try:
            if "card_size_scale" not in html_filename:
                return None
            self.logger.info(f"Detecting card_size variant: {html_filename}")
            selectors = [
                f'div[data-asin="{self.target_asin}"][style*="transform: scale"]',
                f'.s-result-item[data-asin="{self.target_asin}"][style*="transform: scale"]',
                f'.puis-card-container[data-asin="{self.target_asin}"][style*="transform: scale"]'
            ]
            
            for selector in selectors:
                self.logger.info(f"Trying card_size selector: {selector}")
                
                element = page.query_selector(selector)
                if element:
                    bbox = element.bounding_box()
                    if bbox:
                        coordinates = {
                            "x": int(bbox['x']),
                            "y": int(bbox['y']),
                            "width": int(bbox['width']),
                            "height": int(bbox['height']),
                            "method": "card_size_variant",
                            "selector": selector,
                            "asin": self.target_asin
                        }
                        self.logger.info(f"card_size variant succeeded: {coordinates}")
                        return coordinates
            
            text_elements = page.query_selector_all('*[style*="transform: scale"]')
            
            for element in text_elements:
                text_content = element.inner_text().strip()
                if self.target_text.lower() in text_content.lower():
                    bbox = element.bounding_box()
                    if bbox:
                        coordinates = {
                            "x": int(bbox['x']),
                            "y": int(bbox['y']),
                            "width": int(bbox['width']),
                            "height": int(bbox['height']),
                            "method": "card_size_variant",
                            "text_match": text_content[:50],
                            "asin": self.target_asin
                        }
                        self.logger.info(f"card_size variant (text match) succeeded: {coordinates}")
                        return coordinates
            
            return None
            
        except Exception as e:
            self.logger.error(f"card_size variant detection failed: {e}")
            return None
    
    def validate_coordinates(self, coordinates: Dict, min_size: Dict) -> bool:
        """Validate coordinates (avoid 1x1 and full-page detections)."""
        if not coordinates:
            return False
        width = coordinates.get("width", 0)
        height = coordinates.get("height", 0)
        method = coordinates.get("method", "")
        x = coordinates.get("x", 0)
        y = coordinates.get("y", 0)
        if width < min_size["width"] or height < min_size["height"]:
            self.logger.warning(f"Coordinates too small: {width}x{height} < {min_size['width']}x{min_size['height']}")
            return False
        if method == "placement_container":
            if width > 0 and height > 0 and width < 1300 and height < 2000:
                return True
            self.logger.warning(f"Invalid placement container coordinates: {coordinates}")
            return False
        if method == "card_size_variant":
            if width > 0 and height > 0 and width < 2000 and height < 3000:
                return True
            self.logger.warning(f"Invalid card_size variant coordinates: {coordinates}")
            return False
        if method == "product_name":
            if width > 0 and height > 0 and width < 1300 and height < 2000:
                return True
        if width >= 1200 and height >= 5000:
            self.logger.warning(f"Coordinates too large (full page?): {width}x{height}")
            return False
        if x == 0 and y == 0 and width >= 1000 and height >= 5000:
            self.logger.warning(f"Full-page-like coordinates: {width}x{height}")
            return False
        if x < 0 or y < 0:
            self.logger.warning(f"Coordinates out of range: {coordinates}")
            return False
        if width > 1100 or height > 2000:
            self.logger.warning(f"Coordinates outside card range: {width}x{height}")
            return False
        return True
    
    def calculate_all_coordinates(self) -> Dict[str, Any]:
        """Compute coordinates for all HTML variants. Returns result stats."""
        self.logger.info("Starting batch coordinate calculation")
        html_files = list(self.input_dir.glob("*.html"))
        if not html_files:
            self.logger.error(f"No HTML files in {self.input_dir}")
            return {"success": False, "error": "No HTML files found"}
        self.logger.info(f"Found {len(html_files)} HTML files")
        results = {
            "total_files": len(html_files),
            "successful": 0,
            "failed": 0,
            "coordinates": {},
            "errors": []
        }
        
        for html_file in tqdm(html_files, desc="Coordinates"):
            coordinates = self.extract_html_coordinates(str(html_file))
            
            if coordinates:
                results["successful"] += 1
                results["coordinates"][html_file.name] = coordinates
            else:
                results["failed"] += 1
                results["errors"].append({
                    "html_file": html_file.name,
                    "error": "Coordinate extraction failed"
                })
        
        self.save_coordinates(results["coordinates"])
        self.save_results(results)
        self.logger.info(f"Coordinate calculation done: {results['successful']}/{results['total_files']} succeeded")
        return results

    def save_coordinates(self, coordinates: Dict[str, Dict]) -> None:
        coordinates_file = self.output_dir / self.config["output_structure"]["coordinates_file"]
        with open(coordinates_file, "w", encoding="utf-8") as f:
            json.dump(coordinates, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Coordinates saved: {coordinates_file}")

    def save_results(self, results: Dict[str, Any]) -> None:
        results_file = self.output_dir / self.config["output_structure"]["results_file"]
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Results saved: {results_file}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python coordinate_calculator.py <config.json>")
        sys.exit(1)
    config_path = sys.argv[1]
    if not Path(config_path).exists():
        print(f"Config file not found: {config_path}")
        sys.exit(1)
    calculator = CoordinateCalculator(config_path)
    results = calculator.calculate_all_coordinates()
    if results.get("successful", 0) > 0:
        print(f"Coordinates done: {results['successful']}/{results['total_files']} succeeded")
        sys.exit(0)
    print("Coordinate calculation failed")
    sys.exit(1)

if __name__ == "__main__":
    main()
