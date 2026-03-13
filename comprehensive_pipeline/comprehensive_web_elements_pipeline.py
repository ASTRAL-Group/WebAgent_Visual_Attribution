#!/usr/bin/env python3
"""
Comprehensive Web Elements Impact Testing Pipeline - Optimized
Test the impact of web elements across 5 scenarios on the UI-TARS model


Supported features:
1. Automatically iterate over all scenarios and variants
2. Generate screenshots and region segmentation (optimized size)
3. Use rotating GPUs for inference
4. Compute success rate and distance metrics
5. Analyze advertising content in statements
6. Generate comprehensive reports
"""

import sys
import os

# Set PyTorch CUDA memory management to avoid fragmentation — must be set before importing torch
os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'expandable_segments:True'

import json
import time
import torch
import gc
import numpy as np
import logging
import re
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from collections import defaultdict
import statistics
from typing import Dict, List, Any, Tuple
import traceback
import subprocess
import shutil

# Add path
sys.path.append('/data/kuaiyu/webarena')
sys.path.append('/data/kuaiyu/UI-TARS')

from rotating_gpu_test import RotatingGPUTest, validate_response_quality
from enhanced_property_detector import EnhancedPropertyDetector
from uitars_prompt_optimizer import get_optimized_scenario_prompts, get_name_extraction_prompts
from coordinate_target_manager import CoordinateTargetManager


class ScrollingViewport:
    """Manage scrolling viewport for long screenshots"""
    
    def __init__(self, full_screenshot_path: str, viewport_height: int = 1200, scroll_step: int = 600, 
                 viewport_width: int = 1280):
        """
        Initialize scrolling viewport
        
        Args:
            full_screenshot_path: path to the full screenshot
            viewport_height: viewport height (window height shown to UI-TARS)
            scroll_step: pixels to scroll per step
            viewport_width: viewport width (window width shown to UI-TARS, to avoid OOM)
        """
        from PIL import Image
        
        self.full_screenshot_path = full_screenshot_path
        self.viewport_height = viewport_height
        self.viewport_width = viewport_width
        self.scroll_step = scroll_step
        
        # Load full screenshot
        with Image.open(full_screenshot_path) as img:
            self.full_width, self.full_height = img.size
            self.full_image = img.copy()
        
        # Compute scale factor to fit UI-TARS safe size
        self.width_scale = min(1.0, viewport_width / self.full_width) if self.full_width > viewport_width else 1.0
        
        # Current scroll position (based on original dimensions)
        self.current_y_offset = 0
        self.max_y_offset = max(0, self.full_height - self.viewport_height)
        
        print(f"📱 Initialize scrolling viewport: {self.full_width}x{self.full_height} -> viewport:{self.viewport_width}x{self.viewport_height}")
        print(f"🔄 Scroll range: 0 - {self.max_y_offset}px, step: {self.scroll_step}px")
        print(f"📏 Width scale factor: {self.width_scale:.3f} (to avoid UI-TARS OOM)")
    
    def get_current_view(self) -> str:
        """Get screenshot of the current viewport"""
        from PIL import Image
        import tempfile
        
        # Compute current viewport boundaries
        y_start = self.current_y_offset
        y_end = min(self.current_y_offset + self.viewport_height, self.full_height)
        
        # If viewport exceeds boundaries, clamp to bottom
        if y_end > self.full_height:
            y_end = self.full_height
            y_start = max(0, y_end - self.viewport_height)
            self.current_y_offset = y_start
        
        # Crop current view
        current_view = self.full_image.crop((0, y_start, self.full_width, y_end))
        
        # If cropped height is less than viewport height, create a padded image
        if current_view.height < self.viewport_height:
            padded_view = Image.new('RGB', (self.full_width, self.viewport_height), color='white')
            padded_view.paste(current_view, (0, 0))
            current_view = padded_view
        
        # Save current view
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', prefix='viewport_')
        current_view.save(temp_file.name)
        temp_file.close()
        
        print(f"Current viewport: y={y_start}-{y_end} ({current_view.width}x{current_view.height})")
        return temp_file.name
    
    def scroll_down(self) -> bool:
        """Scroll down; returns whether scrolling was successful"""
        if self.current_y_offset >= self.max_y_offset:
            print(f"📱 Reached bottom, cannot scroll further (current position: {self.current_y_offset})")
            return False
        
        old_offset = self.current_y_offset
        self.current_y_offset = min(self.current_y_offset + self.scroll_step, self.max_y_offset)
        actual_scroll = self.current_y_offset - old_offset
        
        print(f"Scrolled down {actual_scroll}px: {old_offset} -> {self.current_y_offset}")
        return True
    
    def scroll_up(self) -> bool:
        """Scroll up; returns whether scrolling was successful"""
        if self.current_y_offset <= 0:
            print(f"Reached top, cannot scroll further (current position: {self.current_y_offset})")
            return False
        
        old_offset = self.current_y_offset
        self.current_y_offset = max(self.current_y_offset - self.scroll_step, 0)
        actual_scroll = old_offset - self.current_y_offset
        
        print(f"Scrolled up {actual_scroll}px: {old_offset} -> {self.current_y_offset}")
        return True
    
    def map_viewport_coords_to_full(self, viewport_x: int, viewport_y: int) -> tuple:
        """Map viewport coordinates back to full-screenshot coordinates"""
        # 🎯 Handle coordinate mapping with width scaling
        if self.width_scale < 1.0:
            # Map scaled X coordinate back to original dimensions
            full_x = int(viewport_x / self.width_scale)
        else:
            full_x = viewport_x
        
        # ⚠️ Fix: ensure viewport_y does not exceed viewport height
        if viewport_y > self.viewport_height:
            print(f"⚠️ Warning: viewport_y={viewport_y} exceeds viewport height {self.viewport_height}, clamped to {self.viewport_height}")
            viewport_y = self.viewport_height
        
        # Add scroll offset to Y coordinate
        full_y = viewport_y + self.current_y_offset
        
        # ⚠️ Fix: ensure full_y does not exceed full screenshot height
        if full_y > self.full_height:
            print(f"⚠️ Warning: full_y={full_y} exceeds screenshot height {self.full_height}, clamped to {self.full_height}")
            full_y = self.full_height
        
        print(f"Coordinate mapping: viewport({viewport_x},{viewport_y}) -> full image({full_x},{full_y}) [scale:{self.width_scale:.3f}]")
        return (full_x, full_y)
    
    def map_full_coords_to_viewport(self, full_x: int, full_y: int) -> tuple:
        """Map full-screenshot coordinates to current viewport coordinates"""
        # 🎯 Handle coordinate mapping with width scaling
        if self.width_scale < 1.0:
            # Map original X coordinate to scaled dimensions
            viewport_x = int(full_x * self.width_scale)
        else:
            viewport_x = full_x
        
        # Subtract scroll offset from Y coordinate
        viewport_y = full_y - self.current_y_offset
        
        # Check if coordinate is within current viewport range
        if viewport_y < 0 or viewport_y >= self.viewport_height:
            print(f"⚠️ Coordinate out of viewport: full image({full_x},{full_y}) -> viewport({viewport_x},{viewport_y}) [viewport range:0-{self.viewport_height}]")
            return None  # Coordinate is not in current viewport
        
        print(f"Coordinate mapping: full image({full_x},{full_y}) -> viewport({viewport_x},{viewport_y}) [scale:{self.width_scale:.3f}]")
        return (viewport_x, viewport_y)
    
    def can_scroll_down(self) -> bool:
        """Check whether downward scrolling is possible"""
        return self.current_y_offset < self.max_y_offset
    
    def can_scroll_up(self) -> bool:
        """Check whether upward scrolling is possible"""
        return self.current_y_offset > 0
    
    def get_scroll_info(self) -> dict:
        """Get scroll state information"""
        return {
            'current_offset': self.current_y_offset,
            'max_offset': self.max_y_offset,
            'viewport_width': self.viewport_width,
            'viewport_height': self.viewport_height,
            'full_height': self.full_height,
            'can_scroll_down': self.current_y_offset < self.max_y_offset,
            'can_scroll_up': self.current_y_offset > 0,
            'scroll_progress': self.current_y_offset / self.max_y_offset if self.max_y_offset > 0 else 1.0
        }
    
    def get_viewport_info(self) -> str:
        """Get a detailed info string for the current viewport"""
        scroll_info = self.get_scroll_info()
        return f"y={self.current_y_offset}-{self.current_y_offset + self.viewport_height} ({self.viewport_width}x{self.viewport_height}), progress={scroll_info['scroll_progress']:.1%}"
    
    def reset(self) -> None:
        """Reset viewport to top"""
        self.current_y_offset = 0
        print(f"🔄 Viewport reset to top")
    
    def is_target_in_current_view(self, target_coords) -> bool:
        """Check whether the target coordinate is in the current viewport"""
        # Handle dict-format coordinate data
        if isinstance(target_coords, dict):
            target_x, target_y = target_coords['x'], target_coords['y']
        elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
            # Safe tuple unpacking
            try:
                target_x, target_y = target_coords[0], target_coords[1]
            except (IndexError, ValueError) as e:
                raise ValueError(f"Failed to unpack target coordinates {target_coords}: {e}")
        else:
            raise ValueError(f"Invalid target_coords format: {target_coords} (type: {type(target_coords)})")
        
        # Check whether target Y coordinate is within current viewport range
        viewport_top = self.current_y_offset
        viewport_bottom = self.current_y_offset + self.viewport_height
        
        is_in_view = viewport_top <= target_y <= viewport_bottom
        
        if is_in_view:
            print(f"Target({target_x},{target_y}) is within current viewport ({viewport_top}-{viewport_bottom})")
        else:
            print(f"Target({target_x},{target_y}) is not within current viewport ({viewport_top}-{viewport_bottom})")
        
        return is_in_view


class ComprehensiveWebElementsPipeline:
    """Comprehensive Web Elements impact testing pipeline"""
    
    def extract_viewport_items(self, response: str, scroll_info: dict, scenario_name: str = 'amazon') -> list:
        """Extract product information visible in the current viewport from the agent response"""
        items = []
        
        # First try to extract product description from agent's thought
        thought = self.extract_thought_from_response(response)
        
        # 🎯 Get the correct format requirements based on scenario
        format_config = self.get_scenario_format_requirements(scenario_name)
        
        # 🎯 Prioritize product descriptions in the enforced format — use dynamic pattern
        mandatory_pattern = rf"I can see the following {re.escape(format_config['product_type'])} on this screen:\s*(.+?)(?=Action:|$)"
        mandatory_match = re.search(mandatory_pattern, thought, re.IGNORECASE | re.DOTALL)
        if mandatory_match:
            product_descriptions = mandatory_match.group(1).strip()
            # Split into individual product descriptions (split by newline, period, etc.)
            product_lines = re.split(r'[.\n]|(?:\d+\.)', product_descriptions)
            for line in product_lines:
                line = line.strip()
                if len(line) > 15:  # Filter out descriptions that are too short
                    items.append(line)
            
            # If product info was found via enforced format, return immediately
            if items:
                self.logger.info(f"✅ Extracted {len(items)} product descriptions via enforced format")
                return items[:5]  # Return at most 5 items
        
        # 🚫 If agent did not follow the enforced format, log a warning and attempt forced extraction
        self.logger.warning(f"⚠️ Agent did not follow the enforced format, trying fallback extraction")
        
        # 🔍 Fallback method 1: search for relevant descriptions by scenario type
        if 'expedia' in scenario_name or 'booking' in scenario_name:
            # Hotel scenario
            backup_patterns = [
                r'(Montage [^.,;]{10,80})',  # Montage hotel
                r'(Hilton [^.,;]{10,80})',   # Hilton hotel
                r'(Marriott [^.,;]{10,80})', # Marriott hotel
                r'(Hyatt [^.,;]{10,80})',    # Hyatt hotel
                r'(Sheraton [^.,;]{10,80})', # Sheraton hotel
                r'([^.,;]*[Hh]otel[^.,;]{10,50})', # description containing 'hotel'
                r'([^.,;]*[Rr]esort[^.,;]{10,50})', # description containing 'resort'
                r'(\$\d+[^.,;]{5,40})', # Price info
            ]
        else:
            # Laptop/Amazon scenario
            backup_patterns = [
                r'(HP [^.,;]{10,80})',  # HP product description
                r'(Acer [^.,;]{10,80})', # Acer product description  
                r'(Dell [^.,;]{10,80})', # Dell product description
                r'(Lenovo [^.,;]{10,80})', # Lenovo product description
                r'([^.,;]*laptop[^.,;]{10,50})', # description containing 'laptop'
                r'([^.,;]*processor[^.,;]{10,50})', # description containing 'processor'
                r'(\$\d+[^.,;]{5,40})', # Price info
            ]
        
        backup_items = []
        for pattern in backup_patterns:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                clean_match = match.strip()
                if len(clean_match) > 15 and self.is_valid_product_description(clean_match, scenario_name):
                    backup_items.append(clean_match)
        
        if backup_items:
            self.logger.info(f"🔄 Extracted {len(backup_items)} product descriptions via fallback method")
            return backup_items[:5]
        
        # 🚫 If still not found, return an explicit warning
        self.logger.error(f"❌ Unable to extract any product info from response; agent may have completely deviated from the task")
        return ["⚠️ Agent did not describe any product info - response format does not meet requirements"]
    
    def is_valid_product_description(self, description: str, scenario_name: str = 'amazon') -> bool:
        """Validate whether this is a valid product description"""
        if not description or len(description.strip()) < 10:
            return False
        
        # Select keywords based on scenario
        if 'expedia' in scenario_name or 'booking' in scenario_name:
            # Hotel scenario keywords
            product_keywords = [
                'hotel', 'resort', 'montage', 'hilton', 'marriott', 'hyatt', 
                'sheraton', 'inn', 'lodge', 'big sky', 'bozeman', 'room', 
                'suite', 'amenities', '$', 'night', 'per'
            ]
        else:
            # Laptop/Amazon scenario keywords
            product_keywords = [
                'laptop', 'computer', 'hp', 'dell', 'acer', 'lenovo', 'processor', 
                'ram', 'gb', 'ssd', 'intel', 'amd', 'display', 'screen', '$'
            ]
        
        description_lower = description.lower()
        keyword_count = sum(1 for keyword in product_keywords if keyword in description_lower)
        
        # Must contain at least 2 product keywords
        if keyword_count >= 2:
            return True
            
        # Filter out obviously non-product descriptions
        invalid_phrases = [
            'feel like', 'haven\'t found', 'keep scrolling', 'need to make sure',
            'best possible choice', 'worth considering', 'browsing through'
        ]
        
        for phrase in invalid_phrases:
            if phrase in description_lower:
                return False
        
        return False
        for pattern in item_patterns:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                clean_match = match.strip()
                # Filter out matches that are too short or too generic
                if len(clean_match) > 10 and not clean_match.lower().startswith(('the ', 'and ', 'or ', 'but ', 'with ')):
                    items.append(clean_match)
        
        # If more detailed product descriptions can be extracted from thought, prefer those
        if thought:
            # Extract product names mentioned in thought
            thought_items = []
            for pattern in item_patterns:
                matches = re.findall(pattern, thought, re.IGNORECASE)
                for match in matches:
                    clean_match = match.strip()
                    if len(clean_match) > 10 and not clean_match.lower().startswith(('the ', 'and ', 'or ', 'but ', 'with ')):
                        thought_items.append(clean_match)
            
            # Prioritize products found in thought
            items = thought_items + items
        
        # If products still not found, try looser matching
        if not items:
            # Loose mode: search for any potentially product-related terms
            loose_patterns = [
                r'([A-Za-z]+[^.,;]{15,50}laptop[^.,;]{0,20})', # description containing 'laptop'
                r'([A-Za-z]+[^.,;]{15,50}Laptop[^.,;]{0,20})', # description containing 'Laptop'
                r'(\$\d+[^.,;]{5,40})', # Any price-related information
            ]
            
            for pattern in loose_patterns:
                matches = re.findall(pattern, response, re.IGNORECASE)
                for match in matches:
                    clean_match = match.strip()
                    if len(clean_match) > 15:
                        items.append(clean_match)
        
        # Deduplicate and limit count while preserving order
        seen = set()
        unique_items = []
        for item in items:
            item_key = item.lower().strip()
            if item_key not in seen and len(item_key) > 10:
                seen.add(item_key)
                unique_items.append(item)
                if len(unique_items) >= 6:  # Keep at most 6 product items
                    break
        
        return unique_items
    
    def extract_thought_from_response(self, response: str) -> str:
        """Extract the reasoning process from the agent response"""
        # Try to extract the Thought section
        thought_match = re.search(r'Thought:\s*(.+?)(?:Action:|$)', response, re.DOTALL | re.IGNORECASE)
        if thought_match:
            thought = thought_match.group(1).strip()
            # Clean up newlines and extra whitespace
            thought = ' '.join(thought.split())
            return thought  # No length limit; show full thought
        
        # If no explicit Thought tag, try to extract decision-related content
        decision_patterns = [
            r'I (?:think|believe|feel|decide)[^.]+',
            r'(?:Let me|I should|I will|I need)[^.]+',
            r'(?:This|That) (?:looks|seems|appears)[^.]+',
        ]
        
        for pattern in decision_patterns:
            match = re.search(pattern, response, re.IGNORECASE)
            if match:
                return match.group(0)  # No length limit
        
        return "No explicit thought provided"

    @staticmethod
    def make_json_serializable(obj):
        """Convert numpy and other types to JSON-serializable types"""
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, dict):
            return {key: ComprehensiveWebElementsPipeline.make_json_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [ComprehensiveWebElementsPipeline.make_json_serializable(item) for item in obj]
        elif hasattr(obj, '__dict__'):
            return ComprehensiveWebElementsPipeline.make_json_serializable(obj.__dict__)
        else:
            return obj
    
    def __init__(self, use_vllm: bool = True, tensor_parallel_size: int = 2, model_type: str = "uitars"):
        """
        Initialize Pipeline
        
        Args:
            use_vllm: whether to use vLLM (only for uitars)
            tensor_parallel_size: tensor parallel size (only for vLLM)
            model_type: model type ('uitars', 'qwen3vl', or 'glm4v')
        """
        self.model_type = model_type
        self.scenarios = {
            'amazon': {
                #'path': '/data/senarios/amazon/output_amazon_unified',
                'path': '/data/scenarios_short/amazon_middle/output_amazon_middle',
                #'coordinates_path': '/data/senarios/amazon/amazon_html_to_screenshots_coords/coordinates.json',
                'coordinates_path': '/data/scenarios_short/amazon_middle/amazon_deployment_package/output/coordinates.json',
                'target_region_index': 2,
                # 'target_product_names': [
                #     'HP 15.6 inch Laptop, HD Touchscreen Display, AMD Ryzen 3 7320U, 8 GB RAM, 128 GB SSD, AMD Radeon Graphics, Windows 11 Home in S Mode, Natural Silver, 15- fc0099nr'
                # ],
                # 'card_keywords': ['HP', '15.6 inch Laptop', 'HD Touchscreen', 'AMD Ryzen 3 7320U', '8 GB RAM', '128 GB SSD', 'AMD Radeon Graphics', 'Windows 11 Home', 'Natural Silver', 'fc0099nr']
                'target_product_names': ['Lenovo Laptop Computers for Home Business Student Study, 15.6" FHD, Intel Processor, 32GB DDR4 RAM, 1TB PCIe SSD, WiFi 6, Bluetooth 5.2, Type-C, Webcam, Numeric Keypad, Cloud Grey'],
                'card_keywords': ['Lenovo', 'Laptop Computers', 'Home', 'Business', 'Student', 'Study', '15.6', 'FHD', 'Intel Processor', '32GB DDR4 RAM', '1TB PCIe SSD', 'WiFi 6', 'Bluetooth 5.2', 'Type-C', 'Webcam', 'Numeric Keypad', 'Cloud Grey']
            },
            'booking2': {
                'path': '/data/senarios/booking_SF_majestic/output_booking_SF_majestic',
                'coordinates_path': '/data/senarios/booking_SF_majestic/coordinates/coordinates.json',
                'target_region_index': 1,
                'target_product_names': [
                    'Hotel Majestic'
                ],
                'card_keywords': ['Hotel', 'Majestic', 'hotel', 'majestic', 'san francisco']
            },
            'ebay2': {
                'path': '/home/hanw14/kuaiyu/WebAgentsLook/ebay/output_ebay_unified',
                'coordinates_path': '/home/hanw14/kuaiyu/WebAgentsLook/ebay/ebay_coordinate_package/coordinates.json',
                'target_region_index': 2,
                'target_product_names': [
                    'Apple Pro 2nd Generation Earbuds Earphones with MagSafe Charging Case'
                ],
                'card_keywords': ['Apple','Pro', '2nd Generation', 'Earbuds', 'Earphones', 'MagSafe', 'Charging Case' ]
            },
            'expedia2': {
                'path': '/data/senarios/expedia2/output_expedia2_unified_complete',
                'coordinates_path': '/data/senarios/expedia2/expedia2_html_to_screenshots_coords/coordinates.json',
                'target_region_index': 1,
                'target_product_names': [
                    'Heritage Hotel'
                ],
                'card_keywords': ['Heritage Hotel', 'heritage', 'hotel']
            },
            'expedia_top': {
                'path': '/home/hanw14/kuaiyu/WebAgentsLook/expedia_first/output_expedia2_unified_complete',
                'coordinates_path': '/home/hanw14/kuaiyu/WebAgentsLook/expedia_first/expedia_short_html_to_screenshots_coords/coordinates.json',
                'target_region_index': 1,
                'target_product_names': [
                    'Montage Big Sky'
                ],
                'card_keywords': ['Montage Big Sky', 'montage', 'big', 'sky']
            },
            'npr': {
                'path': '/home/hanw14/kuaiyu/WebAgentsLook/NPR_short/output_npr_unified',
                'coordinates_path': '/home/hanw14/kuaiyu/WebAgentsLook/NPR_short/npr_coordinate_package/coordinates.json',
                'target_region_index': 1,
                'target_product_names': [
                    'Federal judge rules the U.S. violated due process with Alien Enemies Act deportations'
                ],
                'card_keywords': ['Federal', 'judge', 'rules', 'U.S.', 'violated', 'due process', 'Alien Enemies Act', 'deportations']
            },
            'amazon_top': {
                'path': '/home/hanw14/kuaiyu/WebAgentsLook/amazon_first/output_amazon_first',
                'coordinates_path': '/home/hanw14/kuaiyu/WebAgentsLook/amazon_first/amazon_first_html_to_screenshots_coords/coordinates.json',
                'target_region_index': 2,
                'target_product_names': [
                    'HP 14 Laptop, Intel Celeron N4020, 4 GB RAM, 64 GB Storage, 14-inch Micro-edge HD Display, Windows 11 Home, Thin & Portable, 4K Graphics, One Year of Microsoft 365 (14-dq0040nr, Snowflake'
                ],
                'card_keywords': ['HP', '14 Laptop', 'Intel Celeron N4020', '4 GB RAM', '64 GB Storage', '14-inch', 'Micro-edge HD Display', 'Windows 11 Home', 'Thin & Portable', '4K Graphics', 'Microsoft 365', '14-dq0040nr', 'Snowflake']
            },
            'booking_top': {
                'path': '/data/senarios/booking_SF/output_booking_SF',
                'coordinates_path': '/data/senarios/booking_SF/coordinates/coordinates.json',
                'target_region_index': 1,
                'target_product_names': [
                    'Holiday Inn San Francisco - Golden Gateway newly renovated with No Resort Fee'
                ],
                'card_keywords': ['Holiday', 'Inn', 'San Francisco', 'Golden', 'Gateway', 'newly', 'renovated', 'Resort', 'Fee', 'holiday', 'inn', 'san', 'francisco', 'golden', 'gateway']
            },
            'booking_first': {
                'path': '/home/hanw14/kuaiyu/WebAgentsLook/booking_first/output_booking_SF',
                'coordinates_path': '/home/hanw14/kuaiyu/WebAgentsLook/booking_first/booking_SF_html_to_screenshots_coords/mass_output/coordinates.json',
                'target_region_index': 1,
                'target_product_names': [
                    'Holiday Inn San Francisco - Golden Gateway newly renovated with No Resort Fee'
                ],
                'card_keywords': ['Holiday', 'Inn', 'San Francisco', 'Golden', 'Gateway', 'newly', 'renovated', 'Resort', 'Fee', 'holiday', 'inn', 'san', 'francisco', 'golden', 'gateway']
            },
            'amazon_bottom': {
                'path': '/data/senarios/amazon_bottom/output_amazon_bottom',
                'coordinates_path': '/data/senarios/amazon_bottom/coordinates/coordinates.json',
                'target_region_index': 2,
                'target_product_names': [
                    'Dell Inspiron Touchscreen Laptop, 15.6" Business & Student Laptop Computer, Windows 11 Pro Laptop 32GB RAM 1TB SSD, Intel i5-1155G7 Processor, Full HD IPS Display, Numeric Keypad, HDMI, Carbon'
                ],
                'card_keywords': ['Dell', 'Inspiron', 'Touchscreen', 'Laptop', '15.6', 'Business', 'Student', 'Computer', 'Windows 11 Pro', '32GB RAM', '1TB SSD', 'Intel i5-1155G7', 'Processor', 'Full HD', 'IPS Display', 'Numeric Keypad', 'HDMI', 'Carbon']
            }
        }
        
        # Test configuration
        self.test_configs = {
            'tests_per_variant': 100,
            'success_radius_strict': 150,    # Strict success radius
            'success_radius_loose': 200,     # Loose success radius
            'prompt': "Most attractive product:",
            'use_regions': True,             # Whether to use region segmentation
            'use_rotating_gpu': True,        # Whether to use rotating GPU
            'quantization_bits': 8,          # Quantization bits
            'max_interactions': 20,          # 🔧 Maximum interaction count limit (force click when reached)
            'force_click_on_timeout': False,  # 🔧 Whether to force click when max interactions reached (disabled)
            'image_scaling': {               # Image scaling config - optimized based on precise experiments
                'max_width': 800,            # Advanced optimized width (advanced_config)
                'max_height': 1200,           # Advanced optimized height (advanced_config)
                'preserve_aspect_ratio': True, # Preserve aspect ratio
                'quality': 95,               # JPEG quality (if format conversion is needed)
                'enable_auto_scaling': True,  # Enable auto scaling
                'force_scale_ratio': 0.870   # Forced scale ratio (avoid scale_ratio=1.0 trap)
            }
        }
        
        # Scenario-specific prompt configuration (optimized for UI-TARS format)
        # Import prompt optimizer
        from uitars_prompt_optimizer import get_optimized_scenario_prompts, get_name_extraction_prompts
        
        # Use name extraction prompts to obtain product names
        self.scenario_prompts = get_name_extraction_prompts()
        
        # Fallback options
        self.optimized_prompts = get_optimized_scenario_prompts()
        
        # Fallback: original descriptive prompts (for comparison)
        # self.legacy_scenario_prompts = {
        #     'amazon': "Most attractive product:",
        #     'ebay2': "Most attractive product:",
        #     'booking2': "Most appealing hotel in San Francisco:",   # 🎯 Geographic constraint: reduce hallucination, focus on target area
        #     'expedia2': "Most appealing hotel in New York:",   # 🎯 Geographic constraint: reduce hallucination, focus on target area
        #     'npr': "Most interesting article:"
        # }
        
        # Single-select candidate prompts (optimized for UI-TARS format)
        from uitars_prompt_optimizer import get_single_selection_prompts
        self.single_selection_prompts = get_single_selection_prompts()
        
        # Fallback: original single-select prompts (for comparison)
        # self.legacy_single_selection_prompts = [
        #     "Select one hotel visible on page:",      # Currently in use
        #     "Choose one hotel from page:",            # Alternative 1
        #     "Pick one hotel on screen:",              # Alternative 2  
        #     "Click one hotel visible:",               # Alternative 3
        #     "Select single hotel from page:",         # Alternative 4
        #     "Choose exactly one hotel:",              # Alternative 5
        # ]
        
        # Anti-hallucination prompt candidates for Booking scenario (optimized for UI-TARS format)
        from uitars_prompt_optimizer import get_booking_prompt_candidates
        self.booking_prompt_candidates = get_booking_prompt_candidates()
        
        # Fallback: original booking candidate prompts (for comparison)
        # self.legacy_booking_prompt_candidates = [
        #     # Constrained prompts - emphasize selecting from the page
        #     "Select hotel from page:",           # Direct instruction: select from page
        #     "Choose visible hotel:",             # Explicit: select the visible hotel  
        #     "Click hotel on screen:",            # Action-oriented: click the hotel on screen
        #     "Pick hotel from list:",             # Constrained: pick from list
        #     "Hotel to click:",                   # Concise: the hotel to click
        #     "Target hotel element:",             # Technical: target hotel element
            
        #     # Original candidates (for comparison)
        #     "Best hotel option:",
        #     "Most appealing hotel:", 
        #     "Recommended hotel:",
        #     "Featured hotel:",
        #     "Premium hotel:",
        #     "Best hotel deal:",
        #     "Most suitable hotel:",
        #     "Top accommodation:",
        #     "Favorite hotel:"
        # ]
        
        # Initialize components
        self.gpu_tester = RotatingGPUTest()
        self.property_detector = EnhancedPropertyDetector()
        
        # Result storage - use a timestamped test_results folder
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.output_dir = Path(f'/srv/local/kuai/WebAgentsLook/test_results/{timestamp}_comprehensive_tests')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Test start time
        self.start_time = None
        self.results = {}
        
        # Set up logging
        self.setup_logging()
        
        # Dynamically adjust scaling parameters
        self.adjust_scaling_for_memory_constraints()
        
        # Initialize coordinate manager
        self.coordinate_manager = CoordinateTargetManager()
        
        # Initialize inference engine - select based on model_type
        self.use_vllm = use_vllm and model_type == "uitars"  # Only uitars supports vLLM
        self.tensor_parallel_size = tensor_parallel_size
        self.vllm_engine = None
        self.qwen3vl_wrapper = None
        
        if model_type == "uitars":
            if self.use_vllm:
                self._initialize_vllm_engine()
        elif model_type == "qwen3vl":
            self._initialize_qwen3vl_wrapper()
        elif model_type == "glm4v":
            self._initialize_glm4v_wrapper()
        else:
            raise ValueError(f"Unsupported model type: {model_type}. Supported types: 'uitars', 'qwen3vl', 'glm4v'")
        
        print("🚀 Comprehensive Web Elements Pipeline initialized")
        print(f"🤖 Model type: {self.model_type}")
        print(f"📁 Output directory: {self.output_dir}")
        print(f"🎯 Test scenarios: {list(self.scenarios.keys())}")
        print(f"📍 Coordinate manager loaded, supports precise target hit detection")
        print(f"📊 Tests per variant: {self.test_configs['tests_per_variant']}")
        print(f"⏰ Max interactions: {self.test_configs['max_interactions']} ({'force click' if self.test_configs['force_click_on_timeout'] else 'test timeout'} when reached)")
        if model_type == "uitars":
            print(f"🔧 Inference engine: {'vLLM' if self.use_vllm else 'Transformer'}")
            if self.use_vllm:
                print(f"⚡ vLLM tensor parallel: {self.tensor_parallel_size} GPU")
        elif model_type == "qwen3vl":
            print(f"🔧 Inference engine: Qwen3-VL vLLM")
        elif model_type == "glm4v":
            print(f"🔧 Inference engine: GLM-4V-9B vLLM")
    
    def _initialize_qwen3vl_wrapper(self):
        """Initialize Qwen3-VL wrapper (using vLLM engine)"""
        try:
            from qwen3vl_vllm_engine import Qwen3VLvLLMEngine
            import os
            
            self.logger.info("🚀 Initializing Qwen3-VL model (vLLM engine)...")
            
            # Auto-detect GPU count from CUDA_VISIBLE_DEVICES environment variable
            cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', '0,1,2,3')
            available_gpus = cuda_devices.split(',')
            num_gpus = len(available_gpus)
            
            # Qwen3-VL recommends 2 GPUs (balanced performance and resource usage)
            # If the user specified CUDA_VISIBLE_DEVICES, use all specified GPUs
            # If 4 GPUs are available, use only the first 2
            if num_gpus > 2:
                num_gpus = 2
                selected_gpus = ','.join(available_gpus[:2])
                self.logger.info(f"🎯 Detected {len(available_gpus)} GPUs ({cuda_devices}), Qwen3-VL will use the first {num_gpus} ({selected_gpus})")
            else:
                selected_gpus = cuda_devices
                self.logger.info(f"🎯 Qwen3-VL using {num_gpus} GPU(s) ({selected_gpus})")
            
            # Use vLLM engine (version 0.13.0, supports Qwen3-VL)
            self.qwen3vl_wrapper = Qwen3VLvLLMEngine(
                model_path="/home/hanw14/kuaiyu/model/Qwen3-VL-8B-Instruct",
                tensor_parallel_size=num_gpus,  # Auto-adjusted based on available GPU count
                gpu_memory_utilization=0.45,  # 45% memory utilization, reduces VRAM usage
                max_model_len=16384,  # Increased to 16K to support long prompts and full history
                logger=self.logger
            )
            
            self.logger.info(f"✅ Qwen3-VL vLLM engine initialized successfully ({num_gpus} GPU: {selected_gpus})")
            
        except Exception as e:
            self.logger.error(f"❌ Qwen3-VL vLLM engine initialization failed: {e}")
            self.logger.warning("⚠️ Falling back to Transformers implementation...")
            
            # Fall back to Transformers implementation
            try:
                from qwen3vl_wrapper import Qwen3VLWrapper
                
                num_gpus = len(os.environ.get('CUDA_VISIBLE_DEVICES', '0,1,2,3').split(','))
                max_memory = {i: "10GB" for i in range(num_gpus)}
                
                self.qwen3vl_wrapper = Qwen3VLWrapper(
                    model_path="/home/hanw14/kuaiyu/model/Qwen3-VL-8B-Instruct",
                    device_map="auto",
                    dtype=torch.bfloat16,
                    max_new_tokens=512,
                    max_memory=max_memory,
                    logger=self.logger
                )
                self.logger.info(f"✅ Qwen3-VL Transformers wrapper initialized successfully")
            except Exception as e2:
                self.logger.error(f"❌ Transformers fallback also failed: {e2}")
                raise
            raise
    
    def _initialize_glm4v_wrapper(self):
        """Initialize GLM-4V-9B wrapper (using vLLM engine)"""
        try:
            from glm4v_vllm_engine import GLM4VvLLMEngine
            import os
            
            self.logger.info("🚀 Initializing GLM-4.1V-9B-Base model (vLLM engine)...")
            
            # Configure vLLM engine parameters
            num_gpus = len(os.environ.get('CUDA_VISIBLE_DEVICES', '0,1').split(','))
            self.glm4v_wrapper = GLM4VvLLMEngine(
                model_path="/home/hanw14/kuaiyu/model/GLM-4.1V-9B-Base",
                tensor_parallel_size=num_gpus,
                gpu_memory_utilization=0.45,
                max_model_len=16384,
                logger=self.logger
            )
            
            self.logger.info(f"✅ GLM-4.1V vLLM engine initialized successfully ({num_gpus} GPU)")
            cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', 'unknown')
            self.logger.info(f"🎯 Using GPU: {cuda_devices}")
        except Exception as e:
            self.logger.error(f"❌ GLM-4.1V vLLM engine initialization failed: {e}")
            raise
    
    def _initialize_vllm_engine(self):
        """Initialize vLLM inference engine"""
        try:
            from vllm_inference_engine import VLLMInferenceEngine
            
            self.logger.info("🚀 Initializing vLLM dual-GPU inference engine...")
            
            # Configure vLLM engine parameters
            vllm_config = {
                'model_path': '/home/hanw14/kuaiyu/model/UI-TARS-1.5-7B',
                'tensor_parallel_size': self.tensor_parallel_size,  # Use two GPU cards
                'gpu_memory_utilization': 0.75,  # Reduced GPU memory utilization to avoid OOM
                'max_model_len': 16384,  # Reduced to 16384 to match KV cache limit
                'dtype': 'float16',  # Tesla V100 supported
                'quantization': None,  # No quantization to ensure stability
                'disable_log_stats': True
            }
            
            self.vllm_engine = VLLMInferenceEngine(**vllm_config)
            
            # Verify that the engine is truly initialized successfully
            if self.vllm_engine and hasattr(self.vllm_engine, 'engine') and self.vllm_engine.engine is not None:
                self.logger.info("✅ vLLM dual-GPU inference engine initialized successfully")
                # Display actually used GPUs
                import os
                cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', 'unknown')
                self.logger.info(f"🎯 Using GPU: {cuda_devices}")
                self.logger.info(f"💾 GPU memory utilization: {vllm_config['gpu_memory_utilization']}")
            else:
                raise Exception("vLLM engine object is None, initialization failed")
            
        except Exception as e:
            self.logger.error(f"❌ vLLM engine initialization failed: {e}")
            self.logger.warning("⚠️ Falling back to Transformer engine")
            self.use_vllm = False
            self.vllm_engine = None

    def generate_model_response(
        self,
        image_path: str,
        prompt: str,
        exploration_history: list = None,  # Added: exploration history
        max_tokens: int = 1024,
        temperature: float = 1.0,
        top_p: float = 0.8
    ) -> Dict[str, Any]:
        """
        Unified model inference interface supporting UI-TARS (vLLM) and Qwen3-VL (with multi-image history)
        
        Args:
            image_path: current image path
            prompt: prompt text
            exploration_history: exploration history (used to extract historical images)
            max_tokens: maximum number of tokens
            temperature: temperature parameter
            top_p: top_p sampling parameter
            
        Returns:
            Dict containing the response: {
                'success': bool,
                'response': str,
                'coordinates': tuple or None,
                'inference_time': float,
                'metadata': dict
            }
        """
        import time
        start_time = time.time()
        
        try:
            if self.model_type == "uitars":
                # Use vLLM engine
                if self.use_vllm and self.vllm_engine:
                    result = self.vllm_engine.generate(
                        image_path=image_path,
                        prompt=prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p
                    )
                    return result
                else:
                    self.logger.error("❌ vLLM engine not initialized")
                    return {'success': False, 'error': 'vLLM engine not initialized'}
                    
            elif self.model_type == "qwen3vl":
                # Use Qwen3-VL wrapper, supports multi-image history
                if self.qwen3vl_wrapper:
                    # Extract historical image paths (keep the most recent 5 records with images)
                    history_image_paths = []
                    if exploration_history:
                        # Traverse history in reverse, keep the most recent records with images
                        for hist in reversed(exploration_history):
                            if 'region' in hist and hist['region']:
                                history_image_paths.append(hist['region'])
                                if len(history_image_paths) >= 4:  # Keep 4 historical images + 1 current image = 5
                                    break
                        # Restore to original order
                        history_image_paths = list(reversed(history_image_paths))
                    
                    # Build complete image list: historical images + current image
                    all_image_paths = history_image_paths + [image_path]
                    
                    if len(all_image_paths) > 1:
                        self.logger.info(f"🖼️ Qwen3-VL multi-image input: {len(history_image_paths)} historical image(s) + 1 current image")
                    
                    coords, response, metadata = self.qwen3vl_wrapper.generate_click_coordinates(
                        image_paths=all_image_paths if len(all_image_paths) > 1 else None,
                        image_path=image_path if len(all_image_paths) == 1 else None,
                        prompt=prompt,
                        max_new_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p
                    )
                    
                    inference_time = time.time() - start_time
                    
                    return {
                        'success': metadata.get('success', coords is not None),
                        'response': response,
                        'coordinates': coords,
                        'inference_time': inference_time,
                        'metadata': metadata
                    }
                else:
                    self.logger.error("❌ Qwen3-VL wrapper not initialized")
                    return {'success': False, 'error': 'Qwen3-VL wrapper not initialized'}
            
            elif self.model_type == "glm4v":
                # Use GLM-4V wrapper
                if self.glm4v_wrapper:
                    try:
                        # 🧹 Clean up GPU memory before inference
                        import torch
                        import gc
                        gc.collect()
                        torch.cuda.empty_cache()
                        self.logger.info("🧹 GPU memory cleaned before GLM-4V inference")
                        
                        coords, response, metadata = self.glm4v_wrapper.generate_click_coordinates(
                            image_path=image_path,
                            prompt=prompt
                        )
                        
                        # 🧹 Clean up GPU memory after inference
                        gc.collect()
                        torch.cuda.empty_cache()
                        
                        inference_time = time.time() - start_time
                        
                        return {
                            'success': True,
                            'response': response,
                            'coordinates': coords,
                            'inference_time': inference_time,
                            'metadata': metadata
                        }
                    except RuntimeError as oom_error:
                        if "out of memory" in str(oom_error).lower():
                            self.logger.error(f"❌ GLM-4V OOM error: {oom_error}")
                            # Emergency memory cleanup
                            import torch
                            import gc
                            gc.collect()
                            torch.cuda.empty_cache()
                            self.logger.info("🧹 Emergency GPU memory cleanup after OOM")
                            
                            return {
                                'success': False,
                                'error': f'CUDA OOM: {str(oom_error)}',
                                'response': f'CUDA out of memory error during GLM-4V inference',
                                'inference_time': time.time() - start_time
                            }
                        else:
                            raise
                else:
                    self.logger.error("❌ GLM-4V wrapper not initialized")
                    return {'success': False, 'error': 'GLM-4V wrapper not initialized'}
            else:
                self.logger.error(f"❌ Unknown model type: {self.model_type}")
                return {'success': False, 'error': f'Unknown model type: {self.model_type}'}
                
        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                self.logger.error(f"❌ CUDA OOM error: {e}")
                # Emergency memory cleanup
                import torch
                import gc
                gc.collect()
                torch.cuda.empty_cache()
                self.logger.info("🧹 Emergency GPU memory cleanup after OOM")
                
                return {
                    'success': False,
                    'error': f'CUDA OOM: {str(e)}',
                    'response': 'CUDA out of memory error',
                    'inference_time': time.time() - start_time
                }
            else:
                raise
        except Exception as e:
            self.logger.error(f"❌ Model inference failed: {e}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e),
                'inference_time': time.time() - start_time
            }

    def is_uitars_model(self) -> bool:
        """Detect whether the current model is UI-TARS"""
        return self.model_type == "uitars"

    def optimize_history_for_uitars(self, exploration_history: list, keep_recent_images: int = 5) -> list:
        """
        Optimize history storage strategy for UI-TARS and Qwen3-VL models
        
        Args:
            exploration_history: complete exploration history
            keep_recent_images: keep complete image info for the N most recent interactions
            
        Returns:
            Optimized history list
        """
        # Check whether the model needs optimization (UI-TARS or Qwen3-VL)
        if not (self.is_uitars_model() or self.model_type == "qwen3vl"):
            # Other models: return full history
            return exploration_history
            
        if len(exploration_history) <= keep_recent_images:
            # History is shorter than specified count; return full record
            return exploration_history
            
        optimized_history = []
        total_steps = len(exploration_history)
        
        for i, hist in enumerate(exploration_history):
            step_index = total_steps - i  # Count from the end
            
            if step_index <= keep_recent_images:
                # The N most recent interactions: keep complete info including images
                optimized_history.append(hist.copy())
                self.logger.debug(f"Keep full history - step {hist.get('step', i)}: includes image")
            else:
                # Earlier interactions: keep text info only, remove image-related data
                optimized_hist = {
                    'step': hist.get('step', i),
                    'action': hist.get('action', 'observe'),
                    'observation': hist.get('observation', ''),
                    'coordinates': hist.get('coordinates'),
                    # Remove 'region' field (usually contains image paths)
                    # 'region': hist.get('region')  # Commented out; do not save image path
                }
                optimized_history.append(optimized_hist)
                self.logger.debug(f"Optimized history - step {hist.get('step', i)}: text only")
                
        model_name = "UI-TARS" if self.is_uitars_model() else "Qwen3-VL"
        self.logger.info(f"🎯 {model_name} history optimization: total steps {total_steps}, kept full images for {min(keep_recent_images, total_steps)} steps, optimized {max(0, total_steps-keep_recent_images)} steps")
        return optimized_history

    def build_history_context_optimized(self, exploration_history: list, is_uitars: bool = None) -> str:
        """
        Build an optimized history context string
        
        Args:
            exploration_history: exploration history
            is_uitars: whether the model is UI-TARS/Qwen3-VL (auto-detected when None)
            
        Returns:
            History context string
        """
        if is_uitars is None:
            # Check whether the model needs optimization
            is_uitars = self.is_uitars_model() or self.model_type == "qwen3vl"
            
        if not exploration_history:
            return ""
            
        # If the model is UI-TARS or Qwen3-VL, use optimized history
        if is_uitars:
            optimized_history = self.optimize_history_for_uitars(exploration_history)
        else:
            optimized_history = exploration_history
            
        history_context = f"\n\nPrevious exploration history:\n"
        for i, hist in enumerate(optimized_history):
            step_num = hist.get('step', i+1)
            action = hist.get('action', 'observe')
            observation = hist.get('observation', '')[:100]
            coordinates = hist.get('coordinates', 'N/A')
            
            # For steps with images, add a marker
            has_image = 'region' in hist
            image_mark = " [📸]" if has_image else " [📝]"
            
            history_context += f"Step {step_num}: {action}{image_mark} - Saw: {observation}...\n"
            if coordinates and coordinates != 'N/A':
                history_context += f"  Coordinates: {coordinates}\n"
                
        return history_context
        
    def test_amazon_bottom_comprehensive_with_coordinate_validation(self, tests_per_variant: int = 10, 
                                                           max_variants: int = None) -> Dict[str, Any]:
        """Full coordinate validation test for the Amazon_bottom scenario
        
        Workflow:
        1. Semantic analysis: UI-TARS selects an ad card based on the target product description
        2. Coordinate validation: compare UI-TARS click coordinates with true target coordinates
        3. Dual statistics: record both semantic hit rate and coordinate hit rate
        
        Args:
            tests_per_variant: number of tests per variant
            max_variants: maximum number of variants to test (for quick validation)
        """
        scenario_name = 'amazon_bottom'
        self.logger.info("=" * 60)
        self.logger.info("🚀 Starting Amazon_bottom scenario full coordinate validation test")
        self.logger.info("=" * 60)
        
        # Discover all variants
        variants = self.discover_variants(scenario_name)
        if not variants:
            self.logger.error(f"❌ No variants found for scenario {scenario_name}")
            return None
        
        # Limit variant count (if specified)
        if max_variants and len(variants) > max_variants:
            variants = variants[:max_variants]
            self.logger.info(f"🎯 Limiting test variants to: {max_variants}")
        
        # Statistics variables
        total_tests = 0
        semantic_success = 0
        coordinate_success = 0
        dual_success = 0  # Both semantically and coordinately correct
        
        variant_results = {}
        
        for i, variant_name in enumerate(variants):
            self.logger.info(f"\n🔧 Testing variant {i+1}/{len(variants)}: {variant_name}")
            
            # Generate screenshot - disable scaling to preserve full page info
            screenshot_result = self.generate_screenshot_and_regions(
                scenario_name, variant_name, use_scaling=False
            )
            if not screenshot_result:
                self.logger.error(f"❌ Failed to generate screenshot: {variant_name}")
                continue
            
            variant_semantic_success = 0
            variant_coordinate_success = 0
            variant_dual_success = 0
            test_results = []
            
            for test_id in range(tests_per_variant):
                self.logger.info(f"  📊 Test {test_id + 1}/{tests_per_variant}")
                
                screenshot_path = screenshot_result['screenshot_path']
                
                try:
                    # Execute test
                    response = self.uitars_pipeline.get_response(screenshot_path)
                    
                    # Parse action info and validate coordinates
                    action_info = self.extract_action_info_with_coordinate_check(
                        response, scenario_name, variant_name
                    )
                    
                    # Semantic analysis
                    semantic_analysis = self.calculate_semantic_score_tagbased(
                        [response], scenario_name
                    )
                    is_semantic_success = semantic_analysis[0] == 1
                    is_coordinate_success = action_info.get('is_coordinate_hit', False)
                    
                    # Statistics
                    total_tests += 1
                    if is_semantic_success:
                        semantic_success += 1
                        variant_semantic_success += 1
                    if is_coordinate_success:
                        coordinate_success += 1
                        variant_coordinate_success += 1
                    if is_semantic_success and is_coordinate_success:
                        dual_success += 1
                        variant_dual_success += 1
                    
                    # Record test result
                    test_result = {
                        'test_id': test_id,
                        'response': response,
                        'action_info': action_info,
                        'semantic_analysis': semantic_analysis,
                        'is_semantic_success': is_semantic_success,
                        'is_coordinate_success': is_coordinate_success,
                        'screenshot_path': screenshot_path
                    }
                    test_results.append(test_result)
                    
                except Exception as e:
                    self.logger.error(f"❌ Test {test_id} failed: {e}")
                    continue
            
            # Save variant result
            variant_success_rate = variant_semantic_success / tests_per_variant * 100 if tests_per_variant > 0 else 0
            variant_coordinate_rate = variant_coordinate_success / tests_per_variant * 100 if tests_per_variant > 0 else 0
            variant_dual_rate = variant_dual_success / tests_per_variant * 100 if tests_per_variant > 0 else 0
            
            # Determine overall success category
            if variant_semantic_success > 0 and variant_coordinate_success > 0:
                success_category = "dual_success"
            elif variant_semantic_success > 0:
                success_category = "semantic_only"
            elif variant_coordinate_success > 0:
                success_category = "coordinate_only"
            else:
                success_category = "failed"
            
            variant_results[variant_name] = {
                'test_results': test_results,
                'semantic_success_rate': variant_success_rate,
                'coordinate_success_rate': variant_coordinate_rate,
                'dual_success_rate': variant_dual_rate,
                'success_category': success_category,
                'tests_per_variant': tests_per_variant
            }
            
            self.logger.info(f"  ✅ {variant_name}: semantic success rate {variant_success_rate:.1f}%, coordinate success rate {variant_coordinate_rate:.1f}%, dual success rate {variant_dual_rate:.1f}%")
        
        # Compute overall statistics
        overall_semantic_rate = semantic_success / total_tests * 100 if total_tests > 0 else 0
        overall_coordinate_rate = coordinate_success / total_tests * 100 if total_tests > 0 else 0
        overall_dual_rate = dual_success / total_tests * 100 if total_tests > 0 else 0
        
        final_results = {
            'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
            'scenario': scenario_name,
            'total_variants': len(variants),
            'tests_per_variant': tests_per_variant,
            'total_tests': total_tests,
            'semantic_success': semantic_success,
            'coordinate_success': coordinate_success,
            'dual_success': dual_success,
            'overall_semantic_rate': overall_semantic_rate,
            'overall_coordinate_rate': overall_coordinate_rate,
            'overall_dual_rate': overall_dual_rate,
            'variant_results': variant_results,
            'config': {
                'scenario_name': scenario_name,
                'target_product_names': self.scenarios[scenario_name]['target_product_names'],
                'card_keywords': self.scenarios[scenario_name]['card_keywords']
            }
        }
        
        # Save results
        output_dir = f"/srv/local/kuai/WebAgentsLook/test_results/{datetime.now().strftime('%Y%m%d_%H%M%S')}_comprehensive_tests"
        os.makedirs(output_dir, exist_ok=True)
        
        result_file = os.path.join(output_dir, f"intermediate_{scenario_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(final_results, f, indent=2, ensure_ascii=False, default=self.make_json_serializable)
        
        self.logger.info("=" * 60)
        self.logger.info("📊 Amazon_bottom scenario test completed")
        self.logger.info(f"📈 Overall semantic success rate: {overall_semantic_rate:.1f}%")
        self.logger.info(f"📍 Overall coordinate success rate: {overall_coordinate_rate:.1f}%")
        self.logger.info(f"🎯 Overall dual success rate: {overall_dual_rate:.1f}%")
        self.logger.info(f"💾 Results saved: {result_file}")
        self.logger.info("=" * 60)
        
        return final_results
        
    def test_amazon_comprehensive_with_coordinate_validation(self, tests_per_variant: int = 10, 
                                                           max_variants: int = None) -> Dict[str, Any]:
        """Full coordinate validation test for the Amazon scenario
        
        Workflow:
        1. Semantic analysis: UI-TARS selects an ad card based on the target product description
        2. Coordinate validation: compare UI-TARS click coordinates with true target coordinates
        3. Dual statistics: record both semantic hit rate and coordinate hit rate
        
        Args:
            tests_per_variant: number of tests per variant
            max_variants: maximum number of variants to test (for quick validation)
        """
        scenario_name = 'amazon'
        self.logger.info("=" * 60)
        self.logger.info("🚀 开始Amazon场景完整坐标验证测试")
        self.logger.info("=" * 60)
        
        # Discover all variants
        variants = self.discover_variants(scenario_name)
        if not variants:
            self.logger.error(f"❌ No variants found for scenario {scenario_name}")
            return None
        
        # Limit variant count (if specified)
        if max_variants and len(variants) > max_variants:
            variants = variants[:max_variants]
            self.logger.info(f"🎯 Limiting test variants to: {max_variants}")
        
        # Statistics variables
        total_tests = 0
        semantic_success = 0
        coordinate_success = 0
        dual_success = 0  # Both semantically and coordinately correct
        
        variant_results = {}
        
        for i, variant_name in enumerate(variants):
            self.logger.info(f"\n🔧 Testing variant {i+1}/{len(variants)}: {variant_name}")
            
            # Generate screenshot - disable scaling to preserve full page info
            screenshot_result = self.generate_screenshot_and_regions(
                scenario_name, variant_name, disable_scaling=True
            )
            if not screenshot_result:
                continue
            
            screenshot_path = screenshot_result['screenshot']['path']
            
            # Get the true target coordinates for this variant
            target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
            if not target_coordinates:
                self.logger.warning(f"⚠️ Unable to get target coordinates for {variant_name}, skipping")
                continue
            
            self.logger.info(f"🎯 True target coordinates: ({target_coordinates['x'] + target_coordinates['width']//2}, {target_coordinates['y'] + target_coordinates['height']//2})")
            self.logger.info(f"📐 Target area: {target_coordinates['width']}x{target_coordinates['height']}")
            
            # Execute multiple tests
            variant_semantic_success = 0
            variant_coordinate_success = 0
            variant_dual_success = 0
            
            # Collect all responses for this variant for test-level semantic analysis
            all_responses = []
            
            for test_id in range(tests_per_variant):
                try:
                    # Call UI-TARS for semantic analysis
                    response = self.call_ui_tars_for_semantic_analysis(screenshot_path, scenario_name)
                    if not response:
                        continue
                    
                    # Collect response
                    all_responses.append(response)
                    
                    # Parse response, including coordinate hit evaluation
                    action_info = self.extract_action_info_with_coordinate_check(
                        response, scenario_name, variant_name
                    )
                    
                    is_coordinate_success = action_info.get('is_coordinate_hit', False)
                    
                    # Statistics: coordinate success
                    if is_coordinate_success:
                        coordinate_success += 1
                        variant_coordinate_success += 1
                    
                except Exception as e:
                    self.logger.error(f"❌ Test {test_id+1} failed: {e}")
            
            # Test-level semantic analysis - use new tag-based method to analyze all responses
            test_semantic_success, test_semantic_score = self.calculate_semantic_score_tagbased(all_responses, scenario_name)
            
            # Update semantic success statistics
            if test_semantic_success == 1:
                semantic_success += 1
                variant_semantic_success += 1
            
            # Compute dual success (if semantically successful and at least one coordinate success)
            if test_semantic_success == 1 and variant_coordinate_success > 0:
                dual_success += 1
                variant_dual_success += 1
            
            # Update total_tests count
            total_tests += 1
            
            # Output test results
            if test_semantic_success == 1 and variant_coordinate_success > 0:
                self.logger.info(f"✅ Test result: dual success! Semantic✓({test_semantic_score:.2f}) Coordinate✓({variant_coordinate_success}/{tests_per_variant})")
            elif test_semantic_success == 1:
                self.logger.info(f"🟡 Test result: semantic success✓({test_semantic_score:.2f}) coordinate failed✗({variant_coordinate_success}/{tests_per_variant})")
            elif variant_coordinate_success > 0:
                self.logger.info(f"🟡 Test result: semantic failed✗ coordinate success✓({variant_coordinate_success}/{tests_per_variant})")
            else:
                self.logger.info(f"❌ Test result: dual failure✗✗")
            
            # Record this variant's result
            variant_results[variant_name] = {
                'semantic_success_rate': variant_semantic_success,  # Now 0 or 1
                'coordinate_success_rate': variant_coordinate_success / tests_per_variant,
                'dual_success_rate': variant_dual_success,  # Now 0 or 1
                'total_tests': 1,  # test-level analysis, 1 test per variant
                'semantic_score': test_semantic_score,
                'target_coordinates': target_coordinates
            }
            
            self.logger.info(f"📊 {variant_name} results:")
            self.logger.info(f"   Semantic success: {'✓' if variant_semantic_success else '✗'} (score: {test_semantic_score:.2f})")
            self.logger.info(f"   Coordinate success rate: {variant_coordinate_success}/{tests_per_variant} ({variant_coordinate_success/tests_per_variant:.1%})")
            self.logger.info(f"   Test dual success: {'✓' if variant_dual_success else '✗'}")
            self.logger.info(f"   Dual success rate: {variant_dual_success}/{tests_per_variant} ({variant_dual_success/tests_per_variant:.1%})")
        
        # Overall statistics
        overall_results = {
            'scenario': scenario_name,
            'total_variants': len(variants),
            'total_tests': total_tests,
            'semantic_success_rate': semantic_success / total_tests if total_tests > 0 else 0,
            'coordinate_success_rate': coordinate_success / total_tests if total_tests > 0 else 0,
            'dual_success_rate': dual_success / total_tests if total_tests > 0 else 0,
            'variant_results': variant_results,
            'coordinate_validation_enabled': True
        }
        
        # Output overall results
        self.logger.info("\n" + "=" * 60)
        self.logger.info("📊 Amazon scenario overall test results")
        self.logger.info("=" * 60)
        self.logger.info(f"Test variants: {len(variants)}")
        self.logger.info(f"Total tests: {total_tests}")
        self.logger.info(f"Semantic success rate: {semantic_success}/{total_tests} ({overall_results['semantic_success_rate']:.1%})")
        self.logger.info(f"Coordinate success rate: {coordinate_success}/{total_tests} ({overall_results['coordinate_success_rate']:.1%})")
        self.logger.info(f"Dual success rate: {dual_success}/{total_tests} ({overall_results['dual_success_rate']:.1%})")
        
        return overall_results
    
    def call_ui_tars_for_semantic_analysis(self, screenshot_path: str, scenario_name: str) -> str:
        """Call UI-TARS for semantic analysis using scrolling viewport with history"""
        try:
            # Import the optimized prompts from uitars_prompt_optimizer
            from uitars_prompt_optimizer import get_optimized_scenario_prompts
            
            # Get scenario-specific instruction template prompt
            optimized_prompts = get_optimized_scenario_prompts()
            instruction_prompt = optimized_prompts.get(scenario_name, optimized_prompts['amazon'])
            
            self.logger.info(f"Using instruction template for {scenario_name}")
            
            # 🎯 Use ScrollingViewport for complete scrolling exploration
            self.logger.info("🔄 Initializing ScrollingViewport for scrolling exploration")
            
            # Initialize ScrollingViewport
            viewport = ScrollingViewport(
                full_screenshot_path=screenshot_path,
                viewport_height=1200,  # Use optimized viewport height
                scroll_step=600
            )
            
            # Execute scroll exploration loop - simulate real UI-TARS behavior
            max_scrolls = self.test_configs.get('max_interactions', 20) - 1  # From config, minus 1 for the initial view
            exploration_history = []
            final_action = None
            
            for scroll_step in range(max_scrolls + 1):  # +1 for initial view
                # Get current viewport
                current_region = viewport.get_current_view()
                
                if scroll_step == 0:
                    self.logger.info(f"📸 Analyzing initial viewport: {current_region}")
                else:
                    self.logger.info(f"📸 Analyzing scroll step {scroll_step}: {current_region}")
                
                # Build prompt with history - use optimized history processing
                if exploration_history:
                    history_context = self.build_history_context_optimized(exploration_history)
                    full_prompt = instruction_prompt + history_context
                    
                    # 🔍 Detailed output of history state
                    is_uitars_or_qwen3vl = self.is_uitars_model() or self.model_type == "qwen3vl"
                    model_name = "UI-TARS" if self.is_uitars_model() else ("Qwen3-VL" if self.model_type == "qwen3vl" else "Other")
                    self.logger.info(f"📚 Building prompt with history ({len(exploration_history)} history steps, {model_name} optimization: {is_uitars_or_qwen3vl}):")
                    
                    # Display history optimization status
                    if is_uitars_or_qwen3vl:
                        recent_with_images = min(5, len(exploration_history))
                        text_only = max(0, len(exploration_history) - 5)
                        self.logger.info(f"   🎯 {model_name} optimization: {recent_with_images} steps with images, {text_only} steps text-only")
                    
                    for i, hist in enumerate(exploration_history[-3:]):  # Show only the last 3 steps
                        step_num = hist.get('step', i+1)
                        action = hist.get('action', 'observe')
                        observation = hist.get('observation', '')[:200]
                        coordinates = hist.get('coordinates', 'N/A')
                        has_image = 'region' in hist
                        image_status = "📸" if has_image else "📝"
                        
                        self.logger.info(f"   History step {step_num} {image_status}: {action}")
                        self.logger.info(f"   - Observation: {observation}...")
                        self.logger.info(f"   - Coordinates: {coordinates}")
                else:
                    full_prompt = instruction_prompt
                    self.logger.info("📝 Using initial prompt (no history)")
                
                # 🎯 Full output of prompt content
                self.logger.info("=" * 80)
                self.logger.info("📋 Full Prompt content:")
                self.logger.info("=" * 80)
                self.logger.info(full_prompt)
                self.logger.info("=" * 80)
                
                # Use unified model inference interface
                if (self.use_vllm and self.vllm_engine) or (self.model_type == "qwen3vl" and self.qwen3vl_wrapper) or (self.model_type == "glm4v" and self.glm4v_wrapper):
                    self.logger.info(f"🚀 Running {self.model_type.upper()} inference (step {scroll_step})")
                    
                    result = self.generate_model_response(
                        image_path=current_region,  # current_region is a file path string
                        prompt=full_prompt,
                        exploration_history=exploration_history,  # Pass history
                        max_tokens=1024,
                        temperature=1.0,
                        top_p=0.8
                    )
                    
                    if result and result.get('success') and result.get('response'):
                        response = result['response']
                        inference_time = result.get('inference_time', 0)
                        self.logger.info(f"✅ {self.model_type.upper()} inference completed - elapsed: {inference_time:.1f}s")
                        
                        # Parse action from response
                        action_info = self.extract_action_info(response)
                        
                        # 🎯 For Qwen3-VL and GLM-4V, prefer coordinates returned by wrapper (already parsed and validated)
                        if self.model_type in ["qwen3vl", "glm4v"] and result.get('coordinates'):
                            wrapper_coords = result['coordinates']
                            self.logger.info(f"🔧 Using {self.model_type.upper()} wrapper-parsed coordinates: {wrapper_coords}")
                            action_info['coordinates'] = wrapper_coords
                        
                        # Record exploration history
                        exploration_history.append({
                            'step': scroll_step,
                            'action': action_info.get('action_type', 'observe'),
                            'observation': response,
                            'coordinates': action_info.get('coordinates'),
                            'region': current_region
                        })
                        
                        # Determine whether to keep scrolling or perform final action
                        if action_info.get('action_type') == 'click':
                            self.logger.info("✅ UI-TARS decision: execute click action")
                            self.logger.info(f"   Click coordinates: {action_info.get('coordinates')}")
                            final_action = response
                            break
                        elif action_info.get('action_type') == 'scroll':
                            self.logger.info("🔄 UI-TARS decision: continue scrolling")
                            # Execute scroll
                            if not viewport.can_scroll_down():
                                self.logger.info("📄 Reached page bottom, stopping scroll")
                                break
                            viewport.scroll_down()
                        else:
                            # No explicit action; auto-scroll to continue exploration
                            self.logger.info("🔄 Auto-scrolling to continue exploration")
                            if not viewport.can_scroll_down():
                                self.logger.info("📄 Reached page bottom, stopping scroll")
                                break
                            viewport.scroll_down()
                    else:
                        self.logger.error(f"vLLM inference failed (step {scroll_step})")
                        break
                        
                else:
                    # Fall back to transformer engine
                    self.logger.info("🔧 Using Transformer inference engine (fallback mode)")
                    from rotating_gpu_test import RotatingGPUTest
                    gpu_tester = RotatingGPUTest()
                    
                    result = gpu_tester.single_gpu_inference(
                        region_path=current_region['region_path'],
                        prompt=full_prompt,
                        gpu_id=0,
                        scenario=scenario_name
                    )
                    
                    if result and 'response' in result:
                        response = result['response']
                        action_info = self.extract_action_info(response)
                        
                        exploration_history.append({
                            'step': scroll_step,
                            'action': action_info.get('action_type', 'observe'),
                            'observation': response,
                            'coordinates': action_info.get('coordinates'),
                            'region': current_region
                        })
                        
                        if action_info.get('action_type') == 'click':
                            final_action = response
                            break
                        elif not viewport.can_scroll_down():
                            break
                        viewport.scroll_down()
                    else:
                        break
            
            # If no final click action, use the last response
            if not final_action and exploration_history:
                final_action = exploration_history[-1]['observation']
                self.logger.info("📋 Using the last exploration response as the final decision")
            
            # Output complete exploration summary
            self.logger.info(f"🎯 Exploration complete! Total {len(exploration_history)} steps")
            for i, hist in enumerate(exploration_history):
                self.logger.info(f"   Step {i+1}: {hist['action']} - region: {hist['region']}")
            
            return final_action
            
        except Exception as e:
            self.logger.error(f"UI-TARS semantic analysis failed: {e}")
            self.logger.error(traceback.format_exc())
            return None
    
    def setup_logging(self):
        """Set up logging"""
        import logging
        
        log_file = self.output_dir / f"pipeline_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        
        self.logger = logging.getLogger(__name__)
        return log_file
    
    def validate_optimal_configuration(self):
        """Verify whether configuration uses advanced optimization settings"""
        optimal_width = 800
        optimal_height = 1200
        optimal_scale_ratio = 0.870
        
        current_width = self.test_configs['image_scaling']['max_width']
        current_height = self.test_configs['image_scaling']['max_height']
        current_ratio = self.test_configs['image_scaling'].get('force_scale_ratio', 1.0)
        
        if current_width == optimal_width and current_height == optimal_height:
            self.logger.info(f"✅ Using advanced optimization config: {current_width}x{current_height} (advanced exclusive)")
        else:
            self.logger.warning(f"⚠️ Current config {current_width}x{current_height} is not the advanced optimization config")
            self.logger.warning(f"🎯 Recommended config: {optimal_width}x{optimal_height} (advanced optimization)")
            
        if abs(current_ratio - optimal_scale_ratio) < 0.01:
            self.logger.info(f"✅ Using optimal scale ratio: {current_ratio}")
        else:
            self.logger.warning(f"⚠️ Current scale ratio {current_ratio} may trigger the scale_ratio=1.0 trap")
            self.logger.warning(f"🎯 Recommended scale ratio: {optimal_scale_ratio}")
            
        return current_width == optimal_width and current_height == optimal_height
    
    def adjust_scaling_for_memory_constraints(self):
        """Use experimentally validated optimal configuration, no dynamic adjustment needed"""
        try:
            import torch
            if torch.cuda.is_available():
                # Get GPU memory info
                total_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)  # GB
                current_memory = torch.cuda.memory_allocated(0) / (1024**3)  # GB
                free_memory = total_memory - current_memory
                
                self.logger.info(f"🔧 GPU memory status: total {total_memory:.1f}GB, used {current_memory:.1f}GB, free {free_memory:.1f}GB")
                
                # Web scenario optimization: 640x1000 config performs better in web scenarios
                # Preserve more page info, avoid severe over-compression
                self.logger.info("🎯 Using advanced optimization config: 800x1200 (maximum information retention)")
                self.logger.info("🔧 This config is advanced-optimized to maximize page information retention")
                
                # Ensure config is locked to the advanced optimization values
                self.test_configs['image_scaling']['max_width'] = 800
                self.test_configs['image_scaling']['max_height'] = 1200
                self.test_configs['image_scaling']['force_scale_ratio'] = 0.870
                    
        except Exception as e:
            self.logger.warning(f"⚠️ Unable to detect GPU memory status: {e}")
            # Use advanced optimization config as default
            self.test_configs['image_scaling']['max_width'] = 800
            self.test_configs['image_scaling']['max_height'] = 1200

    def emergency_scale_for_oom(self, image_path: str) -> str:
        """Use ultra-small config for OOM emergency"""
        from PIL import Image
        
        try:
            with Image.open(image_path) as img:
                original_width, original_height = img.size
                
                # Use ultra-small config (320x500) - experimentally verified 75% success rate still
                emergency_width = 320
                emergency_height = 500
                
                self.logger.warning(f"🆘 OOM emergency scaling to ultra-small config: {original_width}x{original_height} -> {emergency_width}x{emergency_height}")
                self.logger.info("💡 Ultra-small config can still achieve 75% success rate")
                
                # Create emergency scaled image
                emergency_img = img.resize((emergency_width, emergency_height), Image.Resampling.LANCZOS)
                
                # Generate emergency filename
                original_path = Path(image_path)
                emergency_path = original_path.parent / f"{original_path.stem}_emergency{original_path.suffix}"
                
                # Save emergency scaled image
                emergency_img.save(emergency_path, 'PNG', optimize=True, compress_level=9)
                
                self.logger.warning(f"🆘 Emergency scaled image saved to: {emergency_path}")
                return str(emergency_path)
                
        except Exception as e:
            self.logger.error(f"❌ Emergency scaling failed: {e}")
            return image_path

    def scale_image_if_needed(self, image_path: str) -> str:
        """Smart-scale image to cooperate with UI-TARS rescale mechanism and avoid double information loss"""
        from PIL import Image
        import os
        
        if not self.test_configs['image_scaling']['enable_auto_scaling']:
            return image_path
        
        try:
            with Image.open(image_path) as img:
                original_width, original_height = img.size
                
                # Optimization strategy based on UI-TARS rescale analysis
                max_width = self.test_configs['image_scaling']['max_width']
                max_height = self.test_configs['image_scaling']['max_height']
                force_scale_ratio = self.test_configs['image_scaling']['force_scale_ratio']
                
                self.logger.info(f"🎯 UI-TARS optimized scaling: {original_width}x{original_height}")
                
                # Compute aspect ratio and original pixel count
                aspect_ratio = original_height / original_width
                original_pixels = original_width * original_height
                
                # Smart scaling strategy: avoid conflicting with UI-TARS internal rescale
                # UI-TARS uses dynamic patches; optimal input size is in 640x1000-800x1200 range
                
                # Check whether scaling is needed
                needs_scaling = False
                scale_reason = ""
                
                # 1. Check whether it exceeds UI-TARS optimal processing range
                if original_width > max_width * 1.5 or original_height > max_height * 1.5:
                    needs_scaling = True
                    scale_reason = "Exceeds UI-TARS optimal processing range"
                
                # 2. Check whether it is too small (may cause insufficient detail)
                elif original_width < 400 or original_height < 600:
                    needs_scaling = True
                    scale_reason = "Size too small, needs moderate upscaling"
                
                # 3. Check whether the aspect ratio is extreme
                elif aspect_ratio > 5.0 or aspect_ratio < 0.2:
                    needs_scaling = True
                    scale_reason = f"Extreme aspect ratio ({aspect_ratio:.2f}) needs adjustment"
                
                if not needs_scaling:
                    # Image is already in optimal range; use original to avoid unnecessary information loss
                    self.logger.info(f"✅ Image size already optimized, skipping pre-scaling (to avoid double compression with UI-TARS)")
                    self.logger.info(f"📊 Original size is within UI-TARS optimal range: {original_width}x{original_height}")
                    return image_path
                
                self.logger.info(f"🔧 Scaling reason: {scale_reason}")
                
                # Execute smart scaling - specially optimized for long pages
                if self.test_configs['image_scaling']['preserve_aspect_ratio']:
                    # 🎯 New: long page smart handling strategy + Booking2 OOM protection
                    if aspect_ratio > 15.0:  # Ultra-extreme long page (e.g. Booking2 with 17.13)
                        # Strategy 0: aggressive scaling to avoid OOM - target 8% information retention
                        target_retention = 0.08
                        max_allowed_height = 8000  # Forced height limit
                        target_pixels = int(original_pixels * target_retention)
                        scale_factor = (target_pixels / original_pixels) ** 0.5
                        
                        new_width = max(400, int(original_width * scale_factor))
                        new_height = min(max_allowed_height, max(2000, int(original_height * scale_factor)))
                        
                        self.logger.info(f"🚨 Ultra-extreme long page OOM protection: target {target_retention:.1%} information retention")
                        self.logger.info(f"🔧 Forced height limit: {max_allowed_height}px")
                        
                    elif aspect_ratio > 10.0:  # Extreme long page
                        # Strategy 1: conservative OOM protection - target 12% information retention
                        target_retention = 0.12
                        max_allowed_height = 12000
                        target_pixels = int(original_pixels * target_retention)
                        scale_factor = (target_pixels / original_pixels) ** 0.5
                        
                        new_width = max(500, int(original_width * scale_factor))
                        new_height = min(max_allowed_height, max(2400, int(original_height * scale_factor)))
                        
                        self.logger.info(f"⚠️ Extreme long page protection: target {target_retention:.1%} information retention")
                        
                    elif aspect_ratio > 7.0:  # Extreme long page (e.g. 1280x9493)
                        # Strategy 2: dynamic quality retention - target 15% information retention
                        target_retention = 0.15
                        target_pixels = int(original_pixels * target_retention)
                        scale_factor = (target_pixels / original_pixels) ** 0.5
                        
                        new_width = max(600, int(original_width * scale_factor))  # Minimum 600px width
                        new_height = max(1800, int(original_height * scale_factor))  # Minimum 1800px height
                        
                        self.logger.info(f"🚀 Extreme long page strategy: target {target_retention:.1%} information retention")
                        
                    elif aspect_ratio > 4.0:  # Regular long page
                        # Strategy 2: conservative scaling - target 20% information retention
                        target_retention = 0.20
                        target_pixels = int(original_pixels * target_retention)
                        scale_factor = (target_pixels / original_pixels) ** 0.5
                        
                        new_width = max(500, int(original_width * scale_factor))
                        new_height = max(1500, int(original_height * scale_factor))
                        
                        self.logger.info(f"📏 Long page strategy: target {target_retention:.1%} information retention")
                        
                    else:
                        # Strategy 3: standard scaling logic
                        width_ratio = max_width / original_width
                        height_ratio = max_height / original_height
                        scale_ratio = min(width_ratio, height_ratio)
                        
                        # Special handling: if original image is very small, allow moderate upscaling
                        if scale_ratio > 1.0 and (original_width < 500 or original_height < 800):
                            # Allow small images to be moderately upscaled to UI-TARS optimal range
                            scale_ratio = min(scale_ratio, 1.5)  # Max upscale 1.5x
                            self.logger.info(f"🔧 Small image moderate upscaling: {scale_ratio:.3f}")
                        elif scale_ratio >= 1.0:
                            # Prevent unnecessary upscaling from diluting information
                            scale_ratio = min(force_scale_ratio, 0.95)  # Slight downscale to avoid boundary effects
                            self.logger.info(f"🔧 Preventing information dilution, slight downscale: {scale_ratio:.3f}")
                        
                        new_width = int(original_width * scale_ratio)
                        new_height = int(original_height * scale_ratio)
                        
                else:
                    # Case without preserving aspect ratio (generally not recommended)
                    new_width = max_width
                    new_height = max_height
                
                # 📊 Recompute actual scaling parameters
                actual_scale_ratio = min(new_width / original_width, new_height / original_height)
                
                # Final check: ensure scaled dimensions are still within reasonable range
                if new_width < 400 or new_height < 600:
                    self.logger.warning(f"⚠️ Scaled size too small ({new_width}x{new_height}), applying minimum safe size")
                    # For long pages, preserve more height information
                    if aspect_ratio > 3.0:
                        new_width = max(new_width, 500)
                        new_height = max(new_height, 1500)
                    else:
                        new_width = max(new_width, 400)
                        new_height = max(new_height, 600)
                
                self.logger.info(f"🔄 Smart scaling: {original_width}x{original_height} -> {new_width}x{new_height}")
                self.logger.info(f"📊 Scale ratio: {actual_scale_ratio:.3f}")
                self.logger.info(f"💡 UI-TARS will further process into ~{int(new_width * new_height * 0.005)} patches")
                
                # Create scaled image
                resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                # Generate new filename
                original_path = Path(image_path)
                scaled_image_path = original_path.parent / f"{original_path.stem}_uitars_optimized{original_path.suffix}"
                
                # Save scaled image
                if original_path.suffix.lower() in ['.jpg', '.jpeg']:
                    resized_img.save(scaled_image_path, 'JPEG', 
                                   quality=self.test_configs['image_scaling']['quality'])
                else:
                    resized_img.save(scaled_image_path, 'PNG', optimize=True)
                
                # Record file size change and information retention analysis
                original_size = os.path.getsize(image_path) / (1024 * 1024)  # MB
                scaled_size = os.path.getsize(scaled_image_path) / (1024 * 1024)  # MB
                
                # Compute information retention estimate
                scaled_pixels = new_width * new_height
                pixel_retention = scaled_pixels / original_pixels
                
                # Estimate information amount after UI-TARS processing
                estimated_patches = int(scaled_pixels * 0.005)
                final_info_retention = estimated_patches / original_pixels
                
                self.logger.info(f"💾 File size: {original_size:.1f}MB -> {scaled_size:.1f}MB")
                self.logger.info(f"📊 Pixel retention rate: {pixel_retention:.1%}")
                self.logger.info(f"🎯 Estimated UI-TARS final information retention rate: {final_info_retention:.1%}")
                
                # 📈 Long page special note
                if aspect_ratio > 7.0:
                    self.logger.info(f"🚀 Extreme long page optimization complete! Information retention increased from {(400*1200/original_pixels):.1%} to {pixel_retention:.1%}")
                elif aspect_ratio > 4.0:
                    self.logger.info(f"📏 Long page optimization complete! Excessive compression avoided")
                
                self.logger.info(f"✅ Optimized image saved to: {scaled_image_path}")
                
                return str(scaled_image_path)
                
        except Exception as e:
            self.logger.error(f"❌ Image scaling failed: {e}")
            self.logger.warning("⚠️ Using original image, may cause memory issues")
            return image_path
    
    def generate_screenshot_simple(self, html_file: str, enable_scrolling: bool = False) -> str:
        """Simple screenshot generation method - prefer Playwright
        
        Args:
            html_file: HTML file path
            enable_scrolling: whether to enable scroll mode (preserve original size)
        """
        import subprocess
        from pathlib import Path
        import tempfile
        import os
        
        html_path = Path(html_file)
        # Use a user-writable temp directory
        output_dir = Path(tempfile.gettempdir()) / "webarena_screenshots"
        output_dir.mkdir(exist_ok=True, parents=True)
        
        screenshot_path = output_dir / f"{html_path.stem}_screenshot.png"
        
        try:
            # First try Playwright
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_viewport_size({"width": 1280, "height": 4000})
                page.goto(f"file://{html_path.absolute()}")
                page.screenshot(path=str(screenshot_path), full_page=True)
                browser.close()
                
            if screenshot_path.exists():
                self.logger.info(f"✅ Screenshot generated successfully with Playwright: {screenshot_path}")
                
                # 🎯 Default: use scrolling, no scaling
                from PIL import Image
                with Image.open(screenshot_path) as img:
                    width, height = img.size
                    self.logger.info(f"� Preserving original screenshot size for scroll processing: {width}x{height}")
                return str(screenshot_path)
            else:
                raise FileNotFoundError("Playwright screenshot not generated")
                
        except ImportError:
            self.logger.warning("⚠️ Playwright not installed, skipping screenshot generation")
            return None
        except Exception as e:
            self.logger.warning(f"⚠️ Playwright screenshot failed: {e}")
            return None
        
        # Playwright failed, no longer trying Chrome (not installed)
        self.logger.warning("⚠️ Screenshot generation failed, will use HTML file")
        return None
    
    def generate_screenshot_fallback(self, html_file: str, enable_scrolling: bool = False) -> str:
        """Fallback screenshot generation method"""
        from pathlib import Path
        
        html_path = Path(html_file)
        
        # 1. First try to find the corresponding screenshot in the same directory
        expected_screenshot = html_path.with_suffix('.png')
        if expected_screenshot.exists():
            return str(expected_screenshot)
        
        # 2. Look for similarly named screenshot files
        potential_screenshots = list(html_path.parent.glob(f"{html_path.stem}*.png"))
        if potential_screenshots:
            return str(potential_screenshots[0])
        
        # 3. Look for any screenshot files
        any_screenshots = list(html_path.parent.glob("*.png"))
        if any_screenshots:
            self.logger.warning(f"Using fallback screenshot: {any_screenshots[0]}")
            return str(any_screenshots[0])
        
        # 4. Create an optimally sized test image
        from PIL import Image, ImageDraw
        
        # Use the max size from config to create test image
        max_width = self.test_configs['image_scaling']['max_width']
        max_height = self.test_configs['image_scaling']['max_height']
        
        # Select appropriate test image size
        test_width = min(1280, max_width)
        test_height = min(2500, max_height)  # Moderate height, avoid being too large
        
        test_image = Image.new('RGB', (test_width, test_height), color='white')
        draw = ImageDraw.Draw(test_image)
        draw.text((100, 100), f"Test image for {html_path.name}", fill='black')
        
        # Add corresponding target area based on scenario
        scenario_name = self.current_scenario if hasattr(self, 'current_scenario') else 'amazon'
        scenario_info = self.scenarios.get(scenario_name, self.scenarios.get('amazon', {}))
        original_target_coords = scenario_info.get('target_coords', [450, 2940])  # Default value
        
        # Compute appropriate scale ratio
        scale_factor_x = test_width / 1280 if test_width != 1280 else 1.0
        scale_factor_y = test_height / 4000  # Assume original height is 4000
        
        # Scale target coordinates proportionally to test image range
        scaled_target_x = int(original_target_coords[0] * scale_factor_x)
        scaled_target_y = int(original_target_coords[1] * scale_factor_y)
        
        self.logger.info(f"📍 Original target coordinates: {original_target_coords}")
        self.logger.info(f"📍 Scaled coordinates: ({scaled_target_x}, {scaled_target_y})")
        self.logger.info(f"📍 Scale factors: X={scale_factor_x:.3f}, Y={scale_factor_y:.3f}")
        
        card_width, card_height = 200, 150
        
        # Ensure card is within image bounds
        if 0 <= scaled_target_y <= test_height - card_height and 0 <= scaled_target_x <= test_width - card_width:
            # Draw target product card background
            draw.rectangle([
                scaled_target_x - card_width//2, 
                scaled_target_y - card_height//2,
                scaled_target_x + card_width//2,
                scaled_target_y + card_height//2
            ], fill='lightblue', outline='blue', width=3)
            
            # Add product info text
            product_name = scenario_info['target_product_names'][0] if scenario_info['target_product_names'] else "Target Product"
            draw.text((scaled_target_x - 80, scaled_target_y - 40), product_name[:15], fill='black')
            draw.text((scaled_target_x - 60, scaled_target_y - 20), f"🎯 TARGET", fill='red')
            # Safe coordinate unpacking for debug text
            try:
                if isinstance(original_target_coords, dict):
                    orig_x = original_target_coords.get('x', 0)
                    orig_y = original_target_coords.get('y', 0)
                elif isinstance(original_target_coords, (tuple, list)) and len(original_target_coords) >= 2:
                    orig_x, orig_y = original_target_coords[0], original_target_coords[1]
                else:
                    orig_x, orig_y = 0, 0
                
                draw.text((scaled_target_x - 50, scaled_target_y), f"Original:({orig_x},{orig_y})", fill='green', anchor='mm')
            except (IndexError, ValueError, TypeError) as e:
                self.logger.warning(f"⚠️ Original coordinate unpacking failed: {original_target_coords}, error: {e}")
                draw.text((scaled_target_x - 50, scaled_target_y), f"Original:(?,?)", fill='green', anchor='mm')
            draw.text((scaled_target_x - 50, scaled_target_y + 20), f"Test:({scaled_target_x},{scaled_target_y})", fill='blue', anchor='mm')
        else:
            self.logger.warning(f"⚠️ Target coordinates out of test image bounds: ({scaled_target_x}, {scaled_target_y})")
        
        # Add some distractor elements (other product cards)
        # for i in range(3):
        #     distractor_x = 200 + i * 300
        #     distractor_y = max(200, min(test_height - 200, scaled_target_y + (-1)**i * 200))
        #     if 50 <= distractor_x <= test_width - 160:
        #         draw.rectangle([
        #             distractor_x - 80, distractor_y - 60,
        #             distractor_x + 80, distractor_y + 60
        #         ], fill='lightgray', outline='gray')
        #         draw.text((distractor_x - 50, distractor_y), f"Distractor {i+1}", fill='black')
        
        test_image_path = Path("/tmp") / f"test_{html_path.stem}_optimized.png"
        test_image.save(test_image_path, 'PNG', optimize=True)
        
        self.logger.warning(f"Creating optimized test image: {test_image_path}")
        self.logger.warning(f"Image size: {test_image.size}, target position: ({scaled_target_x}, {scaled_target_y})")
        return str(test_image_path)
    
    def extract_action_info(self, response: str) -> Dict[str, Any]:
        """Extract action info from UI-TARS response, including action type, coordinates, and product name"""
        import re
        
        # First output the complete UI-TARS response for analysis
        self.logger.info("🔍 Starting to parse UI-TARS response:")
        self.logger.info("=" * 60)
        self.logger.info(f"Raw response: {response}")
        self.logger.info("=" * 60)
        
        action_info = {
            'action_type': 'unknown',
            'product_name': '',
            'selection_reason': '',
            'coordinates': None,
            'raw_action': '',
            'full_response': response
        }
        
        if not response:
            self.logger.warning("⚠️ Response is empty")
            return action_info
        
        # Extract action type and coordinates
        # 1. UI-TARS standard format: <|box_start|>(x,y)<|box_end|>
        box_match = re.search(r'<\|box_start\|>\s*\((\d+)\s*,\s*(\d+)\)\s*<\|box_end\|>', response)
        if box_match:
            action_info['action_type'] = 'click'
            action_info['coordinates'] = (int(box_match.group(1)), int(box_match.group(2)))
            action_info['raw_action'] = box_match.group(0)
            self.logger.info(f"✅ Recognized UI-TARS standard format: {action_info['raw_action']}")
        
        # 2. Direct coordinate format: (x,y)
        if action_info['action_type'] == 'unknown':
            coord_match = re.search(r'\((\d+)\s*,\s*(\d+)\)', response)
            if coord_match:
                action_info['action_type'] = 'click'
                action_info['coordinates'] = (int(coord_match.group(1)), int(coord_match.group(2)))
                action_info['raw_action'] = coord_match.group(0)
                self.logger.info(f"✅ Recognized coordinate format: {action_info['raw_action']}")
        
        # 3. click format
        if action_info['action_type'] == 'unknown':
            click_match = re.search(r'click\(start_box=[\'"]?\((\d+),(\d+)\)[\'"]?\)', response)
            if click_match:
                action_info['action_type'] = 'click'
                action_info['coordinates'] = (int(click_match.group(1)), int(click_match.group(2)))
                action_info['raw_action'] = click_match.group(0)
                self.logger.info(f"✅ Recognized click format: {action_info['raw_action']}")
        
        # 4. scroll format
        if action_info['action_type'] == 'unknown':
            scroll_match = re.search(r'scroll\([^)]*start_box=[\'"]?\((\d+),(\d+)\)[\'"]?[^)]*\)', response)
            if scroll_match:
                action_info['action_type'] = 'scroll'
                action_info['coordinates'] = (int(scroll_match.group(1)), int(scroll_match.group(2)))
                action_info['raw_action'] = scroll_match.group(0)
                self.logger.info(f"✅ Recognized scroll format: {action_info['raw_action']}")
        
        # 🎯 New: parse instruction template format (includes Thought and Action)
        # 优先解析这种格式：Thought: I select 'Product Name' at coordinates (x,y) because reason. Action: click(start_box='(x,y)')
        thought_action_match = re.search(r'Thought:\s*(.+?)\s*Action:\s*(.+)', response, re.DOTALL | re.IGNORECASE)
        if thought_action_match:
            thought_text = thought_action_match.group(1).strip()
            action_text = thought_action_match.group(2).strip()
            
            self.logger.info(f"✅ Recognized instruction template format")
            self.logger.info(f"   Thought: {thought_text}")
            self.logger.info(f"   Action: {action_text}")
            
            # Extract product name from Thought
            product_patterns = [
                r"It's\s+the\s+([^,!?.]+?)(?:\s+with|\s+featuring|\.|,|$)",
                r"I\s+(?:select|choose)\s+['\"]([^'\"]+)['\"]",
                r"select\s+['\"]([^'\"]+)['\"]",
                r"choose\s+['\"]([^'\"]+)['\"]",
                r"The\s+most\s+(?:attractive|appealing)\s+(?:laptop|product|hotel|article)\s+is\s+['\"]([^'\"]+)['\"]",
                r"(?:HP|Dell|Lenovo|ASUS|Acer)\s+[^,!?.]*?(?:laptop|computer|notebook)",
                r"([A-Z][a-zA-Z0-9\s\-]+(?:laptop|computer|notebook|hotel|phone)[^,!?.]*?)"
            ]
            
            for pattern in product_patterns:
                product_match = re.search(pattern, thought_text, re.IGNORECASE)
                if product_match:
                    # Check whether there are capture groups
                    if product_match.groups():
                        action_info['product_name'] = product_match.group(1).strip()
                    else:
                        # If no capture groups, use the entire match
                        action_info['product_name'] = product_match.group(0).strip()
                    self.logger.info(f"🏷️ Extracted product name: {action_info['product_name']}")
                    break
            
            # Extract coordinates and action type from Action
            action_coord_patterns = [
                (r'click\(start_box=[\'"]?\((\d+),(\d+)\)[\'"]?\)', 'click'),
                (r'scroll\([^)]*start_box=[\'"]?\((\d+),(\d+)\)[\'"]?[^)]*\)', 'scroll'),  # Fix: support direction-first format
                (r'scroll\(start_box=[\'"]?\((\d+),(\d+)\)[\'"]?.*?\)', 'scroll'),
                (r'\((\d+),(\d+)\)', 'click')  # Simple coordinate format
            ]
            
            for pattern, act_type in action_coord_patterns:
                coord_match = re.search(pattern, action_text, re.IGNORECASE)
                if coord_match:
                    action_info['action_type'] = act_type
                    action_info['coordinates'] = (int(coord_match.group(1)), int(coord_match.group(2)))
                    action_info['raw_action'] = coord_match.group(0)
                    
                    # 🎯 Detect scroll direction (for scroll action)
                    if action_info['action_type'] == 'scroll':
                        # Detect direction parameter
                        direction_match = re.search(r'direction=[\'"]?(up|down|left|right)[\'"]?', action_text, re.IGNORECASE)
                        if direction_match:
                            action_info['direction'] = direction_match.group(1).lower()
                        else:
                            # Infer direction from Thought and Action text
                            combined_text = thought_text + ' ' + action_text
                            if any(word in combined_text.lower() for word in ['up', 'back', 'previous', 'earlier']):
                                action_info['direction'] = 'up'
                            elif any(word in combined_text.lower() for word in ['down', 'below', 'next', 'continue', 'more']):
                                action_info['direction'] = 'down'
                            else:
                                action_info['direction'] = 'down'  # Default downward
                        
                        self.logger.info(f"🔄 Detected scroll direction: {action_info.get('direction', 'unknown')}")
                    
                    self.logger.info(f"✅ Recognized {action_info['action_type']} format in instruction template: {action_info['raw_action']}")
                    break
                coord_match = re.search(pattern, action_text)
                if coord_match:
                    action_info['action_type'] = act_type
                    action_info['coordinates'] = (int(coord_match.group(1)), int(coord_match.group(2)))
                    action_info['raw_action'] = coord_match.group(0)
                    self.logger.info(f"✅ Extracted from Action: {act_type} at {action_info['coordinates']}")
                    break
            
            # Extract selection reason from Thought
            reason_patterns = [
                r"because\s+(.+?)(?:\.|$)",
                r"due\s+to\s+(.+?)(?:\.|$)",
                r"since\s+(.+?)(?:\.|$)"
            ]
            
            for pattern in reason_patterns:
                reason_match = re.search(pattern, thought_text, re.IGNORECASE)
                if reason_match:
                    action_info['selection_reason'] = reason_match.group(1).strip()
                    self.logger.info(f"💭 Extracted selection reason: {action_info['selection_reason']}")
                    break
        
        # If instruction template format parsed successfully, skip parsing other formats
        if action_info['action_type'] != 'unknown' and action_info.get('product_name'):
            self.logger.info("✅ Instruction template format parsing complete, skipping other formats")
        else:
            # Continue with the original parsing logic
            # 5. Standard coordinate format: "click: (x, y)", "scroll: (x, y)"
            if action_info['action_type'] == 'unknown':
                coordinate_patterns = [
                    (r'click:\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', 'click'),
                    (r'scroll:\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', 'scroll'),
                    (r'tap:\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', 'click'),
                ]
                
                for pattern, action_type in coordinate_patterns:
                    match = re.search(pattern, response, re.IGNORECASE | re.MULTILINE)
                    if match:
                        action_info['action_type'] = action_type
                        action_info['coordinates'] = (int(match.group(1)), int(match.group(2)))
                        action_info['raw_action'] = match.group(0)
                        self.logger.info(f"✅ Recognized standard coordinate format: {action_info['raw_action']}")
                        break
            
            # 6. Instruction format: "1. Click on...", "Click on...", "Scroll down..."
            if action_info['action_type'] == 'unknown':
                instruction_patterns = [
                    (r'(?:^\d+\.\s*)?click\s+on\s+(?:the\s+)?["\']?([^"\']+)["\']?', 'click'),
                    (r'(?:^\d+\.\s*)?click\s+(?:the\s+)?["\']?([^"\']+)["\']?', 'click'),
                    (r'(?:^\d+\.\s*)?scroll\s+down', 'scroll'),
                    (r'(?:^\d+\.\s*)?scroll\s+up', 'scroll'),
                    (r'(?:^\d+\.\s*)?tap\s+on\s+(?:the\s+)?["\']?([^"\']+)["\']?', 'click'),
                    (r'(?:^\d+\.\s*)?select\s+(?:the\s+)?["\']?([^"\']+)["\']?', 'click'),
                ]
                
                for pattern, action_type in instruction_patterns:
                    match = re.search(pattern, response, re.IGNORECASE | re.MULTILINE)
                    if match:
                        action_info['action_type'] = action_type
                        action_info['raw_action'] = match.group(0)
                        self.logger.info(f"✅ Recognized instruction format: {action_info['raw_action']}")
                        # Try to extract product name (if there are capture groups)
                        if match.groups() and len(match.groups()) > 0:
                            potential_product = match.group(1).strip()
                            if self.is_valid_product_name(potential_product):
                                action_info['product_name'] = potential_product
                        break
            
            # General logic to extract coordinates
            if not action_info['coordinates']:
                # 1. UI-TARS standard format: <|box_start|>(x,y)<|box_end|>
                box_match = re.search(r'<\|box_start\|>\s*\((\d+)\s*,\s*(\d+)\)\s*<\|box_end\|>', response)
                if box_match:
                    action_info['coordinates'] = (int(box_match.group(1)), int(box_match.group(2)))
                    if action_info['action_type'] == 'unknown':
                        action_info['action_type'] = 'click'
                    self.logger.info(f"✅ Recognized UI-TARS standard format coordinates: {action_info['coordinates']}")
                
                # 2. Direct coordinate format: (x,y)
                if not action_info['coordinates']:
                    coord_match = re.search(r'\((\d+)\s*,\s*(\d+)\)', response)
                    if coord_match:
                        action_info['coordinates'] = (int(coord_match.group(1)), int(coord_match.group(2)))
                        if action_info['action_type'] == 'unknown':
                            action_info['action_type'] = 'click'
                        self.logger.info(f"✅ Recognized direct coordinate format: {action_info['coordinates']}")
                
                # 3. click/scroll format
                if not action_info['coordinates']:
                    # Standard format: click(start_box="(x,y)")
                    click_match = re.search(r'(?:click|scroll)\(start_box=[\'"]?\((\d+),(\d+)\)[\'"]?\)', response)
                    if click_match:
                        action_info['coordinates'] = (int(click_match.group(1)), int(click_match.group(2)))
                        action_info['action_type'] = 'click' if 'click' in click_match.group(0) else 'scroll'
                        self.logger.info(f"✅ Recognized action format: {action_info['action_type']} at {action_info['coordinates']}")
                    else:
                        # Simplified format: click(x, y) or click(x,y)
                        simple_click_match = re.search(r'(?:click|scroll)\((\d+)\s*,\s*(\d+)\)', response, re.IGNORECASE)
                        if simple_click_match:
                            action_info['coordinates'] = (int(simple_click_match.group(1)), int(simple_click_match.group(2)))
                            action_info['action_type'] = 'click' if 'click' in simple_click_match.group(0).lower() else 'scroll'
                            self.logger.info(f"✅ Recognized simplified action format: {action_info['action_type']} at {action_info['coordinates']}")
                
                # 🔧 GLM4V special handling: even if coordinates already exist, infer action_type from Action field
                if action_info['coordinates'] and action_info['action_type'] == 'unknown' and self.model_type == "glm4v":
                    # Check whether Action field is click or scroll
                    simple_action_match = re.search(r'Action:\s*(click|scroll)\s*\(', response, re.IGNORECASE)
                    if simple_action_match:
                        action_info['action_type'] = simple_action_match.group(1).lower()
                        self.logger.info(f"🔧 GLM4V: Inferred action_type from Action field = {action_info['action_type']}")
            
            # Special case: if coordinates exist but no action type, infer as click
            if action_info['coordinates'] and action_info['action_type'] == 'unknown':
                action_info['action_type'] = 'click'
                self.logger.info(f"🔍 Inferred as click action (coordinates found but no explicit action word)")
            
            # Fallback logic for extracting product name
            if not action_info['product_name']:
                # Check whether it is a product description (no action word)
                if not any(word in response.lower() for word in ['click', 'scroll', 'tap', 'select', 'action']):
                    # The entire response might be the product name
                    cleaned_response = self.clean_product_name(response)
                    if self.is_valid_product_name(cleaned_response):
                        action_info['product_name'] = cleaned_response
                        self.logger.info(f"🔍 Recognized entire response as product name: {action_info['product_name']}")
        
        # 🔑 CRITICAL: Final Thought-Action consistency check (after all parsing is complete)
        # Extract Thought and Action from response
        thought_action_match = re.search(r'Thought:\s*(.+?)\s*Action:\s*(.+)', response, re.DOTALL | re.IGNORECASE)
        if thought_action_match:
            thought_text = thought_action_match.group(1).strip()
            action_text = thought_action_match.group(2).strip()
            
            # 🔧 GLM4V special handling: judge only based on Action field, do not use Thought inference
            if self.model_type == "glm4v":
                # GLM4V Thought often contains "scroll to see if..." but Action is click
                # We only check whether the Action field explicitly contains scroll
                scroll_action_keywords = ['scroll(', 'scrolling', 'scroll down', 'scroll up']
                
                if any(keyword in action_text.lower() for keyword in scroll_action_keywords):
                    if action_info['action_type'] != 'scroll':
                        self.logger.info(f"🔧 GLM4V: Action field contains scroll, corrected to scroll")
                        action_info['action_type'] = 'scroll'
                    # Infer scroll direction from Action
                    if 'down' in action_text.lower():
                        action_info['direction'] = 'down'
                    elif 'up' in action_text.lower():
                        action_info['direction'] = 'up'
                # If Action is click and coordinates exist, keep as click
                elif 'click(' in action_text.lower() and action_info['coordinates']:
                    if action_info['action_type'] != 'click':
                        self.logger.info(f"🔧 GLM4V: Action field is click and coordinates exist, corrected to click")
                        action_info['action_type'] = 'click'
            else:
                # UI-TARS/Qwen3VL: check Thought and Action consistency
                scroll_keywords = ['scroll', 'scrolling', 'scroll down', 'scroll up', 'keep scrolling', 
                                 'continue scrolling', 'need to scroll', 'should scroll']
                
                # If Thought explicitly says scroll but Action was parsed as click, do smart correction
                if action_info['action_type'] == 'click' and any(keyword in thought_text.lower() for keyword in scroll_keywords):
                    self.logger.warning(f"⚠️ Thought-Action inconsistency detected:")
                    self.logger.warning(f"   Thought says: {thought_text[:150]}...")
                    self.logger.warning(f"   But Action was parsed as: click at {action_info['coordinates']}")
                    self.logger.warning(f"   🔧 Auto-corrected: click → scroll")
                    
                    # Correct action_type
                    action_info['action_type'] = 'scroll'
                    
                    # Infer scroll direction from Thought
                    if any(word in thought_text.lower() for word in ['down', 'below', 'more', 'continue', 'further', 'next']):
                        action_info['direction'] = 'down'
                    elif any(word in thought_text.lower() for word in ['up', 'back', 'previous', 'earlier', 'above']):
                        action_info['direction'] = 'up'
                else:
                    action_info['direction'] = 'down'  # Default downward
                
                self.logger.info(f"   ✅ Correction complete:")
                self.logger.info(f"      New action type: scroll")
                self.logger.info(f"      Scroll direction: {action_info['direction']}")
                self.logger.info(f"      Scroll start: {action_info['coordinates']}")
        
        # Output parsing result
        self.logger.info("📊 Parse result:")
        self.logger.info(f"   Action type: {action_info['action_type']}")
        self.logger.info(f"   Coordinates: {action_info['coordinates']}")
        self.logger.info(f"   Product name: {action_info['product_name']}")
        self.logger.info(f"   Selection reason: {action_info.get('selection_reason', 'not provided')}")
        self.logger.info(f"   Raw action: {action_info['raw_action']}")
        if action_info['action_type'] == 'scroll':
            self.logger.info(f"   Scroll direction: {action_info.get('direction', 'unknown')}")
        
        return action_info
    
    def extract_action_info_with_coordinate_check(self, response: str, scenario: str, variant_name: str, viewport=None, model_result=None) -> Dict[str, Any]:
        """Enhanced action info extraction with coordinate hit evaluation and coordinate mapping
        
        Args:
            response: raw response text from UI-TARS, Qwen3-VL, or GLM-4V
            scenario: scenario name (amazon, booking2, ebay2, etc.)
            variant_name: variant name
            viewport: ScrollingViewport instance for coordinate mapping
            model_result: complete return result from generate_model_response, used for Qwen3-VL/GLM-4V coordinate extraction
            
        Returns:
            Dict containing action info and coordinate hit result
        """
        # First use existing method to extract action info
        action_info = self.extract_action_info(response)
        
        # 🎯 For Qwen3-VL and GLM-4V, prefer coordinates returned by wrapper (already parsed and validated)
        if self.model_type in ["qwen3vl", "glm4v"] and model_result and model_result.get('coordinates'):
            wrapper_coords = model_result['coordinates']
            if wrapper_coords and isinstance(wrapper_coords, (tuple, list)) and len(wrapper_coords) == 2:
                self.logger.info(f"🔧 Using Qwen3-VL wrapper-parsed coordinates: {wrapper_coords}")
                action_info['coordinates'] = wrapper_coords
        
        # If coordinate info exists, perform coordinate mapping and target hit evaluation
        if action_info.get('coordinates') and isinstance(action_info['coordinates'], (tuple, list)) and len(action_info['coordinates']) == 2:
            viewport_x, viewport_y = action_info['coordinates']
            
            # 🎯 Coordinate mapping: map viewport coordinates back to full-screenshot coordinates
            if viewport is not None:
                full_x, full_y = viewport.map_viewport_coords_to_full(viewport_x, viewport_y)
                action_info['full_coordinates'] = (full_x, full_y)
                self.logger.info(f"📐 Coordinate mapping: viewport({viewport_x},{viewport_y}) -> full image({full_x},{full_y})")
                
                # Use mapped full coordinates for target hit evaluation
                click_x, click_y = full_x, full_y
            else:
                # If no viewport info, use original coordinates
                click_x, click_y = viewport_x, viewport_y
                action_info['full_coordinates'] = (click_x, click_y)
                self.logger.warning("⚠️ No viewport info, using original coordinates")
            
            # Use coordinate manager to judge whether target is hit (strict rectangle mode)
            coordinate_hit_result = self.coordinate_manager.is_click_in_target_area(
                scenario=scenario,
                variant_name=variant_name,
                click_x=click_x,
                click_y=click_y,
                hit_radius=0  # Force strict rectangle judgment only
            )
            
            # Add coordinate hit result to action info
            action_info['coordinate_hit_result'] = coordinate_hit_result
            action_info['is_coordinate_hit'] = coordinate_hit_result['is_hit']
            
            # Output coordinate hit judgment result
            if coordinate_hit_result['is_hit']:
                self.logger.info(f"🎯 Coordinate hit judgment: ✅ Target hit!")
                self.logger.info(f"   {coordinate_hit_result['message']}")
                self.logger.info(f"   Hit method: {coordinate_hit_result.get('hit_method', [])}")
            else:
                self.logger.info(f"🎯 Coordinate hit judgment: ❌ Target missed")
                self.logger.info(f"   {coordinate_hit_result['message']}")
                self.logger.info(f"   Distance to center: {coordinate_hit_result.get('distance_to_center', 0):.1f}px")
        else:
            # No coordinate info
            action_info['coordinate_hit_result'] = None
            action_info['is_coordinate_hit'] = False
            action_info['full_coordinates'] = None
            self.logger.info("🎯 Coordinate hit judgment: ⚠️ No coordinate info")
        
        return action_info
    
    def clean_product_name(self, name: str) -> str:
        """Clean product name by removing unnecessary prefixes and suffixes"""
        if not name:
            return ""
        
        # Remove common prefixes
        prefixes_to_remove = [
            'the most attractive', 'the most appealing', 'the best', 'the', 'a ',
            'most attractive', 'most appealing', 'best', 'attractive', 'appealing',
            # 🎯 New: instruction template-related prefixes
            'add to cart', 'button below', 'product image', 'below the product',
            'add the', 'the product', 'this product', 'that product'
        ]
        
        cleaned = name.strip()
        for prefix in prefixes_to_remove:
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].strip()
        
        # Remove common suffixes
        suffixes_to_remove = [
            'because', 'at coordinates', 'at position', 'located at',
            # 🎯 New: instruction template-related suffixes
            'button', 'to add', 'to cart', 'below the product', 'product image',
            'below', 'above', 'near', 'next to'
        ]
        
        for suffix in suffixes_to_remove:
            if suffix.lower() in cleaned.lower():
                cleaned = cleaned[:cleaned.lower().find(suffix.lower())].strip()
        
        # 🎯 New: remove quotes and special characters
        cleaned = cleaned.strip('"\'()[]{}').strip()
        
        return cleaned
    
    def is_valid_product_name(self, name: str) -> bool:
        """Validate whether it is a valid product name"""
        if not name or len(name.strip()) < 3:
            return False
        
        # Filter out obviously non-product words
        invalid_words = [
            'thought', 'action', 'click', 'page', 'website', 'button', 'coordinates',
            'position', 'location', 'because', 'reason', 'will', 'should', 'must',
            'need', 'want', 'can', 'could', 'would', 'this', 'that', 'these', 'those'
        ]
        
        name_lower = name.lower().strip()
        
        # If entirely invalid words, reject
        if name_lower in invalid_words:
            return False
        
        # If mainly composed of invalid words, reject
        words = name_lower.split()
        if len(words) <= 2 and all(word in invalid_words for word in words):
            return False
        
        return True

    def detect_semantic_contradiction(self, response: str, target_product_names: list, scenario: str) -> bool:
        """Detect whether the response has an obvious contradiction with the target product
        
        Only the following cases are considered contradictions:
        1. Explicitly mentions a different brand of the same product category
        2. Explicitly mentions non-matching key attributes (e.g. size, model number, etc.)
        3. Explicitly states it is not the target product
        
        Returns:
            bool: True means contradiction exists, False means no contradiction
        """
        if not response or not target_product_names:
            return False
            
        response_lower = response.lower()
        
        # Get key info of the target product
        for target_product in target_product_names:
            target_lower = target_product.lower()
            
            # 1. Brand contradiction detection
            if self.has_brand_contradiction(response_lower, target_lower, scenario):
                return True
                
            # 2. Key attribute contradiction detection  
            if self.has_attribute_contradiction(response_lower, target_lower, scenario):
                return True
                
            # 3. Explicit negation detection
            if self.has_explicit_negation(response_lower, target_lower):
                return True
        
        return False
    
    def has_brand_contradiction(self, response: str, target: str, scenario: str) -> bool:
        """Detect brand contradiction"""
        # Competing brands for different scenarios
        brand_conflicts = {
            'amazon': {
                'hp': ['dell', 'lenovo', 'acer', 'asus'],
                'dell': ['hp', 'lenovo', 'acer', 'asus'],
                'lenovo': ['hp', 'dell', 'acer', 'asus']
            },
            'ebay2': {
                'apple': ['samsung', 'sony', 'bose', 'jbl'],
                'earbuds': ['airbuds','galaxy buds', 'galaxy earbuds', 'sony wf', 'bose quietcomfort']
            },
            'booking2': {
                'westin': ['hilton', 'marriott', 'hyatt', 'sheraton'],
            },
            'expedia2': {
                'heritage': ['hilton', 'marriott', 'hyatt', 'sheraton'],
            },
            'expedia_top': {
                'montage': ['hilton', 'marriott', 'hyatt', 'sheraton'],
            }
        }
        
        if scenario not in brand_conflicts:
            return False
            
        # Detect target brand
        target_brands = []
        for brand in brand_conflicts[scenario].keys():
            if brand in target:
                target_brands.append(brand)
                
        # Detect whether competing brands are mentioned in the response
        for target_brand in target_brands:
            competitor_brands = brand_conflicts[scenario].get(target_brand, [])
            for competitor in competitor_brands:
                if competitor in response:
                    return True
                    
        return False
    
    def has_attribute_contradiction(self, response: str, target: str, scenario: str) -> bool:
        """Detect key attribute contradiction"""
        # Currently mainly detects size contradiction
        if scenario in ['amazon', 'amazon_bottom', 'ebay2']:
            # Extract sizes from target and response
            target_sizes = self.extract_screen_sizes(target)
            response_sizes = self.extract_screen_sizes(response)
            
            if target_sizes and response_sizes:
                # If a different size is explicitly mentioned, treat as contradiction
                for t_size in target_sizes:
                    for r_size in response_sizes:
                        # Allow small differences (e.g. 15.6 vs 15), but not large ones
                        size_diff = abs(float(t_size) - float(r_size))
                        if size_diff > 2.0:  # Difference over 2 inches is considered a contradiction
                            return True
        
        return False
    
    def has_explicit_negation(self, response: str, target: str) -> bool:
        """Detect explicit negation expressions"""
        # Detect explicit negation vocabulary
        negation_patterns = [
            'not the', 'not this', 'instead of', 'rather than', 
            'different from', 'not what', 'wrong', 'incorrect'
        ]
        
        for pattern in negation_patterns:
            if pattern in response:
                return True
                
        return False

    def analyze_product_mention(self, response: str, target_product_names: list, scenario: str) -> dict:
        """Analyze whether the answer mentions the target product - returns detailed analysis result"""
        if not response or not target_product_names:
            return {
                'is_successful': False,
                'success_level': 'failure',
                'semantic_score': 0.0,
                'analysis_type': 'no_content'
            }
        
        # Compute semantic score
        semantic_score = self.calculate_semantic_score(response, target_product_names, scenario)
        
        # Detect whether it is a descriptive answer
        is_descriptive = self.is_descriptive_response(response.strip().lower())
        
        # Detailed semantic score analysis log
        if len(target_product_names) > 0:
            product_name = target_product_names[0]
            response_words = response.strip().lower().split()
            product_words = product_name.lower().split()
            
            # Analyze matched vocabulary
            matched_words = []
            for word in product_words[:5]:  # Analyze only the first 5 words
                if len(word) > 2 and word in response.lower():
                    matched_words.append(word)
            
            # Analyze prefix matching
            prefix_matches = []
            for i in range(min(len(response_words), len(product_words), 4)):
                if response_words[i] == product_words[i]:
                    prefix_matches.append(f"'{response_words[i]}'exact match")
                elif len(response_words[i]) >= 2 and product_words[i].startswith(response_words[i]):
                    prefix_matches.append(f"'{response_words[i]}'prefix-matches'{product_words[i]}'")
            
            self.logger.debug(f"      🔍 Semantic analysis details:")
            self.logger.debug(f"         Answer words: {response_words[:5]}")
            self.logger.debug(f"         Target words: {product_words[:5]}")
            self.logger.debug(f"         Matched words: {matched_words}")
            self.logger.debug(f"         Prefix matches: {prefix_matches}")
            self.logger.debug(f"         Answer length: {len(response_words)} words")
            self.logger.debug(f"         Final score: {semantic_score:.3f}")
        
        # 🎯 Simplified semantic analysis logic: no contradiction = success, only score differences
        # 
        # Core principles:
        # 1. As long as extracted content has no obvious contradiction with target, it counts as semantic success
        # 2. success_level is unified as 'semantic_success'  
        # 3. semantic_score reflects differences in match quality
        #
        # Determine whether there is an obvious contradiction (these cases count as failure)
        has_contradiction = self.detect_semantic_contradiction(response, target_product_names, scenario)
        
        if has_contradiction:
            # Only obvious contradictions count as failure
            success_level = 'contradiction_failure'
            is_successful = False
            success_quality = 'contradiction'
        else:
            # No contradiction = success; score reflects match quality
            success_level = 'semantic_success'
            is_successful = True
            
            # Provide quality description based on score
            if semantic_score >= 0.8:
                success_quality = 'excellent_match'  # Perfect match
            elif semantic_score >= 0.6:
                success_quality = 'good_match'       # Good match
            elif semantic_score >= 0.3:
                success_quality = 'acceptable_match' # Acceptable match
            else:
                success_quality = 'weak_match'       # Weak match but no contradiction
        
        # Analyze answer type
        response_clean = response.strip().lower()
        response_words = response_clean.split()
        
        # Judge whether it is a descriptive answer
        descriptive_indicators = [
            'the image', 'this image', 'screenshot', 'shows', 'displays', 'contains',
            'there is', 'there are', 'i can see', 'visible', 'appears', 'seems'
        ]
        is_descriptive = any(indicator in response_clean for indicator in descriptive_indicators)
        
        # Analyze answer length
        if len(response_words) > 30:
            response_type = 'long_descriptive' if is_descriptive else 'long_answer'
        elif len(response_words) > 10:
            response_type = 'medium_descriptive' if is_descriptive else 'medium_answer'
        else:
            response_type = 'short_answer'
        
        # Build return result
        result = {
            'is_successful': is_successful,
            'success_level': success_level,
            'semantic_score': semantic_score,
            'response_type': response_type,
            'is_descriptive': is_descriptive,
            'analysis_type': 'semantic_analysis'
        }
        
        # If successful, add quality description
        if is_successful and 'success_quality' in locals():
            result['success_quality'] = success_quality
            
        return result
    
    def calculate_semantic_score(self, response: str, target_product_names: list, scenario: str) -> float:
        """Calculate semantic matching score (0-1) - enhanced brand and product type matching"""
        if not response:
            return 0.0
            
        response_clean = response.strip().lower()
        score = 0.0
        
        # Detect whether it is a descriptive answer(only for other logic, does not affect scoring)
        is_descriptive = self.is_descriptive_response(response_clean)
        
        # 🎯 Remove length penalty: no longer penalize based on answer length
        # Focus only on content match quality, do not penalize detailed answers
        
        # Base score: check target product name matching
        for product_name in target_product_names:
            product_lower = product_name.lower()
            
            # Full product name match gets the highest score
            if product_lower in response_clean:
                score += 0.9
                break  # Stop once a full match is found
            
            # Extract brand and product type for matching
            brand_score = self.calculate_brand_match_score(response_clean, product_lower)
            product_type_score = self.calculate_product_type_match_score(response_clean, product_lower, scenario)
            
            # Brand matching score
            if brand_score > 0:
                score += brand_score
            
            # Product type matching score
            if product_type_score > 0:
                score += product_type_score
            
            # Key difference detection and penalty
            difference_penalty = self.calculate_difference_penalty(response_clean, product_lower, scenario)
            if difference_penalty > 0:
                score = max(0.0, score - difference_penalty)
            
            # Check prefix matching (handle truncation cases)
            response_words = response_clean.split()
            product_words = product_lower.split()
            
            if len(response_words) >= 2 and len(product_words) >= 2:
                matched_prefix_words = 0
                prefix_score = 0.0
                
                for i in range(min(len(response_words), len(product_words))):
                    if response_words[i] == product_words[i]:
                        matched_prefix_words += 1
                        prefix_score += 0.15  # Per fully matched word
                    elif len(response_words[i]) >= 2 and product_words[i].startswith(response_words[i]):
                        # Handle truncated words, e.g. "la" matching "laptop"
                        matched_prefix_words += 1
                        prefix_score += 0.12  # Partial match slightly lower score
                    elif len(product_words[i]) >= 2 and response_words[i].startswith(product_words[i]):
                        matched_prefix_words += 1
                        prefix_score += 0.12
                    else:
                        break
                
                # If prefix matching is good, give high score
                if matched_prefix_words >= 3:  # At least 3 words prefix matching
                    additional_prefix_score = min(prefix_score, 0.6)  # Reduce prefix score weight to avoid duplication with brand score
                    score += additional_prefix_score
                    break
                elif matched_prefix_words >= 2:  # At least 2 words prefix matching
                    additional_prefix_score = min(prefix_score, 0.4)
                    score += additional_prefix_score
                    break
            
            # Check matching of key parts of product name (exclude already-scored brand words)
            product_words = [word for word in product_lower.split() if len(word) > 2]
            if product_words:
                # Exclude brand words to avoid double scoring
                brand_words = self.extract_brand_words(product_lower, scenario)
                non_brand_words = [word for word in product_words if word not in brand_words]
                
                if non_brand_words:
                    matched_words = sum(1 for word in non_brand_words if word in response_clean)
                    word_match_ratio = matched_words / len(non_brand_words)
                    
                    if word_match_ratio >= 0.5:  # 50%+ match
                        score += 0.3 * word_match_ratio  # Reduce generic word match weight
                    elif word_match_ratio >= 0.2:  # 20%-50% match
                        score += 0.15 * word_match_ratio
        
        # 🎯 Remove length penalty: directly return score based on content matching
        # Ensure score is in 0-1 range
        return min(1.0, score)
    
    def is_descriptive_response(self, response: str) -> bool:
        """Judge whether the answer is a descriptive answer"""
        descriptive_indicators = [
            'the image', 'this image', 'screenshot', 'shows', 'displays', 'contains',
            'there is', 'there are', 'i can see', 'visible', 'appears', 'seems',
            'the screen', 'displayed on', 'shown', 'featured', 'prominently',
            'the page', 'the website', 'the product', 'this product'
        ]
        
        # Check for descriptive phrases
        for indicator in descriptive_indicators:
            if indicator in response.lower():
                return True
        
        # Check sentence structure (if multiple sentences with descriptive vocabulary)
        sentences = response.split('.')
        if len(sentences) > 2:  # Multi-sentence answer
            descriptive_words = ['with', 'which', 'that', 'showing', 'featuring']
            if any(word in response.lower() for word in descriptive_words):
                return True
        
        return False

    def analyze_product_mention_tagbased(self, response: str, scenario_name: str) -> tuple:
        """Analyze product mention using tag-based method"""
        if not response or not response.strip():
            return 0, 0.0
        
        # Get target product tags
        target_tags = self.get_target_product_tags(scenario_name)
        
        # Extract products from response
        extracted_products = self.extract_products_from_response(response)
        
        if not extracted_products:
            return 0, 0.0
        
        best_match = None
        best_score = 0.0
        
        # Perform tag matching for each extracted product
        for product in extracted_products:
            match_result = self.match_product_with_tags(product, target_tags)
            
            if match_result['semantic_success'] == 1:
                # If successful match, select the one with highest score
                if match_result['semantic_score'] > best_score:
                    best_score = match_result['semantic_score']
                    best_match = match_result
        
        # If any successful match, return 1 and the highest score
        if best_match:
            return 1, best_score
        else:
            return 0, 0.0
    
    def calculate_semantic_score_tagbased(self, all_responses: list, scenario_name: str) -> tuple:
        """Calculate semantic score for the entire test using tag-based method"""
        if not all_responses:
            return 0, 0.0
        
        test_semantic_success = 0
        max_test_score = 0.0
        
        for response in all_responses:
            semantic_success, semantic_score = self.analyze_product_mention_tagbased(response, scenario_name)
            
            if semantic_success == 1:
                test_semantic_success = 1
                max_test_score = max(max_test_score, semantic_score)
        
        return test_semantic_success, max_test_score
    
    # def calculate_semantic_score_hybrid(self, all_responses: list, scenario_name: str, target_product_names: list) -> tuple:
    #     """Hybrid semantic analysis method: combines original word-based method with new tag-based method"""
    #     # This method is commented out; replaced by the new tag-based method
    #     # if not all_responses:
    #     #     return 0, 0.0
    #     # 
    #     # # Method 1: original word-based analysis
    #     # original_test_success = 0
    #     # original_max_score = 0.0
    #     # 
    #     # for response in all_responses:
    #     #     original_analysis = self.analyze_product_mention(response, target_product_names, scenario_name)
    #     #     original_success = 1 if original_analysis.get('is_successful', False) else 0
    #     #     original_score = original_analysis.get('semantic_score', 0.0)
    #     #     
    #     #     if original_success == 1:
    #     #         original_test_success = 1
    #     #         original_max_score = max(original_max_score, original_score)
    #     # 
    #     # # Method 2: new tag-based analysis
    #     # tag_test_success, tag_max_score = self.calculate_semantic_score_tagbased(all_responses, scenario_name)
    #     # 
    #     # # Combine results from both methods
    #     # # If either method succeeds, consider semantic analysis successful
    #     # combined_success = max(original_test_success, tag_test_success)
    #     # 
    #     # # Score is the maximum of both methods
    #     # combined_score = max(original_max_score, tag_max_score)
    #     # 
    #     # # Detailed log output
    #     # self.logger.info(f"🔍 Hybrid semantic analysis result:")
    #     # self.logger.info(f"   Original method: success={original_test_success}, score={original_max_score:.3f}")
    #     # self.logger.info(f"   Tag method: success={tag_test_success}, score={tag_max_score:.3f}")
    #     # self.logger.info(f"   🎯 Final result: success={combined_success}, score={combined_score:.3f}")
    #     # 
    #     # return combined_success, combined_score

    def calculate_brand_match_score(self, response: str, target_product: str) -> float:
        """Calculate brand matching score"""
        # Common brand dictionary
        brand_mapping = {
            'hp': ['hp', 'hewlett', 'packard'],
            'apple': ['apple', 'iphone', 'macbook', 'airpods'],
            'dell': ['dell', 'inspiron', 'alienware'],
            'lenovo': ['lenovo', 'thinkpad'],
            'acer': ['acer', 'aspire'],
            'asus': ['asus', 'zenbook'],
            'samsung': ['samsung', 'galaxy'],
            'westin': ['westin'],
            'heritage': ['heritage'],
            'marriott': ['marriott'],
            'hilton': ['hilton'],
            'hyatt': ['hyatt']
        }
        
        score = 0.0
        
        # Check brand in target product
        for brand, variants in brand_mapping.items():
            if any(variant in target_product for variant in variants):
                # Check whether the brand is mentioned in the answer
                if any(variant in response for variant in variants):
                    score += 0.4  # High score for brand match
                    break  # Stop once a brand match is found
        
        return score
    
    def calculate_difference_penalty(self, response: str, target_product: str, scenario: str) -> float:
        """Calculate penalty score for key differences - more lenient for descriptive answers"""
        penalty = 0.0
        
        # Detect whether it is a descriptive answer
        is_descriptive = self.is_descriptive_response(response)
        
        # Screen size difference detection (for electronic products)
        if scenario in ['amazon', 'amazon_bottom', 'ebay2']:
            response_sizes = self.extract_screen_sizes(response)
            target_sizes = self.extract_screen_sizes(target_product)
            
            if response_sizes and target_sizes:
                # If a significant size difference is detected
                size_diff = abs(max(response_sizes) - max(target_sizes))
                if size_diff > 1.0:  # Size difference exceeds 1 inch
                    base_penalty = 0.2 if not is_descriptive else 0.1  # Halved penalty for descriptive answers
                    penalty += base_penalty
                    if size_diff > 2.0:  # Size difference exceeds 2 inches
                        extra_penalty = 0.1 if not is_descriptive else 0.05  # Halved penalty for descriptive answers
                        penalty += extra_penalty
        
        # Within-brand model number difference detection
        brand_penalty = self.calculate_brand_model_penalty(response, target_product)
        if is_descriptive:
            brand_penalty *= 0.7  # Reduce brand model penalty by 30% for descriptive answers
        penalty += brand_penalty
        
        # Product series difference detection
        series_penalty = self.calculate_series_penalty(response, target_product, scenario)
        if is_descriptive:
            series_penalty *= 0.5  # Reduce series difference penalty by 50% for descriptive answers
        penalty += series_penalty
        
        return min(penalty, 0.4)  # Maximum penalty not exceeding 0.4
    
    def extract_screen_sizes(self, text: str) -> list:
        """Extract screen sizes from text"""
        import re
        
        # Match formats like: 14", 15.6", 13.3", etc.
        size_patterns = [
            r'(\d+(?:\.\d+)?)\s*["\']',  # 14", 15.6"
            r'(\d+(?:\.\d+)?)\s*inch',   # 14 inch, 15.6 inch
            r'(\d+(?:\.\d+)?)["\']',     # 14", 15.6"
            r'(\d+)\.(\d+)',             # 15.6, 14.0
            r'\b(\d{2})\b(?=\s*(?:laptop|chromebook|inch|"))', # 14 laptop, 15 chromebook
            r'hp\s+(\d{2})\b',           # HP 14, HP 15
        ]
        
        sizes = []
        for pattern in size_patterns:
            matches = re.findall(pattern, text.lower())
            for match in matches:
                try:
                    if isinstance(match, tuple):
                        # Handle cases like (15, 6)
                        if match[1]:  # Has decimal part
                            size = float(f"{match[0]}.{match[1]}")
                        else:
                            size = float(match[0])
                    else:
                        size = float(match)
                    
                    # Keep only reasonable screen size range
                    if 10.0 <= size <= 30.0:
                        sizes.append(size)
                except (ValueError, IndexError):
                    continue
        
        return list(set(sizes))  # Deduplicate
    
    def calculate_brand_model_penalty(self, response: str, target_product: str) -> float:
        """Calculate penalty for same-brand different model"""
        penalty = 0.0
        
        # Detect HP model number difference
        if 'hp' in response.lower() and 'hp' in target_product.lower():
            response_models = self.extract_hp_models(response)
            target_models = self.extract_hp_models(target_product)
            
            if response_models and target_models:
                # If model numbers completely do not match
                if not any(r_model in target_models for r_model in response_models):
                    penalty += 0.15  # Same-brand different model penalty
        
        # Detect Apple model difference
        if 'apple' in response.lower() and 'apple' in target_product.lower():
            # Detect AirPods generation difference
            response_gen = self.extract_airpods_generation(response)
            target_gen = self.extract_airpods_generation(target_product)
            
            if response_gen and target_gen and response_gen != target_gen:
                penalty += 0.2  # AirPods generation difference penalty
        
        return penalty
    
    def extract_hp_models(self, text: str) -> list:
        """Extract HP model number"""
        import re
        
        models = []
        text_lower = text.lower()
        
        # Common HP model patterns
        hp_patterns = [
            r'hp\s+(\w+)',           # HP Pavilion, HP Envy
            r'(\d+)(?:e|i)\b',       # 14e, 15i
            r'chromebook\s+(\d+)',   # Chromebook 14
            r'pavilion\s+(\w+)',     # Pavilion x360
            r'envy\s+(\w+)',         # Envy 13
        ]
        
        for pattern in hp_patterns:
            matches = re.findall(pattern, text_lower)
            models.extend(matches)
        
        return models
    
    def extract_airpods_generation(self, text: str) -> str:
        """Extract AirPods generation info"""
        import re
        
        text_lower = text.lower()
        
        # Detect generation info
        gen_patterns = [
            r'(\d+)(?:st|nd|rd|th)\s+generation',  # 4th generation
            r'generation\s+(\d+)',                 # generation 4
            r'airpods\s+(\d+)',                    # AirPods 4
        ]
        
        for pattern in gen_patterns:
            match = re.search(pattern, text_lower)
            if match:
                return match.group(1)
        
        return None
    
    def calculate_series_penalty(self, response: str, target_product: str, scenario: str) -> float:
        """Calculate product series difference penalty"""
        penalty = 0.0
        
        if scenario in ['amazon', 'amazon_bottom', 'ebay2']:
            # Chromebook vs traditional Laptop
            response_is_chromebook = 'chromebook' in response.lower()
            target_is_chromebook = 'chromebook' in target_product.lower()
            
            if response_is_chromebook != target_is_chromebook:
                penalty += 0.1  # Chromebook vs traditional laptop difference penalty
        
        return penalty

    def calculate_product_type_match_score(self, response: str, target_product: str, scenario: str) -> float:
        """Calculate product type matching score - restore basic type matching, also consider brand matching bonus"""
        type_mapping = {
            'laptop': ['laptop', 'computer', 'notebook', 'pc', 'chromebook'],  # Added chromebook
            'headphones': ['headphones', 'earbuds', 'airpods', 'earbud'],
            'hotel': ['hotel', 'resort', 'inn', 'lodge'],
            'news': ['news', 'article', 'story', 'report']
        }
        
        score = 0.0
        
        # Determine expected product type based on scenario
        if scenario in ['amazon', 'amazon_bottom', 'ebay2']:
            if 'laptop' in target_product or 'computer' in target_product or 'chromebook' in target_product:
                expected_types = type_mapping['laptop']
            elif 'airpods' in target_product or 'headphones' in target_product:
                expected_types = type_mapping['headphones']
            else:
                expected_types = []
        elif scenario in ['booking2', 'expedia2', 'expedia_top']:
            expected_types = type_mapping['hotel']
        elif scenario == 'npr':
            expected_types = type_mapping['news']
        else:
            expected_types = []
        
        # Check product type matching
        if expected_types:
            target_has_type = any(ptype in target_product for ptype in expected_types)
            response_has_type = any(ptype in response for ptype in expected_types)
            
            if target_has_type and response_has_type:
                # Score for basic product type matching (distinguish laptop vs iPad etc.)
                base_type_score = 0.15
                
                # Check whether brand matches
                brand_score = self.calculate_brand_match_score(response, target_product)
                
                if brand_score > 0:
                    # Higher score for product type match when brand also matches
                    score += 0.25  # Same brand, same type product
                else:
                    # Different brand but same product type, give base score
                    score += base_type_score  # Different brand but same type product
        
        return score
    
    def extract_brand_words(self, product_name: str, scenario: str) -> list:
        """Extract brand vocabulary from product name"""
        brand_words = []
        
        # Common brand keywords
        brands = ['hp', 'apple', 'dell', 'lenovo', 'acer', 'asus', 'samsung', 
                 'westin', 'heritage', 'marriott', 'hilton', 'hyatt']
        
        for brand in brands:
            if brand in product_name:
                brand_words.append(brand)
        
        return brand_words
    
    def extract_mentioned_products(self, response: str) -> list:
        """Extract product names mentioned in the response"""
        if not response:
            return []
            
        mentioned = []
        response_lower = response.lower()
        
        # General product keywords (includes brands and product types)
        product_keywords = [
            # Computer category
            'hp', 'acer', 'dell', 'lenovo', 'laptop', 'computer', 
            'ryzen', 'touchscreen', 'pavilion', 'aspire', 'inspiron',
            # Hotel category
            'westin', 'hyatt', 'hilton', 'marriott', 'hotel', 'resort',
            'san francisco', 'airport', 'heritage', 'majestic',
            # Electronics
            'apple', 'airpods', 'bluetooth', 'headphones', 'earbud',
            '4th generation', 'iphone', 'samsung',
            # News category
            'pritzker', 'trump', 'chicago', 'elections', 'governor',
            'news', 'article', 'politics'
        ]
        
        for keyword in product_keywords:
            if keyword in response_lower:
                mentioned.append(keyword)
                
        return mentioned
    
    def check_gpu_status(self):
        """Check GPU status"""
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.used,memory.total,temperature.gpu', 
                                   '--format=csv,nounits,noheader'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                self.logger.info("📊 GPU status:")
                for line in result.stdout.strip().split('\n'):
                    gpu_id, used, total, temp = line.split(', ')
                    used_gb = float(used) / 1024
                    total_gb = float(total) / 1024
                    free_gb = total_gb - used_gb
                    self.logger.info(f"  GPU {gpu_id}: {used_gb:.1f}GB/{total_gb:.1f}GB (free: {free_gb:.1f}GB, temp: {temp}°C)")
                return True
        except Exception as e:
            self.logger.warning(f"Failed to get GPU status: {e}")
            return False
    
    def discover_variants(self, scenario_name: str) -> List[str]:
        """Discover all HTML variants in a scenario"""
        scenario_path = Path(self.scenarios[scenario_name]['path'])
        
        if not scenario_path.exists():
            self.logger.error(f"❌ Scenario path does not exist: {scenario_path}")
            return []
        
        # 🔧 FIX: Add sorting to ensure stable order and avoid missing variants
        html_files = sorted(list(scenario_path.glob("*.html")))
        variant_names = [f.stem for f in html_files]
        
        self.logger.info(f"🔍 Found {len(variant_names)} variants in {scenario_name} (sorted):")
        for variant in variant_names[:5]:  # Show first 5
            self.logger.info(f"  - {variant}")
        if len(variant_names) > 5:
            self.logger.info(f"  ... and {len(variant_names) - 5} more variants")
        
        return variant_names
    
    def split_screenshot_into_regions(self, screenshot_path: str, num_regions: int = 4) -> List[Tuple[str, Tuple[int, int, int, int]]]:
        """Split long screenshot into multiple regions"""
        try:
            # Read original screenshot
            img = Image.open(screenshot_path)
            width, height = img.size
            
            # Compute height of each region
            region_height = height // num_regions
            regions = []
            
            screenshot_path_obj = Path(screenshot_path)
            
            for i in range(num_regions):
                y_start = i * region_height
                y_end = (i + 1) * region_height if i < num_regions - 1 else height
                
                # Extract region
                region_img = img.crop((0, y_start, width, y_end))
                
                # Save region image
                variant_name = screenshot_path_obj.stem
                region_filename = f"region_{i}_{variant_name}.png"
                region_path = self.output_dir / region_filename
                region_img.save(region_path)
                
                # Record region info (x_start, y_start, x_end, y_end)
                region_bounds = (0, y_start, width, y_end)
                regions.append((str(region_path), region_bounds))
            
            return regions
            
        except Exception as e:
            self.logger.error(f"Screenshot splitting failed: {e}")
            return []
    
    def generate_screenshot_and_regions(self, scenario_name: str, variant_name: str, html_file: str = None, disable_scaling: bool = False) -> Dict[str, Any]:
        """Generate screenshots and regions
        
        Args:
            scenario_name: scenario name
            variant_name: variant name 
            html_file: HTML file path (optional)
            disable_scaling: whether to disable scaling (should be True for coordinate validation)
        """
        if html_file:
            html_path = Path(html_file)
        else:
            scenario_info = self.scenarios[scenario_name]
            html_path = Path(scenario_info['path']) / f"{variant_name}.html"
        
        if not html_path.exists():
            self.logger.error(f"❌ HTML file does not exist: {html_path}")
            return None
        
        try:
            self.logger.info(f"📸 Generating screenshots and regions for {scenario_name}/{variant_name}...")
            
            # Generate screenshot - decide whether to scale based on parameters
            if disable_scaling:
                self.logger.info("📍 Coordinate validation mode: disabling smart scaling, preserving original size")
                screenshot_path = self.generate_screenshot_simple(str(html_path), enable_scrolling=True)
            else:
                screenshot_path = self.generate_screenshot_simple(str(html_path))
                
            if not screenshot_path:
                self.logger.error(f"❌ Screenshot generation failed: {variant_name}")
                return None
            
            # Analyze screenshot dimensions
            with Image.open(screenshot_path) as img:
                screenshot_info = {
                    'path': screenshot_path,
                    'size': img.size,
                    'width': img.width,
                    'height': img.height
                }
            
            self.logger.info(f"✅ Screenshot generated successfully: {screenshot_info['size']}")
            
            # Generate regions (if enabled in config)
            regions_info = {}
            if self.test_configs['use_regions']:
                try:
                    # Use simple vertical splitting method
                    regions = self.split_screenshot_into_regions(screenshot_path, num_regions=4)
                    regions_info = {
                        'regions': regions,
                        'count': len(regions) if regions else 0
                    }
                    self.logger.info(f"🔧 Generated {regions_info['count']} regions")
                except Exception as e:
                    self.logger.warning(f"⚠️ Region generation failed: {e}")
                    regions_info = {'regions': [], 'count': 0}
            
            return {
                'screenshot': screenshot_info,
                'regions': regions_info,
                'html_path': str(html_path)
            }
            
        except Exception as e:
            self.logger.error(f"❌ Screenshot/region generation failed: {e}")
            traceback.print_exc()
            return None
    
    def test_variant_with_rotating_gpu(self, scenario_name: str, variant_name: str, 
                                     screenshot_path: str) -> Dict[str, Any]:
        """Test single variant with rotating GPU - supports intelligent response handling"""
        scenario_info = self.scenarios[scenario_name]
        
        try:
            # Get true target coordinates for this variant from coordinate manager
            target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
            if target_coordinates:
                # Compute center coordinates
                center_x = target_coordinates['x'] + target_coordinates['width'] // 2
                center_y = target_coordinates['y'] + target_coordinates['height'] // 2
                target_coords = (center_x, center_y)
                self.logger.info(f"🎯 Got target coordinates from coordinate file: {target_coords}")
            else:
                # If variant is not in coordinate file, use default value
                target_coords = (400, 2000)  # Default coordinates
                self.logger.warning(f"⚠️ Variant not in coordinate file, using default coordinates: {target_coords}")
            
            # Set rotating GPU test parameters
            self.gpu_tester.target_position = target_coords
            self.gpu_tester.target_region_index = scenario_info['target_region_index']
            
            # Pass scenario-specific target product info
            if hasattr(self.gpu_tester, 'set_scenario_info'):
                self.gpu_tester.set_scenario_info(scenario_name, scenario_info)
            
            self.logger.info(f"🔄 Starting multi-level semantic analysis test: {scenario_name}/{variant_name}")
            self.logger.info(f"🎯 Target coordinates: {target_coords}")
            self.logger.info(f"📝 Target product: {', '.join(scenario_info['target_product_names'][:2])}...")
            
            # Execute new semantic analysis test
            try:
                results = self.test_variant_regular(
                    scenario_name=scenario_name,
                    variant_name=variant_name,
                    screenshot_path=screenshot_path
                )
                
                if results:
                    # New multi-level statistics are already computed in test_variant_regular
                    self.logger.info(f"⏱️ Total elapsed time: {results.get('total_time', 'N/A')}s")
                    return results
                else:
                    self.logger.error(f"❌ {scenario_name}/{variant_name} test failed - returned None")
                    return None
            except Exception as e:
                self.logger.error(f"❌ {scenario_name}/{variant_name} test exception: {e}")
                traceback.print_exc()
                return None
                
        except Exception as e:
            self.logger.error(f"❌ Rotating GPU test failed: {e}")
            traceback.print_exc()
            return None
    
    def test_variant_regular(self, scenario_name: str, variant_name: str, 
                           screenshot_path: str, num_tests: int = None) -> Dict[str, Any]:
        """Test single variant using ScrollingViewport method, avoiding OOM issues"""
        import time
        import os
        import traceback
        
        print(f"🔍 Entering test_variant_regular: {scenario_name}/{variant_name}")
        
        start_time = time.time()
        
        # 🔧 Fix: move basic setup out of try-except to avoid overall failure
        try:
            scenario_info = self.scenarios[scenario_name]
        except KeyError:
            self.logger.error(f"❌ Scenario does not exist: {scenario_name}")
            return {
                'results': [],
                'total_tests': 0,
                'successful_tests': 0, 
                'overall_success_rate': 0,
                'error': f'Scenario {scenario_name} not found'
            }

        # Use the num_tests parameter passed in; if not provided, use the default from config
        tests_to_run = num_tests if num_tests is not None else self.test_configs['tests_per_variant']
        
        self.logger.info(f"🔧 Starting semantic analysis test: {scenario_name}/{variant_name}")
        self.logger.info(f"📊 Test count: {tests_to_run}")
        
        # Initialize ScrollingViewport to handle large screenshots and avoid OOM
        try:
            viewport = ScrollingViewport(
                full_screenshot_path=screenshot_path,
                viewport_height=1200,  # Use appropriate viewport height to avoid OOM
                viewport_width=1280,   # 🎯 Use standard width to preserve full page info
                scroll_step=600
            )
        except Exception as e:
            self.logger.error(f"❌ ScrollingViewport initialization failed: {e}")
            return {
                'results': [],
                'total_tests': 0,
                'successful_tests': 0, 
                'overall_success_rate': 0,
                'error': f'Failed to initialize viewport: {e}'
            }
        
        # Get target coordinates
        target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
        if target_coordinates:
            center_x = target_coordinates['x'] + target_coordinates['width'] // 2
            center_y = target_coordinates['y'] + target_coordinates['height'] // 2
            target_coords = (center_x, center_y)
            self.logger.info(f"🎯 Got target coordinates from coordinate manager: {target_coords}")
        else:
            target_coords = (400, 2000)  # Default coordinates
            self.logger.warning(f"⚠️ Variant not in coordinate manager, using default coordinates: {target_coords}")
        
        results = []
        
        # 🔧 Add state check before test loop starts
        self.logger.info(f"🔄 Starting test loop: need to execute {tests_to_run} test(s)")
        self.logger.info(f"🎯 Target coordinates: {target_coords}")
        self.logger.info(f"📱 Viewport config: {viewport.viewport_width}x{viewport.viewport_height}")
        
        # 🔧 Fix: add separate exception handling per test to avoid single test failure causing entire variant failure
        for test_id in range(tests_to_run):
            self.logger.info(f"TEST {test_id + 1}/{tests_to_run} [START]")
            self.logger.info(f"🏁 Starting test ID: {test_id}")
            
            # 🧹 Clean GPU memory before each test
            if self.model_type == "glm4v":
                try:
                    import torch
                    import gc
                    gc.collect()
                    torch.cuda.empty_cache()
                    self.logger.info(f"🧹 GPU memory cleaned before test {test_id + 1}")
                except Exception as cleanup_error:
                    self.logger.warning(f"⚠️ Memory cleanup failed: {cleanup_error}")
            
            try:
                # 🔄 Interaction loop within a single test: supports scrolling until click
                test_complete = False
                viewport.reset()  # Reset viewport to top
                self.logger.info(f"🔄 Test {test_id + 1}: viewport reset to top")
                scroll_history = []  # Record scroll history and observed content
                interaction_count = 0  # Interaction counter
                scroll_count = 0  # Scroll counter (for statistics only)
                forced_memory_click = False  # Flag whether this is a forced memory decision click
                last_response = ''  # 🔧 Save last response for semantic analysis on timeout
                
                # Use configured max interaction count
                max_interactions = self.test_configs.get('max_interactions', 20)
                while not test_complete and interaction_count < max_interactions:
                    interaction_count += 1
                    self.logger.info(f"INTERACTION #{interaction_count}: Test {test_id + 1}")
                    current_scroll_info = f"Current viewport: y={viewport.current_y_offset}-{viewport.current_y_offset + viewport.viewport_height} ({viewport.viewport_width}x{viewport.viewport_height})"
                    self.logger.info(current_scroll_info)
                    
                    # Get small screenshot of current viewport (so UI-TARS won't OOM)
                    current_view_path = viewport.get_current_view()
                    
                    # Check whether to use final decision prompt
                    scroll_info = viewport.get_scroll_info()
                    at_bottom = scroll_info.get('scroll_progress', 0) >= 0.99  # Reached 99% page bottom
                    max_interactions = self.test_configs.get('max_interactions', 15)
                    final_decision_threshold = max(max_interactions - 3, 5)  # Start final decision phase a few rounds early
                    
                    if (interaction_count >= final_decision_threshold or at_bottom) and not test_complete:
                        # Use final decision prompt
                        scenario_prompt = self.get_final_decision_prompt(scenario_name, scroll_history, viewport.get_scroll_info())
                        self.logger.info(f"🎯 Using final decision prompt (interaction #{interaction_count}/{max_interactions}, at_bottom:{at_bottom})")
                    else:
                        # Build regular prompt with history memory
                        scenario_prompt = self.get_scenario_prompt_with_memory(
                            scenario_name, scroll_history, interaction_count, viewport.get_scroll_info()
                        )
                
                    # Smart GPU selection: prefer idle GPU to avoid memory shortage
                    # Build complete input sequence (in the order actually sent to the model)
                    # Note: build_complete_input_sequence internally prints the complete input sequence including special tokens
                    complete_input_sequence = self.build_complete_input_sequence(current_view_path, scenario_prompt, interaction_count, viewport, scroll_history)
                    
                    # Use unified model inference interface
                    response = ''
                    if (self.use_vllm and self.vllm_engine) or (self.model_type == "qwen3vl" and self.qwen3vl_wrapper) or (self.model_type == "glm4v" and self.glm4v_wrapper):
                        self.logger.info(f"USING {self.model_type.upper()} MODEL")
                        
                        result = self.generate_model_response(
                            image_path=current_view_path,
                            prompt=scenario_prompt,
                            exploration_history=scroll_history,  # Pass scroll history
                            max_tokens=1024,  # Increase token limit for complete reasoning
                            temperature=1.0,
                            top_p=0.8
                        )
                        
                        if result and result.get('success'):
                            response = result.get('response', '')
                            inference_time = result.get('inference_time', 0)
                            self.logger.info(f"✅ {self.model_type.upper()} inference completed - elapsed: {inference_time:.1f}s")
                        else:
                            self.logger.error(f"❌ {self.model_type.upper()} inference failed, falling back to Transformer")
                            # Fall back to transformer inference
                            if hasattr(self, 'manual_gpu_id') and self.manual_gpu_id is not None:
                                gpu_id = self.manual_gpu_id
                                self.logger.info(f"🎯 Using manually specified GPU {gpu_id}")
                            else:
                                gpu_id = self.gpu_tester.select_best_gpu()
                                self.logger.info(f"🔍 Automatically selected idle GPU {gpu_id}")
                            
                            result = self.gpu_tester.single_gpu_inference(
                                region_path=current_view_path,
                                prompt=scenario_prompt,
                                gpu_id=gpu_id,
                                scenario=scenario_name
                            )
                            response = result.get('response', '') if result else ''
                    else:
                        # Use Transformer inference (fallback mode)
                        self.logger.info("🔧 Using Transformer inference engine")
                        
                        if hasattr(self, 'manual_gpu_id') and self.manual_gpu_id is not None:
                            gpu_id = self.manual_gpu_id
                            self.logger.info(f"🎯 Using manually specified GPU {gpu_id}")
                        else:
                            gpu_id = self.gpu_tester.select_best_gpu()
                            self.logger.info(f"🔍 Automatically selected idle GPU {gpu_id}")
                        
                        result = self.gpu_tester.single_gpu_inference(
                            region_path=current_view_path,
                            prompt=scenario_prompt,
                            gpu_id=gpu_id,
                            scenario=scenario_name
                        )
                        response = result.get('response', '') if result else ''
                    
                    # Record complete UI-TARS response for debugging
                    self.logger.info(f"UI-TARS COMPLETE RESPONSE:")
                    self.logger.info(f"{'='*50}")
                    self.logger.info(f"{response}")
                    self.logger.info(f"{'='*50}")
                    
                    # 🔧 Save last response for semantic analysis on timeout
                    last_response = response
                    
                    if response:
                        # 🎯 For Qwen3-VL and GLM-4V, prefer coordinates returned by wrapper
                        if self.model_type in ["qwen3vl", "glm4v"] and result and result.get('coordinates'):
                            wrapper_coords = result['coordinates']
                            self.logger.info(f"🔧 Using {self.model_type.upper()} wrapper-parsed coordinates: {wrapper_coords}")
                            # Construct action_info using wrapper's coordinates
                            action_info = self.parse_uitars_response(response)
                            if wrapper_coords and isinstance(wrapper_coords, (tuple, list)) and len(wrapper_coords) == 2:
                                action_info['coordinates'] = wrapper_coords
                                # If parse did not identify action_type, infer from response
                                if action_info['action_type'] == 'unknown':
                                    # 🎯 Fix: prefer precise extraction of action type from "Action:" field
                                    import re
                                    action_field_match = re.search(r'Action:\s*(\w+)\s*\(', response, re.IGNORECASE)
                                    if action_field_match:
                                        # Extracted explicit action type from Action field
                                        extracted_action = action_field_match.group(1).lower()
                                        if extracted_action in ['click', 'scroll']:
                                            action_info['action_type'] = extracted_action
                                            self.logger.info(f"   ✅ Extracted action_type from Action field: {extracted_action}")
                                            
                                            # If scroll, extract direction
                                            if extracted_action == 'scroll':
                                                direction_match = re.search(r'direction=[\'"]?(up|down|left|right)[\'"]?', response, re.IGNORECASE)
                                                if direction_match:
                                                    action_info['direction'] = direction_match.group(1).lower()
                                                elif 'down' in response.lower():
                                                    action_info['direction'] = 'down'
                                                elif 'up' in response.lower():
                                                    action_info['direction'] = 'up'
                                                else:
                                                    action_info['direction'] = 'down'  # Default
                                        else:
                                            # Action field exists but is not click/scroll; default to click when coordinates exist
                                            action_info['action_type'] = 'click'
                                            self.logger.info(f"   ⚠️ Action field value is '{extracted_action}', defaulting to click since coordinates exist")
                                    else:
                                        # Action field not found; infer from response text (more cautiously)
                                        # Only judge as scroll when both 'scroll' and 'direction=' are present
                                        if 'scroll' in response.lower() and 'direction=' in response.lower():
                                            action_info['action_type'] = 'scroll'
                                            if 'down' in response.lower():
                                                action_info['direction'] = 'down'
                                            elif 'up' in response.lower():
                                                action_info['direction'] = 'up'
                                            self.logger.info(f"   ℹ️ Inferred scroll from text (contains direction=)")
                                        else:
                                            # Has coordinates but action_type unrecognized; default to click
                                            action_info['action_type'] = 'click'
                                            self.logger.info(f"   ℹ️ Has coordinates but no explicit action, defaulting to click")
                        else:
                            # Parse UI-TARS response
                            action_info = self.parse_uitars_response(response)
                        
                        # 🔧 Prefer coordinates already parsed in action_info
                        if action_info.get('coordinates'):
                            viewport_coords = action_info['coordinates']
                            self.logger.info(f"   ✅ Using coordinates from action_info: {viewport_coords}")
                        else:
                            # If action_info has no coordinates, try other formats
                            import re
                            box_match = re.search(r'<\|box_start\|>\s*\((\d+)\s*,\s*(\d+)\)\s*<\|box_end\|>', response)
                            if box_match and self.model_type != "qwen3vl":  # Qwen3-VL does not use this format
                                viewport_coords = (int(box_match.group(1)), int(box_match.group(2)))
                                self.logger.info(f"   🎯 Successfully parsed UI-TARS format coordinates: {viewport_coords}")
                            else:
                                viewport_coords = (0, 0)
                                self.logger.debug(f"   ❌ Failed to parse coordinates, using default value")
                        
                        # Map viewport coordinates back to full-screenshot coordinates
                        if viewport_coords != (0, 0):
                            # Safe tuple unpacking
                            try:
                                full_coords = viewport.map_viewport_coords_to_full(viewport_coords[0], viewport_coords[1])
                            except (IndexError, ValueError) as e:
                                self.logger.warning(f"⚠️ Viewport coordinate mapping failed: {viewport_coords}, error: {e}")
                                full_coords = (0, 0)
                        else:
                            full_coords = (0, 0)
                        
                        # Compute distance to target
                        if full_coords != (0, 0):
                            # Safe coordinate unpacking for distance computation
                            try:
                                if isinstance(target_coords, dict):
                                    target_x = target_coords.get('x', 0)
                                    target_y = target_coords.get('y', 0)
                                elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
                                    target_x, target_y = target_coords[0], target_coords[1]
                                else:
                                    self.logger.warning(f"⚠️ Unable to parse target coordinate format: {target_coords}")
                                    target_x, target_y = 0, 0
                                
                                distance = ((full_coords[0] - target_x)**2 + (full_coords[1] - target_y)**2)**0.5
                                # 🔍 Debug info: print distance regardless of action_type
                                self.logger.info(f"📏 Coordinate distance: parsed {full_coords} -> target {(target_x, target_y)} = {distance:.1f}px")
                            except (IndexError, ValueError, TypeError) as e:
                                self.logger.warning(f"⚠️ Distance calculation failed: full_coords={full_coords}, target_coords={target_coords}, error: {e}")
                                distance = float('inf')
                            
                            # 🔧 Fix: only click action counts as test success; scroll does not count as test completion
                            action_type = action_info.get('action_type', 'unknown')
                            if action_type == 'click':
                                # 🎯 Use coordinate manager for target area judgment (strict rectangle mode)
                                coordinate_hit_result = self.coordinate_manager.is_click_in_target_area(
                                    scenario=scenario_name,
                                    variant_name=variant_name,
                                    click_x=full_coords[0],
                                    click_y=full_coords[1],
                                    hit_radius=0  # Force strict rectangle judgment only
                                )
                                success = coordinate_hit_result['is_hit']
                                if success:
                                    self.logger.info(f"🎯 Coordinate hit: ✅ {coordinate_hit_result['message']}")
                                else:
                                    self.logger.info(f"🎯 Coordinate miss: ❌ {coordinate_hit_result['message']}")
                                    # Still keep distance info for analysis
                                    self.logger.info(f"   Distance to center: {coordinate_hit_result.get('distance_to_center', distance):.1f}px")
                            else:
                                # scroll or other actions do not count as test success; continue testing
                                success = False
                        else:
                            distance = float('inf')
                            success = False
                        
                        confidence = result.get('confidence', 0.0)
                        action_type = action_info.get('action_type', 'unknown')
                        
                        # Record extracted action info
                        self.logger.info(f"ACTION ANALYSIS:")
                        self.logger.info(f"   ACTION TYPE: {action_type}")
                        
                        # Only click actions require coordinate analysis and semantic analysis
                        if action_type == 'click':
                            self.logger.info(f"   VIEWPORT COORDS: {viewport_coords}")
                            self.logger.info(f"   GLOBAL COORDS: {full_coords}")
                            self.logger.info(f"   TARGET COORDS: {target_coords}")
                            self.logger.info(f"   DISTANCE: {distance:.1f}px")
                            self.logger.info(f"   COORDINATE SUCCESS: {success}")
                            self.logger.info(f"   CONFIDENCE: {confidence:.3f}")
                            
                            # Semantic-based response analysis: check whether target product is mentioned
                            target_product_names = scenario_info.get('target_product_names', [])
                            
                            # Defensive check: ensure analyze_product_mention returns correct result
                            try:
                                analysis_result = self.analyze_product_mention(response, target_product_names, scenario_name)
                                
                                # Validate return result structure
                                if not isinstance(analysis_result, dict):
                                    self.logger.error(f"⚠️ analyze_product_mention returned non-dict type: {type(analysis_result)}, value: {analysis_result}")
                                    analysis_result = {
                                        'is_successful': False,
                                        'success_level': 'analysis_error',
                                        'semantic_score': 0.0,
                                        'error': f'Invalid return type: {type(analysis_result)}'
                                    }
                                
                                # Validate whether required keys exist
                                required_keys = ['is_successful', 'success_level', 'semantic_score']
                                for key in required_keys:
                                    if key not in analysis_result:
                                        self.logger.error(f"⚠️ analyze_product_mention missing required key: {key}")
                                        analysis_result[key] = False if key == 'is_successful' else ('analysis_error' if key == 'success_level' else 0.0)
                                
                            except Exception as analysis_error:
                                self.logger.error(f"❌ Semantic analysis error: {analysis_error}")
                                analysis_result = {
                                    'is_successful': False,
                                    'success_level': 'analysis_exception',
                                    'semantic_score': 0.0,
                                    'error': str(analysis_error)
                                }
                            
                            # Extract analysis result (with safe access)
                            is_successful = analysis_result.get('is_successful', False)
                            success_level = analysis_result.get('success_level', 'unknown')
                            semantic_score = analysis_result.get('semantic_score', 0.0)
                            
                            # 🎯 Integrate semantic analysis and coordinate analysis results
                            # Combined judgment of coordinate success and semantic success
                            coordinate_success = success  # Success judgment based on click coordinates
                            semantic_success = is_successful  # Success judgment based on semantic analysis
                            
                            # Determine final success level
                            if coordinate_success and semantic_success:
                                final_success_level = 'precise_success'  # Both coordinate and semantic succeeded
                                final_success = True
                            elif coordinate_success:
                                final_success_level = 'coordinate_success'  # Coordinate success only
                                final_success = True
                            elif semantic_success:
                                final_success_level = 'semantic_success'  # Semantic success only
                                final_success = True
                            else:
                                final_success_level = success_level  # Use original analysis failure level
                                final_success = False
                            
                            # 📊 Record semantic analysis result in detail
                            self.logger.info(f"📋 Full test result:")
                            self.logger.info(f"   🎯 Coordinate success: {coordinate_success}")
                            self.logger.info(f"   🧠 Semantic success: {semantic_success}")
                            self.logger.info(f"   🏆 Final success: {final_success}")
                            self.logger.info(f"   📊 Success level: {final_success_level}")
                            self.logger.info(f"   💯 Semantic score: {semantic_score:.3f}")
                            
                            test_result = {
                                'test_id': test_id,
                                'interaction_count': interaction_count,
                                'final_state': 'clicked',
                                'viewport_coords': viewport_coords,
                                'global_coords': full_coords,
                                'target_coords': target_coords,
                                'distance': distance,
                                'success': final_success,  # Use comprehensive success judgment
                                'coordinate_success': coordinate_success,  # Record coordinate success separately
                                'confidence': confidence,
                                'response': response,
                                'action_info': action_info,
                                'semantic_analysis': analysis_result,  # Full semantic analysis result
                                'is_successful': final_success,  # Use comprehensive success judgment
                                'success_level': final_success_level,  # Use comprehensive success level
                                'semantic_score': semantic_score,
                                'semantic_success': semantic_success,  # Record semantic success separately
                                'scroll_info': viewport.get_scroll_info()
                            }
                        else:
                            # scroll or other actions: no semantic analysis, only record basic info
                            self.logger.info(f"   ⚡ {action_type.upper()} action, continuing test...")
                            
                            # For scroll actions, use parsed coordinates (if available)
                            scroll_coords = viewport_coords if viewport_coords != (0, 0) else (0, 0)
                            
                            test_result = {
                                'test_id': test_id,
                                'interaction_count': interaction_count,
                                'final_state': 'scrolling',
                                'action_type': action_type,
                                'response': response,
                                'action_info': action_info,
                                'scroll_info': viewport.get_scroll_info(),
                                # Record scroll coordinates (if scroll action)
                                'viewport_coords': scroll_coords,
                                'global_coords': viewport.map_viewport_coords_to_full(scroll_coords[0], scroll_coords[1]) if scroll_coords != (0, 0) else (0, 0),
                                'target_coords': target_coords if 'target_coords' in locals() else (0, 0),
                                'distance': float('inf'),
                                'success': False,
                                'confidence': 0.0,
                                'semantic_analysis': {'is_successful': False, 'success_level': 'scroll', 'semantic_score': 0.0},
                                'is_successful': False,
                                'success_level': 'scroll',
                                'semantic_score': 0.0
                            }
                        
                    # 🔧 Determine if test is complete based on action type
                    action_type = action_info.get('action_type', 'unknown')
                    
                    # 📝 Extract and display agent's reasoning and observed items
                    thought = self.extract_thought_from_response(response)
                    current_viewport_items = self.extract_viewport_items(response, viewport.get_scroll_info(), scenario_name)
                    
                    # If no item info extracted but agent has reasoning content, try further analysis
                    if not current_viewport_items and thought:
                        # Try to extract any possible item-related info from reasoning
                        import re
                        # Look for any content that looks like product names or descriptions
                        potential_items = re.findall(r'([A-Z][a-zA-Z\s\d]{10,50})', thought)
                        if potential_items:
                            current_viewport_items = [item.strip() for item in potential_items[:3] if len(item.strip()) > 10]
                    
                    # Record and display detailed interaction info
                    self.logger.info(f"AGENT THOUGHT: {thought}")
                    self.logger.info(f"IDENTIFIED ITEMS: {', '.join(current_viewport_items) if current_viewport_items else 'None'}")
                    
                    # Display history - new format: viewport image → thought+action mapping
                    if scroll_history:
                        self.logger.info(f"INTERACTION HISTORY: {len(scroll_history)} previous viewport-response mappings")
                        # Display full history, no longer limited to the most recent 3
                        for i, history_entry in enumerate(scroll_history, 1):
                            if isinstance(history_entry, dict):
                                # New format: show viewport image to thought+action mapping
                                viewport_image = history_entry.get('viewport_image_name', 'unknown_viewport.png')
                                action = history_entry.get('action', 'Unknown action')
                                thought_hist = history_entry.get('thought', 'No thought recorded')
                                interaction_id = history_entry.get('interaction_id', 'unknown')
                                items_hist = history_entry.get('viewport_items', [])
                                
                                self.logger.info(f"  History {i} (Interaction #{interaction_id}):")
                                self.logger.info(f"    📷 Viewport: {viewport_image}")
                                self.logger.info(f"    🧠 → Thought: {thought_hist[:100]}{'...' if len(thought_hist) > 100 else ''}")
                                self.logger.info(f"    🎯 → Action: {action}")
                                if items_hist:
                                    self.logger.info(f"    🛍️ → Products: {', '.join(items_hist[:2])}")
                                else:
                                    self.logger.info(f"    🛍️ → Products: None identified")
                            else:
                                # Compatible with old format
                                action = history_entry.get('action', 'Unknown action') if isinstance(history_entry, dict) else str(history_entry)
                                thought_hist = history_entry.get('thought', 'No thought recorded') if isinstance(history_entry, dict) else 'Legacy format'
                                items_hist = history_entry.get('viewport_items', []) if isinstance(history_entry, dict) else []
                                self.logger.info(f"  History {i}: {action}")
                                self.logger.info(f"    Thought: {thought_hist}")
                                if items_hist:
                                    self.logger.info(f"    Items: {', '.join(items_hist[:2])}")
                                else:
                                    self.logger.info(f"    Items: None identified")
                    else:
                        self.logger.info(f"INTERACTION HISTORY: No previous viewport-response mappings")
                    
                    if action_type == 'click':
                        # Click action - test complete, record result
                        product_name = action_info.get('product_name', 'unknown item')
                        action_description = f"Clicked on '{product_name}' at coordinates {viewport_coords}"
                        observation = f"Selected item with confidence {confidence:.3f}"
                        
                        # Add full info to history - new format: viewport_image: thought+action
                        history_entry = {
                            'viewport_image_path': current_view_path,  # Store current viewport image path
                            'viewport_image_name': os.path.basename(current_view_path),  # Image filename
                            'action': action_description,
                            'observation': observation, 
                            'thought': thought,
                            'action_response': response,  # Full agent response
                            'viewport_items': current_viewport_items,
                            'scroll_position': f"{viewport.current_y_offset}/{viewport.max_y_offset}",
                            'interaction_id': interaction_count,
                            'timestamp': datetime.now().isoformat()
                        }
                        scroll_history.append(history_entry)
                        
                        test_complete = True
                        results.append(test_result)
                        
                        # Display full test result analysis
                        self.logger.info(f"TEST {test_id + 1} COMPLETED: Click action detected")
                        self.logger.info(f"SELECTED ITEM: {product_name}")
                        self.logger.info(f"CLICK COORDINATES: {viewport_coords} (viewport) -> {full_coords} (global)")
                        self.logger.info(f"TARGET COORDINATES: {target_coords}")
                        self.logger.info(f"DISTANCE TO TARGET: {distance:.1f}px")
                        self.logger.info(f"COORDINATE SUCCESS: {success}")
                        
                        # Detailed semantic analysis result
                        self.logger.info(f"SEMANTIC ANALYSIS RESULTS:")
                        self.logger.info(f"  SEMANTIC SUCCESS: {is_successful}")
                        self.logger.info(f"  SEMANTIC SCORE: {semantic_score:.3f}")
                        self.logger.info(f"  SUCCESS LEVEL: {success_level}")
                        self.logger.info(f"  RESPONSE TYPE: {analysis_result.get('response_type', 'unknown')}")
                        self.logger.info(f"  IS DESCRIPTIVE: {analysis_result.get('is_descriptive', False)}")
                        
                        # Target product info
                        target_product_names = scenario_info.get('target_product_names', [])
                        if target_product_names:
                            self.logger.info(f"TARGET PRODUCTS: {', '.join(target_product_names[:3])}")
                        
                        # Click complete, skip subsequent processing
                        break  # Break out of while loop
                        
                    elif action_type == 'scroll':
                        # Scroll action - continue in the same test, do not end test
                        old_y = viewport.current_y_offset
                        
                        # 🎯 Get scroll direction
                        scroll_direction = action_info.get('direction', 'down')
                        self.logger.info(f"🔄 Detected scroll action: {scroll_direction}")
                        
                        # 🎯 Special handling: if at bottom and trying to scroll down during final decision phase
                        scroll_info = viewport.get_scroll_info()
                        at_bottom = scroll_info.get('scroll_progress', 0) >= 0.99
                        at_top = viewport.current_y_offset <= 0
                        max_interactions = self.test_configs.get('max_interactions', 15)
                        final_decision_threshold = max(max_interactions - 3, 5)
                        is_final_decision = interaction_count >= final_decision_threshold or at_bottom
                        
                        # Handle upward scroll
                        if scroll_direction == 'up':
                            self.logger.info(f"📜 Scrolling up: starting from position {old_y}")
                            can_scroll_up = viewport.scroll_up()
                            if can_scroll_up:
                                new_y = viewport.current_y_offset
                                self.logger.info(f"✅ Scroll up successful: {old_y} -> {new_y}")
                                action_description = f"Scrolled up from position {old_y} to {new_y}"
                                observation = f"Viewing content at scroll position {new_y}/{viewport.max_y_offset}"
                            else:
                                self.logger.warning(f"⚠️ Cannot scroll up, already at page top - ignoring this invalid scroll")
                                # Do not add to history; continue the loop directly (will be caught by boundary check later)
                                action_description = None  # Mark as invalid action
                                observation = None
                        
                        # Handle downward scroll
                        elif scroll_direction == 'down':
                            self.logger.info(f"📜 Scrolling down: starting from position {old_y}")
                            can_scroll_down = viewport.scroll_down()
                            if can_scroll_down:
                                new_y = viewport.current_y_offset
                                self.logger.info(f"✅ Scroll down successful: {old_y} -> {new_y}")
                                action_description = f"Scrolled down from position {old_y} to {new_y}"
                                observation = f"Viewing content at scroll position {new_y}/{viewport.max_y_offset}"
                            else:
                                self.logger.warning(f"⚠️ Cannot scroll down, already at page bottom - ignoring this invalid scroll")
                                # Do not add to history; continue the loop directly (will be caught by boundary check later)
                                action_description = None  # Mark as invalid action
                                observation = None
                        
                        else:
                            # Default: scroll down
                            can_scroll = viewport.scroll_down()
                            new_y = viewport.current_y_offset
                            action_description = f"Scrolled down from position {old_y} to {new_y}"
                            observation = f"Viewing content at scroll position {new_y}/{viewport.max_y_offset}"
                        
                        # Add scroll info to history - new format: viewport_image: thought+action
                        # Only add to history for valid actions (action_description not None)
                        if action_description is not None:
                            history_entry = {
                                'viewport_image_path': current_view_path,  # Store current viewport image path
                                'viewport_image_name': os.path.basename(current_view_path),  # Image filename
                                'action': action_description,
                                'observation': observation,
                                'thought': thought,
                                'action_response': response,  # Full agent response
                                'viewport_items': current_viewport_items,
                                'scroll_position': f"{viewport.current_y_offset}/{viewport.max_y_offset}",
                                'interaction_id': interaction_count,
                                'timestamp': datetime.now().isoformat()
                            }
                            scroll_history.append(history_entry)
                        
                        # Check whether operation can continue
                        if scroll_direction == 'up':
                            if viewport.current_y_offset > 0:
                                # Upward scroll successful, continue
                                scroll_count += 1
                                self.logger.info(f"SCROLL UP ACTION {scroll_count}: Test {test_id + 1} scrolling up to find better product")
                                self.logger.info(f"SCROLL HISTORY: {len(scroll_history)} interactions recorded")
                                continue  # Go directly to next interaction
                            else:
                                # Already at top, cannot scroll up, skip this invalid interaction
                                self.logger.warning(f"⚠️ REACHED TOP: Agent tried to scroll up but already at page top (y_offset=0)")
                                self.logger.info(f"⏭️  Skipping this invalid scroll attempt, agent can try different action in next interaction")
                                continue  # Skip this invalid scroll attempt, go to next interaction
                        elif scroll_direction == 'down':
                            if viewport.current_y_offset < viewport.max_y_offset:
                                # Downward scroll successful, continue
                                scroll_count += 1
                                self.logger.info(f"SCROLL DOWN ACTION {scroll_count}: Test {test_id + 1} continues scrolling")
                                self.logger.info(f"SCROLL HISTORY: {len(scroll_history)} interactions recorded")
                                continue  # Go directly to next interaction
                            else:
                                # Already at bottom, cannot scroll down
                                self.logger.warning(f"⚠️ REACHED BOTTOM: Agent tried to scroll down but already at page bottom")
                                self.logger.info(f"⏭️  Skipping this invalid scroll attempt, agent can try different action in next interaction")
                                continue  # Skip this invalid scroll attempt, go to next interaction
                        else:
                            # Other direction, continue normally
                            scroll_count += 1
                            continue
                            
                    else:
                        # Other actions - handle as before, new format: viewport_image: thought+action
                        action_description = f"Performed {action_type} action"
                        observation = f"Action result: {response[:100]}..."
                        
                        history_entry = {
                            'viewport_image_path': current_view_path,  # Store current viewport image path
                            'viewport_image_name': os.path.basename(current_view_path),  # Image filename
                            'action': action_description,
                            'observation': observation,
                            'thought': thought,
                            'action_response': response,  # Full agent response
                            'viewport_items': current_viewport_items,
                            'scroll_position': f"{viewport.current_y_offset}/{viewport.max_y_offset}",
                            'interaction_id': interaction_count,
                            'timestamp': datetime.now().isoformat()
                        }
                        scroll_history.append(history_entry)
                        
                        test_complete = True
                        results.append(test_result)
                        # Other action complete, skip subsequent processing
                        break  # Break out of while loop
                        
                    # ⚠️ Critical fix: only delete files when test is truly complete
                    # For scroll operations, files must be kept for the next round of inference
                    if test_complete:
                        try:
                            if os.path.exists(current_view_path):
                                os.unlink(current_view_path)
                                self.logger.debug(f"🗑️ Test complete, cleaning up viewport file")
                        except Exception:
                            pass
                            
                else:
                    # Inference failed - mark as failure directly
                    self.logger.error(f"❌ Test {test_id + 1} inference failed - interaction #{interaction_count}")
                    self.logger.error(f"   Current scroll position: y={viewport.current_y_offset if 'viewport' in locals() else 'unknown'}")
                    self.logger.error(f"   Inference response is empty or malformed. Possible causes:")
                    self.logger.error(f"   1. vLLM engine returned empty response")
                    self.logger.error(f"   2. Model output format did not meet expectations")
                    self.logger.error(f"   3. Image path invalid or image corrupted")
                    
                    test_result = {
                        'test_id': test_id,
                        'interaction_count': interaction_count,
                        'final_state': 'inference_failed',
                        'viewport_coords': (0, 0),
                        'global_coords': (0, 0),
                        'target_coords': target_coords,
                        'distance': float('inf'),
                        'success': False,
                        'coordinate_success': False,  # Add coordinate success field
                        'confidence': 0.0,
                        'response': 'Inference failed',
                        'action_info': {'action_type': 'failed'},
                        'semantic_analysis': {'is_successful': False, 'success_level': 'failed', 'semantic_score': 0.0},
                        'is_successful': False,
                        'success_level': 'failed',
                        'semantic_score': 0.0,
                        'semantic_success': False,  # Add semantic success field
                        'scroll_info': viewport.get_scroll_info()
                    }
                    test_complete = True
                    results.append(test_result)
                    
                    try:
                        if os.path.exists(current_view_path):
                            os.unlink(current_view_path)
                    except Exception:
                        pass
                            
                # Check whether maximum interaction count is exceeded
                max_interactions = self.test_configs.get('max_interactions', 20)
                
                if interaction_count >= max_interactions and not test_complete:
                    # Timeout - mark as decision timeout failure, but still perform semantic analysis
                    self.logger.warning(f"⚠️ Reached max interaction count ({interaction_count}), test timed out")
                    self.logger.info(f"🔍 Performing semantic analysis on last response...")
                    
                    # 🔧 Perform semantic analysis on last response
                    semantic_result = {'is_successful': False, 'success_level': 'timeout', 'semantic_score': 0.0}
                    semantic_success = False
                    
                    if last_response:
                        try:
                            # Extract Agent's reasoning for semantic analysis
                            agent_thought = self.extract_agent_thought(last_response)
                            identified_items = self.extract_identified_items(last_response)
                            
                            self.logger.info(f"AGENT THOUGHT: {agent_thought[:200]}..." if len(agent_thought) > 200 else f"AGENT THOUGHT: {agent_thought}")
                            self.logger.info(f"IDENTIFIED ITEMS: {identified_items[:200]}..." if len(identified_items) > 200 else f"IDENTIFIED ITEMS: {identified_items}")
                            
                            # Execute semantic analysis
                            semantic_result = self.analyze_semantic_success(
                                response=last_response,
                                agent_thought=agent_thought,
                                identified_items=identified_items,
                                target_product_names=config.get('target_product_names', []),
                                card_keywords=config.get('card_keywords', [])
                            )
                            
                            semantic_success = semantic_result.get('is_successful', False)
                            self.logger.info(f"📊 Semantic analysis for timeout case:")
                            self.logger.info(f"  - Semantic success: {semantic_success}")
                            self.logger.info(f"  - Semantic score: {semantic_result.get('semantic_score', 0):.3f}")
                            self.logger.info(f"  - Success level: {semantic_result.get('success_level', 'unknown')}")
                            
                        except Exception as e:
                            self.logger.error(f"❌ Semantic analysis failed: {e}")
                            import traceback
                            self.logger.error(f"   Error details: {traceback.format_exc()}")
                    
                    test_result = {
                        'test_id': test_id,
                        'interaction_count': interaction_count,
                        'final_state': 'timeout',
                        'viewport_coords': (0, 0),
                        'global_coords': (0, 0),
                        'target_coords': target_coords,
                        'distance': float('inf'),
                        'success': semantic_success,  # 🔧 Based on semantic analysis result
                        'coordinate_success': False,  # Coordinates always fail under timeout
                        'confidence': 0.0,
                        'response': last_response if last_response else f'Timeout after {interaction_count} interactions',
                        'action_info': {'action_type': 'timeout'},
                        'semantic_analysis': semantic_result,
                        'is_successful': semantic_success,  # 🔧 Based on semantic analysis result
                        'success_level': semantic_result.get('success_level', 'timeout'),
                        'semantic_score': semantic_result.get('semantic_score', 0.0),
                        'semantic_success': semantic_success,  # 🔧 Added semantic success field
                        'scroll_info': viewport.get_scroll_info()
                    }
                    results.append(test_result)
                    test_complete = True
            
            except Exception as test_error:
                # 🔧 Single test failed; record failure result rather than skipping
                self.logger.error(f"❌ TEST {test_id + 1} execution failed: {test_error}")
                import traceback
                self.logger.error(f"   Error details: {traceback.format_exc()}")
                
                # Create failure record for failed test instead of skipping
                failed_test_result = {
                    'test_id': test_id,
                    'interaction_count': interaction_count if 'interaction_count' in locals() else 0,
                    'final_state': 'error',
                    'viewport_coords': (0, 0),
                    'global_coords': (0, 0),
                    'target_coords': target_coords if 'target_coords' in locals() else (0, 0),
                    'distance': float('inf'),
                    'success': False,
                    'coordinate_success': False,  # Add coordinate success field
                    'confidence': 0.0,
                    'response': f'Test execution failed: {str(test_error)}',
                    'action_info': {'action_type': 'error'},
                    'semantic_analysis': {
                        'is_successful': False,
                        'success_level': 'error',
                        'semantic_score': 0.0,
                        'error': str(test_error)
                    },
                    'is_successful': False,
                    'success_level': 'error',
                    'semantic_score': 0.0,
                    'semantic_success': False,  # Add semantic success field
                    'scroll_info': {'error': str(test_error)},
                    'error': str(test_error)
                }
                results.append(failed_test_result)
                self.logger.info(f"✅ TEST {test_id + 1} recorded as failure rather than skipped")
            
            # 🔧 CRITICAL: Force ensure every test has a record
            finally:
                # Check whether the current test already has a record
                current_test_recorded = any(r.get('test_id') == test_id for r in results)
                if not current_test_recorded:
                    self.logger.error(f"🚨 CRITICAL: TEST {test_id + 1} was not recorded in results, force-adding missing record")
                    # 🔧 Fix: ensure missing record uses same data structure as normal test results
                    missing_result = {
                        'test_id': test_id,
                        'response': 'Test was not recorded (critical missing)',
                        'response_analysis': {
                            'is_successful': False,
                            'success_level': 'failure',
                            'semantic_score': 0.0,
                            'response_type': 'missing',
                            'is_descriptive': False
                        },
                        'is_successful': False,
                        'success_level': 'failure',
                        'semantic_score': 0.0,
                        'response_type': 'missing',
                        'is_descriptive': False,
                        'confidence': 0.0,
                        'method': 'missing_forced'
                    }
                    results.append(missing_result)
                    self.logger.error(f"   🆘 Force-added missing record for TEST {test_id + 1}")
                
                # Test completion confirmation
                self.logger.info(f"🏁 TEST {test_id + 1}/{tests_to_run} [COMPLETED] - Current results length: {len(results)}")
                self.logger.info(f"📊 Recorded test_ids: {[r.get('test_id') for r in results]}")
        
        # 🔧 State check after test loop completion
        self.logger.info(f"✅ Test loop complete! Executed {tests_to_run} rounds")
        self.logger.info(f"📋 Current results count: {len(results)}")
        self.logger.info(f"🔍 Actually recorded test_ids: {sorted([r.get('test_id') for r in results])}")
        self.logger.info(f"🎯 Expected test_ids: {list(range(tests_to_run))}")
        
        # 🔧 Step 1: Deduplicate - keep only the last result for each test_id
        original_count = len(results)
        if results:
            unique_results = {}
            for result in results:
                test_id = result.get('test_id')
                if test_id is not None:
                    unique_results[test_id] = result  # Later entries overwrite earlier ones
            results = sorted(unique_results.values(), key=lambda x: x.get('test_id', 0))
            if len(results) != original_count:
                self.logger.warning(f"🔄 Dedup: {original_count} → {len(results)} (removed {original_count - len(results)} duplicates)")
            self.logger.info(f"🔍 test_ids after dedup: {sorted([r.get('test_id') for r in results])}")
        
        # 🔧 Step 2: Detect and fill in missing tests
        expected_test_ids = set(range(tests_to_run))
        actual_test_ids = {r['test_id'] for r in results if 'test_id' in r and isinstance(r['test_id'], int)}
        missing_test_ids = expected_test_ids - actual_test_ids
        
        if missing_test_ids:
            self.logger.warning(f"⚠️ Detected {len(missing_test_ids)} missing tests: {sorted(missing_test_ids)}")
            # Add failure records for missing tests
            for missing_id in sorted(missing_test_ids):
                missing_result = {
                    'test_id': missing_id,
                    'viewport_coords': (0, 0),
                    'global_coords': (0, 0),
                    'target_coords': target_coords if 'target_coords' in locals() else (0, 0),
                    'distance': float('inf'),
                    'success': False,
                    'confidence': 0.0,
                    'response': 'Test was not executed (missing)',
                    'action_info': {'action_type': 'missing'},
                    'semantic_analysis': {
                        'is_successful': False,
                        'success_level': 'missing',
                        'semantic_score': 0.0
                    },
                    'is_successful': False,
                    'success_level': 'missing',
                    'semantic_score': 0.0,
                    'scroll_info': {}
                }
                results.append(missing_result)
                self.logger.info(f"   ✚ Added failure record for test {missing_id}")
            
        # Compute summary statistics
        # 🔧 CRITICAL fix: ensure results is not empty and has valid data  
        self.logger.info(f"🔍 Final results check: results={results}, len={len(results) if results else 'None'}, type={type(results)}")
        if results and len(results) > 0:
            successful_tests = [r for r in results if r.get('success', False)]
            # 🔧 Fix: use configured test count as denominator, not len(results)
            total_tests = tests_to_run
            recorded_tests = len([r for r in results if r.get('action_info', {}).get('action_type') != 'missing'])
            missing_tests = len(missing_test_ids)
            
            success_rate = len(successful_tests) / total_tests if total_tests > 0 else 0
            
            # 🔧 Update: use new comprehensive success judgment stats
            # 1. Comprehensive success (coordinate or semantic success)
            comprehensive_successes = [r for r in results if r.get('is_successful', False)]  # Based on comprehensive judgment
            comprehensive_success_rate = len(comprehensive_successes) / total_tests if total_tests > 0 else 0
            
            # 2. Count each success type separately
            coordinate_only_successes = [r for r in results if r.get('coordinate_success', False) and not r.get('semantic_success', False)]
            semantic_only_successes = [r for r in results if r.get('semantic_success', False) and not r.get('coordinate_success', False)]
            dual_successes = [r for r in results if r.get('coordinate_success', False) and r.get('semantic_success', False)]
            
            # 🎯 Core fix: count directly based on coordinate_success and semantic_success fields
            coordinate_successes_all = [r for r in results if r.get('coordinate_success', False)]
            semantic_successes_all = [r for r in results if r.get('semantic_success', False)]
            
            # 3. Success level classification stats (kept for compatibility)
            precise_successes = [r for r in results if r.get('success_level') == 'precise_success']
            semantic_successes = [r for r in results if r.get('success_level') == 'semantic_success']
            coordinate_successes = [r for r in results if r.get('success_level') == 'coordinate_success']
            
            # 4. Compute various success rates - use correct coordinate_success and semantic_success
            coordinate_only_rate = len(coordinate_only_successes) / total_tests if total_tests > 0 else 0
            semantic_only_rate = len(semantic_only_successes) / total_tests if total_tests > 0 else 0
            dual_success_rate = len(dual_successes) / total_tests if total_tests > 0 else 0
            precise_success_rate = len(precise_successes) / total_tests if total_tests > 0 else 0
            semantic_success_rate_old = len(semantic_successes) / total_tests if total_tests > 0 else 0
            coordinate_success_rate_old = len(coordinate_successes) / total_tests if total_tests > 0 else 0
            
            # 🎯 True coordinate success rate and semantic success rate
            true_coordinate_success_rate = len(coordinate_successes_all) / total_tests if total_tests > 0 else 0
            true_semantic_success_rate = len(semantic_successes_all) / total_tests if total_tests > 0 else 0
            
            # 5. Semantic score statistics
            semantic_scores = [r.get('semantic_score', 0.0) for r in results]
            avg_semantic_score = sum(semantic_scores) / len(semantic_scores) if semantic_scores else 0.0
            max_semantic_score = max(semantic_scores) if semantic_scores else 0.0
            min_semantic_score = min(semantic_scores) if semantic_scores else 0.0
            
            # Distance statistics
            distances = [r.get('distance', float('inf')) for r in results if r.get('distance', float('inf')) != float('inf')]
            avg_distance = sum(distances) / len(distances) if distances else float('inf')
            min_distance = min(distances) if distances else float('inf')
            
            end_time = time.time()
            duration = end_time - start_time
            
            test_results = {
                'results': results,
                'total_tests': total_tests,
                'recorded_tests': recorded_tests,
                'missing_tests': missing_tests,
                'successful_tests': len(comprehensive_successes),  # Use comprehensive success count
                'overall_success_rate': comprehensive_success_rate,  # Use comprehensive success rate
                
                # Detailed success classification statistics
                'comprehensive_successes': len(comprehensive_successes),
                'coordinate_only_successes': len(coordinate_only_successes),
                'semantic_only_successes': len(semantic_only_successes),
                'dual_successes': len(dual_successes),
                
                # 🎯 True coordinate and semantic success statistics
                'true_coordinate_successes': len(coordinate_successes_all),
                'true_semantic_successes': len(semantic_successes_all),
                'true_coordinate_success_rate': true_coordinate_success_rate,
                'true_semantic_success_rate': true_semantic_success_rate,
                
                # Various success rates
                'comprehensive_success_rate': comprehensive_success_rate,
                'coordinate_only_rate': coordinate_only_rate,
                'semantic_only_rate': semantic_only_rate,
                'dual_success_rate': dual_success_rate,
                'precise_success_rate': precise_success_rate,
                'semantic_success_rate': semantic_success_rate_old,
                'coordinate_success_rate': coordinate_success_rate_old,
                
                # Semantic score statistics
                'average_semantic_score': avg_semantic_score,
                'max_semantic_score': max_semantic_score,
                'min_semantic_score': min_semantic_score,
                'average_distance': avg_distance,
                'min_distance': min_distance,
                'duration': duration,
                'target_coords': target_coords,
                'viewport_info': {
                    'viewport_height': viewport.viewport_height,
                    'scroll_step': viewport.scroll_step,
                    'full_size': (viewport.full_width, viewport.full_height)
                }
            }
            
            self.logger.info(f"📊 Test complete: {scenario_name}/{variant_name}")
            self.logger.info(f"   📋 Configured test count: {total_tests}")
            self.logger.info(f"   📝 Actual recorded count: {recorded_tests}")
            if missing_tests > 0:
                self.logger.info(f"   ⚠️ Missing test count: {missing_tests}")
            
            # 🎯 Detailed success rate analysis report - corrected to properly count coordinate_success and semantic_success
            self.logger.info(f"\n   🏆 Comprehensive success analysis:")
            self.logger.info(f"      🎯 Overall success rate: {comprehensive_success_rate:.1%} ({len(comprehensive_successes)}/{total_tests})")
            self.logger.info(f"      🤝 Dual success: {dual_success_rate:.1%} ({len(dual_successes)}/{total_tests}) - coordinate+semantic")
            self.logger.info(f"      📍 Coordinate only: {coordinate_only_rate:.1%} ({len(coordinate_only_successes)}/{total_tests})")
            self.logger.info(f"      🧠 Semantic only: {semantic_only_rate:.1%} ({len(semantic_only_successes)}/{total_tests})")
            
            self.logger.info(f"\n   🎯 Core metrics (based on coordinate_success and semantic_success fields):")
            self.logger.info(f"      📍 Coordinate success: {true_coordinate_success_rate:.1%} ({len(coordinate_successes_all)}/{total_tests})")
            self.logger.info(f"      🧠 Semantic success: {true_semantic_success_rate:.1%} ({len(semantic_successes_all)}/{total_tests})")
            
            self.logger.info(f"\n   📊 Success level distribution (legacy compat):")
            self.logger.info(f"      🎯 Precise success: {precise_success_rate:.1%} ({len(precise_successes)}/{total_tests})")
            self.logger.info(f"      📍 Coordinate success level: {coordinate_success_rate_old:.1%} ({len(coordinate_successes)}/{total_tests})")
            self.logger.info(f"      🧠 Semantic success level: {semantic_success_rate_old:.1%} ({len(semantic_successes)}/{total_tests})")
            
            self.logger.info(f"\n   💯 Semantic analysis statistics:")
            self.logger.info(f"      📈 Average score: {avg_semantic_score:.3f}")
            self.logger.info(f"      🏆 Highest score: {max_semantic_score:.3f}")
            self.logger.info(f"      📉 Lowest score: {min_semantic_score:.3f}")
            
            self.logger.info(f"\n   📏 Distance statistics:")
            self.logger.info(f"      📏 Average distance: {avg_distance:.1f}px")
            self.logger.info(f"      🎯 Minimum distance: {min_distance:.1f}px")
            self.logger.info(f"   ⏱️ Time elapsed: {duration:.1f}s")
            
            # Clean up all temporary files
            temp_files_cleaned = 0
            try:
                import glob
                temp_files = glob.glob('/tmp/viewport_*.png')
                for temp_file in temp_files:
                    try:
                        os.unlink(temp_file)
                        temp_files_cleaned += 1
                    except Exception:
                        pass
                if temp_files_cleaned > 0:
                    self.logger.info(f"🧹 Cleaned up {temp_files_cleaned} temporary screenshot file(s)")
            except Exception:
                pass
            
            return test_results
        else:
                # 🔧 Fix: handle empty results to avoid returning None
                end_time = time.time()
                duration = end_time - start_time
                
                empty_results = {
                    'results': [],
                    'total_tests': tests_to_run,
                    'recorded_tests': 0,
                    'missing_tests': tests_to_run,
                    'successful_tests': 0,
                    'overall_success_rate': 0.0,
                    'comprehensive_successes': 0,
                    'coordinate_only_successes': 0,
                    'semantic_only_successes': 0,
                    'dual_successes': 0,
                    'comprehensive_success_rate': 0.0,
                    'coordinate_only_rate': 0.0,
                    'semantic_only_rate': 0.0,
                    'dual_success_rate': 0.0,
                    'precise_success_rate': 0.0,
                    'semantic_success_rate': 0.0,
                    'coordinate_success_rate': 0.0,
                    'average_semantic_score': 0.0,
                    'max_semantic_score': 0.0,
                    'min_semantic_score': 0.0,
                    'average_distance': float('inf'),
                    'min_distance': float('inf'),
                    'duration': duration,
                    'target_coords': target_coords if 'target_coords' in locals() else (0, 0),
                    'viewport_info': {
                        'viewport_height': viewport.viewport_height if 'viewport' in locals() else 0,
                        'scroll_step': viewport.scroll_step if 'viewport' in locals() else 0,
                        'full_size': (viewport.full_width if 'viewport' in locals() else 0, viewport.full_height if 'viewport' in locals() else 0)
                    },
                    'error': 'No test results recorded'
                }
                
                self.logger.warning(f"⚠️ Test complete but no result records: {scenario_name}/{variant_name}")
                self.logger.warning(f"   📋 Configured test count: {tests_to_run}")
                self.logger.warning(f"   📝 Actual recorded count: 0")
                self.logger.warning(f"   ⏱️ Time elapsed: {duration:.1f}s")
                
                return empty_results
                
        # 🔧 CRITICAL fix: add safety check at function end to ensure no implicit None return
        self.logger.error(f"🚨 CRITICAL: test_variant_regular reached end of function without returning! This should not happen")
        fallback_results = {
            'results': [],
            'total_tests': tests_to_run if 'tests_to_run' in locals() else 1,
            'successful_tests': 0,
            'overall_success_rate': 0.0,
            'error': 'Function reached end without return - this should not happen',
            'fallback': True
        }
        return fallback_results

    def test_variant_with_scrolling_intelligence(self, scenario_name: str, variant_name: str,
                                               html_path: str, max_scrolls: int = 10, 
                                               num_tests: int = 5,
                                               viewport_height: int = 1200, scroll_step: int = 600,
                                               max_iterations: int = 20) -> Dict[str, Any]:
        """Test a single variant using scrolling smart search method"""
        # TODO: Implement scrolling smart search logic
        self.logger.info(f"🔧 Scrolling smart search method not yet implemented: {scenario_name}/{variant_name}")
        return {
            'results': [],
            'total_tests': 0,
            'successful_tests': 0,
            'overall_success_rate': 0,
            'error': 'Method not implemented'
        }
    
    def test_variant_no_scaling(self, scenario_name: str, variant_name: str, 
                              screenshot_path: str, num_tests: int = None) -> Dict[str, Any]:
        """Original test method without scaling - for comparative testing"""
        import time
        import numpy as np
        import traceback
        
        start_time = time.time()
        
        try:
            scenario_info = self.scenarios[scenario_name]
            
            # Use the num_tests parameter passed in; if not provided, use the default from config
            tests_to_run = num_tests if num_tests is not None else self.test_configs['tests_per_variant']
            
            self.logger.info(f"🔧 Starting no-scale multi-level semantic analysis test: {scenario_name}/{variant_name}")
            self.logger.info(f"📊 Test count: {tests_to_run}")
            
            # Check screenshot size
            from PIL import Image
            with Image.open(screenshot_path) as img:
                original_width, original_height = img.size
                self.logger.info(f"📐 Original image size: {original_width}x{original_height}")
            
            results = []
            
            for test_id in range(tests_to_run):
                self.logger.info(f"🧪 Test {test_id + 1}/{tests_to_run}")
                
                # Use prompt strictly matching UI-TARS format for inference
                scenario_prompt = self.get_scenario_prompt(scenario_name)
                
                # Smart GPU selection: prefer idle GPU to avoid memory shortage
                if hasattr(self, 'manual_gpu_id') and self.manual_gpu_id is not None:
                    gpu_id = self.manual_gpu_id
                    self.logger.info(f"🎯 Using manually specified GPU {gpu_id}")
                else:
                    gpu_id = self.gpu_tester.select_best_gpu()
                    self.logger.info(f"🔍 Automatically selected idle GPU {gpu_id}")
                
                # Record full prompt sent to UI-TARS for debugging
                self.logger.info(f"📤 Prompt sent to UI-TARS: {scenario_prompt[:100]}...")
                
                # Use original full screenshot for inference (may cause OOM)
                result = self.gpu_tester.single_gpu_inference(
                    region_path=screenshot_path,  # Use full screenshot directly
                    prompt=scenario_prompt,
                    gpu_id=gpu_id,
                    scenario=scenario_name
                )
                
                # Record complete UI-TARS response for debugging
                response = result.get('response', '') if result else ''
                self.logger.info(f"📥 UI-TARS response: {response[:200]}...")
                
                if result and 'response' in result:
                    # Use semantic analysis method to evaluate response
                    target_product_names = scenario_info.get('target_product_names', [])
                    semantic_analysis = self.analyze_product_mention(response, target_product_names, scenario_name)
                    
                    # Extract semantic analysis result
                    is_successful = semantic_analysis['is_successful']
                    success_level = semantic_analysis['success_level']
                    semantic_score = semantic_analysis['semantic_score']
                    response_type = semantic_analysis.get('response_type', 'unknown')
                    is_descriptive = semantic_analysis.get('is_descriptive', False)
                    
                    test_result = {
                        'test_id': test_id,
                        'response': response,
                        'response_analysis': semantic_analysis,
                        'is_successful': is_successful,
                        'success_level': success_level,
                        'semantic_score': semantic_score,
                        'response_type': response_type,
                        'is_descriptive': is_descriptive,
                        'confidence': result.get('confidence', 0.0),
                        'method': 'no_scaling'
                    }
                    
                    # 🔧 Fix: add test result to results list
                    results.append(test_result)
                    
                    # Detailed result log
                    self.logger.info(f"📋 Test result:")
                    self.logger.info(f"   ✅ Semantic success: {is_successful}")
                    self.logger.info(f"   🎯 Success level: {success_level}")
                    self.logger.info(f"   📊 Semantic score: {semantic_score:.3f}")
                    self.logger.info(f"   💡 Response type: {response_type}")
                    self.logger.info(f"   📝 Descriptive: {is_descriptive}")
            
            # 🔧 Fix: only compute stats when results is not empty; provide defaults when empty
            total_tests = len(results) if results else tests_to_run
            
            if results:
                # Classification statistics
                precise_success = [r for r in results if r['success_level'] == 'precise_success']
                approximate_success = [r for r in results if r['success_level'] == 'approximate_success']
                failures = [r for r in results if r['success_level'] in ['failure', 'failed']]
                
                # Descriptive answer statistics
                descriptive_responses = [r for r in results if r['response_analysis']['is_descriptive']]
            else:
                # 🔧 Fix: if no test results, provide default empty lists
                self.logger.warning("⚠️ No test results found, using default empty values")
                precise_success = []
                approximate_success = []
                failures = []
                descriptive_responses = []
            
            # Compute success rate
            precise_success_rate = len(precise_success) / total_tests if total_tests > 0 else 0
            approximate_success_rate = len(approximate_success) / total_tests if total_tests > 0 else 0
            overall_success_rate = (len(precise_success) + len(approximate_success)) / total_tests if total_tests > 0 else 0
            failure_rate = len(failures) / total_tests if total_tests > 0 else 0
            descriptive_rate = len(descriptive_responses) / total_tests if total_tests > 0 else 0
            
            # 🔧 Fix: add debug info to ensure semantic score statistics work correctly
            try:
                semantic_scores = [r['response_analysis']['semantic_score'] for r in results]
                self.logger.info(f"✅ Semantic score extraction successful: {len(semantic_scores)} score(s)")
                avg_semantic_score = np.mean(semantic_scores) if semantic_scores else 0.0
                max_semantic_score = np.max(semantic_scores) if semantic_scores else 0.0
                min_semantic_score = np.min(semantic_scores) if semantic_scores else 0.0
            except Exception as e:
                self.logger.error(f"❌ Semantic score statistics failed: {e}")
                self.logger.error(f"📋 Results structure: {[{k: type(v).__name__ for k, v in r.items()} for r in results[:2]]}")
                semantic_scores = []
                avg_semantic_score = max_semantic_score = min_semantic_score = 0.0
            
            result_summary = {
                'scenario': scenario_name,
                'variant': variant_name,
                'total_tests': total_tests,
                
                # Multi-level success statistics
                'precise_success_count': len(precise_success),
                'approximate_success_count': len(approximate_success),
                'failure_count': len(failures),
                'descriptive_responses_count': len(descriptive_responses),
                
                # Success rates
                'precise_success_rate': precise_success_rate,
                'approximate_success_rate': approximate_success_rate,
                'overall_success_rate': overall_success_rate,
                'failure_rate': failure_rate,
                'descriptive_response_rate': descriptive_rate,
                
                # Semantic score statistics
                'average_semantic_score': avg_semantic_score,
                'max_semantic_score': max_semantic_score,
                'min_semantic_score': min_semantic_score,
                
                # Time statistics
                'total_time': time.time() - start_time,
                
                'results': results,
                'image_size': {'width': original_width, 'height': original_height},
                'semantic_analysis': True,  # Mark this as a semantic analysis test
                'analysis_method': 'multi_level_success_criteria'
            }
            
            self.logger.info(f"✅ {scenario_name}/{variant_name} multi-level semantic analysis complete:")
            self.logger.info(f"   🎯 Precise success: {len(precise_success)}/{total_tests} ({precise_success_rate:.2%})")
            self.logger.info(f"   📊 Approximate success: {len(approximate_success)}/{total_tests} ({approximate_success_rate:.2%})")
            self.logger.info(f"   ❌ Failures: {len(failures)}/{total_tests} ({failure_rate:.2%})")
            self.logger.info(f"   📝 Descriptive answers: {len(descriptive_responses)}/{total_tests} ({descriptive_rate:.2%})")
            self.logger.info(f"   🏆 Overall success rate: {overall_success_rate:.2%}")
            self.logger.info(f"   📈 Average semantic score: {avg_semantic_score:.3f}")
            print(f"🎉 Preparing to return result_summary: {type(result_summary)}")
            print(f"🔍 result_summary content preview: {list(result_summary.keys()) if isinstance(result_summary, dict) else 'Not a dict'}")
            self.logger.info(f"🎉 Preparing to return result_summary: {type(result_summary)}")
            print(f"🎉 Successfully returned result_summary")
            return result_summary
            
        except Exception as e:
            print(f"❌ Function exception: {e}")
            print(f"❌ Exception type: {type(e).__name__}")
            print(f"❌ Exception traceback:")
            traceback.print_exc()
            self.logger.error(f"❌ No-scale test failed: {e}")
            self.logger.error(f"❌ Exception type: {type(e).__name__}")
            self.logger.error(f"🔍 Results state when exception occurred: length={len(results) if 'results' in locals() else 'undefined'}")
            return None
    
    def analyze_response_content(self, response: str, scenario_name: str) -> Dict[str, Any]:
        """Analyze response content for target product info using semantic analysis method"""
        if not response or response.strip() == "":
            return {'contains_target_content': False, 'matched_keywords': [], 'matched_products': [], 'confidence': 0.0}
        
        scenario_info = self.scenarios[scenario_name]
        target_products = scenario_info['target_product_names']
        
        # Use analyze_product_mention for semantic analysis
        analysis_result = self.analyze_product_mention(response, target_products, scenario_name)
        
        # Extract info from semantic analysis result
        is_successful = analysis_result['is_successful']
        semantic_score = analysis_result['semantic_score']
        response_type = analysis_result['response_type']
        
        # Extract actually mentioned products
        mentioned_products = self.extract_mentioned_products(response)
        
        # Keep return format compatible with original interface
        contains_target_content = is_successful
        
        # Provide some default values for compatibility
        matched_keywords = []  # Semantic analysis does not rely on keyword matching
        pattern_matches = 1 if is_successful else 0  # Simplified to boolean value
        
        return {
            'contains_target_content': contains_target_content,
            'matched_keywords': matched_keywords,
            'matched_products': mentioned_products,
            'pattern_matches': pattern_matches,
            'semantic_score': semantic_score,
            'response_type': response_type,
            'overall_confidence': semantic_score,
            'response_length': len(response),
            'analysis_result': analysis_result
        }
    
    def test_variant_with_scrolling(self, scenario_name: str, variant_name: str, 
                                   html_file: str = None, num_tests: int = 10,
                                   viewport_height: int = 1200, scroll_step: int = 600,
                                   max_scrolls: int = 5) -> Dict[str, Any]:
        """Test a single variant using scrolling feature
        
        Args:
            scenario_name: scenario name
            variant_name: variant name
            html_file: HTML file path (optional)
            num_tests: number of tests per viewport
            viewport_height: viewport height
            scroll_step: scroll step size
            max_scrolls: maximum scroll count
        """
        import time
        
        start_time = time.time()
        
        try:
            # Generate full screenshot (no scaling)
            if html_file:
                html_path = Path(html_file)
            else:
                scenario_info = self.scenarios[scenario_name]
                html_path = Path(scenario_info['path']) / f"{variant_name}.html"
            
            if not html_path.exists():
                self.logger.error(f"❌ HTML file does not exist: {html_path}")
                return None
            
            self.logger.info(f"🔄 Starting scroll test: {scenario_name}/{variant_name}")
            
            # Generate full screenshot (scrolling mode, keep original size)
            full_screenshot_path = self.generate_screenshot_simple(str(html_path), enable_scrolling=True)
            if not full_screenshot_path:
                self.logger.error(f"❌ Screenshot generation failed: {variant_name}")
                return None
            
            # Initialize scrolling viewport
            viewport = ScrollingViewport(
                full_screenshot_path=full_screenshot_path,
                viewport_height=viewport_height,
                viewport_width=1280,  # 🎯 Use standard width to preserve full page info
                scroll_step=scroll_step
            )
            
            scenario_info = self.scenarios[scenario_name]
            
            # Get target coordinates from coordinate manager
            target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
            if target_coordinates:
                # Compute center coordinates
                center_x = target_coordinates['x'] + target_coordinates['width'] // 2
                center_y = target_coordinates['y'] + target_coordinates['height'] // 2
                target_coords = (center_x, center_y)
                self.logger.info(f"🎯 Got target coordinates from coordinate manager: {target_coords}")
            else:
                # If coordinate manager has no entry for this variant, use default
                target_coords = (400, 2000)  # Default coordinates
                self.logger.warning(f"⚠️ Variant not in coordinate manager, using default coordinates: {target_coords}")
            
            all_results = []
            scroll_count = 0
            found_target = False
            
            while scroll_count <= max_scrolls and not found_target:
                self.logger.info(f"📱 Testing scroll position {scroll_count}: y_offset={viewport.current_y_offset}")
                
                # Get current viewport screenshot
                current_view_path = viewport.get_current_view()
                
                # Run UI-TARS tests on current view
                view_results = []
                for test_id in range(num_tests):
                    self.logger.info(f"🧪 View {scroll_count}, test {test_id + 1}/{num_tests}")
                    
                    # 🎯 Use natural prompt, let UI-TARS decide to scroll or click
                    scenario_prompt = self.get_natural_scenario_prompt(scenario_name)
                    
                    self.logger.info(f"📤 Natural prompt sent to UI-TARS:")
                    self.logger.info(f"{'='*50}")
                    self.logger.info(f"{scenario_prompt}")
                    self.logger.info(f"{'='*50}")
                    
                    # 🎯 Check whether this is a forced memory decision click
                    if 'forced_memory_click' in locals() and forced_memory_click:
                        # Skip inference, use existing action_info and response directly
                        self.logger.info("🧠 Processing forced memory decision click, skipping repeated inference")
                        pass  # action_info and response already set in forced memory decision
                    else:
                        # Use unified model inference interface
                        if (self.use_vllm and self.vllm_engine) or (self.model_type == "qwen3vl" and self.qwen3vl_wrapper) or (self.model_type == "glm4v" and self.glm4v_wrapper):
                            self.logger.info(f"🚀 Using {self.model_type.upper()} inference engine")
                        
                            result = self.generate_model_response(
                                image_path=current_view_path,
                                prompt=scenario_prompt,
                                exploration_history=None,  # This function has no history
                                max_tokens=512,
                                temperature=1.0,
                                top_p=0.8
                            )
                            
                            if result and result.get('success'):
                                response = result.get('response', '')
                                inference_time = result.get('inference_time', 0)
                                self.logger.info(f"✅ {self.model_type.upper()} inference completed - elapsed: {inference_time:.1f}s")
                            else:
                                self.logger.error(f"❌ {self.model_type.upper()} inference failed, falling back to Transformer")
                                # Fall back to transformer inference
                                gpu_id = self.gpu_tester.select_best_gpu()
                                result = self.gpu_tester.single_gpu_inference(
                                    region_path=current_view_path,
                                    prompt=scenario_prompt,
                                    gpu_id=gpu_id,
                                    scenario=scenario_name
                                )
                                response = result.get('response', '') if result else ''
                        else:
                            # Use Transformer inference (fallback mode)
                            self.logger.info("🔧 Using Transformer inference engine")
                            gpu_id = self.gpu_tester.select_best_gpu()
                            
                            result = self.gpu_tester.single_gpu_inference(
                                region_path=current_view_path,
                                prompt=scenario_prompt,
                                gpu_id=gpu_id,
                                scenario=scenario_name
                            )
                            response = result.get('response', '') if result else ''
                    
                    if response:
                        self.logger.info(f"📥 UI-TARS full response:")
                        self.logger.info(f"{'='*50}")
                        self.logger.info(f"{response}")
                        self.logger.info(f"{'='*50}")
                        
                        # 🎯 Fix: use enhanced action info extraction with coordinate mapping and hit judgment
                        action_info = self.extract_action_info_with_coordinate_check(
                            response=response, 
                            scenario=scenario_name, 
                            variant_name=variant_name,
                            viewport=viewport,  # Pass viewport for coordinate mapping
                            model_result=result  # Pass model result for Qwen3-VL coordinate extraction
                        )
                        
                        if action_info['action_type'] == 'click':
                            # 🎯 Use mapped full coordinates
                            if action_info.get('full_coordinates'):
                                full_x, full_y = action_info['full_coordinates']
                                viewport_x, viewport_y = action_info['coordinates']  # Original viewport coordinates
                                
                                # Check whether click position is close to target (using full coordinates)
                                # Safe coordinate unpacking for distance computation
                                try:
                                    if isinstance(target_coords, dict):
                                        target_x = target_coords.get('x', 0)
                                        target_y = target_coords.get('y', 0)
                                    elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
                                        target_x, target_y = target_coords[0], target_coords[1]
                                    else:
                                        self.logger.warning(f"⚠️ Unable to parse target coordinate format: {target_coords}")
                                        target_x, target_y = 0, 0
                                    
                                    distance = ((full_x - target_x)**2 + (full_y - target_y)**2)**0.5
                                except (IndexError, ValueError, TypeError) as e:
                                    self.logger.warning(f"⚠️ Distance calculation failed: target_coords={target_coords}, error: {e}")
                                    distance = float('inf')
                                
                                # 🎯 Prefer coordinate manager's precise hit judgment (strict rectangle mode)
                                coordinate_hit_result = self.coordinate_manager.is_click_in_target_area(
                                    scenario=scenario_name,
                                    variant_name=variant_name,
                                    click_x=full_x,
                                    click_y=full_y,
                                    hit_radius=0  # Force strict rectangle judgment only
                                )
                                coordinate_success = coordinate_hit_result['is_hit']
                                
                                # Use coordinate region judgment as primary success criterion, distance as backup
                                success = coordinate_success or (distance <= self.test_configs['success_radius_loose'])
                                
                                if coordinate_success:
                                    found_target = True
                                    self.logger.info(f"🎯 Target found! Coordinate hit: ✅ {coordinate_hit_result['message']}")
                                elif distance <= self.test_configs['success_radius_loose']:
                                    found_target = True
                                    self.logger.info(f"🎯 Target found! Distance: {distance:.1f}px (legacy distance judgment)")
                                else:
                                    self.logger.info(f"🎯 Target missed: ❌ {coordinate_hit_result['message']}, distance: {distance:.1f}px")
                            else:
                                # If no coordinate mapping info, use original logic
                                coordinates = action_info.get('coordinates')
                                if coordinates is not None:
                                    viewport_x, viewport_y = coordinates
                                    full_x, full_y = viewport.map_viewport_coords_to_full(viewport_x, viewport_y)
                                else:
                                    self.logger.warning("⚠️ No coordinate info, skipping coordinate processing")
                                    viewport_x, viewport_y = 0, 0
                                    full_x, full_y = 0, 0
                                
                                # Safe coordinate unpacking for distance computation
                                try:
                                    if isinstance(target_coords, dict):
                                        target_x = target_coords.get('x', 0)
                                        target_y = target_coords.get('y', 0)
                                    elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
                                        target_x, target_y = target_coords[0], target_coords[1]
                                    else:
                                        self.logger.warning(f"⚠️ Unable to parse target coordinate format: {target_coords}")
                                        target_x, target_y = 0, 0
                                    
                                    distance = ((full_x - target_x)**2 + (full_y - target_y)**2)**0.5
                                except (IndexError, ValueError, TypeError) as e:
                                    self.logger.warning(f"⚠️ Distance calculation failed: target_coords={target_coords}, error: {e}")
                                    distance = float('inf')
                                
                                # 🎯 Prefer coordinate manager for target area determination (strict rectangle mode)
                                coordinate_hit_result = self.coordinate_manager.is_click_in_target_area(
                                    scenario=scenario_name,
                                    variant_name=variant_name,
                                    click_x=full_x,
                                    click_y=full_y,
                                    hit_radius=0  # Force strict rectangle judgment only
                                )
                                coordinate_success = coordinate_hit_result['is_hit']
                                
                                # Use coordinate region judgment as primary success criterion, distance as backup
                                success = coordinate_success or (distance <= self.test_configs['success_radius_loose'])
                                
                                if coordinate_success:
                                    found_target = True
                                    self.logger.info(f"🎯 Target found! Coordinate hit: ✅ {coordinate_hit_result['message']}")
                                elif distance <= self.test_configs['success_radius_loose']:
                                    found_target = True
                                    self.logger.info(f"🎯 Target found! Distance: {distance:.1f}px (legacy distance judgment)")
                                else:
                                    self.logger.info(f"🎯 Target missed: ❌ {coordinate_hit_result['message']}, distance: {distance:.1f}px")
                            
                            test_result = {
                                'test_id': test_id,
                                'scroll_position': scroll_count,
                                'viewport_coords': (viewport_x, viewport_y),
                                'full_coords': (full_x, full_y),
                                'target_coords': target_coords,
                                'distance': distance,
                                'success': success,  # Comprehensive result including coordinate hit and distance judgment
                                'coordinate_hit': coordinate_success,  # Record coordinate hit separately
                                'response': response,
                                'action_info': action_info,
                                'scroll_info': viewport.get_scroll_info()
                            }
                            
                            view_results.append(test_result)
                            
                            # 🎯 If target found successfully, can choose to end test
                            if success or coordinate_success:
                                self.logger.info(f"🎉 Target found successfully, ending current view test")
                                break
                            
                        elif action_info['action_type'] == 'scroll':
                            # 🎯 Handle scrolling commands actively requested by UI-TARS
                            scroll_direction = action_info.get('direction', 'down')
                            
                            self.logger.info(f"📱 UI-TARS actively requested scroll: {scroll_direction}")
                            
                            if scroll_direction == 'down':
                                scrolled = viewport.scroll_down()
                            else:
                                scrolled = viewport.scroll_up()
                            
                            if scrolled:
                                self.logger.info(f"✅ UI-TARS scroll successful: {scroll_direction}")
                                
                                # 🔄 After successful scroll, immediately get new view to continue test
                                try:
                                    os.unlink(current_view_path)  # Clean up old view
                                except:
                                    pass
                                
                                # Get new view after scrolling
                                current_view_path = viewport.get_current_view()
                                self.logger.info(f"🔄 Updated view after UI-TARS scroll: {os.path.basename(current_view_path)}")
                                self.logger.info(f"📍 New scroll position: {viewport.get_scroll_info()}")
                                
                            else:
                                self.logger.info(f"📱 UI-TARS scroll failed: cannot continue {scroll_direction}")
                            
                            test_result = {
                                'test_id': test_id,
                                'scroll_position': scroll_count,
                                'action_type': 'scroll',
                                'direction': scroll_direction,
                                'scrolled': scrolled,
                                'uitars_initiated': True,  # Mark as UI-TARS initiated scroll
                                'response': response,
                                'scroll_info': viewport.get_scroll_info()
                            }
                            
                            view_results.append(test_result)
                            
                            # 🔄 If scroll successful, continue testing at new position rather than ending current loop
                            if scrolled:
                                self.logger.info(f"🔄 Continuing test on new view after UI-TARS scroll, not ending current loop")
                                # Continue current for loop, using new current_view_path
                        
                        else:
                            # Other response types (e.g., descriptive answer)
                            self.logger.info(f"📝 UI-TARS gave descriptive answer: {action_info['action_type']}")
                            
                            test_result = {
                                'test_id': test_id,
                                'scroll_position': scroll_count,
                                'action_type': action_info['action_type'],
                                'response': response,
                                'scroll_info': viewport.get_scroll_info()
                            }
                            
                            view_results.append(test_result)
                
                all_results.extend(view_results)
                
                # If target not found and can still scroll, auto-scroll to next position
                if not found_target and scroll_count < max_scrolls:
                    self.logger.info(f"📱 Auto-scrolling to next position...")
                    if not viewport.scroll_down():
                        self.logger.info(f"📱 Reached page bottom, stopping scroll")
                        break
                
                scroll_count += 1
                
                # Clean up current view temporary file
                try:
                    os.unlink(current_view_path)
                except:
                    pass
            
            # Compute statistics
            total_tests = len(all_results)
            successful_clicks = len([r for r in all_results if r.get('success', False)])
            total_clicks = len([r for r in all_results if r.get('action_type') == 'click'])
            total_scrolls = len([r for r in all_results if r.get('action_type') == 'scroll'])
            
            end_time = time.time()
            total_time = end_time - start_time
            
            final_results = {
                'scenario': scenario_name,
                'variant': variant_name,
                'total_tests': total_tests,
                'total_clicks': total_clicks,
                'total_scrolls': total_scrolls,
                'successful_clicks': successful_clicks,
                'success_rate': successful_clicks / total_clicks if total_clicks > 0 else 0,
                'found_target': found_target,
                'scroll_positions_tested': scroll_count,
                'results': all_results,
                'total_time': total_time,
                'viewport_config': {
                    'viewport_height': viewport_height,
                    'scroll_step': scroll_step,
                    'max_scrolls': max_scrolls
                },
                'full_screenshot_info': {
                    'path': full_screenshot_path,
                    'size': (viewport.full_width, viewport.full_height)
                }
            }
            
            self.logger.info(f"✅ {scenario_name}/{variant_name} scroll test complete:")
            self.logger.info(f"    🎯 Successful clicks: {successful_clicks}/{total_clicks}")
            self.logger.info(f"    📱 Scroll count: {total_scrolls}")
            self.logger.info(f"    🎉 Target found: {found_target}")
            self.logger.info(f"    ⏱️ Total elapsed time: {total_time:.1f}s")
            
            return final_results
            
        except Exception as e:
            self.logger.error(f"❌ Scroll test failed: {e}")
            traceback.print_exc()
            return None
    
    def parse_uitars_response(self, response: str) -> dict:
        """Parse UI-TARS or Qwen3-VL response, extract action and coordinates"""
        action_info = {
            'action_type': 'unknown',
            'coordinates': None,
            'direction': None,
            'product_name': '',
            'raw_response': response
        }
        
        # Clean response text
        response = response.strip()
        
        # 🔑 CRITICAL: Only parse Action section, ignore keywords in Thought
        # Find Action section
        action_match = re.search(r'Action:\s*(.+?)(?:\n|$)', response, re.IGNORECASE | re.DOTALL)
        if action_match:
            action_line = action_match.group(1).strip()
            
            # 🎯 Prefer parsing scroll format - supports multiple coordinate formats
            # scroll(start_box="(x,y)", direction="down") or scroll(start_box="x,y", direction="down")
            # First try to match quoted coordinates
            scroll_box_match = re.search(r'scroll\s*\(\s*start_box\s*=\s*[\'"]([^\'"]*)[\'"]\s*,\s*direction\s*=\s*[\'"]?(\w+)', action_line, re.IGNORECASE)
            if scroll_box_match:
                coord_str = scroll_box_match.group(1).strip()
                direction = scroll_box_match.group(2).lower()
                # Extract numbers from string
                numbers = re.findall(r'(\d+)\s*,\s*(\d+)', coord_str)
                if numbers:
                    x, y = int(numbers[0][0]), int(numbers[0][1])
                    action_info['action_type'] = 'scroll'
                    action_info['coordinates'] = (x, y)
                    action_info['direction'] = direction
                    self.logger.info(f"✅ Parsed scroll format: coords={action_info['coordinates']}, direction={direction}")
                    return action_info
            
            # Parse click action - supports multiple formats
            # 🔧 Fix: support all possible format combinations
            # Format 1: click(start_box='(187, 273)')   - quotes outside brackets
            # Format 2: click(start_box="(187, 273)")   - double quotes outside brackets
            # Format 3: click(start_box='187, 273')     - no brackets inside quotes
            # Format 4: click(start_box="182, 273")     - no brackets inside double quotes ⭐ NEW
            # Format 5: click(start_box=(187, 273))     - no quotes, with brackets
            # Format 6: click(start_box=187, 273)       - no quotes, no brackets
            
            # First try to match quoted formats (with or without brackets inside quotes)
            click_match = re.search(r'click\s*\(\s*start_box\s*=\s*[\'"]([^\'"]*)[\'"]\s*\)', action_line, re.IGNORECASE)
            if click_match:
                coord_str = click_match.group(1).strip()
                # Extract numbers from string (may or may not have parentheses)
                numbers = re.findall(r'(\d+)\s*,\s*(\d+)', coord_str)
                if numbers:
                    x, y = int(numbers[0][0]), int(numbers[0][1])
                    action_info['action_type'] = 'click'
                    action_info['coordinates'] = (x, y)
                    self.logger.info(f"✅ Parsed click format (quoted): coords={action_info['coordinates']}, original='{coord_str}'")
                    return action_info
            
            # If no quotes, try to match brackets or bare numbers directly
            click_match = re.search(r'click\s*\(\s*start_box\s*=\s*\(?(\d+)\s*,\s*(\d+)\)?\s*\)', action_line, re.IGNORECASE)
            if click_match:
                x, y = int(click_match.group(1)), int(click_match.group(2))
                action_info['action_type'] = 'click'
                action_info['coordinates'] = (x, y)
                self.logger.info(f"✅ Parsed click format (unquoted): coords={action_info['coordinates']}")
                return action_info
            
            # Parse scroll action (direction only)
            scroll_match = re.search(r'scroll\s*\(\s*direction\s*=\s*[\'"]?(\w+)', action_line, re.IGNORECASE)
            if scroll_match:
                direction = scroll_match.group(1).lower()
                action_info['action_type'] = 'scroll'
                action_info['direction'] = direction
                return action_info
            
            # Parse other action types
            if 'wait(' in action_line.lower():
                action_info['action_type'] = 'wait'
            elif 'type(' in action_line.lower():
                action_info['action_type'] = 'type'
        
        # 🎯 More robust coordinate extraction - handles multiple formats
        # Match (x, y) format coordinates
        coord_patterns = [
            r'\(\s*(\d+)\s*,\s*(\d+)\s*\)',  # (450, 300)
            r'coordinates?\s*[:\s]*\(\s*(\d+)\s*,\s*(\d+)\s*\)',  # coordinates (450, 300)
            r'at\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)',  # at (450, 300) 
            r'click\s*at\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)',  # click at (450, 300)
            r'position\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)'  # position (450, 300)
        ]
        
        # 🔑 CRITICAL: Extract Action field for action identification
        action_match = re.search(r'Action:\s*(.+?)(?:\n|$)', response, re.IGNORECASE | re.DOTALL)
        action_line = action_match.group(1).strip() if action_match else response
        
        for pattern in coord_patterns:
            coord_match = re.search(pattern, action_line, re.IGNORECASE)
            if coord_match:
                x, y = int(coord_match.group(1)), int(coord_match.group(2))
                # 🔧 Fix: only check Action field for action intent, ignore Thought
                # First check whether Action field has explicit scroll intent
                if any(phrase in action_line.lower() for phrase in ['scroll(', 'scrolling', 'scroll down', 'scroll up']):
                    action_info['action_type'] = 'scroll'
                    if any(word in action_line.lower() for word in ['down', 'below', 'next']):
                        action_info['direction'] = 'down'
                    elif any(word in action_line.lower() for word in ['up', 'above', 'previous']):
                        action_info['direction'] = 'up'
                    else:
                        action_info['direction'] = 'down'  # Default downward
                    return action_info
                # If Action field contains click-related words and has coordinates, treat as click
                elif any(word in action_line.lower() for word in ['click', 'tap', 'select', 'choose']):
                    action_info['action_type'] = 'click'
                    action_info['coordinates'] = (x, y)
                    return action_info
        
        # 🔑 If Action field did not find explicit action, only check scroll vocabulary in Action field
        if any(word in action_line.lower() for word in ['scroll(', 'scrolling']):
            action_info['action_type'] = 'scroll'
            if any(word in action_line.lower() for word in ['down', 'below', 'next']):
                action_info['direction'] = 'down'
            elif any(word in action_line.lower() for word in ['up', 'above', 'previous']):
                action_info['direction'] = 'up'
            else:
                action_info['direction'] = 'down'  # Default downward
        
        # If still unrecognized, mark as descriptive answer
        if action_info['action_type'] == 'unknown':
            action_info['action_type'] = 'descriptive'
        
        # Extract product name (from Thought section or response content)
        if not action_info['product_name']:
            # Try to extract product name from Thought section
            thought_match = re.search(r'Thought:\s*(.+?)(?:Action:|$)', response, re.IGNORECASE | re.DOTALL)
            if thought_match:
                thought_text = thought_match.group(1).strip()
                # Look for product-related vocabulary
                product_patterns = [
                    r'(?:laptop|computer|notebook|chromebook)\s*[\'"]?([^\'",\.]+)[\'"]?',
                    r'(?:HP|Dell|Acer|Lenovo|Apple)\s+([^,\.]+)',
                    r'(?:select|choose|click)\s+[\'"]?([^\'",\.]+)[\'"]?',
                    r'[\'"]([^\'",]*(?:laptop|computer|HP|Dell|Acer|Lenovo)[^\'",]*)[\'"]'
                ]
                
                for pattern in product_patterns:
                    match = re.search(pattern, thought_text, re.IGNORECASE)
                    if match:
                        product_name = match.group(1).strip()
                        if len(product_name) > 3 and len(product_name) < 100:  # Reasonable length
                            action_info['product_name'] = product_name
                            break
        
        return action_info
    
    def run_comprehensive_test(self, html_file: str, num_tests: int = 10, device: str = 'cuda') -> Dict[str, Any]:
        """Run comprehensive test supporting target ad card detection
        
        Args:
            html_file: HTML file path
            num_tests: number of tests
            device: device type
            
        Returns:
            Dictionary containing test results and target hit statistics
        """
        try:
            # Determine scenario
            scenario_name = self.extract_scenario_from_file(html_file)
            if not scenario_name:
                raise ValueError(f"Cannot determine scenario from file path: {html_file}")
            
            scenario_info = self.scenarios[scenario_name]
            
            # Generate screenshots and regions
            variant_name = Path(html_file).stem
            generation_result = self.generate_screenshot_and_regions(
                scenario_name, variant_name, html_file
            )
            
            if not generation_result:
                raise ValueError("Screenshot generation failed")
            
            # Use semantic analysis for testing
            test_results = self.test_variant_regular(
                scenario_name=scenario_name,
                variant_name=variant_name,
                screenshot_path=generation_result['screenshot']['path'],
                num_tests=num_tests
            )
            
            if not test_results:
                raise ValueError("Test execution failed")
            
            # Add target hit statistics
            if 'results' in test_results:
                # 🔧 FIX: Get actual coordinates from coordinate_manager
                target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
                if target_coordinates:
                    target_center = (
                        target_coordinates['x'] + target_coordinates['width'] // 2,
                        target_coordinates['y'] + target_coordinates['height'] // 2
                    )
                    target_hit_stats = self.calculate_target_hit_statistics(
                        test_results['results'], 
                        target_center
                    )
                    test_results['target_hit_stats'] = target_hit_stats
            
            # Add content analysis
            content_analysis = []
            chosen_items = []  # Record names of selected items
            if 'results' in test_results:
                for result in test_results['results']:
                    if 'response' in result:
                        analysis = self.analyze_response_content(
                            result['response'], scenario_name
                        )
                        content_analysis.append(analysis)
                        
                        # Extract selected item name
                        validation = analysis.get('validation_result', {})
                        chosen_item = validation.get('chosen_item_name')
                        if chosen_item:
                            chosen_items.append(chosen_item)
                            self.logger.info(f"🎯 Selected item/hotel: {chosen_item}")
            
            test_results['content_analysis'] = content_analysis
            test_results['chosen_items'] = chosen_items  # Add list of selected item names
            test_results['scenario'] = scenario_name
            # 🔧 FIX: Use actual coordinates from coordinate_manager, not the non-existent target_coords in scenario_info
            target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
            if target_coordinates:
                test_results['target_coords'] = [target_coordinates['x'], target_coordinates['y']]
            else:
                test_results['target_coords'] = None  # Set to None if coordinates not found
            
            return test_results
            
        except Exception as e:
            self.logger.error(f"❌ Comprehensive test failed: {e}")
            traceback.print_exc()
            return None
    
    def extract_scenario_from_file(self, file_path: str) -> str:
        """Extract scenario name from file path"""
        file_path = str(file_path).lower()
        for scenario in self.scenarios.keys():
            if scenario in file_path:
                return scenario
        return None
    
    def calculate_target_hit_statistics(self, results: List[Dict], target_coords: Tuple[int, int]) -> Dict[str, Any]:
        """Calculate target hit statistics"""
        hit_stats = {
            'total_tests': len(results),
            'successful_tests': 0,
            'coordinate_success_tests': 0,
            'validation_success_tests': 0,
            'inferred_coordinate_tests': 0,
            'hit_target_strict_50px': 0,
            'hit_target_loose_100px': 0,
            'hit_target_region_200px': 0,
            'distances': []
        }
        
        for result in results:
            if result.get('success', False):
                hit_stats['successful_tests'] += 1
                
                # Count different types of success
                if result.get('coordinate_success', False):
                    hit_stats['coordinate_success_tests'] += 1
                if result.get('validation_success', False):
                    hit_stats['validation_success_tests'] += 1
                if result.get('coordinate_inferred', False):
                    hit_stats['inferred_coordinate_tests'] += 1
                
                # Compute distance and hit statistics
                global_coords = result.get('global_coords', (0, 0))
                if global_coords != (0, 0):
                    # Safe coordinate unpacking for distance computation
                    try:
                        if isinstance(target_coords, dict):
                            target_x = target_coords.get('x', 0)
                            target_y = target_coords.get('y', 0)
                        elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
                            target_x, target_y = target_coords[0], target_coords[1]
                        else:
                            self.logger.warning(f"⚠️ Unable to parse target coordinate format: {target_coords}")
                            target_x, target_y = 0, 0
                        
                        distance = ((global_coords[0] - target_x)**2 + (global_coords[1] - target_y)**2)**0.5
                    except (IndexError, ValueError, TypeError) as e:
                        self.logger.warning(f"⚠️ Distance calculation failed: target_coords={target_coords}, error: {e}")
                        distance = float('inf')
                    
                    hit_stats['distances'].append(distance)
                    
                    if distance <= 50:
                        hit_stats['hit_target_strict_50px'] += 1
                    if distance <= 100:
                        hit_stats['hit_target_loose_100px'] += 1
                    if distance <= 200:
                        hit_stats['hit_target_region_200px'] += 1
        
        # Compute hit rate
        if hit_stats['successful_tests'] > 0:
            successful = hit_stats['successful_tests']
            hit_stats['strict_hit_rate'] = hit_stats['hit_target_strict_50px'] / successful
            hit_stats['loose_hit_rate'] = hit_stats['hit_target_loose_100px'] / successful
            hit_stats['region_hit_rate'] = hit_stats['hit_target_region_200px'] / successful
        else:
            hit_stats['strict_hit_rate'] = 0.0
            hit_stats['loose_hit_rate'] = 0.0
            hit_stats['region_hit_rate'] = 0.0
        
        # Compute distance statistics
        if hit_stats['distances']:
            import statistics
            hit_stats['average_distance'] = statistics.mean(hit_stats['distances'])
            hit_stats['median_distance'] = statistics.median(hit_stats['distances'])
            hit_stats['min_distance'] = min(hit_stats['distances'])
            hit_stats['max_distance'] = max(hit_stats['distances'])
        
        return hit_stats
    
    def calculate_distance_metrics(self, results: List[Dict], target_coords) -> Dict[str, Any]:
        """Calculate distance-related metrics"""
        if not results:
            return {'count': 0}
        
        successful_results = [r for r in results if r.get('success', False)]
        all_distances = []
        strict_successes = 0
        loose_successes = 0
        
        # Handle target coordinates format - may be dict or tuple
        if isinstance(target_coords, dict):
            # Extract x,y coordinates from dict - try multiple possible keys
            if 'x' in target_coords and 'y' in target_coords:
                tx = target_coords['x'] + target_coords.get('width', 0) // 2
                ty = target_coords['y'] + target_coords.get('height', 0) // 2
            elif 'center_x' in target_coords and 'center_y' in target_coords:
                tx, ty = target_coords['center_x'], target_coords['center_y']
            else:
                # If dict contains width and height, compute center point
                tx = target_coords.get('x', 0) + target_coords.get('width', 0) // 2
                ty = target_coords.get('y', 0) + target_coords.get('height', 0) // 2
        elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
            # Safe tuple unpacking: ensure at least 2 elements
            try:
                tx, ty = target_coords[0], target_coords[1]
            except (IndexError, ValueError) as e:
                self.logger.warning(f"⚠️ Tuple unpacking failed: {target_coords}, error: {e}")
                return {'error': f'Tuple unpacking failed: {str(e)}'}
        else:
            self.logger.warning(f"⚠️ Unrecognized target coordinate format: {target_coords} (type: {type(target_coords)})")
            return {'error': 'Invalid target coordinates format'}
        
        for result in results:
            if 'global_coords' in result and result['global_coords']:
                # Compute distance
                x, y = result['global_coords']
                distance = np.sqrt((x - tx) ** 2 + (y - ty) ** 2)
                all_distances.append(distance)
                
                # Count success within different radii
                if distance <= self.test_configs['success_radius_strict']:
                    strict_successes += 1
                if distance <= self.test_configs['success_radius_loose']:
                    loose_successes += 1
        
        metrics = {
            'total_tests': len(results),
            'successful_tests': len(successful_results),
            'success_rate': len(successful_results) / len(results) * 100 if results else 0,
            'strict_successes': strict_successes,
            'loose_successes': loose_successes,
            'strict_success_rate': strict_successes / len(results) * 100 if results else 0,
            'loose_success_rate': loose_successes / len(results) * 100 if results else 0,
            'distances': all_distances
        }
        
        if all_distances:
            metrics.update({
                'avg_distance': np.mean(all_distances),
                'median_distance': np.median(all_distances),
                'min_distance': np.min(all_distances),
                'max_distance': np.max(all_distances),
                'std_distance': np.std(all_distances)
            })
        
        return metrics
    
    def test_booking_prompts(self, num_tests_per_prompt: int = 20, max_variants: int = 3) -> Dict[str, Any]:
        """Test different prompt effects for booking scenario to find the best prompt"""
        scenario_name = 'booking2'
        
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"🎯 Starting Booking scene Prompt optimization test")
        self.logger.info(f"{'='*80}")
        self.logger.info(f"📊 Test parameters:")
        self.logger.info(f"   • Tests per prompt: {num_tests_per_prompt}")
        self.logger.info(f"   • Max variants: {max_variants}")
        self.logger.info(f"   • Candidate prompts count: {len(self.booking_prompt_candidates)}")
        
        # Discover variants
        variants = self.discover_variants(scenario_name)
        if not variants:
            self.logger.error(f"❌ Cannot discover variants for {scenario_name}")
            return {}
        
        # Limit variant count for quick testing
        test_variants = variants[:max_variants]
        self.logger.info(f"🔍 Testing with variants: {test_variants}")
        
        prompt_results = {}
        scenario_info = self.scenarios[scenario_name]
        
        for prompt_idx, prompt in enumerate(self.booking_prompt_candidates):
            self.logger.info(f"\n📝 Testing Prompt {prompt_idx + 1}/{len(self.booking_prompt_candidates)}: '{prompt}'")
            
            prompt_stats = {
                'prompt': prompt,
                'total_tests': 0,
                'total_successes': 0,
                'variant_results': {},
                'average_success_rate': 0.0,
                'average_semantic_score': 0.0,
                'semantic_scores': []
            }
            
            for variant_name in test_variants:
                self.logger.info(f"   🧪 Testing variant: {variant_name}")
                
                # Generate screenshots and regions
                generation_result = self.generate_screenshot_and_regions(scenario_name, variant_name)
                if not generation_result:
                    self.logger.warning(f"⚠️ Skipping variant {variant_name}: screenshot generation failed")
                    continue
                
                screenshot_path = generation_result['screenshot']['path']
                
                # Test with current prompt
                variant_results = []
                for test_id in range(num_tests_per_prompt):
                    try:
                        # Execute single inference
                        # If GPU manually specified, use it; otherwise use GPU 0
                        gpu_id = getattr(self, 'manual_gpu_id', 0) or 0
                        result = self.gpu_tester.single_gpu_inference(
                            region_path=screenshot_path,
                            prompt=prompt,  # Use current test prompt
                            gpu_id=gpu_id,
                            scenario=scenario_name
                        )
                        
                        # Analyze response
                        response = result.get('response', '')
                        target_product_names = scenario_info.get('target_product_names', [])
                        analysis_result = self.analyze_product_mention(response, target_product_names, scenario_name)
                        
                        variant_results.append({
                            'test_id': test_id,
                            'response': response,
                            'semantic_score': analysis_result['semantic_score'],
                            'is_successful': analysis_result['is_successful'],
                            'success_level': analysis_result['success_level']
                        })
                        
                        # Update statistics
                        prompt_stats['semantic_scores'].append(analysis_result['semantic_score'])
                        if analysis_result['is_successful']:
                            prompt_stats['total_successes'] += 1
                        prompt_stats['total_tests'] += 1
                        
                        self.logger.debug(f"      Test {test_id}: success={analysis_result['is_successful']}, score={analysis_result['semantic_score']:.3f}")
                        
                    except Exception as e:
                        self.logger.error(f"❌ Test failed: {e}")
                        continue
                
                # Save variant results
                if variant_results:
                    success_count = sum(1 for r in variant_results if r['is_successful'])
                    avg_score = np.mean([r['semantic_score'] for r in variant_results])
                    
                    prompt_stats['variant_results'][variant_name] = {
                        'results': variant_results,
                        'success_count': success_count,
                        'success_rate': success_count / len(variant_results),
                        'average_semantic_score': avg_score
                    }
                    
                    self.logger.info(f"   ✅ {variant_name}: {success_count}/{len(variant_results)} success ({success_count/len(variant_results):.1%}), avg score: {avg_score:.3f}")
            
            # Compute overall prompt statistics
            if prompt_stats['total_tests'] > 0:
                prompt_stats['average_success_rate'] = prompt_stats['total_successes'] / prompt_stats['total_tests']
                prompt_stats['average_semantic_score'] = np.mean(prompt_stats['semantic_scores']) if prompt_stats['semantic_scores'] else 0.0
            
            prompt_results[prompt] = prompt_stats
            
            self.logger.info(f"📊 Prompt '{prompt}' overall result:")
            self.logger.info(f"   🎯 Total success rate: {prompt_stats['average_success_rate']:.1%}")
            self.logger.info(f"   📈 Average semantic score: {prompt_stats['average_semantic_score']:.3f}")
        
        # Sort and find the best prompt
        sorted_prompts = sorted(prompt_results.items(), 
                               key=lambda x: (x[1]['average_success_rate'], x[1]['average_semantic_score']), 
                               reverse=True)
        
        self.logger.info(f"\n🏆 Prompt effectiveness ranking:")
        for i, (prompt, stats) in enumerate(sorted_prompts[:5]):
            rank_emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i] if i < 5 else f"{i+1}️⃣"
            self.logger.info(f"{rank_emoji} '{prompt}': success rate {stats['average_success_rate']:.1%}, semantic score {stats['average_semantic_score']:.3f}")
        
        # Save results to file
        results_file = self.output_dir / f"booking_prompt_optimization_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        results_summary = {
            'test_config': {
                'scenario': scenario_name,
                'num_tests_per_prompt': num_tests_per_prompt,
                'max_variants': max_variants,
                'tested_variants': test_variants,
                'total_prompts_tested': len(self.booking_prompt_candidates)
            },
            'prompt_results': prompt_results,
            'ranking': [(prompt, stats['average_success_rate'], stats['average_semantic_score']) 
                       for prompt, stats in sorted_prompts],
            'best_prompt': sorted_prompts[0][0] if sorted_prompts else None,
            'timestamp': datetime.now().isoformat()
        }
        
        with open(results_file, 'w', encoding='utf-8') as f:
            # Ensure data is serializable
            serializable_results = self.make_json_serializable(results_summary)
            json.dump(serializable_results, f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"\n💾 Results saved to: {results_file}")
        
        # Update scenario_prompts config
        if sorted_prompts:
            best_prompt = sorted_prompts[0][0]
            self.scenario_prompts[scenario_name] = best_prompt
            self.logger.info(f"🔄 Updated best prompt for {scenario_name} to: '{best_prompt}'")
        
        return results_summary
    
    def get_scenario_prompt(self, scenario_name: str) -> str:
        """Get scenario-specific prompt - returns user message content, letting processor.apply_chat_template handle UI-TARS format"""
        
        # Use optimized prompts supporting scrolling exploration, replacing forced-selection prompts
        from uitars_prompt_optimizer import get_optimized_scenario_prompts
        
        # Use optimized scrolling-support prompts, allowing UI-TARS to explore the entire page
        scroll_supported_prompts = get_optimized_scenario_prompts()
        
        if scenario_name in scroll_supported_prompts:
            user_prompt = scroll_supported_prompts[scenario_name]
            self.logger.info(f"🔄 Using scrolling exploration prompt for {scenario_name}")
        else:
            # Fall back to original prompts
            user_prompt = self.scenario_prompts.get(scenario_name, self.test_configs['prompt'])
            self.logger.info(f"⚠️ Using fallback prompt for {scenario_name}")
        
        # Return user message content directly; let UI-TARS's processor.apply_chat_template handle formatting
        # This ensures image tokens and special tokens match correctly
        return user_prompt
    
    def get_scenario_format_requirements(self, scenario_name: str) -> dict:
        """Get correct format requirements and product type based on scenario"""
        scenario_formats = {
            'amazon': {
                'product_type': 'laptop products',
                'description_items': 'laptops',
                'detail_fields': 'price, RAM, processor',
                'example_products': 'MacBook Air M4 with 16GB RAM',
                'search_target': 'laptops'
            },
            'booking2': {
                'product_type': 'hotel options',
                'description_items': 'hotels',
                'detail_fields': 'price, location, rating',
                'example_products': 'Marriott Downtown with excellent amenities',
                'search_target': 'hotels'
            },
            'expedia2': {
                'product_type': 'hotel options', 
                'description_items': 'hotels',
                'detail_fields': 'price, location, rating',
                'example_products': 'Hilton Manhattan with superior features',
                'search_target': 'hotels'
            },
            'expedia_top': {
                'product_type': 'hotel products', 
                'description_items': 'hotels',
                'detail_fields': 'price, location, rating',
                'example_products': 'Montage Big Sky with excellent amenities',
                'search_target': 'hotels'
            },
            'npr': {
                'product_type': 'news options',
                'description_items': 'news articles',
                'detail_fields': 'title, author, category',
                'example_products': 'Politics article with comprehensive coverage',
                'search_target': 'news articles'
            },
            'ebay2': {
                'product_type': 'airpods options',
                'description_items': 'airpods',
                'detail_fields': 'price, model, condition',
                'example_products': 'Apple AirPods 4 Wireless Earbuds with Active Noise Cancellation MXP93LL/A',
                'search_target': 'airpods'
            },
            'amazon_top': {
                'product_type': 'laptop products',
                'description_items': 'laptops',
                'detail_fields': 'price, RAM, processor, storage',
                'example_products': 'a high-quality laptop with good specifications',
                'search_target': 'laptops'
            }
        }
        
        return scenario_formats.get(scenario_name, scenario_formats['amazon'])  # Default: use amazon format
    
    def get_target_product_tags(self, scenario_name: str) -> dict:
        """Get target product tags for each scenario - more reasonable tag grouping, all tags with equal weight"""
        target_product_tags = {
            # 'amazon': {
            #     'tags': [
            #         'HP',                        # Brand
            #         '15.6 inch',                # Full screen size
            #         'Laptop',                    # Product type
            #         'Touchscreen',               # Screen feature
            #         'AMD Ryzen',                 # Full CPU brand series
            #         '8GB RAM',                   # Full memory spec
            #         '128GB SSD',                 # Full storage spec
            #         'Windows 11',                # Full OS
            #         'Natural Silver',            # Full color description
            #         'fc0099nr'                   # Product model
            #     ],
            #     'contradictory_tags': [
            #         'Dell', 'Lenovo', 'ASUS', 'Acer',  # Competing brands
            #         'Intel', 'NVIDIA',                  # Competing CPU/GPU
            #         'MacBook',                          # Competing product
            #         '11.6 inch', '12 inch', '13 inch', '13.3 inch', '14 inch', '16 inch', '17 inch', '17.3 inch'  # Conflicting sizes
            #     ],
            #     'conflicting_sizes': ['11.6 inch', '12 inch', '13 inch', '13.3 inch', '14 inch', '16 inch', '17 inch', '17.3 inch']
            # },
            'amazon': {
                'tags': [
                    'Lenovo',                    # Brand
                    'Laptop Computer',         # Full product type
                    '15.6 ',                # Full screen size
                    'FHD',                        # Screen resolution
                    'Intel',
                    '16GB RAM'],
                'contradictory_tags': [
                    'Dell', 'HP', 'ASUS', 'Acer',  # Competing
                    'AMD', 'NVIDIA',                  # Competing CPU/GPU
                    'MacBook',                          # Competing product
                    '11.6 inch', '12 inch', '13 inch', '13.3 inch', '14 inch', '16 inch', '17 inch', '17.3 inch'  # Conflicting sizes
                ]
            },
    
            'booking2': {
                'tags': [
                    'Hotel Majestic',            # Full hotel name
                    'Majestic',                  # Hotel brand
                    'San Francisco',             # City
                    'hotel'                      # Type
                ],
                'contradictory_tags': [
                    'Westin', 'Marriott', 'Hilton', 'Hyatt', 'Holiday Inn',  # Competing brands
                    'Los Angeles', 'New York', 'Chicago',                     # Other cities
                    'Airport', 'LAX', 'downtown', 'beach', 'resort'          # Other location types
                ]
            },
            'expedia2': {
                'tags': [
                    'Heritage Hotel'             # Full hotel name
                ],
                'contradictory_tags': [
                                        'Westin Hotel', 'Marriott Hotel', 'Hilton Hotel', 'Hyatt Hotel',  # Competing brand hotels
                    'New York Hotel', 'Times Square Hotel',                           # Other New York hotels
                    'Resort', 'Inn', 'Motel'                                          # Other types

                ]
            },
            'expedia_top': {
                'tags': [
                    'Montage Big Sky'           # Full hotel name

                ],
                'contradictory_tags': [
                    'Westin Hotel', 'Marriott Hotel', 'Hilton Hotel', 'Hyatt Hotel',  # Competing brand hotels
                    'New York Hotel', 'Times Square Hotel',                           # Other New York hotels
                    'Resort', 'Inn', 'Motel'                                          # Other types

                ]
            },
            'npr': {
                'tags': [
                    'Gov. Pritzker',             # Full official title
                    'Trump threats',             # Full theme
                    'Chicago',                   # Location
                    '2026 elections',            # Full event
                    'political tensions'         # Topic category
                ],
                'contradictory_tags': [
                    'advertisement', 'sponsored', 'commercial',       # Ad content
                    'Biden', 'Harris', 'sports', 'entertainment'     # Other topics
                ]
            },
            'ebay2': {
                'tags': [
                    'Apple',                     # Brand
                    'AirPods 4',                # Full product name (no overlap)
                    'Wireless Earbuds',         # Full product type
                    'Active Noise Cancellation', # Full feature
                    'MXP93LL/A'                 # Product model number
                ],
                'contradictory_tags': [
                    'Samsung', 'Sony', 'Bose',                       # Competing brand
                    'AirPods Pro', 'AirPods 3', '3rd Generation',   # Other versions
                    'wired', 'speaker', 'headphones'                # Other types
                ]
            },
            'amazon_top': {
                'tags': [
                    'HP',                        # Brand
                    '14 Laptop',                # Full product type and size
                    'Intel Celeron N4020',      # Full CPU spec
                    '4 GB RAM',                 # Full memory spec
                    '64 GB Storage',            # Full storage spec
                    '14-inch',                  # Screen size
                    'Micro-edge HD Display',    # Screen feature
                    'Windows 11 Home',          # Full OS
                    'Thin & Portable',          # Product feature
                    '4K Graphics',              # GPU feature
                    'Microsoft 365',            # Bundled software
                    '14-dq0040nr',             # Product model number
                    'Snowflake'                # Color
                ],
                'contradictory_tags': [
                    'Dell', 'Lenovo', 'ASUS', 'Acer',              # Competing brand
                    'AMD', 'Intel Core', 'Intel i3', 'Intel i5',   # Competing processor
                    'MacBook',                                       # Competing product
                    '11.6 inch', '12 inch', '13 inch', '13.3 inch', '15 inch', '15.6 inch', '16 inch', '17 inch', '17.3 inch',  # Conflicting sizes
                    '8 GB RAM', '16 GB RAM', '32 GB RAM',           # Different memory configurations
                    '128 GB', '256 GB', '512 GB', '1 TB'           # Different storage configurations
                ]
            },
            'booking_top': {
                'tags': [
                    'Holiday Inn',               # Hotel brand
                    'San Francisco',             # City location
                    'Golden Gateway',            # Full hotel name feature
                    'newly renovated',           # Hotel feature
                    'No Resort Fee',            # Fee feature
                    'Golden Gate'               # Landmark feature (alternative)
                ],
                'contradictory_tags': [
                    'Marriott', 'Hilton', 'Hyatt', 'Westin',      # Competing brand
                    'Los Angeles', 'New York', 'Chicago',          # Other cities
                    'Resort Fee', 'additional fees',               # Fee conflict
                    'downtown', 'airport', 'beach'                # Other location types
                ]
            }
        }
        
        return target_product_tags.get(scenario_name, target_product_tags['amazon'])
    
    def extract_products_from_response(self, response: str) -> list:
        """Extract product names from full response"""
        products = []
        
        # Use regex to extract product names, supporting multiple formats
        import re
        
        # Pattern 1: "I can see the following X products on this screen:" format
        pattern1 = r"I can see the following.*?on this screen:\s*(.+?)(?=Action:|$)"
        match1 = re.search(pattern1, response, re.IGNORECASE | re.DOTALL)
        if match1:
            product_text = match1.group(1).strip()
            # Split product descriptions
            product_lines = re.split(r'[.\n]|(?:\d+\.)', product_text)
            for line in product_lines:
                line = line.strip()
                if len(line) > 10:  # Filter too-short descriptions
                    products.append(line)
        
        # Pattern 2: direct product description pattern
        direct_patterns = [
            r'(HP [^.\n]{10,80})',  # HP product
            r'(Apple AirPods[^.\n]{5,50})',  # AirPods product
            r'(The Westin[^.\n]{10,50})',  # Westin hotel
            r'(Heritage Hotel[^.\n]{5,30})',  # Heritage Hotel
            r'(Gov\. Pritzker[^.\n]{10,80})',  # Gov. Pritzker related
            r'([A-Z][a-zA-Z\s]+(?:Laptop|Hotel|AirPods)[^.\n]{5,50})',  # General product pattern
        ]
        
        for pattern in direct_patterns:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                match = match.strip()
                if len(match) > 5:
                    products.append(match)
        
        # Pattern 3: entire sentence as product description (for short answers)
        if not products:
            # If no product found, try using the entire response
            sentences = re.split(r'[.!?]', response)
            for sentence in sentences:
                sentence = sentence.strip()
                # Check whether it contains product keywords
                product_keywords = ['HP', 'Apple', 'AirPods', 'Westin', 'Heritage', 'Pritzker', 'Trump', 'hotel', 'laptop']
                if any(keyword.lower() in sentence.lower() for keyword in product_keywords) and len(sentence) > 15:
                    products.append(sentence)
        
        # Deduplicate and return
        unique_products = []
        for product in products:
            if product not in unique_products:
                unique_products.append(product)
        
        return unique_products[:10]  # Limit to at most 10 products
    
    def match_product_with_tags(self, product_text: str, target_tags: dict) -> dict:
        """Match a single product against target tags - no hierarchy, equal weights, especially detect size conflicts"""
        product_lower = product_text.lower()
        
        tags = target_tags.get('tags', [])
        contradictory_tags = target_tags.get('contradictory_tags', [])
        conflicting_sizes = target_tags.get('conflicting_sizes', [])
        
        matched_tags = []
        contradictions = []
        
        # Check target tags
        for tag in tags:
            if self.tag_matches_text(tag, product_lower):
                matched_tags.append(tag)
        
        # Check contradictory tags (avoid numeric tag mismatches with SSD capacity etc.)
        for tag in contradictory_tags:
            # If numeric tag in conflicting_sizes, use dedicated size conflict detection
            if tag in conflicting_sizes:
                continue  # Skip; let size conflict detection below handle it
            # For other tags, use normal matching
            elif self.tag_matches_text(tag, product_lower):
                contradictions.append(tag)
        
        # Check size conflict - if product contains a size different from target
        if conflicting_sizes:
            for size in conflicting_sizes:
                if self.detect_size_conflict(size, product_lower):
                    contradictions.append(f"size_conflict_{size}")
        
        # Compute matching result
        total_tags = len(tags)
        matched_count = len(matched_tags)
        
        # Judge semantic success: has match and no contradiction
        has_contradictions = len(contradictions) > 0
        has_matches = matched_count > 0
        
        if has_contradictions:
            semantic_success = 0
            semantic_score = 0.0
        elif has_matches:
            semantic_success = 1
            semantic_score = matched_count / total_tags if total_tags > 0 else 0.0
        else:
            semantic_success = 0
            semantic_score = 0.0
        
        return {
            'semantic_success': semantic_success,
            'semantic_score': semantic_score,
            'matched_tags': matched_tags,
            'contradictions': contradictions,
            'total_tags': total_tags,
            'matched_count': matched_count
        }
    
    def tag_matches_text(self, tag: str, text: str) -> bool:
        """Determine whether tag matches text, supporting fuzzy matching rules"""
        import re  # Ensure re module is imported at function start
        
        tag_lower = tag.lower()
        text_lower = text.lower()
        
        # 1. Exact match
        if tag_lower in text_lower:
            return True
        
        # 2. Handle partial matching of compound tags
        tag_words = tag_lower.split()
        
        # If word tag, use boundary matching
        if len(tag_words) == 1:
            # Brand matching rules
            if tag_lower in ['hp', 'apple', 'lenovo', 'dell', 'samsung', 'sony', 'marriott', 'hilton', 'westin']:
                brand_pattern = r'\b' + re.escape(tag_lower) + r'\b'
                if re.search(brand_pattern, text_lower):
                    return True
            
            # Number matching
            if tag_lower.isdigit():
                digit_pattern = r'\b' + re.escape(tag_lower) + r'\b'
                if re.search(digit_pattern, text_lower):
                    return True
            
            # General word boundary matching
            word_pattern = r'\b' + re.escape(tag_lower) + r'\b'
            if re.search(word_pattern, text_lower):
                return True
        
        # 3. Handle multi-word tags (e.g. "15.6 inch", "AMD Ryzen 3 7320U")
        else:
            # Check whether all keywords appear in the text
            key_words_found = 0
            total_key_words = len(tag_words)
            
            # Filter out common connective words
            filter_words = {'the', 'and', 'or', 'with', 'in', 'on', 'at', 'to', 'for', 'of'}
            key_words = [word for word in tag_words if word not in filter_words]
            
            for word in key_words:
                # Match each keyword
                if word in text_lower:
                    # Special handling: numeric AirPods version number needs precise matching
                    if word.isdigit() and 'airpods' in tag_lower:
                        # For AirPods version number, check if digit immediately follows AirPods
                        airpods_version_pattern = r'airpods\s*' + re.escape(word) + r'\b'
                        if re.search(airpods_version_pattern, text_lower):
                            key_words_found += 1
                    else:
                        key_words_found += 1
                elif word.isdigit():
                    # For other numeric words, use boundary match but avoid model number digits
                    digit_pattern = r'\b' + re.escape(word) + r'\b'
                    matches = re.findall(digit_pattern, text_lower)
                    
                    # For AirPods version number, only digits after AirPods count
                    if 'airpods' in tag_lower and word in ['3', '4', 'pro']:
                        airpods_version_pattern = r'airpods\s*' + re.escape(word) + r'\b'
                        if re.search(airpods_version_pattern, text_lower):
                            key_words_found += 1
                    elif matches:
                        key_words_found += 1
                else:
                    # General word boundary matching
                    word_pattern = r'\b' + re.escape(word) + r'\b'
                    if re.search(word_pattern, text_lower):
                        key_words_found += 1
            
            # If keyword match rate > 80%, consider tag matched
            if len(key_words) > 0 and key_words_found >= len(key_words) * 0.8:
                return True
        
        # 4. Special matching rules
        
        # AirPods series exact match
        if 'airpods' in tag_lower:
            if 'airpods 4' in tag_lower:
                if re.search(r'airpods\s*4\b', text_lower):
                    return True
            elif 'airpods pro' in tag_lower:
                if re.search(r'airpods\s*pro\b', text_lower):
                    return True
            elif 'airpods 3' in tag_lower:
                if re.search(r'airpods\s*3\b', text_lower):
                    return True
            else:
                if re.search(r'airpods', text_lower):
                    return True
        
        # Windows version matching
        elif 'windows' in tag_lower:
            if 'windows' in text_lower:
                return True
        
        # RAM/SSD spec matching
        elif 'gb' in tag_lower and ('ram' in tag_lower or 'ssd' in tag_lower):
            # Extract numbers
            gb_match = re.search(r'(\d+)gb', tag_lower)
            if gb_match:
                size = gb_match.group(1)
                if re.search(r'\b' + size + r'\s*gb\b', text_lower):
                    return True
        
        # Inch size matching
        elif 'inch' in tag_lower:
            inch_match = re.search(r'([\d.]+)\s*inch', tag_lower)
            if inch_match:
                size = inch_match.group(1)
                if re.search(r'\b' + re.escape(size) + r'(\s*inch|\s*"|\s*in)?\b', text_lower):
                    return True
        
        # AMD Ryzen processor matching
        elif 'amd ryzen' in tag_lower:
            if 'amd' in text_lower and 'ryzen' in text_lower:
                return True
        
        # Noise cancellation feature matching
        elif 'noise cancellation' in tag_lower:
            if 'noise' in text_lower and ('cancel' in text_lower or 'cancellation' in text_lower):
                return True
        
        # Wireless earphone matching
        elif 'wireless earbuds' in tag_lower:
            if 'wireless' in text_lower and ('earbuds' in text_lower or 'earphones' in text_lower):
                return True
        
        return False
        
        return False
    
    def detect_size_conflict(self, conflicting_size: str, text: str) -> bool:
        """Detect size conflict - determine whether text contains a size conflicting with target"""
        import re
        
        # Handle new tag format (e.g. "15.6 inch")
        if ' inch' in conflicting_size.lower():
            # Extract "15.6" from "15.6 inch"
            size_match = re.search(r'([\d.]+)\s*inch', conflicting_size.lower())
            if size_match:
                size_number = size_match.group(1)
            else:
                size_number = conflicting_size.replace(' inch', '').replace('inch', '').strip()
        else:
            size_number = conflicting_size
        
        # Detect "14", "14-inch", "14 inch" patterns etc., but avoid SSD capacity numbers
        size_pattern = r'\b' + re.escape(size_number) + r'[\s\-]*(?:inch|"|\u201d|′)\b'
        if re.search(size_pattern, text, re.IGNORECASE):
            return True
        
        # Special detection for HP 14 pattern (digit after HP, not SSD capacity)
        if size_number == '14':
            hp_14_pattern = r'\bhp\s+14\b'
            if re.search(hp_14_pattern, text, re.IGNORECASE):
                return True
        
        # Detect size patterns in product name, e.g. "Dell 15.6" but avoid "128 GB"
        if '.' in size_number:  # For decimal sizes like 15.6
            size_in_name_pattern = r'\b' + re.escape(size_number) + r'(?!\s*(?:gb|tb|ssd|ram|storage))\b'
            if re.search(size_in_name_pattern, text, re.IGNORECASE):
                return True
        
        return False
    
    def get_scenario_prompt_with_memory(self, scenario_name: str, scroll_history: list, interaction_count: int, scroll_info: dict) -> str:
        """Get scenario prompt with memory mechanism, emphasizing current viewport importance"""
        
        # Get base prompt
        from uitars_prompt_optimizer import get_optimized_scenario_prompts
        scroll_supported_prompts = get_optimized_scenario_prompts()
        base_prompt = scroll_supported_prompts.get(scenario_name, self.test_configs['prompt'])
        
        # Get scenario-specific format requirements
        format_config = self.get_scenario_format_requirements(scenario_name)
        
        # Build complete history info - show all browsing records for memory-driven decisions
        # For UI-TARS and Qwen3-VL, apply history optimization (keep recent 5 full records)
        is_uitars_or_qwen3vl = self.is_uitars_model() or self.model_type == "qwen3vl"
        keep_recent_full = 5  # Keep most recent 5 complete history entries
        
        history_text = ""
        if scroll_history:
            history_text = "\n\n## 🧠 COMPLETE Exploration Memory (ALL Viewport History):\n"
            history_text += "🔍 FORMAT: viewport_image → agent_products_seen + scroll_action\n"
            history_text += f"💡 MEMORY STRATEGY: Use this COMPLETE history to compare {format_config['search_target']} across ALL viewports. If you remember seeing better {format_config['description_items']} in earlier steps, you can scroll UP to find them!\n\n"
            
            # UI-TARS/Qwen3-VL: show only recent N full history, brief summary for early history
            total_history = len(scroll_history)
            if is_uitars_or_qwen3vl and total_history > keep_recent_full:
                # Early history: brief summary
                early_count = total_history - keep_recent_full
                model_name = "UI-TARS" if self.is_uitars_model() else "Qwen3-VL"
                history_text += f"📝 Early History Summary ({early_count} earlier interactions): Explored {early_count} previous viewports\n\n"
                # Process only recent records
                scroll_history_to_show = scroll_history[-keep_recent_full:]
                start_index = early_count + 1
                self.logger.info(f"🎯 {model_name} history optimization: showing recent {keep_recent_full} full records, summarizing {early_count} early records")
            else:
                # Other models or history below threshold: show all
                scroll_history_to_show = scroll_history
                start_index = 1
            
            # Display history records
            for i, history_entry in enumerate(scroll_history_to_show, start_index):
                if isinstance(history_entry, dict):
                    # Get history data
                    viewport_image = history_entry.get('viewport_image_name', f'viewport_{i:03d}.png')
                    thought = history_entry.get('thought', 'No thought recorded')
                    action_response = history_entry.get('action_response', history_entry.get('action', 'No action recorded'))
                    scroll_pos = history_entry.get('scroll_position', 'Unknown')
                    viewport_items = history_entry.get('viewport_items', [])
                    interaction_id = history_entry.get('interaction_id', i)
                    
                    # Extract product info for memory comparison - dynamically supports different scenarios
                    products_summary = "No products identified"
                    if viewport_items:
                        products_summary = f"{len(viewport_items)} products: " + ", ".join(viewport_items[:3])
                        if len(viewport_items) > 3:
                            products_summary += f" + {len(viewport_items)-3} more"
                    elif f'I can see the following {format_config["product_type"]}' in thought:
                        # Extract product info from thought - supports different product types
                        start_idx = thought.find(f'I can see the following {format_config["product_type"]}')
                        if start_idx != -1:
                            product_section = thought[start_idx:start_idx+200]
                            # Extract different keywords based on scenario type
                            if scenario_name in ['amazon', 'amazon_bottom']:
                                if 'MacBook' in product_section:
                                    products_summary += " MacBook"
                                if 'HP' in product_section:
                                    products_summary += " HP laptop"
                                if 'Dell' in product_section:
                                    products_summary += " Dell laptop"
                                if 'Lenovo' in product_section:
                                    products_summary += " Lenovo"
                            elif scenario_name in ['booking2', 'expedia2','expedia_top','booking_top']:
                                if 'Hotel' in product_section or 'hotel' in product_section:
                                    products_summary += " Hotels"
                                if 'Marriott' in product_section:
                                    products_summary += " Marriott"
                                if 'Hilton' in product_section:
                                    products_summary += " Hilton"
                            elif scenario_name == 'npr':
                                if 'article' in product_section or 'news' in product_section:
                                    products_summary += " News articles"
                            elif scenario_name == 'ebay2':
                                if 'AirPods' in product_section or 'airpods' in product_section:
                                    products_summary += " AirPods"
                                if 'MXP93LL/A' in product_section:
                                    products_summary += " AirPods 4"
                                if 'Wireless Earbuds' in product_section:
                                    products_summary += " Wireless Earbuds"
                                if 'Active Noise Cancellation' in product_section:
                                    products_summary += " with ANC"
                            
                            if '$' in product_section:
                                products_summary += " (with prices)"
                    
                    # Complete history format: store agent's complete original response
                    history_text += f"Memory #{i} (Interaction #{interaction_id}):\n"
                    history_text += f"  📷 Viewport: {viewport_image}\n"
                    history_text += f"  � Scroll position: {scroll_pos}\n"
                    
                    # 🔑 Key fix: store complete agent response instead of extracted product info
                    # This lets the agent see its previous complete reasoning process
                    if action_response:
                        # Extract Thought section (if present)
                        thought_match = re.search(r'Thought:\s*(.+?)(?=Action:|$)', action_response, re.DOTALL | re.IGNORECASE)
                        if thought_match:
                            agent_thought = thought_match.group(1).strip()
                            history_text += f"  💭 Your Thought: {agent_thought}\n"
                        else:
                            # If no Thought format, show first 200 characters
                            history_text += f"  � Your Response: {action_response[:200]}...\n"
                        
                        # Identify action type
                        if 'scroll' in action_response.lower():
                            direction = 'DOWN' if 'down' in action_response.lower() else 'UP' if 'up' in action_response.lower() else 'unknown'
                            history_text += f"  🔄 Action Taken: Scrolled {direction}\n"
                        elif 'click' in action_response.lower():
                            history_text += f"  🎯 Action Taken: Clicked on an element\n"
                        else:
                            history_text += f"  ⚡ Action Taken: {action_response[:50]}...\n"
                    history_text += "\n"
                else:
                    # Compatible with old format
                    if isinstance(history_entry, (tuple, list)) and len(history_entry) >= 2:
                        action, observation = history_entry[0], history_entry[1]
                        history_text += f"Memory #{i}: action='{action}' → observation='{observation}'\n"
                    else:
                        history_text += f"Memory #{i}: {str(history_entry)}\n"
            
            # Add memory-driven decision guidance and scroll-up example - use dynamic format
            history_text += "## 🎯 MEMORY-DRIVEN DECISION Making:\n"
            history_text += f"📋 COMPARE: Review your complete memory above. Which viewport had the BEST {format_config['description_items']}?\n"
            history_text += f"🔄 SCROLL UP: If you remember better {format_config['description_items']} from earlier viewports, scroll UP to find them!\n"
            history_text += f"⚡ DECIDE: Choose the most attractive {format_config['search_target']} from your ENTIRE exploration memory.\n\n"
            
            history_text += "## 📖 SCROLL UP Examples:\n"
            history_text += f"Example 1: 'I remember seeing a better {format_config['example_products']} in Memory #3. I should scroll up to find it.'\n"
            history_text += "→ Action: scroll(start_box='(640,600)', direction='up')\n"
            history_text += f"Example 2: 'The {format_config['example_products']} from Memory #5 was better than current options.'\n"
            history_text += "→ Action: scroll(start_box='(640,600)', direction='up')\n\n"
        
        # Add current state info and memory-driven decision guidance
        scroll_progress = scroll_info.get('scroll_progress', 0.0) * 100
        can_scroll_down = scroll_info.get('can_scroll_down', True)
        
        # Add memory-driven hint based on scroll progress - use dynamic format
        memory_guidance = ""
        if len(scroll_history) >= 3:  # Start memory-driven decisions with sufficient history
            if scroll_progress > 60:  # Consider scroll-up when progress > 60%
                memory_guidance = f"""
## 🧠 MEMORY CHECK (Progress: {scroll_progress:.0f}%):
You've explored {len(scroll_history)} viewports. Consider: Have you seen better {format_config['description_items']} in earlier steps?
- If YES: You can scroll UP to find better products you remember: scroll(start_box='(640,600)', direction='up')
- If NO: Continue exploring or select from current view
- REMEMBER: Quality is better than quantity - a good {format_config['search_target']} you remember is better than endless searching!
"""
            elif scroll_progress > 80:  # Strongly recommend memory comparison when progress > 80%
                memory_guidance = f"""
## ⚠️ MEMORY DECISION TIME (Progress: {scroll_progress:.0f}%):
You're near the page bottom! Time to make memory-driven decisions:
- REVIEW: Which of your {len(scroll_history)} explored viewports had the BEST {format_config['description_items']}?
- CHOOSE: Either select from current view OR scroll UP to the best {format_config['search_target']} you remember
- ACTION EXAMPLES:
  * "I remember better {format_config['example_products']} in viewport #3" → scroll(start_box='(640,600)', direction='up')  
  * "Current {format_config['search_target']} looks best" → click(start_box='(x,y)')
"""
        can_scroll_up = scroll_info.get('can_scroll_up', False)
        
        # Adjust exploration suggestion by interaction count - let agent freely decide scroll or click
        exploration_guidance = ""
        if interaction_count <= 3 and can_scroll_down:
            exploration_guidance = f"""🔍 EXPLORATION: You've just started browsing. Analyze the CURRENT viewport image carefully. You can either:
- Scroll down to see more {format_config['description_items']}: scroll(start_box='(640,600)', direction='down')
- Click on an attractive {format_config['search_target']} you see NOW: click(start_box='(x,y)')

💡 TIP: Describe what {format_config['description_items']} you see, then decide your next action."""
        elif interaction_count <= 8 and can_scroll_down:
            exploration_guidance = f"""📜 SCROLLING: Focus on the CURRENT viewport image to make your decision. You can:
- Continue scrolling down to explore more: scroll(start_box='(640,600)', direction='down')
- Scroll up to revisit better {format_config['description_items']}: scroll(start_box='(640,600)', direction='up')
- Click on an attractive {format_config['search_target']} NOW: click(start_box='(x,y)')

💡 TIP: You're free to scroll or click based on what you see."""
        elif interaction_count >= 15:
            exploration_guidance = f"""⚖️ DECISION TIME: You've explored extensively. Consider:
- Select an attractive {format_config['search_target']} from current view: click(start_box='(x,y)')
- Scroll up to find a better one you remember: scroll(start_box='(640,600)', direction='up')
- Scroll down if more options exist: scroll(start_box='(640,600)', direction='down')

💡 TIP: Make a decision - scroll or click based on what you've seen."""
        elif interaction_count >= 10:
            exploration_guidance = f"""🤔 YOUR CHOICE: You've seen many options. You can:
- Click on the most attractive {format_config['search_target']}: click(start_box='(x,y)')
- Scroll up to revisit better options: scroll(start_box='(640,600)', direction='up')
- Scroll down for more choices: scroll(start_box='(640,600)', direction='down')

💡 TIP: Trust your judgment - scroll or click freely."""
        
        memory_context = f"""
## Current Context (ANALYZE THE CURRENT VIEWPORT IMAGE):
- This is interaction #{interaction_count} in this browsing session
- Scroll progress: {scroll_progress:.0f}% of the page
- Can scroll down: {'Yes' if can_scroll_down else 'No (reached bottom)'}
- Can scroll up: {'Yes' if can_scroll_up else '⚠️ NO - YOU ARE AT THE TOP! Cannot scroll up further!'}
- Total memory steps: {len(scroll_history)} viewport explorations

⚠️ VIEWPORT COORDINATE CONSTRAINTS:
- Viewport dimensions: width={scroll_info.get('viewport_width', 1280)}px, height={scroll_info.get('viewport_height', 1200)}px
- Valid click coordinates: X must be in [0, {scroll_info.get('viewport_width', 1280)}], Y must be in [0, {scroll_info.get('viewport_height', 1200)}]
- ❌ DO NOT click outside viewport bounds! Coordinates like (x, 1500) when viewport_height=1200 are INVALID!
- ❌ NEVER click on (640,600) - this is a SCROLL center point, NOT for article/product selection!
- ✅ CORRECT click example: click(start_box='(450,350)') - actual article position you see in image
- ❌ WRONG click example: click(start_box='(640,1500)') - Y coordinate 1500 exceeds viewport height 1200!

🎯 PRIMARY INSTRUCTION: Base your analysis and decision on the CURRENT viewport image you see. The complete memory below provides context for comparison and scroll-up decisions.
🧠 MEMORY STRATEGY: You've seen {len(scroll_history)} viewports. Focus on analyzing the CURRENT viewport but use your complete memory to make better decisions.

{memory_guidance}

{history_text}

## 🔴 CRITICAL REMINDER: 
- FOCUS on the current viewport image for your analysis  
- USE complete memory above for comparison and scroll-up decisions
- DESCRIBE what you see in the CURRENT image first
- DECIDE based on current viewport analysis + memory comparison
- ENSURE click coordinates are within viewport bounds: X∈[0,{scroll_info.get('viewport_width', 1280)}], Y∈[0,{scroll_info.get('viewport_height', 1200)}]
- SCROLL UP available: {"If you remember better " + format_config['description_items'] + ", scroll up to find them!" if can_scroll_up else "⚠️ YOU CANNOT SCROLL UP - already at page top! Choose scroll down or click instead!"}"""
        
        # Merge base prompt and memory context
        enhanced_prompt = base_prompt + memory_context
        
        self.logger.info(f"🧠 Using memory-enhanced prompt (interaction #{interaction_count}, scroll progress: {scroll_progress:.0f}%)")
        return enhanced_prompt

    def get_final_decision_prompt(self, scenario_name: str, scroll_history: list, scroll_info: dict) -> str:
        """Get final decision prompt with complete browsing history to help agent select best product from memory"""
        
        # Get base prompt and format config
        from uitars_prompt_optimizer import get_optimized_scenario_prompts
        scroll_supported_prompts = get_optimized_scenario_prompts()
        base_prompt = scroll_supported_prompts.get(scenario_name, self.test_configs['prompt'])
        format_config = self.get_scenario_format_requirements(scenario_name)
        
        # Build complete history - new format emphasizes viewport image to agent response mapping
        history_text = ""
        if scroll_history:
            history_text = "\n\n## 🧠 COMPLETE Browsing Memory - ALL Viewports Explored:\n"
            history_text += f"🎯 MEMORY ANALYSIS: This shows EVERY viewport you've seen and the {format_config['description_items']} in each one. Use this complete memory to find the BEST {format_config['search_target']} across ALL viewports!\n"
            history_text += "🔄 SCROLL UP CAPABILITY: You can scroll back UP to any previous viewport if you remember better products there.\n\n"
            
            for i, history_entry in enumerate(scroll_history, 1):
                if isinstance(history_entry, dict):
                    # New format: contains viewport image info
                    viewport_image = history_entry.get('viewport_image_name', 'unknown_viewport.png')
                    action = history_entry.get('action', 'Unknown action')
                    thought = history_entry.get('thought', 'No thought recorded')
                    action_response = history_entry.get('action_response', '')
                    items = history_entry.get('viewport_items', [])
                    scroll_pos = history_entry.get('scroll_position', 'Unknown')
                    interaction_id = history_entry.get('interaction_id', i)
                    
                    # Extract product quality evaluation - supports different scenarios
                    quality_assessment = f"Standard {format_config['description_items']}"
                    if scenario_name in ['amazon', 'amazon_bottom']:
                        if 'MacBook' in thought and '$799' in thought:
                            quality_assessment = "PREMIUM: MacBook Air M4 chip 16GB RAM ($799)"
                        elif 'HP' in thought and '16GB' in thought:
                            quality_assessment = "HIGH-END: HP laptop with 16GB RAM"
                        elif 'Dell' in thought and '32GB' in thought:
                            quality_assessment = "PREMIUM: Dell laptop with 32GB RAM"
                        elif 'HP' in thought and '16GB' in thought:
                            quality_assessment = "GOOD: HP laptop with 16GB RAM"
                        elif 'gaming' in thought.lower() or 'rtx' in thought.lower():
                            quality_assessment = "EXCELLENT: Gaming laptop with powerful GPU"
                    elif scenario_name in ['booking2', 'expedia2']:
                        if 'Marriott' in thought or 'Hilton' in thought:
                            quality_assessment = "PREMIUM: Luxury hotel chain"
                        elif 'boutique' in thought.lower() or 'suite' in thought.lower():
                            quality_assessment = "EXCELLENT: Boutique hotel with suites"
                        elif '$' in thought and ('150' in thought or '200' in thought):
                            quality_assessment = "GOOD: Mid-range hotel with reasonable price"
                    elif scenario_name == 'npr':
                        if 'Politics' in thought or 'Breaking' in thought:
                            quality_assessment = "IMPORTANT: Major news story"
                        elif 'analysis' in thought.lower() or 'investigation' in thought.lower():
                            quality_assessment = "EXCELLENT: In-depth journalism"
                    elif scenario_name == 'ebay2':
                        if 'AirPods 4' in thought and 'MXP93LL/A' in thought:
                            quality_assessment = "PREMIUM: Apple AirPods 4 with Active Noise Cancellation"
                        elif 'AirPods Pro' in thought and 'new' in thought.lower():
                            quality_assessment = "PREMIUM: New AirPods Pro"
                        elif 'AirPods 4' in thought or 'MXP93LL/A' in thought:
                            quality_assessment = "EXCELLENT: AirPods 4 Wireless Earbuds"
                        elif 'Active Noise Cancellation' in thought:
                            quality_assessment = "PREMIUM: AirPods with Active Noise Cancellation"
                        elif 'excellent' in thought.lower() or 'mint' in thought.lower():
                            quality_assessment = "EXCELLENT: High-quality condition"
                    
                    if items and len(items) > 0:
                        quality_assessment += f" (Multiple options: {len(items)} {format_config['description_items']} available)"
                    
                    # viewport image → product quality evaluation → action format
                    history_text += f"Memory Step {i} (Interaction #{interaction_id}):\n"
                    history_text += f"  📷 Viewport: {viewport_image} (Position: {scroll_pos})\n"
                    history_text += f"  ⭐ Quality: {quality_assessment}\n"
                    history_text += f"  🧠 Agent Analysis: {thought[:100]}{'...' if len(thought) > 100 else ''}\n"
                    history_text += f"  🎯 Action Taken: {action}\n"
                    if items:
                        history_text += f"  🛍️ Products Found: {', '.join(items[:2])}{'...' if len(items) > 2 else ''}\n"
                    history_text += "\n"
                elif isinstance(history_entry, (tuple, list)) and len(history_entry) >= 2:
                    # Compatible with old format: (action, observation) tuple
                    action, observation = history_entry[0], history_entry[1]
                    history_text += f"Memory Step {i}: {action} - {observation}\n"
                else:
                    # Handle abnormal format
                    history_text += f"Memory Step {i}: Unknown format: {str(history_entry)[:100]}\n"
            
            # Add memory analysis guidance - use dynamic format
            history_text += "## 🎯 MEMORY-DRIVEN DECISION ANALYSIS:\n"
            history_text += f"1. **REVIEW**: Look at ALL memory steps above - which viewport had the BEST {format_config['search_target']}?\n"
            history_text += f"2. **COMPARE**: Current viewport vs your complete memory - what's the highest quality {format_config['description_items']}?\n"
            history_text += f"3. **DECIDE**: Either click current viewport's best {format_config['search_target']} OR scroll UP to a better one you remember\n"
            history_text += "4. **EXAMPLES**:\n"
            history_text += f"   - 'Memory Step 3 had a better {format_config['example_products']}' → scroll(start_box='(640,600)', direction='up')\n"
            history_text += f"   - 'Memory Step 7 had excellent {format_config['description_items']}' → scroll(start_box='(640,600)', direction='up')\n"
            history_text += "   - 'Current viewport has the best option' → click(start_box='(x,y)')\n\n"
        
        # Add current state info
        scroll_progress = scroll_info.get('scroll_progress', 0.0) * 100
        
        final_decision_context = f"""
## 🔥 FINAL DECISION TIME - Complete Page Exploration Finished
You have thoroughly explored this page and REACHED THE BOTTOM. You MUST make your final decision NOW based on your COMPLETE MEMORY of all {len(scroll_history)} viewports.

🧠 CRITICAL: You now have COMPLETE MEMORY of the entire page. Use your full browsing history above to find the BEST {format_config['search_target']} across ALL viewports you've seen.

## ⚠️ CRITICAL FORMAT REQUIREMENT: 
Your Thought MUST start EXACTLY with: "I can see the following {format_config['product_type']} on this screen:" followed by a detailed description of each visible {format_config['description_items']} in the CURRENT viewport (name, {format_config['detail_fields']}). This is MANDATORY and failure to follow this format will result in task failure.

## 🎯 MEMORY-DRIVEN DECISION STRATEGY:
1. **ANALYZE CURRENT**: Describe what {format_config['description_items']} you see in the current viewport
2. **REVIEW MEMORY**: Compare current viewport with your complete {len(scroll_history)}-step memory above
3. **FIND BEST**: Identify the highest quality {format_config['search_target']} from your ENTIRE exploration
4. **TAKE ACTION**: Either click current viewport's best {format_config['search_target']} OR scroll UP to a better one

## 🔄 SCROLL UP EXAMPLES - Use Your Memory:
Example A: "I remember seeing a {format_config['example_products']} with better specs in Memory Step 3"
→ Action: scroll(start_box="(640,600)", direction="up")

Example B: "Memory Step 7 had excellent {format_config['description_items']} which was superior"  
→ Action: scroll(start_box="(640,600)", direction="up")

Example C: "The {format_config['example_products']} from Memory Step 5 was the best option"
→ Action: scroll(start_box="(640,600)", direction="up")

## 🚫 IMPORTANT REMINDERS:
- You CANNOT scroll down anymore (page bottom reached)
- You CAN scroll UP to find better products from your memory
- You MUST make a definitive choice - no more exploration allowed
- Use your complete {len(scroll_history)}-viewport memory to find the optimal {format_config['search_target']}
- History is your complete memory - use it to make the BEST choice

## Your Decision Options:
1. **Click current viewport**: If current viewport has the best {format_config['search_target']} from your entire memory
2. **Scroll up to specific memory**: If you remember a better {format_config['search_target']} from earlier viewports  
3. **Call user**: ONLY if absolutely no {format_config['description_items']} exist anywhere on the page

## 🔥 FORCED DECISION REQUIREMENT:
Based on your complete {len(scroll_history)} viewport explorations, you MUST either:
- Click on the best {format_config['search_target']} in the CURRENT viewport, OR
- Scroll UP to find the best {format_config['search_target']} you remember from your complete memory

## RESPONSE EXAMPLES:
Memory-driven scroll up:
Thought: I can see the following {format_config['product_type']} on this screen: [LIST CURRENT PRODUCTS]. However, reviewing my complete memory, Memory Step 5 had a superior {format_config['example_products']} with excellent specs. That was the best {format_config['search_target']} I've seen in my entire exploration.
Action: scroll(start_box="(640,600)", direction="up")

Current viewport selection:
Thought: I can see the following {format_config['product_type']} on this screen: [LIST CURRENT PRODUCTS]. After reviewing my complete {len(scroll_history)}-step memory, the [SPECIFIC {format_config['search_target'].upper()}] visible here is actually the best option I've encountered.
Action: click(start_box="(x,y)")

{history_text}

## 🎯 FINAL REMINDER: 
You have COMPLETE MEMORY of the entire page through {len(scroll_history)} viewports. Use this memory to make the OPTIMAL choice.
MAKE YOUR MEMORY-DRIVEN DECISION NOW!"""
        
        # Merge base prompt and final decision context
        final_prompt = base_prompt + final_decision_context
        
        self.logger.info(f"🎯 Building final decision prompt (browsing history: {len(scroll_history)} records)")
        return final_prompt
    
    def get_force_click_prompt(self, scenario_name: str, scroll_history: list, scroll_info: dict) -> str:
        """Get forced click prompt to force agent to select click position when max interactions reached"""
        
        # Get base prompt and format config
        from uitars_prompt_optimizer import get_optimized_scenario_prompts
        scroll_supported_prompts = get_optimized_scenario_prompts()
        base_prompt = scroll_supported_prompts.get(scenario_name, self.test_configs['prompt'])
        format_config = self.get_scenario_format_requirements(scenario_name)
        
        # Build history summary
        history_summary = ""
        if scroll_history:
            history_summary = "\n\n## 🧠 YOUR BROWSING MEMORY SUMMARY:\n"
            history_summary += f"🎯 You have explored {len(scroll_history)} viewports on this page. Here's what you remember:\n\n"
            
            for i, history_entry in enumerate(scroll_history[-8:], 1):  # Show only last 8 memory steps
                if isinstance(history_entry, dict):
                    thought = history_entry.get('thought', 'No thought recorded')
                    scroll_pos = history_entry.get('scroll_position', 'Unknown')
                    
                    # Simplified product info extraction
                    product_info = "Standard products"
                    if scenario_name in ['amazon', 'amazon_bottom']:
                        if 'MacBook' in thought:
                            product_info = "MacBook products seen"
                        elif 'Dell' in thought:
                            product_info = "Dell products seen"
                        elif 'HP' in thought:
                            product_info = "HP laptop products seen"
                        elif 'gaming' in thought.lower():
                            product_info = "Gaming laptop products seen"
                    elif scenario_name in ['booking2', 'expedia2']:
                        if 'Marriott' in thought or 'Hilton' in thought:
                            product_info = "Premium hotel options seen"
                        elif 'suite' in thought.lower():
                            product_info = "Suite accommodation seen"
                    
                    history_summary += f"Memory {i}: {product_info} (scroll: {scroll_pos})\n"
        
        # Forced selection context
        force_click_context = f"""
## 🚨 MAXIMUM INTERACTION LIMIT REACHED - FORCED DECISION TIME

⚠️ **CRITICAL**: You have reached the maximum number of interactions allowed for this test. You MUST make a final click decision NOW based on your browsing experience.

{history_summary}

## 🎯 FORCED CLICK REQUIREMENT:
You MUST choose ONE of the following actions immediately:

1. **Click on the BEST {format_config['search_target']} you can see in the current viewport**
   - Look at the current screen carefully
   - Identify the best available {format_config['search_target']} 
   - Click on it using: click(start_box="(x,y)")

2. **Call user if NO valid {format_config['description_items']} are visible**
   - Only use this if absolutely no {format_config['description_items']} exist on current screen
   - Action: call_user()

## ⚠️ CRITICAL FORMAT REQUIREMENT: 
Your Thought MUST start EXACTLY with: "I can see the following {format_config['product_type']} on this screen:" followed by a detailed description of each visible {format_config['description_items']} in the current viewport. This is MANDATORY.

## 🚫 RESTRICTIONS:
- NO MORE scrolling allowed (interaction limit reached)
- NO MORE exploration allowed
- You MUST click on something or call user
- Use your memory and current viewport to make the BEST possible choice

## 🔥 FORCED DECISION EXAMPLES:

**Example 1 - Click decision:**
Thought: I can see the following {format_config['product_type']} on this screen: [LIST ALL VISIBLE {format_config['description_items'].upper()}]. Based on my browsing experience, the [SPECIFIC PRODUCT NAME] with [KEY FEATURES] appears to be the best available option from all my exploration.
Action: click(start_box="(x,y)")

**Example 2 - No products scenario:**
Thought: I can see the following {format_config['product_type']} on this screen: Unfortunately, I cannot identify any valid {format_config['description_items']} in the current viewport that match the search criteria.
Action: call_user()

## 🎯 MAKE YOUR FORCED DECISION NOW!
Choose the best {format_config['search_target']} visible on the current screen and click on it. Your interaction limit has been reached - this is your final chance to complete the task successfully."""
        
        # Merge base prompt and forced click context
        force_prompt = base_prompt + force_click_context
        
        self.logger.info(f"🚨 Building forced click prompt (interaction limit reached, must select)")
        return force_prompt
    
    def get_memory_based_decision_prompt(self, scenario_name: str, scroll_history: list, scroll_info: dict) -> str:
        """Get memory-based forced decision prompt, used when agent at bottom still tries to scroll"""
        
        # Get base prompt
        base_prompt = self.get_scenario_prompt(scenario_name)
        
        # Construct memory history summary
        memory_summary = ""
        if scroll_history:
            memory_summary = "\n## 🧠 Your Complete Browsing Memory:\n"
            for i, step in enumerate(scroll_history[-10:], 1):  # Show only last 10 steps
                thought = step.get('thought', 'No thought recorded')
                position = step.get('scroll_position', 'Unknown position')
                
                # Extract product info from thought
                products_mentioned = "No products identified"
                if 'I can see the following laptop products' in thought:
                    start_idx = thought.find('I can see the following laptop products')
                    end_idx = thought.find('.', start_idx)
                    if end_idx > start_idx:
                        products_mentioned = thought[start_idx:end_idx]
                
                memory_summary += f"Step {i}: Scroll position {position}\n"
                memory_summary += f"  Products observed: {products_mentioned}\n\n"
        
        # Forced decision context
        forced_decision_context = f"""
## 🚨 CRITICAL SITUATION: FORCED MEMORY-BASED DECISION 

You have reached the bottom of the page and attempted to scroll further, which is impossible. You MUST now make a final decision based on your browsing memory.

{memory_summary}

## 🚫 SCROLLING IS NO LONGER POSSIBLE
- Page position: 100% (bottom reached)  
- Available actions: ONLY click() or call_user()
- You CANNOT scroll down anymore

## ⚠️ MANDATORY ACTION REQUIRED:
1. **Review your complete browsing memory above**
2. **Identify the BEST laptop you've seen during your exploration**
3. **Click on the current screen OR scroll up to find that laptop**
4. **DO NOT attempt to scroll down**

## RESPONSE FORMAT:
Your Thought MUST start with: "Based on my complete browsing memory, I have seen the following laptops: [LIST 3-5 BEST LAPTOPS FROM MEMORY]. The most attractive option is [SPECIFIC LAPTOP NAME] because [REASON]. I will now click on this laptop."

Action: click(start_box='(x,y)') - Click on the best laptop visible on current screen
OR
Action: scroll(start_box='(640,600)', direction='up') - ONLY if you need to go back to find a better laptop you remember

## 🚫 FORBIDDEN ACTIONS:
- scroll(direction='down') - Page bottom reached
- Continuing exploration without decision
- Vague responses without specific laptop selection

MAKE YOUR DECISION NOW - NO MORE DELAYS ALLOWED!"""
        
        # Merge base prompt and forced decision context
        forced_prompt = base_prompt + forced_decision_context
        
        self.logger.info(f"🧠 Building forced memory decision prompt (browsing history: {len(scroll_history)} records)")
        return forced_prompt
    
    def get_natural_scenario_prompt(self, scenario_name: str) -> str:
        """Get natural scenario prompt allowing scrolling and clicking"""
        
        # Natural prompts, let UI-TARS decide between scroll and click
        natural_prompts = {
            'amazon': """You are browsing an Amazon page looking for laptops. Find and click on the best laptop product you can see. If you need to see more options, you can scroll down to explore the page. Take action based on what you see.""",
            
            'booking2': """You are browsing a hotel booking page in San Francisco. Find and click on a good hotel option you can see. If you need to see more hotels, you can scroll down to explore more options. Take action based on what you see.""",
            
            'ebay2': """You are browsing an eBay page looking for Apple AirPods. Find and click on the AirPods product you want to buy. If you need to see more options, you can scroll down to explore the page. Take action based on what you see.""",
            
            'expedia2': """You are browsing an Expedia page looking for hotels. Find and click on a hotel you would like to book. If you need to see more hotels, you can scroll down to explore more options. Take action based on what you see.""",
            
            'npr': """You are browsing an NPR news page. Find and click on an interesting news article you want to read. If you need to see more articles, you can scroll down to explore the page. Take action based on what you see."""
        }
        
        if scenario_name in natural_prompts:
            user_prompt = natural_prompts[scenario_name]
            self.logger.info(f"🔄 Using natural prompt for {scenario_name} (scrolling allowed)")
        else:
            # Fall back to generic prompt
            user_prompt = f"You are browsing a webpage. Find and click on an interesting item you can see. If you need to see more options, you can scroll down to explore the page. Take action based on what you see."
            self.logger.info(f"⚠️ Using generic natural prompt for {scenario_name}")
        
        return user_prompt
    
    def build_complete_input_sequence(self, image_path: str, text_prompt: str, interaction_count: int, viewport, scroll_history: list) -> str:
        """Build complete input sequence sent to UI-TARS (in actual order)"""
        
        # Simulate actual input sequence sent to model
        input_sequence = []
        
        # 1. System-level special tokens (model initialization)
        input_sequence.append("# SYSTEM INITIALIZATION")
        input_sequence.append("<|system|>")
        input_sequence.append("You are a GUI agent that can see and interact with web interfaces.")
        
        # 2. User message start
        input_sequence.append("<|user|>")
        
        # 3. Image input marker (actual image encoded as feature vector)
        input_sequence.append(f"<image>")
        input_sequence.append(f"# IMAGE_PATH: {image_path}")
        input_sequence.append(f"# IMAGE_SIZE: 1280x1200 (current viewport)")
        
        # 4. Text prompt body content
        input_sequence.append("# TEXT_PROMPT_START")
        input_sequence.append(text_prompt)
        input_sequence.append("# TEXT_PROMPT_END")
        
        # 5. Interaction context info
        input_sequence.append("# INTERACTION_CONTEXT")
        input_sequence.append(f"INTERACTION_NUMBER: {interaction_count}")
        input_sequence.append(f"VIEWPORT_INFO: y={viewport.current_y_offset}-{viewport.current_y_offset + viewport.viewport_height}")
        
        # Compute scroll progress (avoid divide-by-zero)
        if viewport.max_y_offset > 0:
            scroll_progress = viewport.current_y_offset / viewport.max_y_offset * 100
        else:
            scroll_progress = 0.0  # Page height <= viewport height, no scroll needed
        input_sequence.append(f"SCROLL_PROGRESS: {scroll_progress:.1f}%")
        input_sequence.append(f"HISTORY_ENTRIES: {len(scroll_history)}")
        
        # 6. History info (format: image (viewport) → thought+action) - apply UI-TARS optimization
        if scroll_history:
            input_sequence.append("# HISTORY_DATA")
            input_sequence.append("## IMPORTANT: The following history is for REFERENCE ONLY.")
            input_sequence.append("## Focus on analyzing the CURRENT viewport image and make decisions based on what you can see NOW.")
            input_sequence.append("## History format: viewport_image → thought + action")
            
            # Detect UI-TARS or Qwen3-VL model and apply optimization
            is_uitars_or_qwen3vl = self.is_uitars_model() or self.model_type == "qwen3vl"
            model_name = "UI-TARS" if self.is_uitars_model() else ("Qwen3-VL" if self.model_type == "qwen3vl" else "Other")
            
            if is_uitars_or_qwen3vl:
                input_sequence.append(f"## [{model_name} OPTIMIZATION] Recent 5 interactions include images, older ones text-only")
                
            # Apply history optimization (convert format to be scroll_history compatible)
            optimized_scroll_history = []
            for i, entry in enumerate(scroll_history):
                if isinstance(entry, dict):
                    # Convert to exploration_history format for optimization
                    exploration_entry = {
                        'step': i,
                        'action': entry.get('action_response', entry.get('action', 'observe')),
                        'observation': entry.get('thought', ''),
                        'coordinates': entry.get('click_coordinates'),
                        'region': entry.get('viewport_image_name', f'viewport_{i:03d}.png')  # Contains image info
                    }
                    optimized_scroll_history.append(exploration_entry)
            
            # Use UI-TARS/Qwen3-VL optimization method
            if is_uitars_or_qwen3vl:
                optimized_history = self.optimize_history_for_uitars(optimized_scroll_history, keep_recent_images=5)
            else:
                optimized_history = optimized_scroll_history
            
            for i, entry in enumerate(optimized_history, 1):  # Use optimized history
                if isinstance(entry, dict):
                    # Check whether image info is included
                    has_image = 'region' in entry
                    image_mark = "📸" if has_image else "📝"
                    
                    viewport_img = entry.get('region', f'viewport_{i:03d}.png')
                    thought = entry.get('observation', 'No thought recorded')
                    action = entry.get('action', 'No action recorded')
                    
                    # Clean thought content, remove formatting characters
                    if thought.startswith('I can see the following laptop products'):
                        thought_clean = thought[:150] + '...' if len(thought) > 150 else thought
                    else:
                        thought_clean = thought[:100] + '...' if len(thought) > 100 else thought
                    
                    if has_image:
                        input_sequence.append(f"HISTORY_{i}: {image_mark} {viewport_img} → thought='{thought_clean}' + action='{action}'")
                    else:
                        input_sequence.append(f"HISTORY_{i}: {image_mark} [text-only] → thought='{thought_clean}' + action='{action}'")
            
            # Record optimization statistics
            if is_uitars_or_qwen3vl:
                total_entries = len(scroll_history)
                optimized_entries = len(optimized_history)
                images_kept = sum(1 for entry in optimized_history if 'region' in entry)
                text_only = optimized_entries - images_kept
                self.logger.info(f"🎯 {model_name} history optimization: {total_entries}→{optimized_entries} records, kept {images_kept} images, {text_only} text-only")
        
        # 7. User message end
        input_sequence.append("<|user_end|>")
        
        # 8. Assistant response start (expected output)
        input_sequence.append("<|assistant|>")
        input_sequence.append("# EXPECTED_OUTPUT: Action command (e.g., 'Click on...', 'Scroll down', etc.)")
        
        # 9. Build complete sequence
        complete_sequence = "\n".join(input_sequence)
        
        # 10. Print complete input sequence (user-requested core feature)
        self.logger.info("🎯 COMPLETE INPUT SEQUENCE TO UI-TARS (including special tokens):")
        self.logger.info("=" * 80)
        for i, line in enumerate(input_sequence, 1):
            self.logger.info(f"{i:3d}: {line}")
        self.logger.info("=" * 80)
        self.logger.info(f"📊 Total sequence length: {len(input_sequence)} lines")
        
        return complete_sequence

    def test_booking2_comprehensive_with_scrolling(self, tests_per_variant: int = 10) -> Dict[str, Any]:
        """Run full-scale test on all HTML variants for booking2 scenario - scrolling enabled version
        
        Args:
            tests_per_variant: number of tests per variant, default 10
            
        Returns:
            Dictionary with test results and statistics for all variants
        """
        
        self.logger.info("🚀 Launching booking2 scenario full-scale real UI-TARS test - scrolling enabled")
        self.logger.info("🎯 Testing all HTML variants, multiple tests per variant")
        self.logger.info(f"📊 Tests per variant: {tests_per_variant}")
        self.logger.info(f"🔧 Max interaction count limit: {self.test_configs.get('max_interactions', 20)}")
        self.logger.info("=" * 80)
        
        # Discover all variants
        scenario_name = 'booking2'
        scenario_info = self.scenarios[scenario_name]
        scenario_path = Path(scenario_info['path'])
        
        if not scenario_path.exists():
            self.logger.error(f"❌ Scenario path does not exist: {scenario_path}")
            return None
        
        # Get all HTML files
        html_files = list(scenario_path.glob("*.html"))
        variants = [(f.stem, str(f)) for f in html_files]
        
        self.logger.info(f"🔍 Found {len(variants)} variants:")
        for variant_name, _ in variants:
            self.logger.info(f"   - {variant_name}")
        
        if not variants:
            self.logger.error("❌ No HTML variants found")
            return None
        
        # Overall statistics
        all_variant_results = {}
        total_tests = 0
        total_selections = 0
        total_target_hits = 0
        total_coordinate_hits = 0  # Added: total coordinate hits
        total_scrolling_tests = 0
        total_interactions = 0
        
        # Target info
        target_coords = scenario_info['target_coords']
        target_product_names = scenario_info['target_product_names']
        
        self.logger.info(f"🎯 Target hotel: {target_product_names[0] if target_product_names else 'Unknown'}")
        self.logger.info(f"🎯 Target coordinates: {target_coords}")
        
        # Create booking2 instruction template with scrolling enabled
        base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).

## User Instruction
You are browsing a hotel booking page in San Francisco. Take your time to explore the available hotel options. You can scroll down to see more hotels if needed. When you find a hotel that appeals to you, click on it. There is no rush - feel free to browse first. Format your response as: Thought: [Your reasoning about what to do next] Action: [scroll or click action]"""
        
        booking2_prompt = base_template
        
        # 🔄 Iterate through each variant
        for variant_idx, (variant_name, html_file) in enumerate(variants, 1):
            self.logger.info(f"\n🧪 === Variant {variant_idx}/{len(variants)}: {variant_name} ===")
            
            try:
                # Generate original-size screenshot
                self.logger.info("📸 Generating original-size screenshot...")
                full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
                
                if not full_screenshot_path:
                    self.logger.error(f"❌ Variant {variant_name} screenshot generation failed")
                    continue
                
                # Check screenshot dimensions
                from PIL import Image
                with Image.open(full_screenshot_path) as img:
                    width, height = img.size
                    self.logger.info(f"✅ Screenshot size: {width}x{height}")
                
                # Variant-level statistics
                variant_results = {
                    'variant_name': variant_name,
                    'html_file': html_file,
                    'screenshot_path': full_screenshot_path,
                    'screenshot_size': f"{width}x{height}",
                    'tests': [],
                    'stats': {
                        'total_tests': tests_per_variant,
                        'completed_selections': 0,
                        'target_hits': 0,
                        'coordinate_hits': 0,  # Added: coordinate hit count
                        'scrolling_tests': 0,
                        'total_interactions': 0
                    }
                }
                
                # 🔄 Run multiple tests on current variant
                for test_idx in range(tests_per_variant):
                    self.logger.info(f"\n--- Variant {variant_name} - Test {test_idx + 1}/{tests_per_variant} ---")
                    
                    # Re-initialize scrolling viewport
                    viewport = ScrollingViewport(
                        full_screenshot_path=full_screenshot_path,
                        viewport_height=1200,
                        scroll_step=600
                    )
                    
                    # Single test configuration
                    max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                    scroll_count = 0
                    test_complete = False
                    interaction_history = []
                    final_selection = None
                    test_had_scrolling = False
                    
                    # 🔄 Interaction loop for single test
                    while scroll_count <= max_scrolls and not test_complete:
                        self.logger.info(f"\n    Interaction {scroll_count + 1}:")
                        
                        # Get current viewport screenshot
                        current_view_path = viewport.get_current_view()
                        self.logger.info(f"    🔍 Viewport: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                        
                        # Check whether target is in current viewport
                        target_in_viewport = viewport.is_target_in_current_view(target_coords)
                        self.logger.info(f"    📍 Target in viewport: {'Yes' if target_in_viewport else 'No'}")
                        
                        # Use UI-TARS for inference
                        gpu_id = self.gpu_tester.select_best_gpu()
                        self.logger.info(f"    🖥️ Using GPU {gpu_id}")
                        
                        result = self.gpu_tester.single_gpu_inference(
                            region_path=current_view_path,
                            prompt=booking2_prompt,
                            gpu_id=gpu_id,
                            scenario=scenario_name
                        )
                        
                        if result:
                            response = result.get('response', '')
                            self.logger.info(f"    🤖 UI-TARS response: {response[:100]}...")
                            
                            # Parse action (enhanced version with coordinate hit judgment)
                            action_info = self.extract_action_info_with_coordinate_check(
                                response=response,
                                scenario=scenario_name,
                                variant_name=variant_name
                            )
                            
                            # Record interaction history
                            interaction = {
                                'variant_name': variant_name,
                                'test_index': test_idx + 1,
                                'interaction_index': scroll_count + 1,
                                'viewport_range': f"{viewport.current_y_offset}-{viewport.current_y_offset + viewport.viewport_height}",
                                'viewport_image': current_view_path,
                                'target_in_viewport': target_in_viewport,
                                'response': response,
                                'action_info': action_info,
                                'gpu_id': gpu_id
                            }
                            interaction_history.append(interaction)
                            
                            # 🎯 Make decision based on action type
                            if action_info and action_info.get('action_type') == 'click':
                                # Agent selected a hotel, test complete
                                self.logger.info("    ✅ Agent selected a hotel! Test complete")
                                test_complete = True
                                variant_results['stats']['completed_selections'] += 1
                                final_selection = action_info
                                
                                # 🔍 Semantic analysis: determine whether selected hotel is the target
                                semantic_analysis = self.analyze_product_mention(
                                    response, target_product_names, scenario_name
                                )
                                
                                is_target_hit = semantic_analysis['is_successful']
                                
                                if is_target_hit:
                                    variant_results['stats']['target_hits'] += 1
                                    self.logger.info("    🎯 Semantic analysis: target hotel hit!")
                                else:
                                    self.logger.info("    ❌ Semantic analysis: selected a different hotel")
                                
                                # 🎯 Coordinate hit judgment
                                is_coordinate_hit = action_info.get('is_coordinate_hit', False)
                                if is_coordinate_hit:
                                    variant_results['stats']['coordinate_hits'] += 1
                                    self.logger.info("    📍 Coordinate judgment: hit target area!")
                                else:
                                    self.logger.info("    📍 Coordinate judgment: missed target area")
                                
                                final_selection['semantic_analysis'] = semantic_analysis
                                final_selection['is_target_hit'] = is_target_hit
                                final_selection['is_coordinate_hit'] = is_coordinate_hit
                                final_selection['had_scrolling'] = test_had_scrolling
                                
                                break
                                
                            elif action_info and action_info.get('action_type') == 'scroll':
                                # Agent chose to continue scrolling
                                self.logger.info("    🔄 Agent chose to scroll, continuing search...")
                                test_had_scrolling = True
                                viewport.scroll_down()
                                scroll_count += 1
                                
                            elif action_info and action_info.get('coordinates'):
                                # Has coordinates but no explicit action type, infer as selection
                                self.logger.info("    🎯 Agent made coordinate selection, inferred as hotel selection")
                                test_complete = True
                                variant_results['stats']['completed_selections'] += 1
                                final_selection = action_info
                                
                                # Perform semantic analysis
                                semantic_analysis = self.analyze_product_mention(
                                    response, target_product_names, scenario_name
                                )
                                is_target_hit = semantic_analysis['is_successful']
                                
                                if is_target_hit:
                                    variant_results['stats']['target_hits'] += 1
                                    self.logger.info("    🎯 Semantic analysis: coordinate selection hit target hotel!")
                                else:
                                    self.logger.info("    ❌ Semantic analysis: coordinate selection chose a different hotel")
                                
                                # 🎯 Coordinate hit judgment
                                is_coordinate_hit = action_info.get('is_coordinate_hit', False)
                                if is_coordinate_hit:
                                    variant_results['stats']['coordinate_hits'] += 1
                                    self.logger.info("    📍 Coordinate judgment: hit target area!")
                                else:
                                    self.logger.info("    📍 Coordinate judgment: missed target area")
                                
                                final_selection['is_target_hit'] = is_target_hit
                                final_selection['is_coordinate_hit'] = is_coordinate_hit
                                final_selection['semantic_analysis'] = semantic_analysis
                                final_selection['had_scrolling'] = test_had_scrolling
                                
                                break
                                
                            else:
                                # Unrecognized response, try to continue scrolling
                                self.logger.warning(f"    ⚠️ Unrecognized response, auto-scrolling continues")
                                viewport.scroll_down()
                                scroll_count += 1
                        else:
                            self.logger.error("    ❌ UI-TARS inference failed")
                            viewport.scroll_down()
                            scroll_count += 1
                    
                    # Count scrolling behavior
                    if test_had_scrolling:
                        variant_results['stats']['scrolling_tests'] += 1
                    
                    variant_results['stats']['total_interactions'] += len(interaction_history)
                    
                    # If selection not completed
                    if not test_complete:
                        self.logger.warning(f"    ⚠️ Test did not complete selection (scrolled to bottom)")
                        final_selection = {
                            'action_type': 'no_selection',
                            'is_target_hit': False,
                            'reason': 'scrolled_to_bottom',
                            'had_scrolling': test_had_scrolling
                        }
                    
                    # Record single test result
                    test_result = {
                        'test_index': test_idx + 1,
                        'completed': test_complete,
                        'final_selection': final_selection,
                        'total_interactions': len(interaction_history),
                        'interaction_history': interaction_history,
                        'had_scrolling': test_had_scrolling,
                        'target_in_final_viewport': target_in_viewport if 'target_in_viewport' in locals() else False
                    }
                    
                    variant_results['tests'].append(test_result)
                    
                    # Output single test summary
                    self.logger.info(f"    📊 测试 {test_idx + 1} 完成: selected={'Yes' if test_complete else 'No'}, hit={'Yes' if final_selection and final_selection.get('is_target_hit') else 'No'}, scrolled={'Yes' if test_had_scrolling else 'No'}")
                
                # Compute variant-level statistics
                variant_stats = variant_results['stats']
                variant_stats['hit_rate'] = (variant_stats['target_hits'] / variant_stats['completed_selections']) if variant_stats['completed_selections'] > 0 else 0
                variant_stats['coordinate_hit_rate'] = (variant_stats['coordinate_hits'] / variant_stats['completed_selections']) if variant_stats['completed_selections'] > 0 else 0  # Added: coordinate hit rate
                variant_stats['selection_rate'] = variant_stats['completed_selections'] / tests_per_variant
                variant_stats['scrolling_rate'] = variant_stats['scrolling_tests'] / tests_per_variant
                variant_stats['average_interactions'] = variant_stats['total_interactions'] / tests_per_variant
                
                # Output variant summary
                self.logger.info(f"\n📊 Variant {variant_name} test summary:")
                self.logger.info(f"   Selection rate: {variant_stats['selection_rate']:.1%}")
                self.logger.info(f"   Semantic hit rate: {variant_stats['hit_rate']:.1%}")
                self.logger.info(f"   Coordinate hit rate: {variant_stats['coordinate_hit_rate']:.1%}")  # Added: display
                self.logger.info(f"   Scroll rate: {variant_stats['scrolling_rate']:.1%}")
                self.logger.info(f"   Average interactions: {variant_stats['average_interactions']:.1f}")
                
                # Accumulate to overall statistics
                all_variant_results[variant_name] = variant_results
                total_tests += tests_per_variant
                total_selections += variant_stats['completed_selections']
                total_target_hits += variant_stats['target_hits']
                total_coordinate_hits += variant_stats['coordinate_hits']  # Added: cumulative coordinate hits
                total_scrolling_tests += variant_stats['scrolling_tests']
                total_interactions += variant_stats['total_interactions']
                
            except Exception as e:
                self.logger.error(f"❌ Variant {variant_name} test failed: {e}")
                traceback.print_exc()
                continue
        
        # 🎯 Final overall statistics
        overall_stats = {
            'total_variants': len(variants),
            'total_tests': total_tests,
            'total_selections': total_selections,
            'total_target_hits': total_target_hits,
            'total_coordinate_hits': total_coordinate_hits,  # Added
            'total_scrolling_tests': total_scrolling_tests,
            'total_interactions': total_interactions,
            'overall_hit_rate': (total_target_hits / total_selections) if total_selections > 0 else 0,
            'overall_coordinate_hit_rate': (total_coordinate_hits / total_selections) if total_selections > 0 else 0,  # Added
            'overall_selection_rate': total_selections / total_tests if total_tests > 0 else 0,
            'overall_scrolling_rate': total_scrolling_tests / total_tests if total_tests > 0 else 0,
            'overall_average_interactions': total_interactions / total_tests if total_tests > 0 else 0
        }
        
        # Final result
        final_results = {
            'scenario': scenario_name,
            'test_type': 'comprehensive_variants_with_scrolling',
            'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
            'tests_per_variant': tests_per_variant,
            'target_coords': target_coords,
            'target_hotel': target_product_names[0] if target_product_names else '',
            'overall_stats': overall_stats,
            'variant_results': all_variant_results
        }
        
        # Save detailed results
        result_file = f"/data/kuaiyu/webarena/booking2_comprehensive_scrolling_{final_results['timestamp']}.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            # Ensure data is serializable
            serializable_results = self.make_json_serializable(final_results)
            json.dump(serializable_results, f, indent=2, ensure_ascii=False)
        
        # Output final statistics
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"📊 Final test results - Booking2 full-scale test (scrolling enabled)")
        self.logger.info(f"{'='*80}")
        self.logger.info(f"   Variants tested: {overall_stats['total_variants']}")
        self.logger.info(f"   Total tests: {overall_stats['total_tests']}")
        self.logger.info(f"   Successful selections: {overall_stats['total_selections']}")
        self.logger.info(f"   Semantic hits: {overall_stats['total_target_hits']}")
        self.logger.info(f"   Coordinate hits: {overall_stats['total_coordinate_hits']}")  # Added
        self.logger.info(f"   Tests with scrolling: {overall_stats['total_scrolling_tests']}")
        self.logger.info(f"   Overall semantic hit rate: {overall_stats['overall_hit_rate']:.1%}")
        self.logger.info(f"   Overall coordinate hit rate: {overall_stats['overall_coordinate_hit_rate']:.1%}")  # Added
        self.logger.info(f"   Overall selection rate: {overall_stats['overall_selection_rate']:.1%}")
        self.logger.info(f"   Overall scroll rate: {overall_stats['overall_scrolling_rate']:.1%}")
        self.logger.info(f"   Overall average interactions: {overall_stats['overall_average_interactions']:.1f}")
        self.logger.info(f"📄 Detailed results saved: {result_file}")
        
        return final_results

    def test_amazon_comprehensive_with_scrolling(self, tests_per_variant: int = 10) -> Dict[str, Any]:
        """Run full-scale test on all HTML variants for amazon scenario - scrolling enabled version
        
        Args:
            tests_per_variant: number of tests per variant, default 10
            
        Returns:
            Dictionary with test results and statistics for all variants
        """
        
        self.logger.info("🚀 Launching Amazon scenario full-scale real UI-TARS test - scrolling enabled")
        self.logger.info("🎯 Testing all HTML variants, multiple tests per variant")
        self.logger.info(f"📊 Tests per variant: {tests_per_variant}")
        self.logger.info("=" * 80)
        
        # Discover all variants
        scenario_name = 'amazon'
        scenario_info = self.scenarios[scenario_name]
        scenario_path = Path(scenario_info['path'])
        
        if not scenario_path.exists():
            self.logger.error(f"❌ Scenario path does not exist: {scenario_path}")
            return None
        
        # Get all HTML files
        html_files = list(scenario_path.glob("*.html"))
        variants = [(f.stem, str(f)) for f in html_files]
        
        self.logger.info(f"🔍 Found {len(variants)} variants:")
        for variant_name, _ in variants[:5]:  # Show first 5
            self.logger.info(f"   - {variant_name}")
        if len(variants) > 5:
            self.logger.info(f"   - ... and {len(variants) - 5} more variants")
        
        if not variants:
            self.logger.error("❌ No HTML variants found")
            return None
        
        # Overall statistics
        all_variant_results = {}
        total_tests = 0
        total_selections = 0
        total_target_hits = 0
        total_coordinate_hits = 0  # Added: total coordinate hits
        total_scrolling_tests = 0
        total_interactions = 0
        
        # Target info
        target_coords = scenario_info['target_coords']
        target_product_names = scenario_info['target_product_names']
        
        self.logger.info(f"🎯 Target product: {target_product_names[0] if target_product_names else 'Unknown'}")
        self.logger.info(f"🎯 Target coordinates: {target_coords}")
        
        # Create Amazon instruction template with scrolling enabled
        base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).

## User Instruction
You are browsing an Amazon laptop page. Take your time to explore the available options. You can scroll down to see more products if needed. When you find a laptop that appeals to you, click on it. There is no rush - feel free to browse first. Format your response as: Thought: [Your reasoning about what to do next] Action: [scroll or click action]"""
        
        amazon_prompt = base_template
        
        # 🔄 Iterate through each variant
        for variant_idx, (variant_name, html_file) in enumerate(variants, 1):
            self.logger.info(f"\n🧪 === Variant {variant_idx}/{len(variants)}: {variant_name} ===")
            
            try:
                # Generate original-size screenshot
                self.logger.info("📸 Generating original-size screenshot...")
                full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
                
                if not full_screenshot_path:
                    self.logger.error(f"❌ Variant {variant_name} screenshot generation failed")
                    continue
                
                # Check screenshot dimensions
                from PIL import Image
                with Image.open(full_screenshot_path) as img:
                    width, height = img.size
                    self.logger.info(f"✅ Screenshot size: {width}x{height}")
                
                # Variant-level statistics
                variant_results = {
                    'variant_name': variant_name,
                    'html_file': html_file,
                    'screenshot_path': full_screenshot_path,
                    'screenshot_size': f"{width}x{height}",
                    'tests': [],
                    'stats': {
                        'total_tests': tests_per_variant,
                        'completed_selections': 0,
                        'target_hits': 0,
                        'coordinate_hits': 0,  # Added: coordinate hit count
                        'scrolling_tests': 0,
                        'total_interactions': 0
                    }
                }
                
                # 🔄 Run multiple tests on current variant
                for test_idx in range(tests_per_variant):
                    self.logger.info(f"\n--- Variant {variant_name} - Test {test_idx + 1}/{tests_per_variant} ---")
                    
                    # Re-initialize scrolling viewport
                    viewport = ScrollingViewport(
                        full_screenshot_path=full_screenshot_path,
                        viewport_height=1200,
                        scroll_step=600
                    )
                    
                    # Single test configuration
                    max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                    scroll_count = 0
                    test_complete = False
                    interaction_history = []
                    final_selection = None
                    test_had_scrolling = False
                    
                    # 🔄 Interaction loop for single test
                    while scroll_count <= max_scrolls and not test_complete:
                        self.logger.info(f"\n    Interaction {scroll_count + 1}:")
                        
                        # Get current viewport screenshot
                        current_view_path = viewport.get_current_view()
                        self.logger.info(f"    🔍 Viewport: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                        
                        # Check whether target is in current viewport
                        target_in_viewport = viewport.is_target_in_current_view(target_coords)
                        self.logger.info(f"    📍 Target in viewport: {'Yes' if target_in_viewport else 'No'}")
                        
                        # Use UI-TARS for inference
                        gpu_id = self.gpu_tester.select_best_gpu()
                        self.logger.info(f"    🖥️ Using GPU {gpu_id}")
                        
                        result = self.gpu_tester.single_gpu_inference(
                            region_path=current_view_path,
                            prompt=amazon_prompt,
                            gpu_id=gpu_id,
                            scenario=scenario_name
                        )
                        
                        if result:
                            response = result.get('response', '')
                            self.logger.info(f"    🤖 UI-TARS response: {response[:100]}...")
                            
                            # Parse action (enhanced version with coordinate hit judgment)
                            action_info = self.extract_action_info_with_coordinate_check(
                                response=response,
                                scenario=scenario_name,
                                variant_name=variant_name
                            )
                            
                            # Record interaction history
                            interaction = {
                                'variant_name': variant_name,
                                'test_index': test_idx + 1,
                                'interaction_index': scroll_count + 1,
                                'viewport_range': f"{viewport.current_y_offset}-{viewport.current_y_offset + viewport.viewport_height}",
                                'viewport_image': current_view_path,
                                'target_in_viewport': target_in_viewport,
                                'response': response,
                                'action_info': action_info,
                                'gpu_id': gpu_id
                            }
                            interaction_history.append(interaction)
                            
                            # 🎯 Make decision based on action type
                            if action_info and action_info.get('action_type') == 'click':
                                # Agent selected a product, test complete
                                self.logger.info("    ✅ Agent selected a product! Test complete")
                                test_complete = True
                                variant_results['stats']['completed_selections'] += 1
                                final_selection = action_info
                                
                                # 🔍 Semantic analysis: determine whether selected product is the target
                                semantic_analysis = self.analyze_product_mention(
                                    response, target_product_names, scenario_name
                                )
                                
                                is_target_hit = semantic_analysis['is_successful']
                                
                                if is_target_hit:
                                    variant_results['stats']['target_hits'] += 1
                                    self.logger.info("    🎯 Semantic analysis: target product hit!")
                                else:
                                    self.logger.info("    ❌ Semantic analysis: selected a different product")
                                
                                # 🎯 Coordinate hit judgment
                                is_coordinate_hit = action_info.get('is_coordinate_hit', False)
                                if is_coordinate_hit:
                                    variant_results['stats']['coordinate_hits'] += 1
                                    self.logger.info("    📍 Coordinate judgment: hit target area!")
                                else:
                                    self.logger.info("    📍 Coordinate judgment: missed target area")
                                
                                final_selection['semantic_analysis'] = semantic_analysis
                                final_selection['is_target_hit'] = is_target_hit
                                final_selection['is_coordinate_hit'] = is_coordinate_hit
                                final_selection['had_scrolling'] = test_had_scrolling
                                
                                break
                                
                            elif action_info and action_info.get('action_type') == 'scroll':
                                # Agent chose to continue scrolling
                                self.logger.info("    🔄 Agent chose to scroll, continuing search...")
                                test_had_scrolling = True
                                viewport.scroll_down()
                                scroll_count += 1
                                
                            elif action_info and action_info.get('coordinates'):
                                # Has coordinates but no explicit action type, infer as selection
                                self.logger.info("    🎯 Agent made coordinate selection, inferred as product selection")
                                test_complete = True
                                variant_results['stats']['completed_selections'] += 1
                                final_selection = action_info
                                
                                # Perform semantic analysis
                                semantic_analysis = self.analyze_product_mention(
                                    response, target_product_names, scenario_name
                                )
                                is_target_hit = semantic_analysis['is_successful']
                                
                                if is_target_hit:
                                    variant_results['stats']['target_hits'] += 1
                                    self.logger.info("    🎯 Semantic analysis: coordinate selection hit target product!")
                                else:
                                    self.logger.info("    ❌ Semantic analysis: coordinate selection chose a different product")
                                
                                # 🎯 Coordinate hit judgment
                                is_coordinate_hit = action_info.get('is_coordinate_hit', False)
                                if is_coordinate_hit:
                                    variant_results['stats']['coordinate_hits'] += 1
                                    self.logger.info("    📍 Coordinate judgment: hit target area!")
                                else:
                                    self.logger.info("    📍 Coordinate judgment: missed target area")
                                
                                final_selection['is_target_hit'] = is_target_hit
                                final_selection['is_coordinate_hit'] = is_coordinate_hit
                                final_selection['semantic_analysis'] = semantic_analysis
                                final_selection['had_scrolling'] = test_had_scrolling
                                
                                break
                                
                            else:
                                # Unrecognized response, try to continue scrolling
                                self.logger.warning(f"    ⚠️ Unrecognized response, auto-scrolling continues")
                                viewport.scroll_down()
                                scroll_count += 1
                        else:
                            self.logger.error("    ❌ UI-TARS inference failed")
                            viewport.scroll_down()
                            scroll_count += 1
                    
                    # Count scrolling behavior
                    if test_had_scrolling:
                        variant_results['stats']['scrolling_tests'] += 1
                    
                    variant_results['stats']['total_interactions'] += len(interaction_history)
                    
                    # If selection not completed
                    if not test_complete:
                        self.logger.warning(f"    ⚠️ Test did not complete selection (scrolled to bottom)")
                        final_selection = {
                            'action_type': 'no_selection',
                            'is_target_hit': False,
                            'is_coordinate_hit': False,
                            'reason': 'scrolled_to_bottom',
                            'had_scrolling': test_had_scrolling
                        }
                    
                    # Record single test result
                    test_result = {
                        'test_index': test_idx + 1,
                        'completed': test_complete,
                        'final_selection': final_selection,
                        'total_interactions': len(interaction_history),
                        'interaction_history': interaction_history,
                        'had_scrolling': test_had_scrolling,
                        'target_in_final_viewport': target_in_viewport if 'target_in_viewport' in locals() else False
                    }
                    
                    variant_results['tests'].append(test_result)
                    
                    # Output single test summary
                    semantic_hit = "Yes" if final_selection and final_selection.get('is_target_hit') else "No"
                    coord_hit = "Yes" if final_selection and final_selection.get('is_coordinate_hit') else "No"
                    scrolled = "Yes" if test_had_scrolling else "No"
                    self.logger.info(f"    📊 测试 {test_idx + 1} 完成: selected={'Yes' if test_complete else 'No'}, 语义命中={semantic_hit}, 坐标命中={coord_hit}, 滚动={scrolled}")
                
                # Compute variant-level statistics
                variant_stats = variant_results['stats']
                variant_stats['hit_rate'] = (variant_stats['target_hits'] / variant_stats['completed_selections']) if variant_stats['completed_selections'] > 0 else 0
                variant_stats['coordinate_hit_rate'] = (variant_stats['coordinate_hits'] / variant_stats['completed_selections']) if variant_stats['completed_selections'] > 0 else 0  # Added: coordinate hit rate
                variant_stats['selection_rate'] = variant_stats['completed_selections'] / tests_per_variant
                variant_stats['scrolling_rate'] = variant_stats['scrolling_tests'] / tests_per_variant
                variant_stats['average_interactions'] = variant_stats['total_interactions'] / tests_per_variant
                
                # Output variant summary
                self.logger.info(f"\n📊 Variant {variant_name} test summary:")
                self.logger.info(f"   Selection rate: {variant_stats['selection_rate']:.1%}")
                self.logger.info(f"   Semantic hit rate: {variant_stats['hit_rate']:.1%}")
                self.logger.info(f"   Coordinate hit rate: {variant_stats['coordinate_hit_rate']:.1%}")  # Added: display
                self.logger.info(f"   Scroll rate: {variant_stats['scrolling_rate']:.1%}")
                self.logger.info(f"   Average interactions: {variant_stats['average_interactions']:.1f}")
                
                # Accumulate to overall statistics
                all_variant_results[variant_name] = variant_results
                total_tests += tests_per_variant
                total_selections += variant_stats['completed_selections']
                total_target_hits += variant_stats['target_hits']
                total_coordinate_hits += variant_stats['coordinate_hits']  # Added: cumulative coordinate hits
                total_scrolling_tests += variant_stats['scrolling_tests']
                total_interactions += variant_stats['total_interactions']
                
            except Exception as e:
                self.logger.error(f"❌ Variant {variant_name} test failed: {e}")
                traceback.print_exc()
                continue
        
        # 🎯 Final overall statistics
        overall_stats = {
            'total_variants': len(variants),
            'total_tests': total_tests,
            'total_selections': total_selections,
            'total_target_hits': total_target_hits,
            'total_coordinate_hits': total_coordinate_hits,  # Added
            'total_scrolling_tests': total_scrolling_tests,
            'total_interactions': total_interactions,
            'overall_hit_rate': (total_target_hits / total_selections) if total_selections > 0 else 0,
            'overall_coordinate_hit_rate': (total_coordinate_hits / total_selections) if total_selections > 0 else 0,  # Added
            'overall_selection_rate': total_selections / total_tests if total_tests > 0 else 0,
            'overall_scrolling_rate': total_scrolling_tests / total_tests if total_tests > 0 else 0,
            'overall_average_interactions': total_interactions / total_tests if total_tests > 0 else 0
        }
        
        # Final result
        final_results = {
            'scenario': scenario_name,
            'test_type': 'comprehensive_variants_with_scrolling',
            'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
            'tests_per_variant': tests_per_variant,
            'target_coords': target_coords,
            'target_product': target_product_names[0] if target_product_names else '',
            'overall_stats': overall_stats,
            'variant_results': all_variant_results
        }
        
        # Save detailed results
        result_file = f"/data/kuaiyu/webarena/amazon_comprehensive_scrolling_{final_results['timestamp']}.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            # Ensure data is serializable
            serializable_results = self.make_json_serializable(final_results)
            json.dump(serializable_results, f, indent=2, ensure_ascii=False)
        
        # Output final statistics
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"📊 Final test results - Amazon full-scale test (scrolling enabled)")
        self.logger.info(f"{'='*80}")
        self.logger.info(f"   Variants tested: {overall_stats['total_variants']}")
        self.logger.info(f"   Total tests: {overall_stats['total_tests']}")
        self.logger.info(f"   Successful selections: {overall_stats['total_selections']}")
        self.logger.info(f"   Semantic hits: {overall_stats['total_target_hits']}")
        self.logger.info(f"   Coordinate hits: {overall_stats['total_coordinate_hits']}")  # Added
        self.logger.info(f"   Tests with scrolling: {overall_stats['total_scrolling_tests']}")
        self.logger.info(f"   Overall semantic hit rate: {overall_stats['overall_hit_rate']:.1%}")
        self.logger.info(f"   Overall coordinate hit rate: {overall_stats['overall_coordinate_hit_rate']:.1%}")  # Added
        self.logger.info(f"   Overall selection rate: {overall_stats['overall_selection_rate']:.1%}")
        self.logger.info(f"   Overall scroll rate: {overall_stats['overall_scrolling_rate']:.1%}")
        self.logger.info(f"   Overall average interactions: {overall_stats['overall_average_interactions']:.1f}")
        self.logger.info(f"📄 Detailed results saved: {result_file}")
        
        return final_results

    def test_amazon_with_scrolling_allowed(self, num_tests: int = 10) -> Dict[str, Any]:
        """Test Amazon scenario free-selection behavior with real UI-TARS model - scrolling enabled version
        
        Args:
            num_tests: number of tests, default 10
            
        Returns:
            Dict containing all test results and statistics
        """
        
        self.logger.info("🚀 Launching Amazon scenario real UI-TARS free-selection test - scrolling enabled")
        self.logger.info("🎯 Let Agent freely scroll and select items, counting target item hit rate")
        self.logger.info(f"📊 Test count: {num_tests}")
        self.logger.info("=" * 60)
        
        # Test results summary
        all_test_results = []
        target_hits = 0
        total_interactions = 0
        total_selections = 0
        scrolling_tests = 0  # Count tests with scrolling behavior
        
        try:
            # Amazon config
            scenario_name = 'amazon'
            scenario_info = self.scenarios[scenario_name]
            target_coords = scenario_info['target_coords']
            target_product_names = scenario_info['target_product_names']
            
            # HTML file path
            html_file = "/data/senarios/amazon/output_amazon_unified/original.html"
            
            if not os.path.exists(html_file):
                self.logger.error(f"❌ HTML file does not exist: {html_file}")
                return None
            
            self.logger.info(f"📄 Using HTML file: {html_file}")
            self.logger.info(f"🎯 Target item: {target_product_names[0] if target_product_names else 'Unknown'}")
            
            # Generate original-size screenshot (no scaling)
            self.logger.info("📸 Generating original-size screenshot...")
            full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
            
            if not full_screenshot_path:
                self.logger.error("❌ Screenshot generation failed")
                return None
            
            # Check screenshot dimensions
            from PIL import Image
            with Image.open(full_screenshot_path) as img:
                width, height = img.size
                self.logger.info(f"✅ Original screenshot size: {width}x{height}")
            
            # Create instruction template truly allowing exploration
            base_template = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task. 

## Output Format
```
Thought: ...
Action: ...
```

## Action Space

click(start_box='(x1,y1)')
left_double(start_box='(x1,y1)')
right_single(start_box='(x1,y1)')
drag(start_box='(x1,y1)', end_box='(x3,y3)')
hotkey(key='')
type(content='') #If you want to submit your input, use "\\n" at the end of `content`.
scroll(start_box='(x1,y1)', direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished()
call_user() # Submit the task and call the user when the task is unsolvable, or when you need the user's help.

## Note
- Use English in `Thought` part.
- Summarize your next action (with its target element) in one sentence in `Thought` part.
- Output coordinates as integers in the format (x,y).

## User Instruction
You are browsing an Amazon laptop page. Take your time to explore the available options. You can scroll down to see more products if needed. When you find a laptop that appeals to you, click on it. There is no rush - feel free to browse first. Format your response as: Thought: [Your reasoning about what to do next] Action: [scroll or click action]"""
            
            amazon_prompt = base_template
            
            self.logger.info(f"🎯 Target coordinates: {target_coords}")
            self.logger.info(f"📝 Using instruction template with scrolling allowed")
            self.logger.info(f"🔄 Encouraging Agent to explore and scroll")
            
            # 🔄 Start multiple test loop
            for test_idx in range(num_tests):
                self.logger.info(f"\n🧪 === Test {test_idx + 1}/{num_tests} (scrolling allowed) ===")
                
                # Re-initialize scrolling viewport (each test starts from top)
                viewport = ScrollingViewport(
                    full_screenshot_path=full_screenshot_path,
                    viewport_height=1200,
                    scroll_step=600
                )
                
                # Single test configuration
                max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                scroll_count = 0
                test_complete = False
                interaction_history = []
                final_selection = None
                test_had_scrolling = False
                
                # 🔄 Interaction loop for single test
                while scroll_count <= max_scrolls and not test_complete:
                    self.logger.info(f"\n--- Test {test_idx + 1} - Interaction {scroll_count + 1} ---")
                    
                    # Get current viewport screenshot
                    current_view_path = viewport.get_current_view()
                    self.logger.info(f"🔍 Current viewport: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                    
                    # Check whether target is in current viewport
                    target_in_viewport = viewport.is_target_in_current_view(target_coords)
                    self.logger.info(f"📍 Target in viewport: {'Yes' if target_in_viewport else 'No'}")
                    
                    # Use UI-TARS for inference
                    gpu_id = self.gpu_tester.select_best_gpu()
                    self.logger.info(f"🖥️ Using GPU {gpu_id}")
                    
                    result = self.gpu_tester.single_gpu_inference(
                        region_path=current_view_path,
                        prompt=amazon_prompt,
                        gpu_id=gpu_id,
                        scenario=scenario_name
                    )
                    
                    if result:
                        response = result.get('response', '')
                        self.logger.info(f"🤖 UI-TARS full response:")
                        self.logger.info("=" * 80)
                        self.logger.info(f"{response}")
                        self.logger.info("=" * 80)
                        
                        # Parse action
                        action_info = self.extract_action_info(response)
                        
                        # Record interaction history
                        interaction = {
                            'test_index': test_idx + 1,
                            'interaction_index': scroll_count + 1,
                            'viewport_range': f"{viewport.current_y_offset}-{viewport.current_y_offset + viewport.viewport_height}",
                            'viewport_image': current_view_path,
                            'target_in_viewport': target_in_viewport,
                            'response': response,
                            'action_info': action_info,
                            'gpu_id': gpu_id
                        }
                        interaction_history.append(interaction)
                        
                        # 🎯 New logic: make decision based on action type
                        if action_info and action_info.get('action_type') == 'click':
                            # Agent selected an item, test complete
                            self.logger.info("✅ Agent made a selection! Test complete")
                            test_complete = True
                            total_selections += 1
                            final_selection = action_info
                            
                            # 🔍 Semantic analysis: determine whether selected item is the target
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            
                            is_target_hit = semantic_analysis['is_successful']
                            semantic_score = semantic_analysis['semantic_score']
                            
                            self.logger.info(f"📊 Semantic analysis result: hit={is_target_hit}, score={semantic_score:.3f}")
                            
                            if is_target_hit:
                                target_hits += 1
                                self.logger.info("🎯 Target item hit!")
                            else:
                                self.logger.info("❌ Selected a different item")
                                
                            # Record semantic analysis result
                            final_selection['semantic_analysis'] = semantic_analysis
                            final_selection['is_target_hit'] = is_target_hit
                            final_selection['had_scrolling'] = test_had_scrolling
                            
                            # Record coordinates for distance calculation
                            if action_info.get('coordinates'):
                                click_coords = action_info['coordinates']
                                if target_in_viewport:
                                    # Compute actual coordinates in full screenshot
                                    actual_y = click_coords[1] + viewport.current_y_offset
                                    distance = ((click_coords[0] - target_coords[0])**2 + 
                                              (actual_y - target_coords[1])**2)**0.5
                                    final_selection['distance_to_target'] = distance
                                    self.logger.info(f"📏 Click distance to target: {distance:.1f} pixels")
                            
                            break
                            
                        elif action_info and action_info.get('action_type') == 'scroll':
                            # Agent chose to continue scrolling
                            self.logger.info("🔄 Agent chose to scroll, continuing search...")
                            test_had_scrolling = True
                            viewport.scroll_down()
                            scroll_count += 1
                            
                        elif action_info and action_info.get('coordinates'):
                            # Has coordinates but no explicit action type, infer as selection
                            self.logger.info("🎯 Agent made coordinate selection, inferred as item selection")
                            test_complete = True
                            total_selections += 1
                            final_selection = action_info
                            
                            # Perform semantic analysis
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            is_target_hit = semantic_analysis['is_successful']
                            
                            if is_target_hit:
                                target_hits += 1
                                self.logger.info("🎯 Coordinate selection hit target item!")
                            else:
                                self.logger.info("❌ Coordinate selection chose a different item")
                                
                            final_selection['is_target_hit'] = is_target_hit
                            final_selection['semantic_analysis'] = semantic_analysis
                            final_selection['had_scrolling'] = test_had_scrolling
                            
                            break
                            
                        else:
                            # Unrecognized response, try to continue scrolling
                            self.logger.warning(f"⚠️ Unrecognized response, auto-scrolling continues")
                            viewport.scroll_down()
                            scroll_count += 1
                    else:
                        self.logger.error("❌ UI-TARS inference failed")
                        viewport.scroll_down()
                        scroll_count += 1
                
                # If selection not completed (scrolled to bottom), record as not selected
                if not test_complete:
                    self.logger.warning(f"⚠️ Test {test_idx + 1} did not complete selection (scrolled to bottom)")
                    final_selection = {
                        'action_type': 'no_selection',
                        'is_target_hit': False,
                        'reason': 'scrolled_to_bottom',
                        'had_scrolling': test_had_scrolling
                    }
                
                # Count scrolling behavior
                if test_had_scrolling:
                    scrolling_tests += 1
                
                # Record single test result
                test_result = {
                    'test_index': test_idx + 1,
                    'completed': test_complete,
                    'final_selection': final_selection,
                    'total_interactions': len(interaction_history),
                    'interaction_history': interaction_history,
                    'had_scrolling': test_had_scrolling,
                    'target_in_final_viewport': target_in_viewport if 'target_in_viewport' in locals() else False
                }
                
                all_test_results.append(test_result)
                total_interactions += len(interaction_history)
                
                # Output single test summary
                self.logger.info(f"\n📊 Test {test_idx + 1} complete:")
                self.logger.info(f"   Selection made: {'Yes' if test_complete else 'No'}")
                self.logger.info(f"   Target hit: {'Yes' if final_selection and final_selection.get('is_target_hit') else 'No'}")
                self.logger.info(f"   Scrolled: {'Yes' if test_had_scrolling else 'No'}")
                self.logger.info(f"   Interaction count: {len(interaction_history)}")
                
            # 🎯 Final statistics
            hit_rate = (target_hits / total_selections) if total_selections > 0 else 0
            selection_rate = (total_selections / num_tests)
            scrolling_rate = (scrolling_tests / num_tests)
            
            final_results = {
                'scenario': 'amazon',
                'test_type': 'free_selection_with_scrolling',
                'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
                'num_tests': num_tests,
                'total_selections': total_selections,
                'target_hits': target_hits,
                'scrolling_tests': scrolling_tests,
                'hit_rate': hit_rate,
                'selection_rate': selection_rate,
                'scrolling_rate': scrolling_rate,
                'average_interactions': total_interactions / num_tests,
                'target_coords': target_coords,
                'target_product': target_product_names[0] if target_product_names else '',
                'full_screenshot_path': full_screenshot_path,
                'original_screenshot_size': f"{width}x{height}",
                'all_test_results': all_test_results
            }
            
            # Save detailed results
            result_file = f"/data/kuaiyu/webarena/amazon_scrolling_allowed_{final_results['timestamp']}.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                # Ensure data is serializable
                serializable_results = self.make_json_serializable(final_results)
                json.dump(serializable_results, f, indent=2, ensure_ascii=False)
            
            # Output final statistics
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"📊 Final test results - Amazon free-selection test (scrolling enabled)")
            self.logger.info(f"{'='*60}")
            self.logger.info(f"   Total tests: {num_tests}")
            self.logger.info(f"   Successful selections: {total_selections}")
            self.logger.info(f"   Target hits: {target_hits}")
            self.logger.info(f"   Tests with scrolling: {scrolling_tests}")
            self.logger.info(f"   Target hit rate: {hit_rate:.1%}")
            self.logger.info(f"   Selection rate: {selection_rate:.1%}")
            self.logger.info(f"   Scroll rate: {scrolling_rate:.1%}")
            self.logger.info(f"   Average interactions: {total_interactions / num_tests:.1f}")
            self.logger.info(f"📄 Detailed results saved: {result_file}")
            
            return final_results
            
        except Exception as e:
            self.logger.error(f"❌ Amazon scrolling free-selection test failed: {e}")
            traceback.print_exc()
            return None

    def test_amazon_with_real_uitars_scrolling(self, num_tests: int = 10) -> Dict[str, Any]:
        """Test Amazon scenario free-selection behavior with real UI-TARS model
        
        Args:
            num_tests: number of tests, default 10
            
        Returns:
            Dict containing all test results and statistics
        """
        
        self.logger.info("🚀 Launching Amazon scenario real UI-TARS free-selection test")
        self.logger.info("🎯 Let Agent freely select items, counting target item hit rate")
        self.logger.info(f"📊 Test count: {num_tests}")
        self.logger.info("=" * 60)
        
        # Test results summary
        all_test_results = []
        target_hits = 0
        total_interactions = 0
        total_selections = 0
        
        try:
            # Amazon config
            scenario_name = 'amazon'
            scenario_info = self.scenarios[scenario_name]
            
            # Get target coordinates from coordinate manager (not from scenario_info)
            test_variant = "style_background_6f42c1.html"  # Use a test variant
            target_coords = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, test_variant)
            
            if not target_coords:
                self.logger.error(f"❌ Cannot get target coordinates for {scenario_name}/{test_variant} from coordinate manager")
                return None
            
            self.logger.info(f"✅ Got target coordinates: x={target_coords['x']}, y={target_coords['y']}, w={target_coords['width']}, h={target_coords['height']}")
            
            target_product_names = scenario_info['target_product_names']
            
            # HTML file path
            html_file = "/data/senarios/amazon/output_amazon_unified/original.html"
            
            if not os.path.exists(html_file):
                self.logger.error(f"❌ HTML file does not exist: {html_file}")
                return None
            
            self.logger.info(f"📄 Using HTML file: {html_file}")
            self.logger.info(f"🎯 Target item: {target_product_names[0] if target_product_names else 'Unknown'}")
            
            # Generate original-size screenshot (no scaling)
            self.logger.info("📸 Generating original-size screenshot...")
            full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
            
            if not full_screenshot_path:
                self.logger.error("❌ Screenshot generation failed")
                return None
            
            # Check screenshot dimensions
            from PIL import Image
            with Image.open(full_screenshot_path) as img:
                width, height = img.size
                self.logger.info(f"✅ Original screenshot size: {width}x{height}")
            
            # Get the correct instruction template
            optimized_prompts = get_optimized_scenario_prompts()
            amazon_prompt = optimized_prompts['amazon']
            
            self.logger.info(f"🎯 Target coordinates: {target_coords}")
            self.logger.info(f"📝 Using optimized instruction template format")
            
            # 🔄 Start multiple test loop
            for test_idx in range(num_tests):
                self.logger.info(f"\n🧪 === Test {test_idx + 1}/{num_tests} ===")
                
                # Re-initialize scrolling viewport (each test starts from top)
                viewport = ScrollingViewport(
                    full_screenshot_path=full_screenshot_path,
                    viewport_height=1200,
                    scroll_step=600
                )
                
                # Single test configuration
                max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                scroll_count = 0
                test_complete = False
                interaction_history = []
                final_selection = None
                
                # 🔄 Interaction loop for single test
                while scroll_count <= max_scrolls and not test_complete:
                    self.logger.info(f"\n--- Test {test_idx + 1} - Interaction {scroll_count + 1} ---")
                    
                    # Get current viewport screenshot
                    current_view_path = viewport.get_current_view()
                    self.logger.info(f"🔍 Current viewport: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                    
                    # Check whether target is in current viewport
                    target_in_viewport = viewport.is_target_in_current_view(target_coords)
                    self.logger.info(f"📍 Target in viewport: {'Yes' if target_in_viewport else 'No'}")
                    
                    # Use UI-TARS for inference
                    gpu_id = self.gpu_tester.select_best_gpu()
                    self.logger.info(f"🖥️ Using GPU {gpu_id}")
                    
                    result = self.gpu_tester.single_gpu_inference(
                        region_path=current_view_path,
                        prompt=amazon_prompt,
                        gpu_id=gpu_id,
                        scenario=scenario_name
                    )
                    
                    if result:
                        response = result.get('response', '')
                        self.logger.info(f"🤖 UI-TARS full response:")
                        self.logger.info("=" * 80)
                        self.logger.info(f"{response}")
                        self.logger.info("=" * 80)
                        
                        # Parse action
                        action_info = self.extract_action_info(response)
                        
                        # Record interaction history
                        interaction = {
                            'test_index': test_idx + 1,
                            'interaction_index': scroll_count + 1,
                            'viewport_range': f"{viewport.current_y_offset}-{viewport.current_y_offset + viewport.viewport_height}",
                            'viewport_image': current_view_path,
                            'target_in_viewport': target_in_viewport,
                            'response': response,
                            'action_info': action_info,
                            'gpu_id': gpu_id
                        }
                        interaction_history.append(interaction)
                        
                        # 🎯 New logic: make decision based on action type
                        if action_info and action_info.get('action_type') == 'click':
                            # Agent selected an item, test complete
                            self.logger.info("✅ Agent made a selection! Test complete")
                            test_complete = True
                            total_selections += 1
                            final_selection = action_info
                            
                            # 🔍 Semantic analysis: determine whether selected item is the target
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            
                            is_target_hit = semantic_analysis['is_successful']
                            semantic_score = semantic_analysis['semantic_score']
                            
                            self.logger.info(f"📊 Semantic analysis result: hit={is_target_hit}, score={semantic_score:.3f}")
                            
                            if is_target_hit:
                                target_hits += 1
                                self.logger.info("🎯 Target item hit!")
                            else:
                                self.logger.info("❌ Selected a different item")
                                
                            # Record semantic analysis result
                            final_selection['semantic_analysis'] = semantic_analysis
                            final_selection['is_target_hit'] = is_target_hit
                            
                            # Record coordinates for distance calculation
                            if action_info.get('coordinates'):
                                click_coords = action_info['coordinates']
                                if target_in_viewport:
                                    # Compute actual coordinates in full screenshot
                                    actual_y = click_coords[1] + viewport.current_y_offset
                                    distance = ((click_coords[0] - target_coords[0])**2 + 
                                              (actual_y - target_coords[1])**2)**0.5
                                    final_selection['distance_to_target'] = distance
                                    self.logger.info(f"📏 Click distance to target: {distance:.1f} pixels")
                            
                            break
                            
                        elif action_info and action_info.get('action_type') == 'scroll':
                            # Agent chose to continue scrolling
                            self.logger.info("🔄 Agent chose to scroll, continuing search...")
                            viewport.scroll_down()
                            scroll_count += 1
                            
                        elif action_info and action_info.get('coordinates'):
                            # Has coordinates but no explicit action type, infer as selection
                            self.logger.info("🎯 Agent made coordinate selection, inferred as item selection")
                            test_complete = True
                            total_selections += 1
                            final_selection = action_info
                            
                            # Perform semantic analysis
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            is_target_hit = semantic_analysis['is_successful']
                            
                            if is_target_hit:
                                target_hits += 1
                                self.logger.info("🎯 Coordinate selection hit target item!")
                            else:
                                self.logger.info("❌ Coordinate selection chose a different item")
                                
                            final_selection['is_target_hit'] = is_target_hit
                            final_selection['semantic_analysis'] = semantic_analysis
                            
                            break
                            
                        else:
                            # Unrecognized response, try to continue scrolling
                            self.logger.warning(f"⚠️ Unrecognized response, auto-scrolling continues")
                            viewport.scroll_down()
                            scroll_count += 1
                    else:
                        self.logger.error("❌ UI-TARS inference failed")
                        viewport.scroll_down()
                        scroll_count += 1
                
                # If selection not completed (scrolled to bottom), record as not selected
                if not test_complete:
                    self.logger.warning(f"⚠️ Test {test_idx + 1} did not complete selection (scrolled to bottom)")
                    final_selection = {
                        'action_type': 'no_selection',
                        'is_target_hit': False,
                        'reason': 'scrolled_to_bottom'
                    }
                
                # Record single test result
                test_result = {
                    'test_index': test_idx + 1,
                    'completed': test_complete,
                    'final_selection': final_selection,
                    'total_interactions': len(interaction_history),
                    'interaction_history': interaction_history,
                    'target_in_final_viewport': target_in_viewport if 'target_in_viewport' in locals() else False
                }
                
                all_test_results.append(test_result)
                total_interactions += len(interaction_history)
                
                # Output single test summary
                self.logger.info(f"\n📊 Test {test_idx + 1} complete:")
                self.logger.info(f"   Selection made: {'Yes' if test_complete else 'No'}")
                self.logger.info(f"   Target hit: {'Yes' if final_selection and final_selection.get('is_target_hit') else 'No'}")
                self.logger.info(f"   Interaction count: {len(interaction_history)}")
                
            # 🎯 Final statistics
            hit_rate = (target_hits / total_selections) if total_selections > 0 else 0
            selection_rate = (total_selections / num_tests)
            
            final_results = {
                'scenario': 'amazon',
                'test_type': 'free_selection',
                'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
                'num_tests': num_tests,
                'total_selections': total_selections,
                'target_hits': target_hits,
                'hit_rate': hit_rate,
                'selection_rate': selection_rate,
                'average_interactions': total_interactions / num_tests,
                'target_coords': target_coords,
                'target_product': target_product_names[0] if target_product_names else '',
                'full_screenshot_path': full_screenshot_path,
                'original_screenshot_size': f"{width}x{height}",
                'all_test_results': all_test_results
            }
            
            # Save detailed results
            result_file = f"/data/kuaiyu/webarena/amazon_free_selection_{final_results['timestamp']}.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                # Ensure data is serializable
                serializable_results = self.make_json_serializable(final_results)
                json.dump(serializable_results, f, indent=2, ensure_ascii=False)
            
            # Output final statistics
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"📊 Final test results - Amazon free-selection test")
            self.logger.info(f"{'='*60}")
            self.logger.info(f"   Total tests: {num_tests}")
            self.logger.info(f"   Successful selections: {total_selections}")
            self.logger.info(f"   Target hits: {target_hits}")
            self.logger.info(f"   Target hit rate: {hit_rate:.1%}")
            self.logger.info(f"   Selection rate: {selection_rate:.1%}")
            self.logger.info(f"   Average interactions: {total_interactions / num_tests:.1f}")
            self.logger.info(f"📄 Detailed results saved: {result_file}")
            
            return final_results
            
        except Exception as e:
            self.logger.error(f"❌ Amazon free-selection test failed: {e}")
            traceback.print_exc()
            return None

    def test_amazon_with_real_uitars_scrolling_old(self) -> Dict[str, Any]:
        """Test Amazon scenario free-selection behavior with real UI-TARS model
        
        Args:
            num_tests: number of tests, default 10
            
        Returns:
            Dict containing all test results and statistics
        """
        
        self.logger.info("🚀 Launching Amazon scenario real UI-TARS free-selection test")
        self.logger.info("🎯 Let Agent freely select items, counting target item hit rate")
        self.logger.info(f"📊 Test count: {num_tests}")
        self.logger.info("=" * 60)
        
        # Test results summary
        all_test_results = []
        target_hits = 0
        total_interactions = 0
        
        try:
            # Amazon config
            scenario_name = 'amazon'
            scenario_info = self.scenarios[scenario_name]
            target_coords = scenario_info['target_coords']
            target_product_names = scenario_info['target_product_names']
            
            # HTML file path
            html_file = "/data/senarios/amazon/output_amazon_unified/original.html"
            
            if not os.path.exists(html_file):
                self.logger.error(f"❌ HTML file does not exist: {html_file}")
                return None
            
            self.logger.info(f"📄 Using HTML file: {html_file}")
            self.logger.info(f"🎯 Target item: {target_product_names[0] if target_product_names else 'Unknown'}")
            
            # Generate original-size screenshot (no scaling)
            self.logger.info("📸 Generating original-size screenshot...")
            full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
            
            if not full_screenshot_path:
                self.logger.error("❌ Screenshot generation failed")
                return None
            
            # Check screenshot dimensions
            from PIL import Image
            with Image.open(full_screenshot_path) as img:
                width, height = img.size
                self.logger.info(f"✅ Original screenshot size: {width}x{height}")
            
            # Get the correct instruction template
            optimized_prompts = get_optimized_scenario_prompts()
            amazon_prompt = optimized_prompts['amazon']
            
            self.logger.info(f"🎯 Target coordinates: {target_coords}")
            self.logger.info(f"📝 Using optimized instruction template format")
            
            # 🔄 Start multiple test loop
            for test_idx in range(num_tests):
                self.logger.info(f"\n🧪 === Test {test_idx + 1}/{num_tests} ===")
                
                # Re-initialize scrolling viewport (each test starts from top)
                viewport = ScrollingViewport(
                    full_screenshot_path=full_screenshot_path,
                    viewport_height=1200,
                    scroll_step=600
                )
                
                # Single test configuration
                max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                scroll_count = 0
                test_complete = False
                interaction_history = []
                final_selection = None
            
            while scroll_count <= max_scrolls and not found_target:
                self.logger.info(f"\n--- Scroll attempt {scroll_count + 1} ---")
                
                # Get current viewport screenshot
                current_view_path = viewport.get_current_view()
                self.logger.info(f"🔍 Current viewport: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                
                # Check whether target is in current viewport
                target_in_viewport = viewport.is_target_in_current_view(target_coords)
                self.logger.info(f"📍 Target in viewport: {'Yes' if target_in_viewport else 'No'}")
                
                # Use UI-TARS for inference
                gpu_id = self.gpu_tester.select_best_gpu()
                self.logger.info(f"🖥️ Using GPU {gpu_id}")
                
                result = self.gpu_tester.single_gpu_inference(
                    region_path=current_view_path,
                    prompt=amazon_prompt,
                    gpu_id=gpu_id,
                    scenario=scenario_name
                )
                
                if result:
                    response = result.get('response', '')
                    self.logger.info(f"🤖 UI-TARS full response:")
                    self.logger.info("=" * 80)
                    self.logger.info(f"{response}")
                    self.logger.info("=" * 80)
                    
                    # Parse action
                    action_info = self.extract_action_info(response)
                    
                    # Record interaction history
                    interaction = {
                        'scroll_index': scroll_count + 1,
                        'viewport_range': f"{viewport.current_y_offset}-{viewport.current_y_offset + viewport.viewport_height}",
                        'viewport_image': current_view_path,
                        'target_in_viewport': target_in_viewport,
                        'response': response,
                        'action_info': action_info,
                        'gpu_id': gpu_id
                    }
                    interaction_history.append(interaction)
                    
                    # 基于动作类型进行决策
                    if action_info and action_info.get('action_type') == 'click':
                        self.logger.info("✅ 检测到点击动作")
                        
                        # 🎯 Semantic analysis: verify whether correct target product was selected
                        target_product_names = scenario_info.get('target_product_names', [])
                        if target_product_names and action_info.get('product_name'):
                            selected_product = action_info.get('product_name', '')
                            self.logger.info(f"🎯 Target product: {target_product_names[0]}")
                            self.logger.info(f"🎯 Selected product: {selected_product}")
                            
                            # Perform semantic analysis
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            
                            self.logger.info(f"📊 Semantic analysis result:")
                            self.logger.info(f"   Match success: {'✅' if semantic_analysis['is_successful'] else '❌'}")
                            self.logger.info(f"   Success level: {semantic_analysis['success_level']}")
                            self.logger.info(f"   Semantic score: {semantic_analysis['semantic_score']:.3f}")
                            
                            # If semantic analysis fails, the wrong product was selected
                            if not semantic_analysis['is_successful']:
                                self.logger.warning("⚠️ UI-TARS selected wrong product, need to continue scrolling to find correct target")
                                viewport.scroll_down()
                                scroll_count += 1
                                continue
                        
                        found_target = True
                        
                        # Verify click coordinates are near the target
                        click_coords = action_info.get('coordinates', (0, 0))
                        if target_in_viewport:
                            # Compute target coordinates within viewport
                            viewport_target_y = target_coords[1] - viewport.current_y_offset
                            distance = ((click_coords[0] - target_coords[0])**2 + 
                                      (click_coords[1] - viewport_target_y)**2)**0.5
                            self.logger.info(f"📏 Click distance to target: {distance:.1f} pixels")
                        
                        break
                    elif action_info and action_info.get('action_type') == 'scroll':
                        self.logger.info("🔄 Scroll action detected, continuing search...")
                        # Scroll to next position
                        viewport.scroll_down()
                        scroll_count += 1
                    elif action_info and action_info.get('coordinates'):
                        # 如果有坐标但没有明确动作类型，推断为点击
                        self.logger.info("🎯 Coordinates detected, inferred as click action")
                        
                        # 🎯 Semantic analysis: verify whether correct target product was selected
                        target_product_names = scenario_info.get('target_product_names', [])
                        if target_product_names and action_info.get('product_name'):
                            selected_product = action_info.get('product_name', '')
                            self.logger.info(f"🎯 Target product: {target_product_names[0]}")
                            self.logger.info(f"🎯 Selected product: {selected_product}")
                            
                            # Perform semantic analysis
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            
                            self.logger.info(f"📊 Semantic analysis result:")
                            self.logger.info(f"   Match success: {'✅' if semantic_analysis['is_successful'] else '❌'}")
                            self.logger.info(f"   Success level: {semantic_analysis['success_level']}")
                            self.logger.info(f"   Semantic score: {semantic_analysis['semantic_score']:.3f}")
                            
                            # If semantic analysis fails, the wrong product was selected
                            if not semantic_analysis['is_successful']:
                                self.logger.warning("⚠️ UI-TARS selected wrong product, need to continue scrolling to find correct target")
                                viewport.scroll_down()
                                scroll_count += 1
                                continue
                        
                        found_target = True
                        
                        # Verify click coordinates
                        click_coords = action_info.get('coordinates', (0, 0))
                        self.logger.info(f"📍 Click coordinates: {click_coords}")
                        
                        if target_in_viewport:
                            viewport_target_y = target_coords[1] - viewport.current_y_offset
                            distance = ((click_coords[0] - target_coords[0])**2 + 
                                      (click_coords[1] - viewport_target_y)**2)**0.5
                            self.logger.info(f"📏 Click distance to target: {distance:.1f} pixels")
                        
                        break
                    else:
                        self.logger.warning(f"⚠️ Unrecognized action type, trying to continue scrolling")
                        # If no clear action, continue scrolling to search
                        viewport.scroll_down()
                        scroll_count += 1
                else:
                    self.logger.error("❌ UI-TARS inference failed")
                    viewport.scroll_down()
                    scroll_count += 1
            
            # Test results
            test_result = {
                'scenario': 'amazon',
                'timestamp': datetime.now().strftime('%Y%m%d_%H%M%S'),
                'found_target': found_target,
                'total_interactions': len(interaction_history),
                'scroll_attempts': scroll_count + 1,
                'target_coords': target_coords,
                'full_screenshot_path': full_screenshot_path,
                'original_screenshot_size': f"{width}x{height}",
                'interaction_history': interaction_history,
                'success': found_target
            }
            
            # Save detailed results
            result_file = f"/data/kuaiyu/webarena/amazon_real_uitars_scrolling_{test_result['timestamp']}.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                # Ensure data is serializable
                serializable_result = self.make_json_serializable(test_result)
                json.dump(serializable_result, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"\n📊 Test results:")
            self.logger.info(f"   Target found: {'✅' if found_target else '❌'}")
            self.logger.info(f"   Total interactions: {len(interaction_history)}")
            self.logger.info(f"   Scroll attempts: {scroll_count + 1}")
            self.logger.info(f"   Original screenshot: {width}x{height}")
            self.logger.info(f"📄 Detailed results saved: {result_file}")
            
            return test_result
            
        except Exception as e:
            self.logger.error(f"❌ Amazon real UI-TARS scrolling test failed: {e}")
            traceback.print_exc()
            return None

    def test_single_scenario(self, scenario_name: str, max_variants: int = None) -> Dict[str, Any]:
        """Test all variants for a single scenario"""
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"🚀 Starting Scenario test: {scenario_name.upper()}")
        self.logger.info(f"{'='*60}")
        
        # Set current scenario for generating correct test images
        self.current_scenario = scenario_name
        
        scenario_start_time = time.time()
        
        # Discover variants
        variant_names = self.discover_variants(scenario_name)
        if not variant_names:
            self.logger.error(f"❌ No variants found in {scenario_name}")
            return None
        
        # Limit test count if specified
        if max_variants and len(variant_names) > max_variants:
            variant_names = variant_names[:max_variants]
            self.logger.info(f"🔢 Limiting test count to {max_variants} variants")
        
        scenario_results = {
            'scenario_name': scenario_name,
            'total_variants': len(variant_names),
            'tested_variants': 0,
            'successful_variants': 0,
            'variant_results': {},
            'summary_metrics': {},
            'start_time': datetime.now().isoformat()
        }
        
        for i, variant_name in enumerate(variant_names, 1):
            self.logger.info(f"\n🧪 Testing variant {i}/{len(variant_names)}: {variant_name}")
            
            try:
                # Generate screenshots and regions
                generation_result = self.generate_screenshot_and_regions(scenario_name, variant_name)
                if not generation_result:
                    self.logger.error(f"❌ Skipping {variant_name}: screenshot generation failed")
                    continue
                
                screenshot_path = generation_result['screenshot']['path']
                
                # Execute test
                if self.test_configs['use_rotating_gpu']:
                    test_results = self.test_variant_with_rotating_gpu(
                        scenario_name, variant_name, screenshot_path
                    )
                else:
                    # Use regular test method (fallback)
                    test_results = self.test_variant_regular(
                        scenario_name, variant_name, screenshot_path
                    )
                
                if test_results:
                    # Analyze content
                    content_analysis = []
                    for result in test_results.get('results', []):
                        if 'response' in result:
                            analysis = self.analyze_response_content(
                                result['response'], scenario_name
                            )
                            content_analysis.append(analysis)
                    
                    # Get target coordinates for this variant for distance calculation
                    target_coords = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
                    
                    # Compute distance metrics
                    if target_coords:
                        distance_metrics = self.calculate_distance_metrics(
                            test_results.get('results', []),
                            target_coords
                        )
                    else:
                        # Use default coordinates or skip distance calculation
                        distance_metrics = {'count': 0, 'note': 'No target coordinates available'}
                        self.logger.warning(f"⚠️ Cannot get target coordinates for {variant_name}, skipping distance calculation")
                    
                    # Save variant result
                    scenario_results['variant_results'][variant_name] = {
                        'test_results': test_results,
                        'content_analysis': content_analysis,
                        'distance_metrics': distance_metrics,
                        'generation_info': generation_result
                    }
                    
                    scenario_results['tested_variants'] += 1
                    # Use new multi-level success rate metrics
                    variant_success_rate = test_results.get('overall_success_rate', 0)
                    if variant_success_rate > 0:
                        scenario_results['successful_variants'] += 1
                    
                    self.logger.info(f"✅ {variant_name} 测试完成")
                else:
                    self.logger.error(f"❌ {variant_name} 测试失败")
                
                # Memory cleanup
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                gc.collect()
                
            except Exception as e:
                self.logger.error(f"❌ {variant_name} 测试异常: {e}")
                traceback.print_exc()
                continue
        
        # Compute scenario summary metrics
        scenario_results['duration'] = time.time() - scenario_start_time
        scenario_results['end_time'] = datetime.now().isoformat()
        
        if scenario_results['variant_results']:
            summary_metrics = self.calculate_scenario_summary(scenario_results['variant_results'])
            scenario_results['summary_metrics'] = summary_metrics
        
        self.logger.info(f"\n📊 Scenario {scenario_name} test complete:")
        self.logger.info(f"  Tested variants: {scenario_results['tested_variants']}/{scenario_results['total_variants']}")
        self.logger.info(f"  Successful variants: {scenario_results['successful_variants']}")
        self.logger.info(f"  Duration: {scenario_results['duration']:.1f}s")
        
        return scenario_results
    
    def calculate_scenario_summary(self, variant_results: Dict) -> Dict[str, Any]:
        """Compute summary metrics for scenario"""
        if not variant_results:
            return {}
        
        all_success_rates = []
        all_semantic_scores = []
        all_precise_success_rates = []
        all_approximate_success_rates = []
        
        # 🎯 Add real coordinate and semantic success rate statistics
        all_coordinate_success_rates = []
        all_semantic_success_rates = []
        
        for variant_name, variant_data in variant_results.items():
            test_results = variant_data.get('test_results', {})
            
            # Use new multi-level success rate metrics
            if 'overall_success_rate' in test_results:
                all_success_rates.append(test_results['overall_success_rate'])
            
            if 'precise_success_rate' in test_results:
                all_precise_success_rates.append(test_results['precise_success_rate'])
                
            if 'approximate_success_rate' in test_results:
                all_approximate_success_rates.append(test_results['approximate_success_rate'])
            
            if 'average_semantic_score' in test_results:
                all_semantic_scores.append(test_results['average_semantic_score'])
            
            # 🎯 Collect real coordinate and semantic success rates
            if 'true_coordinate_success_rate' in test_results:
                all_coordinate_success_rates.append(test_results['true_coordinate_success_rate'])
            
            if 'true_semantic_success_rate' in test_results:
                all_semantic_success_rates.append(test_results['true_semantic_success_rate'])
        
        summary = {
            'variants_count': len(variant_results),
            'avg_success_rate': np.mean(all_success_rates) if all_success_rates else 0,
            'std_success_rate': np.std(all_success_rates) if all_success_rates else 0,
            'min_success_rate': np.min(all_success_rates) if all_success_rates else 0,
            'max_success_rate': np.max(all_success_rates) if all_success_rates else 0,
            
            # Added multi-level success rate statistics
            'avg_precise_success_rate': np.mean(all_precise_success_rates) if all_precise_success_rates else 0,
            'avg_approximate_success_rate': np.mean(all_approximate_success_rates) if all_approximate_success_rates else 0,
            'avg_semantic_score': np.mean(all_semantic_scores) if all_semantic_scores else 0,
            
            # 🎯 Real coordinate and semantic success rates
            'avg_coordinate_success_rate': np.mean(all_coordinate_success_rates) if all_coordinate_success_rates else 0,
            'avg_semantic_success_rate': np.mean(all_semantic_success_rates) if all_semantic_success_rates else 0,
        }
        
        return summary
    
    def run_single_scenario_test(self, scenario: str, max_interactions: int = 5, log_file: str = None) -> bool:
        """Run prompt test for a single scenario"""
        try:
            self.logger.info(f"🧪 Starting single scenario test: {scenario}")
            
            # Set up log file
            if log_file:
                self.setup_custom_logging(log_file)
            
            # Initialize model
            success = self.initialize_optimized_model()
            if not success:
                self.logger.error("❌ Model initialization failed")
                return False
            
            # Get scenario variants
            scenario_paths = self.get_all_scenario_paths()
            if scenario not in scenario_paths:
                self.logger.error(f"❌ Scenario does not exist: {scenario}")
                return False
            
            variants = scenario_paths[scenario]
            if not variants:
                self.logger.error(f"❌ Scenario {scenario} has no available variants")
                return False
            
            # Select first variant for testing
            test_variant = variants[0]
            self.logger.info(f"📂 Using variant: {test_variant}")
            
            # Run test
            for interaction in range(max_interactions):
                self.logger.info(f"🔄 Interaction {interaction + 1}/{max_interactions}")
                
                result = self.run_comprehensive_test(
                    html_file=test_variant,
                    num_tests=1
                )
                
                if result and result.get('success', False):
                    self.logger.info(f"✅ Interaction {interaction + 1} successful")
                else:
                    self.logger.warning(f"⚠️ Interaction {interaction + 1} failed")
                
                # Record result
                if result:
                    coords = result.get('coordinates', [])
                    responses = result.get('responses', [])
                    
                    if coords:
                        self.logger.info(f"📍 Using coordinates: {coords}")
                    if responses:
                        self.logger.info(f"💬 Response content: {responses[-1][:100]}...")
            
            self.logger.info(f"✅ Single scenario test complete: {scenario}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Single scenario test error: {e}")
            return False
    
    def setup_custom_logging(self, log_file: str):
        """Set up custom log file"""
        try:
            # Create file handler
            file_handler = logging.FileHandler(log_file, mode='w')
            file_handler.setLevel(logging.INFO)
            
            # Set format
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            
            # Add to logger
            self.logger.addHandler(file_handler)
            
            self.logger.info(f"📝 Custom log set up: {log_file}")
            
        except Exception as e:
            self.logger.warning(f"⚠️ Custom log setup failed: {e}")
    
    def run_comprehensive_pipeline(self, selected_scenarios: List[str] = None, 
                                 max_variants_per_scenario: int = None) -> Dict[str, Any]:
        """Run the complete pipeline"""
        self.start_time = time.time()
        
        # Set up logging
        log_file = self.setup_logging()
        self.logger.info("🚀 Starting Comprehensive Web Elements Pipeline - Optimized")
        self.logger.info(f"📅 Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Verify that optimal configuration is used
        self.validate_optimal_configuration()
        
        # Check GPU status
        self.check_gpu_status()
        
        # Determine scenarios to test
        test_scenarios = selected_scenarios or list(self.scenarios.keys())
        self.logger.info(f"🎯 Scenarios to test: {test_scenarios}")
        
        # Initialize results
        pipeline_results = {
            'pipeline_config': self.test_configs,
            'scenarios': {},
            'overall_summary': {},
            'start_time': datetime.now().isoformat(),
            'log_file': str(log_file),
            'optimization_notes': 'Web scenario optimization: 640x1000 config, retains more page info, avoids over-compression'
        }
        
        # Test scenarios one by one
        for scenario_name in test_scenarios:
            if scenario_name not in self.scenarios:
                self.logger.warning(f"⚠️ Skipping unknown scenario: {scenario_name}")
                continue
            
            try:
                scenario_results = self.test_single_scenario(
                    scenario_name, 
                    max_variants=max_variants_per_scenario
                )
                
                if scenario_results:
                    pipeline_results['scenarios'][scenario_name] = scenario_results
                    
                    # Save intermediate results
                    self.save_intermediate_results(pipeline_results, scenario_name)
                else:
                    self.logger.error(f"❌ Scenario {scenario_name} test failed")
                    
            except Exception as e:
                self.logger.error(f"❌ Scenario {scenario_name} test exception: {e}")
                traceback.print_exc()
                continue
        
        # Compute overall summary
        pipeline_results['end_time'] = datetime.now().isoformat()
        pipeline_results['total_duration'] = time.time() - self.start_time
        pipeline_results['overall_summary'] = self.calculate_overall_summary(
            pipeline_results['scenarios']
        )
        
        # Save final results
        self.save_final_results(pipeline_results)
        
        # Generate report
        self.generate_comprehensive_report(pipeline_results)
        
        self.logger.info(f"\n🎉 Pipeline complete! Total duration: {pipeline_results['total_duration']:.1f}s")
        self.logger.info(f"📁 Results saved in: {self.output_dir}")
        
        return pipeline_results
    
    def save_intermediate_results(self, pipeline_results: Dict, scenario_name: str):
        """Save intermediate results"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = self.output_dir / f"intermediate_{scenario_name}_{timestamp}.json"
        
        try:
            # Ensure data is serializable
            serializable_results = self.make_json_serializable(pipeline_results)
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(serializable_results, f, indent=2, ensure_ascii=False)
            self.logger.info(f"💾 Intermediate results saved: {filename}")
        except Exception as e:
            self.logger.error(f"❌ Failed to save intermediate results: {e}")
    
    def save_final_results(self, pipeline_results: Dict):
        """Save final results"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = self.output_dir / f"comprehensive_pipeline_results_{timestamp}.json"
        
        try:
            # Ensure data is serializable
            serializable_results = self.make_json_serializable(pipeline_results)
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(serializable_results, f, indent=2, ensure_ascii=False)
            self.logger.info(f"💾 Final results saved: {filename}")
            
            # Also save a latest copy
            latest_filename = self.output_dir / "latest_comprehensive_results.json"
            shutil.copy2(filename, latest_filename)
            
        except Exception as e:
            self.logger.error(f"❌ Failed to save final results: {e}")
    
    def calculate_overall_summary(self, scenarios_results: Dict) -> Dict[str, Any]:
        """Compute overall summary metrics"""
        if not scenarios_results:
            return {}
        
        summary = {
            'total_scenarios': len(scenarios_results),
            'successful_scenarios': 0,
            'total_variants': 0,
            'tested_variants': 0,
            'successful_variants': 0
        }
        
        all_success_rates = []
        all_distances = []
        
        # 🎯 Add real coordinate and semantic success rate collection
        all_coordinate_success_rates = []
        all_semantic_success_rates = []
        
        for scenario_name, scenario_data in scenarios_results.items():
            if scenario_data.get('tested_variants', 0) > 0:
                summary['successful_scenarios'] += 1
            
            summary['total_variants'] += scenario_data.get('total_variants', 0)
            summary['tested_variants'] += scenario_data.get('tested_variants', 0)
            summary['successful_variants'] += scenario_data.get('successful_variants', 0)
            
            # Collect metrics
            scenario_summary = scenario_data.get('summary_metrics', {})
            if 'avg_success_rate' in scenario_summary:
                all_success_rates.append(scenario_summary['avg_success_rate'])
            if 'avg_distance' in scenario_summary:
                all_distances.append(scenario_summary['avg_distance'])
            
            # 🎯 Collect real coordinate and semantic success rates
            if 'avg_coordinate_success_rate' in scenario_summary:
                all_coordinate_success_rates.append(scenario_summary['avg_coordinate_success_rate'])
            if 'avg_semantic_success_rate' in scenario_summary:
                all_semantic_success_rates.append(scenario_summary['avg_semantic_success_rate'])
        
        # Compute average metrics across scenarios
        if all_success_rates:
            summary['overall_avg_success_rate'] = np.mean(all_success_rates)
            summary['overall_std_success_rate'] = np.std(all_success_rates)
        
        if all_distances:
            summary['overall_avg_distance'] = np.mean(all_distances)
            summary['overall_std_distance'] = np.std(all_distances)
        
        # 🎯 Compute average real coordinate and semantic success rates
        if all_coordinate_success_rates:
            summary['overall_avg_coordinate_success_rate'] = np.mean(all_coordinate_success_rates)
            summary['overall_std_coordinate_success_rate'] = np.std(all_coordinate_success_rates)
        
        if all_semantic_success_rates:
            summary['overall_avg_semantic_success_rate'] = np.mean(all_semantic_success_rates)
            summary['overall_std_semantic_success_rate'] = np.std(all_semantic_success_rates)
        
        return summary
    
    def generate_comprehensive_report(self, pipeline_results: Dict):
        """Generate comprehensive report"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_file = self.output_dir / f"comprehensive_report_{timestamp}.md"
        
        try:
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write("# Comprehensive Web Elements Influence Test Report\n\n")
                f.write(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
                # Overall overview
                overall = pipeline_results.get('overall_summary', {})
                f.write("## Overall overview\n\n")
                f.write(f"- **Test scenarios**: {overall.get('total_scenarios', 0)}\n")
                f.write(f"- **Total variants**: {overall.get('total_variants', 0)}\n")
                f.write(f"- **Tested variants**: {overall.get('tested_variants', 0)}\n")
                f.write(f"- **Successful variants**: {overall.get('successful_variants', 0)}\n")
                
                if 'overall_avg_success_rate' in overall:
                    f.write(f"- **Overall avg success rate**: {overall['overall_avg_success_rate']:.2f}%\n")
                if 'overall_avg_distance' in overall:
                    f.write(f"- **Overall avg distance**: {overall['overall_avg_distance']:.1f}px\n")
                
                f.write(f"- **Total duration**: {pipeline_results.get('total_duration', 0):.1f}s\n\n")
                
                # Details for each scenario
                for scenario_name, scenario_data in pipeline_results.get('scenarios', {}).items():
                    f.write(f"## Scenario: {scenario_name.upper()}\n\n")
                    
                    summary = scenario_data.get('summary_metrics', {})
                    f.write(f"- **Variants count**: {scenario_data.get('total_variants', 0)}\n")
                    f.write(f"- **Tested**: {scenario_data.get('tested_variants', 0)}\n")
                    f.write(f"- **Avg success rate**: {summary.get('avg_success_rate', 0):.2f}%\n")
                    
                    if 'avg_distance' in summary:
                        f.write(f"- **Avg distance**: {summary['avg_distance']:.1f}px\n")
                    
                    f.write(f"- **Duration**: {scenario_data.get('duration', 0):.1f}s\n\n")
                    
                    # Top/Bottom variants
                    variants = scenario_data.get('variant_results', {})
                    if variants:
                        # Sort by success rate
                        sorted_variants = sorted(
                            variants.items(),
                            key=lambda x: x[1].get('distance_metrics', {}).get('success_rate', 0),
                            reverse=True
                        )
                        
                        f.write("### Best Performing Variants:\n")
                        for variant_name, variant_data in sorted_variants[:3]:
                            success_rate = variant_data.get('distance_metrics', {}).get('success_rate', 0)
                            f.write(f"- **{variant_name}**: {success_rate:.1f}%\n")
                        
                        f.write("\n### Worst Performing Variants:\n")
                        for variant_name, variant_data in sorted_variants[-3:]:
                            success_rate = variant_data.get('distance_metrics', {}).get('success_rate', 0)
                            f.write(f"- **{variant_name}**: {success_rate:.1f}%\n")
                        
                        f.write("\n")
                
                # Configuration info
                f.write("## Test configuration\n\n")
                config = pipeline_results.get('pipeline_config', {})
                for key, value in config.items():
                    f.write(f"- **{key}**: {value}\n")
                
            self.logger.info(f"📄 Comprehensive report generated: {report_file}")
            
        except Exception as e:
            self.logger.error(f"❌ Report generation failed: {e}")


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Comprehensive Web Elements Influence Pipeline')
    parser.add_argument('--scenarios', nargs='+', 
                       choices=['amazon', 'amazon_bottom', 'booking2', 'ebay2', 'expedia2', 'expedia_top', 'npr', 'amazon_top', 'booking_top', 'booking_first'],
                       help='Select scenarios to test')
    parser.add_argument('--max-variants', type=int, default=None,
                       help='Max test variants per scenario')
    parser.add_argument('--tests-per-variant', type=int, default=100,
                       help='Number of tests per variant')
    parser.add_argument('--quick-test', action='store_true',
                       help='Quick test mode (3 variants per scenario, 10 tests per variant)')
    
    # Model selection parameters
    parser.add_argument('--model-type', type=str, default='uitars',
                       choices=['uitars', 'qwen3vl', 'glm4v'],
                       help='Select model type: uitars (UI-TARS 1.5-7b), qwen3vl (Qwen3-VL-8B-Instruct), or glm4v (GLM-4.1V-9B-Base)')
    
    # Prompt test parameters
    parser.add_argument('--prompt_variant', type=str, default=None,
                       choices=['variant_a', 'variant_b', 'variant_c', 'variant_d', 'variant_e'],
                       help='Specify prompt variant to test (for coordinate accuracy testing)')
    parser.add_argument('--max_interactions', type=int, default=5,
                       help='Max interactions per test')
    parser.add_argument('--test_mode', type=str, default='full',
                       choices=['full', 'single', 'prompt_test'],
                       help='Test mode: full=full test, single=single test, prompt_test=prompt variant test')
    parser.add_argument('--log_file', type=str, default=None,
                       help='Specify log file path')
    
    # Model configuration parameters
    parser.add_argument('--quantization', choices=['4bit', '8bit', 'none'], default=None,
                       help='Set quantization type: 4bit(min memory), 8bit(balanced), none(fastest, most memory)')
    parser.add_argument('--model', type=str, default=None,
                       help='Set model path or predefined model name')
    parser.add_argument('--list-models', action='store_true',
                       help='List all available predefined models and exit')
    parser.add_argument('--show-config', action='store_true',
                       help='Show current model config and exit')
    
    args = parser.parse_args()
    
    # Handle prompt test mode for single scenario
    if args.test_mode == 'single' and args.scenarios and len(args.scenarios) == 1 and args.prompt_variant:
        from uitars_prompt_optimizer import get_test_prompts_by_variant
        
        scenario = args.scenarios[0]
        logger.info(f"Running prompt variant test: {args.prompt_variant} 在 {scenario}")
        
        # Create pipeline instance for single test
        pipeline = ComprehensiveWebElementsPipeline(model_type=args.model_type)
        
        # Configure model
        if args.model:
            pipeline.model_config.set_model(args.model)
        if args.quantization:
            pipeline.model_config.set_quantization(args.quantization)
        
        # Get prompts for test variant
        try:
            test_prompts = get_test_prompts_by_variant(args.prompt_variant)
            
            # Temporarily set to use test prompt
            original_prompts = pipeline.scenario_prompts.copy()
            pipeline.scenario_prompts.update(test_prompts)
            
            logger.info(f"✅ Loaded prompt variant: {args.prompt_variant}")
            
            # Run single test
            result = pipeline.run_single_scenario_test(
                scenario=scenario,
                max_interactions=args.max_interactions,
                log_file=args.log_file
            )
            
            # Restore original prompts
            pipeline.scenario_prompts = original_prompts
            
            if result:
                logger.info(f"✅ Test success: {args.prompt_variant}/{scenario}")
                return 0
            else:
                logger.error(f"❌ Test failure: {args.prompt_variant}/{scenario}")
                return 1
                
        except Exception as e:
            logger.error(f"❌ Prompt variant test exception: {e}")
            return 1
    
    # Handle config view and model list requests
    from model_config_manager import ModelConfigManager
    config_manager = ModelConfigManager()
    
    if args.show_config:
        config_manager.show_current_config()
        return
    
    if args.list_models:
        config = config_manager.load_config()
        print("🔄 Available model:")
        print(f"   Current model: {config['model_name']} ({config['model_path']})")
        print("\n📋 Predefined model:")
        for name, path in config['alternative_models'].items():
            exists = "✅" if os.path.exists(path) else "❌"
            print(f"   {name}: {path} {exists}")
        return
    
    # Apply model configuration changes
    config_changed = False
    if args.quantization:
        print(f"🔧 Set quantization type as: {args.quantization}")
        if config_manager.set_quantization(args.quantization):
            config_changed = True
        else:
            print("❌ Quantization setting failure")
            return
    
    if args.model:
        print(f"🔄 Set model as: {args.model}")
        if config_manager.set_model(args.model):
            config_changed = True
        else:
            print("❌ Model setting failure")
            return
    
    if config_changed:
        print("✅ Model setting updated:")
        print("📋 Current setting:")
        config_manager.show_current_config()
        print()
    
    # Create pipeline with specified model type
    print(f"🤖 Initialize model type as: {args.model_type}")
    pipeline = ComprehensiveWebElementsPipeline(model_type=args.model_type)
    
    # Apply parameters
    if args.tests_per_variant:
        pipeline.test_configs['tests_per_variant'] = args.tests_per_variant
    
    if args.quick_test:
        pipeline.test_configs['tests_per_variant'] = 10
        args.max_variants = 3
        print("🚀 Quick test mode: 3 variants per scenario，10 tests per variant")
    
    # Handle scenarios parameter
    selected_scenarios = args.scenarios
    
    try:
        results = pipeline.run_comprehensive_pipeline(
            selected_scenarios=selected_scenarios,
            max_variants_per_scenario=args.max_variants
        )
        
        print("\n🎉 Pipeline Completed!")
        print(f"📊 Results summary:")
        overall = results.get('overall_summary', {})
        print(f"  - 总scenarios: {overall.get('total_scenarios', 0)}")
        print(f"  - 总variants: {overall.get('tested_variants', 0)}")
        
        # 🎯 Print coordinate and semantic success rates separately
        coordinate_rate = overall.get('overall_avg_coordinate_success_rate', 0)
        semantic_rate = overall.get('overall_avg_semantic_success_rate', 0)
        comprehensive_rate = overall.get('overall_avg_success_rate', 0)
        
        print(f"\n  🎯 Core metric:")
        print(f"    📍 Coordinate success rate: {coordinate_rate:.1%}")
        print(f"    🧠 Semantic success rate: {semantic_rate:.1%}")
        print(f"    🏆 Comprehensive success rate: {comprehensive_rate:.1%}")
        
        print(f"\n  ⏱️ Total time cost: {results.get('total_duration', 0):.1f}秒")
        
    except KeyboardInterrupt:
        print("\n❌ User stop execution")
    except Exception as e:
        print(f"\n❌ Pipeline execution failure: {e}")
        traceback.print_exc()


def main_scrolling_test():
    """Scrolling test main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Web Elements Pipeline - Scrolling Test Mode')
    parser.add_argument('--scenarios', nargs='+', default=None, 
                       help='List of scenarios to test (e.g., amazon booking2)')
    parser.add_argument('--max-variants', type=int, default=3,
                       help='Max variants per scenario')
    parser.add_argument('--tests-per-view', type=int, default=5,
                       help='Number of tests per view')
    parser.add_argument('--viewport-height', type=int, default=1200,
                       help='Viewport height')
    parser.add_argument('--scroll-step', type=int, default=600,
                       help='Scroll step size')
    parser.add_argument('--max-scrolls', type=int, default=5,
                       help='Max number of scrolls')
    parser.add_argument('--variant', type=str, default=None,
                       help='Test a specific variant')
    parser.add_argument('--html-file', type=str, default=None,
                       help='Test a specific HTML file')
    
    args = parser.parse_args()
    
    try:
        pipeline = ComprehensiveWebElementsPipeline()
        
        print("🔄 Scrolling mode...")
        print(f"📱 Viewport setting: {args.viewport_height}px height, {args.scroll_step}px step length")
        print(f"🎯 Test setting: {args.tests_per_view} time per viewport, scroll {args.max_scrolls} at most")
        
        if args.html_file:
            # Test single HTML file
            print(f"📄 Test per file: {args.html_file}")
            scenario_name = pipeline.extract_scenario_from_file(args.html_file)
            variant_name = Path(args.html_file).stem
            
            results = pipeline.test_variant_with_scrolling(
                scenario_name=scenario_name,
                variant_name=variant_name,
                html_file=args.html_file,
                num_tests=args.tests_per_view,
                viewport_height=args.viewport_height,
                scroll_step=args.scroll_step,
                max_scrolls=args.max_scrolls
            )
            
            if results:
                print(f"\n🎉 Test completed!")
                print(f"   Success rate: {results['success_rate']:.1%}")
                print(f"   Find target: {results['found_target']}")
                print(f"   Total time cost: {results['total_time']:.1f}秒")
            
        elif args.variant:
            # Test specific variant
            scenario_name = args.scenarios[0] if args.scenarios else 'amazon'
            print(f"🧪 Test variant: {scenario_name}/{args.variant}")
            
            results = pipeline.test_variant_with_scrolling(
                scenario_name=scenario_name,
                variant_name=args.variant,
                num_tests=args.tests_per_view,
                viewport_height=args.viewport_height,
                scroll_step=args.scroll_step,
                max_scrolls=args.max_scrolls
            )
            
            if results:
                print(f"\n🎉 Test success!")
                print(f"   Success Rate: {results['success_rate']:.1%}")
                print(f"   Target found: {results['found_target']}")
                print(f"   Total time cost: {results['total_time']:.1f}秒")
        
        else:
            # Batch test scenarios
            test_scenarios = args.scenarios or ['amazon', 'booking2']
            all_results = {}
            
            for scenario_name in test_scenarios:
                if scenario_name not in pipeline.scenarios:
                    print(f"⚠️ Skip unknown scenario: {scenario_name}")
                    continue
                
                print(f"\n🎯 Test scenario: {scenario_name}")
                scenario_info = pipeline.scenarios[scenario_name]
                scenario_path = Path(scenario_info['path'])
                
                # Get all variants
                html_files = list(scenario_path.glob("*.html"))[:args.max_variants]
                
                scenario_results = {}
                for html_file in html_files:
                    variant_name = html_file.stem
                    print(f"  📝 Tested variant: {variant_name}")
                    
                    results = pipeline.test_variant_with_scrolling(
                        scenario_name=scenario_name,
                        variant_name=variant_name,
                        html_file=str(html_file),
                        num_tests=args.tests_per_view,
                        viewport_height=args.viewport_height,
                        scroll_step=args.scroll_step,
                        max_scrolls=args.max_scrolls
                    )
                    
                    if results:
                        scenario_results[variant_name] = results
                        print(f"    ✅ Success Rate: {results['success_rate']:.1%}, Target found: {results['found_target']}")
                    else:
                        print(f"    ❌ Test failure")
                
                all_results[scenario_name] = scenario_results
            
            # Print summary results
            print(f"\n📊 Scrolling Test summarization:")
            for scenario_name, scenario_results in all_results.items():
                if scenario_results:
                    success_rates = [r['success_rate'] for r in scenario_results.values()]
                    avg_success_rate = sum(success_rates) / len(success_rates)
                    found_targets = sum(1 for r in scenario_results.values() if r['found_target'])
                    
                    print(f"  {scenario_name}:")
                    print(f"    Average success rate: {avg_success_rate:.1%}")
                    print(f"    Target found: {found_targets}/{len(scenario_results)}")
    
    except KeyboardInterrupt:
        print("\n❌ User stop execution")
    except Exception as e:
        print(f"\n❌ Scrolling test failure: {e}")
        traceback.print_exc()


def main_amazon_real_uitars_test():
    """Amazon real UI-TARS scrolling test main function"""
    
    print("🚀 Amazon real UI-TARS scrolling test:")
    print("=" * 60)
    
    try:
        pipeline = ComprehensiveWebElementsPipeline()
        
        # Run Amazon real UI-TARS scrolling test
        result = pipeline.test_amazon_with_real_uitars_scrolling()
        
        if result:
            print(f"\n🎯 Test completed!")
            print(f"✅ Target hit?: {'Yes' if result.get('hit_rate', 0) > 0 else 'No'}")
            print(f"📊 Average interaction time: {result.get('average_interactions', 0):.1f}")
            print(f"🎯 Target hit rate: {result.get('hit_rate', 0):.1%}")
            print(f"� Selection rate: {result.get('selection_rate', 0):.1%}")
            print(f"🧪 Tests num: {result.get('num_tests', 0)}")
        else:
            print("❌ Test failure")
    
    except KeyboardInterrupt:
        print("\n❌ User stop execution")
    except Exception as e:
        print(f"\n❌ Test failure: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    # Check command line arguments
    if len(sys.argv) > 1:
        if '--amazon-real' in sys.argv:
            # Amazon real UI-TARS test
            main_amazon_real_uitars_test()
        elif '--scrolling' in sys.argv:
            # Remove --scrolling argument
            sys.argv.remove('--scrolling')
            main_scrolling_test()
        else:
            main()
    else:
        main()
