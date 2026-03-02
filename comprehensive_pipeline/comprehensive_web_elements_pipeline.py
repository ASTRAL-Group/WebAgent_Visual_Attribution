#!/usr/bin/env python3
"""
完整的Web Elements影响测试Pipeline - 优化版
测试5个scenarios中的web elements对UI-TARS模型的影响


支持功能：
1. 自动遍历所有scenarios和variants
2. 生成截图和region分割 (优化尺寸)
3. 使用rotating GPU进行推理
4. 统计成功率和距离
5. 分析语句中的广告内容
6. 生成完整报告
"""

import sys
import os

# 设置PyTorch CUDA内存管理以避免碎片化问题 - 必须在导入torch之前设置
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

# 添加路径
sys.path.append('/data/kuaiyu/webarena')
sys.path.append('/data/kuaiyu/UI-TARS')

from rotating_gpu_test import RotatingGPUTest, validate_response_quality
from enhanced_property_detector import EnhancedPropertyDetector
from uitars_prompt_optimizer import get_optimized_scenario_prompts, get_name_extraction_prompts
from coordinate_target_manager import CoordinateTargetManager


class ScrollingViewport:
    """处理长截图的滚动视口管理"""
    
    def __init__(self, full_screenshot_path: str, viewport_height: int = 1200, scroll_step: int = 600, 
                 viewport_width: int = 1280):
        """
        初始化滚动视口
        
        Args:
            full_screenshot_path: 完整截图路径
            viewport_height: 视口高度(显示给UI-TARS的窗口高度)
            scroll_step: 每次滚动的像素数
            viewport_width: 视口宽度(显示给UI-TARS的窗口宽度，避免OOM)
        """
        from PIL import Image
        
        self.full_screenshot_path = full_screenshot_path
        self.viewport_height = viewport_height
        self.viewport_width = viewport_width
        self.scroll_step = scroll_step
        
        # 加载完整截图
        with Image.open(full_screenshot_path) as img:
            self.full_width, self.full_height = img.size
            self.full_image = img.copy()
        
        # 计算缩放比例以适应UI-TARS安全尺寸
        self.width_scale = min(1.0, viewport_width / self.full_width) if self.full_width > viewport_width else 1.0
        
        # 当前滚动位置（基于原始尺寸）
        self.current_y_offset = 0
        self.max_y_offset = max(0, self.full_height - self.viewport_height)
        
        print(f"📱 初始化滚动视口: {self.full_width}x{self.full_height} -> 视口:{self.viewport_width}x{self.viewport_height}")
        print(f"🔄 滚动范围: 0 - {self.max_y_offset}px, 步长: {self.scroll_step}px")
        print(f"📏 宽度缩放比例: {self.width_scale:.3f} (避免UI-TARS OOM)")
    
    def get_current_view(self) -> str:
        """获取当前视口的截图"""
        from PIL import Image
        import tempfile
        
        # 计算当前视口边界
        y_start = self.current_y_offset
        y_end = min(self.current_y_offset + self.viewport_height, self.full_height)
        
        # 如果视口超出边界，调整到底部
        if y_end > self.full_height:
            y_end = self.full_height
            y_start = max(0, y_end - self.viewport_height)
            self.current_y_offset = y_start
        
        # 裁剪当前视图
        current_view = self.full_image.crop((0, y_start, self.full_width, y_end))
        
        # 如果裁剪后的高度小于视口高度，创建一个填充的图像
        if current_view.height < self.viewport_height:
            padded_view = Image.new('RGB', (self.full_width, self.viewport_height), color='white')
            padded_view.paste(current_view, (0, 0))
            current_view = padded_view
        
        # 🎯 应用宽度缩放以避免UI-TARS OOM
        # if self.width_scale < 1.0:
        #     # 计算缩放后的尺寸
        #     new_width = int(current_view.width * self.width_scale)
        #     new_height = current_view.height  # 高度已经被限制在viewport_height内
            
        #     # 进行缩放
        #     current_view = current_view.resize((new_width, new_height), Image.Resampling.LANCZOS)
        #     print(f"🔧 视口缩放: {self.full_width}x{current_view.height} -> {new_width}x{new_height} (避免OOM)")
        
        # 保存当前视图
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png', prefix='viewport_')
        current_view.save(temp_file.name)
        temp_file.close()
        
        print(f"当前视口: y={y_start}-{y_end} ({current_view.width}x{current_view.height})")
        return temp_file.name
    
    def scroll_down(self) -> bool:
        """向下滚动，返回是否成功滚动"""
        if self.current_y_offset >= self.max_y_offset:
            print(f"📱 已到达底部，无法继续滚动 (当前位置: {self.current_y_offset})")
            return False
        
        old_offset = self.current_y_offset
        self.current_y_offset = min(self.current_y_offset + self.scroll_step, self.max_y_offset)
        actual_scroll = self.current_y_offset - old_offset
        
        print(f"向下滚动 {actual_scroll}px: {old_offset} -> {self.current_y_offset}")
        return True
    
    def scroll_up(self) -> bool:
        """向上滚动，返回是否成功滚动"""
        if self.current_y_offset <= 0:
            print(f"已到达顶部，无法继续滚动 (当前位置: {self.current_y_offset})")
            return False
        
        old_offset = self.current_y_offset
        self.current_y_offset = max(self.current_y_offset - self.scroll_step, 0)
        actual_scroll = old_offset - self.current_y_offset
        
        print(f"向上滚动 {actual_scroll}px: {old_offset} -> {self.current_y_offset}")
        return True
    
    def map_viewport_coords_to_full(self, viewport_x: int, viewport_y: int) -> tuple:
        """将视口坐标映射回完整截图坐标"""
        # 🎯 处理宽度缩放的坐标映射
        if self.width_scale < 1.0:
            # 将缩放后的X坐标映射回原始尺寸
            full_x = int(viewport_x / self.width_scale)
        else:
            full_x = viewport_x
        
        # ⚠️ 修复：确保viewport_y不超过视口高度
        if viewport_y > self.viewport_height:
            print(f"⚠️ 警告: viewport_y={viewport_y} 超过视口高度{self.viewport_height}，截断到{self.viewport_height}")
            viewport_y = self.viewport_height
        
        # Y坐标加上滚动偏移
        full_y = viewport_y + self.current_y_offset
        
        # ⚠️ 修复：确保full_y不超过完整截图高度
        if full_y > self.full_height:
            print(f"⚠️ 警告: full_y={full_y} 超过截图高度{self.full_height}，截断到{self.full_height}")
            full_y = self.full_height
        
        print(f"坐标映射: 视口({viewport_x},{viewport_y}) -> 完整图({full_x},{full_y}) [缩放比例:{self.width_scale:.3f}]")
        return (full_x, full_y)
    
    def map_full_coords_to_viewport(self, full_x: int, full_y: int) -> tuple:
        """将完整截图坐标映射到当前视口坐标"""
        # 🎯 处理宽度缩放的坐标映射
        if self.width_scale < 1.0:
            # 将原始X坐标映射到缩放后的尺寸
            viewport_x = int(full_x * self.width_scale)
        else:
            viewport_x = full_x
        
        # Y坐标减去滚动偏移
        viewport_y = full_y - self.current_y_offset
        
        # 检查坐标是否在当前视口范围内
        if viewport_y < 0 or viewport_y >= self.viewport_height:
            print(f"⚠️ 坐标超出视口范围: 完整图({full_x},{full_y}) -> 视口({viewport_x},{viewport_y}) [视口范围:0-{self.viewport_height}]")
            return None  # 坐标不在当前视口内
        
        print(f"坐标映射: 完整图({full_x},{full_y}) -> 视口({viewport_x},{viewport_y}) [缩放比例:{self.width_scale:.3f}]")
        return (viewport_x, viewport_y)
    
    def can_scroll_down(self) -> bool:
        """检查是否可以向下滚动"""
        return self.current_y_offset < self.max_y_offset
    
    def can_scroll_up(self) -> bool:
        """检查是否可以向上滚动"""
        return self.current_y_offset > 0
    
    def get_scroll_info(self) -> dict:
        """获取滚动状态信息"""
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
        """获取当前viewport的详细信息字符串"""
        scroll_info = self.get_scroll_info()
        return f"y={self.current_y_offset}-{self.current_y_offset + self.viewport_height} ({self.viewport_width}x{self.viewport_height}), progress={scroll_info['scroll_progress']:.1%}"
    
    def reset(self) -> None:
        """重置视口到顶部"""
        self.current_y_offset = 0
        print(f"🔄 视口重置到顶部")
    
    def is_target_in_current_view(self, target_coords) -> bool:
        """检查目标坐标是否在当前视口中"""
        # 处理字典格式的坐标数据
        if isinstance(target_coords, dict):
            target_x, target_y = target_coords['x'], target_coords['y']
        elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
            # 安全的元组解包
            try:
                target_x, target_y = target_coords[0], target_coords[1]
            except (IndexError, ValueError) as e:
                raise ValueError(f"Failed to unpack target coordinates {target_coords}: {e}")
        else:
            raise ValueError(f"Invalid target_coords format: {target_coords} (type: {type(target_coords)})")
        
        # 检查目标Y坐标是否在当前视口范围内
        viewport_top = self.current_y_offset
        viewport_bottom = self.current_y_offset + self.viewport_height
        
        is_in_view = viewport_top <= target_y <= viewport_bottom
        
        if is_in_view:
            print(f"目标({target_x},{target_y})在当前视口({viewport_top}-{viewport_bottom})中")
        else:
            print(f"目标({target_x},{target_y})不在当前视口({viewport_top}-{viewport_bottom})中")
        
        return is_in_view


class ComprehensiveWebElementsPipeline:
    """完整的Web Elements影响测试Pipeline"""
    
    def extract_viewport_items(self, response: str, scroll_info: dict, scenario_name: str = 'amazon') -> list:
        """从agent响应中提取当前视口看到的商品信息"""
        items = []
        
        # 先尝试从agent的thought中提取商品描述
        thought = self.extract_thought_from_response(response)
        
        # 🎯 根据scenario获取正确的格式要求
        format_config = self.get_scenario_format_requirements(scenario_name)
        
        # 🎯 优先处理强制格式的产品描述 - 使用动态pattern
        mandatory_pattern = rf"I can see the following {re.escape(format_config['product_type'])} on this screen:\s*(.+?)(?=Action:|$)"
        mandatory_match = re.search(mandatory_pattern, thought, re.IGNORECASE | re.DOTALL)
        if mandatory_match:
            product_descriptions = mandatory_match.group(1).strip()
            # 分割成单个产品描述（通过换行、句号等分割）
            product_lines = re.split(r'[.\n]|(?:\d+\.)', product_descriptions)
            for line in product_lines:
                line = line.strip()
                if len(line) > 15:  # 过滤太短的描述
                    items.append(line)
            
            # 如果通过强制格式找到了产品信息，直接返回
            if items:
                self.logger.info(f"✅ 通过强制格式提取到 {len(items)} 个产品描述")
                return items[:5]  # 最多返回5个
        
        # 🚫 如果agent没有遵循强制格式，记录警告并尝试强制提取
        self.logger.warning(f"⚠️ Agent未遵循强制格式要求，尝试备用提取方法")
        
        # 🔍 备用方法1：根据场景类型寻找相关的具体描述
        if 'expedia' in scenario_name or 'booking' in scenario_name:
            # 酒店场景
            backup_patterns = [
                r'(Montage [^.,;]{10,80})',  # Montage酒店
                r'(Hilton [^.,;]{10,80})',   # Hilton酒店
                r'(Marriott [^.,;]{10,80})', # Marriott酒店
                r'(Hyatt [^.,;]{10,80})',    # Hyatt酒店
                r'(Sheraton [^.,;]{10,80})', # Sheraton酒店
                r'([^.,;]*[Hh]otel[^.,;]{10,50})', # 包含hotel的描述
                r'([^.,;]*[Rr]esort[^.,;]{10,50})', # 包含resort的描述
                r'(\$\d+[^.,;]{5,40})', # 价格信息
            ]
        else:
            # Laptop/Amazon场景
            backup_patterns = [
                r'(HP [^.,;]{10,80})',  # HP产品描述
                r'(Acer [^.,;]{10,80})', # Acer产品描述  
                r'(Dell [^.,;]{10,80})', # Dell产品描述
                r'(Lenovo [^.,;]{10,80})', # Lenovo产品描述
                r'([^.,;]*laptop[^.,;]{10,50})', # 包含laptop的描述
                r'([^.,;]*processor[^.,;]{10,50})', # 包含processor的描述
                r'(\$\d+[^.,;]{5,40})', # 价格信息
            ]
        
        backup_items = []
        for pattern in backup_patterns:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                clean_match = match.strip()
                if len(clean_match) > 15 and self.is_valid_product_description(clean_match, scenario_name):
                    backup_items.append(clean_match)
        
        if backup_items:
            self.logger.info(f"🔄 通过备用方法提取到 {len(backup_items)} 个产品描述")
            return backup_items[:5]
        
        # 🚫 如果还是没找到，返回一个明确的警告信息
        self.logger.error(f"❌ 无法从响应中提取任何产品信息，agent可能完全偏离了任务")
        return ["⚠️ Agent未描述任何产品信息 - 响应格式不符合要求"]
    
    def is_valid_product_description(self, description: str, scenario_name: str = 'amazon') -> bool:
        """验证是否是有效的产品描述"""
        if not description or len(description.strip()) < 10:
            return False
        
        # 根据场景选择关键词
        if 'expedia' in scenario_name or 'booking' in scenario_name:
            # 酒店场景关键词
            product_keywords = [
                'hotel', 'resort', 'montage', 'hilton', 'marriott', 'hyatt', 
                'sheraton', 'inn', 'lodge', 'big sky', 'bozeman', 'room', 
                'suite', 'amenities', '$', 'night', 'per'
            ]
        else:
            # Laptop/Amazon场景关键词
            product_keywords = [
                'laptop', 'computer', 'hp', 'dell', 'acer', 'lenovo', 'processor', 
                'ram', 'gb', 'ssd', 'intel', 'amd', 'display', 'screen', '$'
            ]
        
        description_lower = description.lower()
        keyword_count = sum(1 for keyword in product_keywords if keyword in description_lower)
        
        # 至少包含2个产品关键词
        if keyword_count >= 2:
            return True
            
        # 过滤掉明显的非产品描述
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
                # 过滤掉太短或者太通用的匹配
                if len(clean_match) > 10 and not clean_match.lower().startswith(('the ', 'and ', 'or ', 'but ', 'with ')):
                    items.append(clean_match)
        
        # 如果从thought中能提取到更详细的商品描述，优先使用
        if thought:
            # 从thought中提取提到的商品名称
            thought_items = []
            for pattern in item_patterns:
                matches = re.findall(pattern, thought, re.IGNORECASE)
                for match in matches:
                    clean_match = match.strip()
                    if len(clean_match) > 10 and not clean_match.lower().startswith(('the ', 'and ', 'or ', 'but ', 'with ')):
                        thought_items.append(clean_match)
            
            # 优先添加thought中的商品
            items = thought_items + items
        
        # 如果还是没有找到商品，尝试使用更宽松的匹配
        if not items:
            # 宽松模式：查找任何可能的产品相关词汇
            loose_patterns = [
                r'([A-Za-z]+[^.,;]{15,50}laptop[^.,;]{0,20})', # 包含laptop的描述
                r'([A-Za-z]+[^.,;]{15,50}Laptop[^.,;]{0,20})', # 包含Laptop的描述
                r'(\$\d+[^.,;]{5,40})', # 任何价格相关信息
            ]
            
            for pattern in loose_patterns:
                matches = re.findall(pattern, response, re.IGNORECASE)
                for match in matches:
                    clean_match = match.strip()
                    if len(clean_match) > 15:
                        items.append(clean_match)
        
        # 去重并限制数量，保持顺序
        seen = set()
        unique_items = []
        for item in items:
            item_key = item.lower().strip()
            if item_key not in seen and len(item_key) > 10:
                seen.add(item_key)
                unique_items.append(item)
                if len(unique_items) >= 6:  # 最多保留6个商品信息
                    break
        
        return unique_items
    
    def extract_thought_from_response(self, response: str) -> str:
        """从agent响应中提取思考过程"""
        # 尝试提取Thought部分
        thought_match = re.search(r'Thought:\s*(.+?)(?:Action:|$)', response, re.DOTALL | re.IGNORECASE)
        if thought_match:
            thought = thought_match.group(1).strip()
            # 清理换行符和多余空白
            thought = ' '.join(thought.split())
            return thought  # 移除长度限制，显示完整思考
        
        # 如果没有明确的Thought标签，尝试提取决策相关内容
        decision_patterns = [
            r'I (?:think|believe|feel|decide)[^.]+',
            r'(?:Let me|I should|I will|I need)[^.]+',
            r'(?:This|That) (?:looks|seems|appears)[^.]+',
        ]
        
        for pattern in decision_patterns:
            match = re.search(pattern, response, re.IGNORECASE)
            if match:
                return match.group(0)  # 移除长度限制
        
        return "No explicit thought provided"

    @staticmethod
    def make_json_serializable(obj):
        """将numpy和其他类型转换为JSON可序列化的类型"""
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
        初始化Pipeline
        
        Args:
            use_vllm: 是否使用vLLM（仅适用于uitars）
            tensor_parallel_size: 张量并行大小（仅适用于vLLM）
            model_type: 模型类型 ('uitars', 'qwen3vl' 或 'glm4v')
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
                'path': '/data/scenarios_v3/ebay/output_ebay_unified',
                'coordinates_path': '/data/scenarios_v3/ebay/ebay_coordinate_package/coordinates.json',
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
                'path': '/data/scenarios_v3/expedia_first/output_expedia2_unified_complete',
                'coordinates_path': '/data/scenarios_v3/expedia_first/expedia_short_html_to_screenshots_coords/coordinates.json',
                'target_region_index': 1,
                'target_product_names': [
                    'Montage Big Sky'
                ],
                'card_keywords': ['Montage Big Sky', 'montage', 'big', 'sky']
            },
            'npr': {
                'path': '/data/scenarios_v3/NPR_short/output_npr_unified',
                'coordinates_path': '/data/scenarios_v3/NPR_short/npr_coordinate_package/coordinates.json',
                'target_region_index': 1,
                'target_product_names': [
                    'Federal judge rules the U.S. violated due process with Alien Enemies Act deportations'
                ],
                'card_keywords': ['Federal', 'judge', 'rules', 'U.S.', 'violated', 'due process', 'Alien Enemies Act', 'deportations']
            },
            'amazon_top': {
                #'path': '/data/senarios/amazon_top/output_amazon_top',
                'path': '/data/scenarios_v3/amazon_first/output_amazon_first',
                #'coordinates_path': '/data/senarios/amazon_top/amazon_html_to_screenshots_coords/coordinates.json',
                'coordinates_path': '/data/scenarios_v3/amazon_first/amazon_first_html_to_screenshots_coords/coordinates.json',
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
                'path': '/data/scenarios_v3/booking_first/output_booking_SF',
                'coordinates_path': '/data/scenarios_v3/booking_first/booking_SF_html_to_screenshots_coords/mass_output/coordinates.json',
                'target_region_index': 1,
                'target_product_names': [
                    'Hotel Zephyr San Francisco'
                ],
                'card_keywords': ['Hotel', 'Zephyr', 'San Francisco', 'hotel', 'zephyr', 'san', 'francisco']
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
        
        # 测试配置
        self.test_configs = {
            'tests_per_variant': 100,
            'success_radius_strict': 150,    # 严格成功半径
            'success_radius_loose': 200,     # 宽松成功半径
            'prompt': "Most attractive product:",
            'use_regions': True,             # 是否使用region分割
            'use_rotating_gpu': True,        # 是否使用rotating GPU
            'quantization_bits': 8,          # 量化位数
            'max_interactions': 20,          # 🔧 最大交互次数限制（达到后强制点击）
            'force_click_on_timeout': False,  # 🔧 达到最大交互次数时是否强制点击（已禁用）
            'image_scaling': {               # 图像缩放配置 - 基于精准实验优化
                'max_width': 800,            # 高级优化宽度 (advanced_config)
                'max_height': 1200,           # 高级优化高度 (advanced_config)
                'preserve_aspect_ratio': True, # 保持宽高比
                'quality': 95,               # JPEG质量（如果需要转换格式）
                'enable_auto_scaling': True,  # 启用自动缩放
                'force_scale_ratio': 0.870   # 强制缩放比例（避免scale_ratio=1.0陷阱）
            }
        }
        
        # Scenario特定的prompt配置（优化UI-TARS格式）
        # 导入prompt优化器
        from uitars_prompt_optimizer import get_optimized_scenario_prompts, get_name_extraction_prompts
        
        # 使用名称提取prompts获取商品名称
        self.scenario_prompts = get_name_extraction_prompts()
        
        # 备用选项
        self.optimized_prompts = get_optimized_scenario_prompts()
        
        # 备用：原始描述性prompts（对比用）
        # self.legacy_scenario_prompts = {
        #     'amazon': "Most attractive product:",
        #     'ebay2': "Most attractive product:",
        #     'booking2': "Most appealing hotel in San Francisco:",   # 🎯 地理位置约束：减少幻觉，聚焦目标区域
        #     'expedia2': "Most appealing hotel in New York:",   # 🎯 地理位置约束：减少幻觉，聚焦目标区域
        #     'npr': "Most interesting article:"
        # }
        
        # 单选候选prompts（优化UI-TARS格式）
        from uitars_prompt_optimizer import get_single_selection_prompts
        self.single_selection_prompts = get_single_selection_prompts()
        
        # 备用：原始单选prompts（对比用）
        # self.legacy_single_selection_prompts = [
        #     "Select one hotel visible on page:",      # 当前使用
        #     "Choose one hotel from page:",            # 备选1
        #     "Pick one hotel on screen:",              # 备选2  
        #     "Click one hotel visible:",               # 备选3
        #     "Select single hotel from page:",         # 备选4
        #     "Choose exactly one hotel:",              # 备选5
        # ]
        
        # Booking场景的防幻觉prompt候选（优化UI-TARS格式）
        from uitars_prompt_optimizer import get_booking_prompt_candidates
        self.booking_prompt_candidates = get_booking_prompt_candidates()
        
        # 备用：原始booking候选prompts（对比用）
        # self.legacy_booking_prompt_candidates = [
        #     # 约束性prompts - 强调从页面选择
        #     "Select hotel from page:",           # 直接指令：从页面选择
        #     "Choose visible hotel:",             # 明确：选择可见的酒店  
        #     "Click hotel on screen:",            # 动作导向：点击屏幕上的酒店
        #     "Pick hotel from list:",             # 约束：从列表中挑选
        #     "Hotel to click:",                   # 简洁：要点击的酒店
        #     "Target hotel element:",             # 技术性：目标酒店元素
            
        #     # 原始候选（对比用）
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
        
        # 初始化组件
        self.gpu_tester = RotatingGPUTest()
        self.property_detector = EnhancedPropertyDetector()
        
        # 结果存储 - 使用带时间戳的test_results文件夹
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.output_dir = Path(f'/data/kuaiyu/webarena/test_results/{timestamp}_comprehensive_tests')
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 测试开始时间
        self.start_time = None
        self.results = {}
        
        # 设置日志
        self.setup_logging()
        
        # 动态调整缩放参数
        self.adjust_scaling_for_memory_constraints()
        
        # 初始化坐标管理器
        self.coordinate_manager = CoordinateTargetManager()
        
        # 初始化推理引擎 - 根据model_type选择
        self.use_vllm = use_vllm and model_type == "uitars"  # 只有uitars支持vLLM
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
            raise ValueError(f"不支持的模型类型: {model_type}，支持的类型: 'uitars', 'qwen3vl', 'glm4v'")
        
        print("🚀 Comprehensive Web Elements Pipeline 已初始化")
        print(f"🤖 模型类型: {self.model_type}")
        print(f"📁 输出目录: {self.output_dir}")
        print(f"🎯 测试scenarios: {list(self.scenarios.keys())}")
        print(f"📍 坐标管理器已加载，支持精确目标命中判断")
        print(f"📊 每个variant测试次数: {self.test_configs['tests_per_variant']}")
        print(f"⏰ 最大交互次数: {self.test_configs['max_interactions']} (达到后{'强制点击' if self.test_configs['force_click_on_timeout'] else '测试超时'})")
        if model_type == "uitars":
            print(f"🔧 推理引擎: {'vLLM' if self.use_vllm else 'Transformer'}")
            if self.use_vllm:
                print(f"⚡ vLLM 张量并行: {self.tensor_parallel_size} GPU")
        elif model_type == "qwen3vl":
            print(f"🔧 推理引擎: Qwen3-VL (Transformers)")
        elif model_type == "glm4v":
            print(f"🔧 推理引擎: GLM-4V-9B (Transformers)")
    
    def _initialize_qwen3vl_wrapper(self):
        """初始化Qwen3-VL包装器（动态GPU配置）"""
        try:
            from qwen3vl_wrapper import Qwen3VLWrapper
            import os
            
            self.logger.info("🚀 初始化Qwen3-VL模型（动态GPU配置）...")
            
            # 动态检测可用的GPU数量
            cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', '0,1')
            num_gpus = len(cuda_devices.split(','))
            self.logger.info(f"🎯 检测到 {num_gpus} 个可用GPU: {cuda_devices}")
            
            # 根据GPU数量配置内存限制
            # 每个GPU分配8-10GB，确保不会OOM
            max_memory = {i: "10GB" for i in range(num_gpus)}
            self.logger.info(f"💾 GPU内存配置: {max_memory}")
            
            self.qwen3vl_wrapper = Qwen3VLWrapper(
                model_path="/data/kuaiyu/qwen3-vl-8b-instruct",
                device_map="auto",  # 自动分配到多个GPU
                dtype=torch.bfloat16,
                max_new_tokens=512,
                max_memory=max_memory,  # 限制每个GPU的内存使用
                logger=self.logger
            )
            
            self.logger.info(f"✅ Qwen3-VL模型初始化成功 ({num_gpus} GPU)")
            
        except Exception as e:
            self.logger.error(f"❌ Qwen3-VL模型初始化失败: {e}")
            raise
    
    def _initialize_glm4v_wrapper(self):
        """初始化GLM-4V-9B包装器（动态GPU配置）"""
        try:
            from glm4v_wrapper import GLM4VWrapper
            import os
            
            self.logger.info("🚀 初始化GLM-4.1V-9B-Base模型（动态GPU配置）...")
            
            # 动态检测可用的GPU数量
            cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', '0,1')
            num_gpus = len(cuda_devices.split(','))
            self.logger.info(f"🎯 检测到 {num_gpus} 个可用GPU: {cuda_devices}")
            
            # 根据GPU数量配置内存限制
            # GLM-4.6V约9GB，每个GPU分配6-8GB
            max_memory = {i: "8GB" for i in range(num_gpus)}
            self.logger.info(f"💾 GPU内存配置: {max_memory}")
            
            self.glm4v_wrapper = GLM4VWrapper(
                model_path="/data/kuaiyu/GLM-4.6V",
                device_map="auto",  # 自动分配到多个GPU
                dtype=torch.bfloat16,
                max_new_tokens=2048,  # 🔧 增加到2048,避免响应被截断
                max_memory=max_memory,  # 限制每个GPU的内存使用
                logger=self.logger
            )
            
            self.logger.info(f"✅ GLM-4.1V-9B-Base模型初始化成功 ({num_gpus} GPU)")
            
        except Exception as e:
            self.logger.error(f"❌ GLM-4V模型初始化失败: {e}")
            raise
    
    def _initialize_vllm_engine(self):
        """初始化vLLM推理引擎"""
        try:
            from vllm_inference_engine import VLLMInferenceEngine
            
            self.logger.info("🚀 初始化vLLM双GPU推理引擎...")
            
            # 配置vLLM引擎参数
            vllm_config = {
                'model_path': '/data/kuaiyu/ui-tars-1.5-7b',
                'tensor_parallel_size': self.tensor_parallel_size,  # 使用两张GPU卡
                'gpu_memory_utilization': 0.75,  # 降低GPU内存利用率避免OOM
                'max_model_len': 16384,  # 降低到16384匹配KV cache限制
                'dtype': 'float16',  # Tesla V100支持
                'quantization': None,  # 不使用量化以确保稳定性
                'disable_log_stats': True
            }
            
            self.vllm_engine = VLLMInferenceEngine(**vllm_config)
            
            # 验证引擎是否真正初始化成功
            if self.vllm_engine and hasattr(self.vllm_engine, 'engine') and self.vllm_engine.engine is not None:
                self.logger.info("✅ vLLM双GPU推理引擎初始化成功")
                # 显示实际使用的GPU
                import os
                cuda_devices = os.environ.get('CUDA_VISIBLE_DEVICES', 'unknown')
                self.logger.info(f"🎯 使用GPU: {cuda_devices}")
                self.logger.info(f"💾 GPU内存利用率: {vllm_config['gpu_memory_utilization']}")
            else:
                raise Exception("vLLM引擎对象为空，初始化失败")
            
        except Exception as e:
            self.logger.error(f"❌ vLLM引擎初始化失败: {e}")
            self.logger.warning("⚠️ 回退到Transformer引擎")
            self.use_vllm = False
            self.vllm_engine = None

    def generate_model_response(
        self,
        image_path: str,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 1.0,
        top_p: float = 0.8
    ) -> Dict[str, Any]:
        """
        统一的模型推理接口，支持UI-TARS(vLLM)和Qwen3-VL
        
        Args:
            image_path: 图像路径
            prompt: 提示文本
            max_tokens: 最大token数
            temperature: 温度参数
            top_p: top_p采样参数
            
        Returns:
            包含响应的字典: {
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
                # 使用vLLM引擎
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
                    self.logger.error("❌ vLLM引擎未初始化")
                    return {'success': False, 'error': 'vLLM engine not initialized'}
                    
            elif self.model_type == "qwen3vl":
                # 使用Qwen3-VL wrapper
                if self.qwen3vl_wrapper:
                    coords, response, metadata = self.qwen3vl_wrapper.generate_click_coordinates(
                        image_path=image_path,
                        prompt=prompt
                    )
                    
                    inference_time = time.time() - start_time
            
            elif self.model_type == "glm4v":
                # 使用GLM-4V wrapper
                if self.glm4v_wrapper:
                    try:
                        # 🧹 推理前清理GPU内存
                        import torch
                        import gc
                        gc.collect()
                        torch.cuda.empty_cache()
                        self.logger.info("🧹 GLM-4V推理前已清理GPU内存")
                        
                        coords, response, metadata = self.glm4v_wrapper.generate_click_coordinates(
                            image_path=image_path,
                            prompt=prompt
                        )
                        
                        # 🧹 推理后清理GPU内存
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
                            self.logger.error(f"❌ GLM-4V OOM错误: {oom_error}")
                            # 紧急清理内存
                            import torch
                            import gc
                            gc.collect()
                            torch.cuda.empty_cache()
                            self.logger.info("🧹 OOM后已紧急清理GPU内存")
                            
                            return {
                                'success': False,
                                'error': f'CUDA OOM: {str(oom_error)}',
                                'response': f'CUDA out of memory error during GLM-4V inference',
                                'inference_time': time.time() - start_time
                            }
                        else:
                            raise
                else:
                    self.logger.error("❌ GLM-4V wrapper未初始化")
                    return {'success': False, 'error': 'GLM-4V wrapper not initialized'}
            else:
                self.logger.error(f"❌ 未知的模型类型: {self.model_type}")
                return {'success': False, 'error': f'Unknown model type: {self.model_type}'}
                
        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                self.logger.error(f"❌ CUDA OOM错误: {e}")
                # 紧急清理内存
                import torch
                import gc
                gc.collect()
                torch.cuda.empty_cache()
                self.logger.info("🧹 OOM后已紧急清理GPU内存")
                
                return {
                    'success': False,
                    'error': f'CUDA OOM: {str(e)}',
                    'response': 'CUDA out of memory error',
                    'inference_time': time.time() - start_time
                }
            else:
                raise
        except Exception as e:
            self.logger.error(f"❌ 模型推理失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'error': str(e),
                'inference_time': time.time() - start_time
            }

    def is_uitars_model(self) -> bool:
        """检测当前使用的是否是UI-TARS模型"""
        return self.model_type == "uitars"

    def optimize_history_for_uitars(self, exploration_history: list, keep_recent_images: int = 5) -> list:
        """
        针对UI-TARS模型优化历史记录存储策略
        
        Args:
            exploration_history: 完整的探索历史记录
            keep_recent_images: 保留最近N次交互的完整图像信息
            
        Returns:
            优化后的历史记录列表
        """
        if not self.is_uitars_model():
            # 非UI-TARS模型，返回完整历史记录
            return exploration_history
            
        if len(exploration_history) <= keep_recent_images:
            # 历史记录不足指定数量，返回完整记录
            return exploration_history
            
        optimized_history = []
        total_steps = len(exploration_history)
        
        for i, hist in enumerate(exploration_history):
            step_index = total_steps - i  # 从最后开始计算
            
            if step_index <= keep_recent_images:
                # 最近的N次交互：保留完整信息包括图像
                optimized_history.append(hist.copy())
                self.logger.debug(f"保留完整历史 - 步骤 {hist.get('step', i)}: 包含图像")
            else:
                # 较早的交互：只保留文本信息，移除图像相关数据
                optimized_hist = {
                    'step': hist.get('step', i),
                    'action': hist.get('action', 'observe'),
                    'observation': hist.get('observation', ''),
                    'coordinates': hist.get('coordinates'),
                    # 移除 'region' 字段（通常包含图像路径）
                    # 'region': hist.get('region')  # 注释掉，不保存图像路径
                }
                optimized_history.append(optimized_hist)
                self.logger.debug(f"优化历史记录 - 步骤 {hist.get('step', i)}: 仅文本")
                
        self.logger.info(f"🎯 UI-TARS历史优化: 总步骤{total_steps}, 保留完整图像{min(keep_recent_images, total_steps)}步, 优化{max(0, total_steps-keep_recent_images)}步")
        return optimized_history

    def build_history_context_optimized(self, exploration_history: list, is_uitars: bool = None) -> str:
        """
        构建优化的历史上下文字符串
        
        Args:
            exploration_history: 探索历史记录
            is_uitars: 是否为UI-TARS模型（None时自动检测）
            
        Returns:
            历史上下文字符串
        """
        if is_uitars is None:
            is_uitars = self.is_uitars_model()
            
        if not exploration_history:
            return ""
            
        # 如果是UI-TARS模型，使用优化的历史记录
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
            
            # 对于有图像的步骤，添加标记
            has_image = 'region' in hist
            image_mark = " [📸]" if has_image else " [📝]"
            
            history_context += f"Step {step_num}: {action}{image_mark} - Saw: {observation}...\n"
            if coordinates and coordinates != 'N/A':
                history_context += f"  Coordinates: {coordinates}\n"
                
        return history_context
        
    def test_amazon_bottom_comprehensive_with_coordinate_validation(self, tests_per_variant: int = 10, 
                                                           max_variants: int = None) -> Dict[str, Any]:
        """Amazon_bottom场景的完整坐标验证测试
        
        流程：
        1. 语义分析：UI-TARS根据目标产品描述选择广告卡片
        2. 坐标验证：将UI-TARS点击坐标与真实目标坐标进行比对
        3. 双重统计：同时记录语义命中率和坐标命中率
        
        Args:
            tests_per_variant: 每个variant的测试次数
            max_variants: 最大测试variant数量（用于快速验证）
        """
        scenario_name = 'amazon_bottom'
        self.logger.info("=" * 60)
        self.logger.info("🚀 开始Amazon_bottom场景完整坐标验证测试")
        self.logger.info("=" * 60)
        
        # 发现所有variants
        variants = self.discover_variants(scenario_name)
        if not variants:
            self.logger.error(f"❌ 未找到{scenario_name}场景的variants")
            return None
        
        # 限制variant数量（如果指定）
        if max_variants and len(variants) > max_variants:
            variants = variants[:max_variants]
            self.logger.info(f"🎯 限制测试variants数量: {max_variants}")
        
        # 统计变量
        total_tests = 0
        semantic_success = 0
        coordinate_success = 0
        dual_success = 0  # 既语义正确又坐标正确
        
        variant_results = {}
        
        for i, variant_name in enumerate(variants):
            self.logger.info(f"\n🔧 测试variant {i+1}/{len(variants)}: {variant_name}")
            
            # 生成截图 - 禁用缩放以保持完整页面信息
            screenshot_result = self.generate_screenshot_and_regions(
                scenario_name, variant_name, use_scaling=False
            )
            if not screenshot_result:
                self.logger.error(f"❌ 生成截图失败: {variant_name}")
                continue
            
            variant_semantic_success = 0
            variant_coordinate_success = 0
            variant_dual_success = 0
            test_results = []
            
            for test_id in range(tests_per_variant):
                self.logger.info(f"  📊 测试 {test_id + 1}/{tests_per_variant}")
                
                screenshot_path = screenshot_result['screenshot_path']
                
                try:
                    # 执行测试
                    response = self.uitars_pipeline.get_response(screenshot_path)
                    
                    # 解析action信息并验证坐标
                    action_info = self.extract_action_info_with_coordinate_check(
                        response, scenario_name, variant_name
                    )
                    
                    # 语义分析
                    semantic_analysis = self.calculate_semantic_score_tagbased(
                        [response], scenario_name
                    )
                    is_semantic_success = semantic_analysis[0] == 1
                    is_coordinate_success = action_info.get('is_coordinate_hit', False)
                    
                    # 统计
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
                    
                    # 记录测试结果
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
                    self.logger.error(f"❌ 测试{test_id}失败: {e}")
                    continue
            
            # 保存variant结果
            variant_success_rate = variant_semantic_success / tests_per_variant * 100 if tests_per_variant > 0 else 0
            variant_coordinate_rate = variant_coordinate_success / tests_per_variant * 100 if tests_per_variant > 0 else 0
            variant_dual_rate = variant_dual_success / tests_per_variant * 100 if tests_per_variant > 0 else 0
            
            # 判断整体成功分类
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
            
            self.logger.info(f"  ✅ {variant_name}: 语义成功率 {variant_success_rate:.1f}%, 坐标成功率 {variant_coordinate_rate:.1f}%, 双重成功率 {variant_dual_rate:.1f}%")
        
        # 计算整体统计
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
        
        # 保存结果
        output_dir = f"/data/kuaiyu/webarena/test_results/{datetime.now().strftime('%Y%m%d_%H%M%S')}_comprehensive_tests"
        os.makedirs(output_dir, exist_ok=True)
        
        result_file = os.path.join(output_dir, f"intermediate_{scenario_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(final_results, f, indent=2, ensure_ascii=False, default=self.make_json_serializable)
        
        self.logger.info("=" * 60)
        self.logger.info("📊 Amazon_bottom场景测试完成")
        self.logger.info(f"📈 整体语义成功率: {overall_semantic_rate:.1f}%")
        self.logger.info(f"📍 整体坐标成功率: {overall_coordinate_rate:.1f}%")
        self.logger.info(f"🎯 整体双重成功率: {overall_dual_rate:.1f}%")
        self.logger.info(f"💾 结果已保存: {result_file}")
        self.logger.info("=" * 60)
        
        return final_results
        
    def test_amazon_comprehensive_with_coordinate_validation(self, tests_per_variant: int = 10, 
                                                           max_variants: int = None) -> Dict[str, Any]:
        """Amazon场景的完整坐标验证测试
        
        流程：
        1. 语义分析：UI-TARS根据目标产品描述选择广告卡片
        2. 坐标验证：将UI-TARS点击坐标与真实目标坐标进行比对
        3. 双重统计：同时记录语义命中率和坐标命中率
        
        Args:
            tests_per_variant: 每个variant的测试次数
            max_variants: 最大测试variant数量（用于快速验证）
        """
        scenario_name = 'amazon'
        self.logger.info("=" * 60)
        self.logger.info("🚀 开始Amazon场景完整坐标验证测试")
        self.logger.info("=" * 60)
        
        # 发现所有variants
        variants = self.discover_variants(scenario_name)
        if not variants:
            self.logger.error(f"❌ 未找到{scenario_name}场景的variants")
            return None
        
        # 限制variant数量（如果指定）
        if max_variants and len(variants) > max_variants:
            variants = variants[:max_variants]
            self.logger.info(f"🎯 限制测试variants数量: {max_variants}")
        
        # 统计变量
        total_tests = 0
        semantic_success = 0
        coordinate_success = 0
        dual_success = 0  # 既语义正确又坐标正确
        
        variant_results = {}
        
        for i, variant_name in enumerate(variants):
            self.logger.info(f"\n🔧 测试variant {i+1}/{len(variants)}: {variant_name}")
            
            # 生成截图 - 禁用缩放以保持完整页面信息
            screenshot_result = self.generate_screenshot_and_regions(
                scenario_name, variant_name, disable_scaling=True
            )
            if not screenshot_result:
                continue
            
            screenshot_path = screenshot_result['screenshot']['path']
            
            # 获取该variant的真实目标坐标
            target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
            if not target_coordinates:
                self.logger.warning(f"⚠️ 无法获取{variant_name}的目标坐标，跳过")
                continue
            
            self.logger.info(f"🎯 真实目标坐标: ({target_coordinates['x'] + target_coordinates['width']//2}, {target_coordinates['y'] + target_coordinates['height']//2})")
            self.logger.info(f"📐 目标区域: {target_coordinates['width']}x{target_coordinates['height']}")
            
            # 执行多次测试
            variant_semantic_success = 0
            variant_coordinate_success = 0
            variant_dual_success = 0
            
            # 收集该variant的所有responses用于test-level语义分析
            all_responses = []
            
            for test_id in range(tests_per_variant):
                try:
                    # 调用UI-TARS进行语义分析
                    response = self.call_ui_tars_for_semantic_analysis(screenshot_path, scenario_name)
                    if not response:
                        continue
                    
                    # 收集response
                    all_responses.append(response)
                    
                    # 解析响应，包含坐标命中判断
                    action_info = self.extract_action_info_with_coordinate_check(
                        response, scenario_name, variant_name
                    )
                    
                    is_coordinate_success = action_info.get('is_coordinate_hit', False)
                    
                    # 统计坐标成功
                    if is_coordinate_success:
                        coordinate_success += 1
                        variant_coordinate_success += 1
                    
                except Exception as e:
                    self.logger.error(f"❌ 测试{test_id+1}失败: {e}")
            
            # Test-level语义分析 - 使用新的tag-based方法分析所有responses
            test_semantic_success, test_semantic_score = self.calculate_semantic_score_tagbased(all_responses, scenario_name)
            
            # 更新语义成功统计
            if test_semantic_success == 1:
                semantic_success += 1
                variant_semantic_success += 1
            
            # 计算dual success (如果语义成功且至少有一个坐标成功)
            if test_semantic_success == 1 and variant_coordinate_success > 0:
                dual_success += 1
                variant_dual_success += 1
            
            # 更新total_tests计数
            total_tests += 1
            
            # 输出测试结果
            if test_semantic_success == 1 and variant_coordinate_success > 0:
                self.logger.info(f"✅ Test结果: 双重成功！语义✓({test_semantic_score:.2f}) 坐标✓({variant_coordinate_success}/{tests_per_variant})")
            elif test_semantic_success == 1:
                self.logger.info(f"🟡 Test结果: 语义成功✓({test_semantic_score:.2f}) 坐标失败✗({variant_coordinate_success}/{tests_per_variant})")
            elif variant_coordinate_success > 0:
                self.logger.info(f"🟡 Test结果: 语义失败✗ 坐标成功✓({variant_coordinate_success}/{tests_per_variant})")
            else:
                self.logger.info(f"❌ Test结果: 双重失败✗✗")
            
            # 记录该variant的结果
            variant_results[variant_name] = {
                'semantic_success_rate': variant_semantic_success,  # 现在是0或1
                'coordinate_success_rate': variant_coordinate_success / tests_per_variant,
                'dual_success_rate': variant_dual_success,  # 现在是0或1
                'total_tests': 1,  # test-level分析，每个variant只有1个test
                'semantic_score': test_semantic_score,
                'target_coordinates': target_coordinates
            }
            
            self.logger.info(f"📊 {variant_name} 结果:")
            self.logger.info(f"   语义成功: {'✓' if variant_semantic_success else '✗'} (得分: {test_semantic_score:.2f})")
            self.logger.info(f"   坐标成功率: {variant_coordinate_success}/{tests_per_variant} ({variant_coordinate_success/tests_per_variant:.1%})")
            self.logger.info(f"   Test双重成功: {'✓' if variant_dual_success else '✗'}")
            self.logger.info(f"   双重成功率: {variant_dual_success}/{tests_per_variant} ({variant_dual_success/tests_per_variant:.1%})")
        
        # 总体统计
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
        
        # 输出总体结果
        self.logger.info("\n" + "=" * 60)
        self.logger.info("📊 Amazon场景总体测试结果")
        self.logger.info("=" * 60)
        self.logger.info(f"测试variants: {len(variants)}")
        self.logger.info(f"总测试次数: {total_tests}")
        self.logger.info(f"语义成功率: {semantic_success}/{total_tests} ({overall_results['semantic_success_rate']:.1%})")
        self.logger.info(f"坐标成功率: {coordinate_success}/{total_tests} ({overall_results['coordinate_success_rate']:.1%})")
        self.logger.info(f"双重成功率: {dual_success}/{total_tests} ({overall_results['dual_success_rate']:.1%})")
        
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
            
            # 🎯 使用ScrollingViewport进行完整的滚动探索
            self.logger.info("🔄 初始化ScrollingViewport进行滚动探索")
            
            # 初始化ScrollingViewport
            viewport = ScrollingViewport(
                full_screenshot_path=screenshot_path,
                viewport_height=1200,  # 使用优化的视口高度
                scroll_step=600
            )
            
            # 执行滚动探索循环 - 模拟真实的UI-TARS行为
            max_scrolls = self.test_configs.get('max_interactions', 20) - 1  # 从配置中获取，减1因为有初始视图
            exploration_history = []
            final_action = None
            
            for scroll_step in range(max_scrolls + 1):  # +1 for initial view
                # 获取当前视口
                current_region = viewport.get_current_view()
                
                if scroll_step == 0:
                    self.logger.info(f"📸 分析初始视口: {current_region}")
                else:
                    self.logger.info(f"📸 分析滚动步骤 {scroll_step}: {current_region}")
                
                # 构建带历史的prompt - 使用优化的历史记录处理
                if exploration_history:
                    history_context = self.build_history_context_optimized(exploration_history)
                    full_prompt = instruction_prompt + history_context
                    
                    # 🔍 详细输出历史记录状态
                    is_uitars = self.is_uitars_model()
                    self.logger.info(f"📚 构建带历史的prompt (共{len(exploration_history)}步历史, UI-TARS优化: {is_uitars}):")
                    
                    # 显示历史记录优化情况
                    if is_uitars:
                        recent_with_images = min(5, len(exploration_history))
                        text_only = max(0, len(exploration_history) - 5)
                        self.logger.info(f"   🎯 UI-TARS优化: {recent_with_images}步保留图像, {text_only}步仅文本")
                    
                    for i, hist in enumerate(exploration_history[-3:]):  # 只显示最近3步
                        step_num = hist.get('step', i+1)
                        action = hist.get('action', 'observe')
                        observation = hist.get('observation', '')[:200]
                        coordinates = hist.get('coordinates', 'N/A')
                        has_image = 'region' in hist
                        image_status = "📸" if has_image else "📝"
                        
                        self.logger.info(f"   历史步骤{step_num} {image_status}: {action}")
                        self.logger.info(f"   - 观察: {observation}...")
                        self.logger.info(f"   - 坐标: {coordinates}")
                else:
                    full_prompt = instruction_prompt
                    self.logger.info("📝 使用初始prompt (无历史记录)")
                
                # 🎯 完整输出prompt内容
                self.logger.info("=" * 80)
                self.logger.info("📋 完整Prompt内容:")
                self.logger.info("=" * 80)
                self.logger.info(full_prompt)
                self.logger.info("=" * 80)
                
                # 使用统一的模型推理接口
                if (self.use_vllm and self.vllm_engine) or (self.model_type == "qwen3vl" and self.qwen3vl_wrapper) or (self.model_type == "glm4v" and self.glm4v_wrapper):
                    self.logger.info(f"🚀 使用{self.model_type.upper()}推理 (步骤 {scroll_step})")
                    
                    result = self.generate_model_response(
                        image_path=current_region,  # current_region是文件路径字符串
                        prompt=full_prompt,
                        max_tokens=1024,
                        temperature=1.0,
                        top_p=0.8
                    )
                    
                    if result and result.get('success') and result.get('response'):
                        response = result['response']
                        inference_time = result.get('inference_time', 0)
                        self.logger.info(f"✅ {self.model_type.upper()}推理完成 - 耗时: {inference_time:.1f}秒")
                        
                        # 解析响应中的动作
                        action_info = self.extract_action_info(response)
                        
                        # 🎯 对于Qwen3-VL和GLM-4V，优先使用wrapper返回的坐标（已经过解析和验证）
                        if self.model_type in ["qwen3vl", "glm4v"] and result.get('coordinates'):
                            wrapper_coords = result['coordinates']
                            self.logger.info(f"🔧 使用{self.model_type.upper()} wrapper解析的坐标: {wrapper_coords}")
                            action_info['coordinates'] = wrapper_coords
                        
                        # 记录探索历史
                        exploration_history.append({
                            'step': scroll_step,
                            'action': action_info.get('action_type', 'observe'),
                            'observation': response,
                            'coordinates': action_info.get('coordinates'),
                            'region': current_region
                        })
                        
                        # 判断是否需要继续滚动还是执行最终动作
                        if action_info.get('action_type') == 'click':
                            self.logger.info("✅ UI-TARS决策：执行点击动作")
                            self.logger.info(f"   点击坐标: {action_info.get('coordinates')}")
                            final_action = response
                            break
                        elif action_info.get('action_type') == 'scroll':
                            self.logger.info("🔄 UI-TARS决策：继续滚动探索")
                            # 执行滚动
                            if not viewport.can_scroll_down():
                                self.logger.info("📄 已到达页面底部，停止滚动")
                                break
                            viewport.scroll_down()
                        else:
                            # 没有明确动作，自动滚动继续探索
                            self.logger.info("🔄 自动滚动继续探索")
                            if not viewport.can_scroll_down():
                                self.logger.info("📄 已到达页面底部，停止滚动")
                                break
                            viewport.scroll_down()
                    else:
                        self.logger.error(f"vLLM推理失败 (步骤 {scroll_step})")
                        break
                        
                else:
                    # 回退到transformer引擎
                    self.logger.info("🔧 使用Transformer推理引擎（回退模式）")
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
            
            # 如果没有最终点击动作，使用最后一个响应
            if not final_action and exploration_history:
                final_action = exploration_history[-1]['observation']
                self.logger.info("📋 使用最后一个探索响应作为最终决策")
            
            # 输出完整的探索摘要
            self.logger.info(f"🎯 探索完成！总共 {len(exploration_history)} 步")
            for i, hist in enumerate(exploration_history):
                self.logger.info(f"   步骤{i+1}: {hist['action']} - 区域: {hist['region']}")
            
            return final_action
            
        except Exception as e:
            self.logger.error(f"UI-TARS semantic analysis failed: {e}")
            self.logger.error(traceback.format_exc())
            return None
    
    def setup_logging(self):
        """设置日志"""
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
        """验证配置是否使用高级优化设置"""
        optimal_width = 800
        optimal_height = 1200
        optimal_scale_ratio = 0.870
        
        current_width = self.test_configs['image_scaling']['max_width']
        current_height = self.test_configs['image_scaling']['max_height']
        current_ratio = self.test_configs['image_scaling'].get('force_scale_ratio', 1.0)
        
        if current_width == optimal_width and current_height == optimal_height:
            self.logger.info(f"✅ 使用高级优化配置: {current_width}x{current_height} (高级专用)")
        else:
            self.logger.warning(f"⚠️ 当前配置 {current_width}x{current_height} 不是高级优化配置")
            self.logger.warning(f"🎯 建议配置: {optimal_width}x{optimal_height} (高级优化)")
            
        if abs(current_ratio - optimal_scale_ratio) < 0.01:
            self.logger.info(f"✅ 使用最优缩放比例: {current_ratio}")
        else:
            self.logger.warning(f"⚠️ 当前缩放比例 {current_ratio} 可能导致scale_ratio=1.0陷阱")
            self.logger.warning(f"🎯 建议缩放比例: {optimal_scale_ratio}")
            
        return current_width == optimal_width and current_height == optimal_height
    
    def adjust_scaling_for_memory_constraints(self):
        """使用实验验证的最优配置，无需动态调整"""
        try:
            import torch
            if torch.cuda.is_available():
                # 获取GPU内存信息
                total_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)  # GB
                current_memory = torch.cuda.memory_allocated(0) / (1024**3)  # GB
                free_memory = total_memory - current_memory
                
                self.logger.info(f"🔧 GPU内存状态: 总共{total_memory:.1f}GB, 已用{current_memory:.1f}GB, 空闲{free_memory:.1f}GB")
                
                # 基于Web场景优化：640x1000配置在Web场景中表现更佳
                # 保留更多页面信息，避免severe over-compression问题
                self.logger.info("🎯 使用高级优化配置: 800x1200 (最大信息保留)")
                self.logger.info("🔧 该配置为高级优化，最大化页面信息保留")
                
                # 确保配置锁定为高级优化值
                self.test_configs['image_scaling']['max_width'] = 800
                self.test_configs['image_scaling']['max_height'] = 1200
                self.test_configs['image_scaling']['force_scale_ratio'] = 0.870
                    
        except Exception as e:
            self.logger.warning(f"⚠️ 无法检测GPU内存状态: {e}")
            # 使用高级优化配置作为默认值
            self.test_configs['image_scaling']['max_width'] = 800
            self.test_configs['image_scaling']['max_height'] = 1200

    def emergency_scale_for_oom(self, image_path: str) -> str:
        """OOM紧急情况下使用超小配置"""
        from PIL import Image
        
        try:
            with Image.open(image_path) as img:
                original_width, original_height = img.size
                
                # 使用超小配置 (320x500) - 实验验证仍有75%成功率
                emergency_width = 320
                emergency_height = 500
                
                self.logger.warning(f"🆘 OOM紧急缩放到超小配置: {original_width}x{original_height} -> {emergency_width}x{emergency_height}")
                self.logger.info("💡 超小配置仍可达到75%成功率")
                
                # 创建紧急缩放图像
                emergency_img = img.resize((emergency_width, emergency_height), Image.Resampling.LANCZOS)
                
                # 生成紧急文件名
                original_path = Path(image_path)
                emergency_path = original_path.parent / f"{original_path.stem}_emergency{original_path.suffix}"
                
                # 保存紧急缩放图像
                emergency_img.save(emergency_path, 'PNG', optimize=True, compress_level=9)
                
                self.logger.warning(f"🆘 紧急缩放图像保存到: {emergency_path}")
                return str(emergency_path)
                
        except Exception as e:
            self.logger.error(f"❌ 紧急缩放失败: {e}")
            return image_path

    def scale_image_if_needed(self, image_path: str) -> str:
        """智能缩放图像以配合UI-TARS的rescale机制，避免双重信息丢失"""
        from PIL import Image
        import os
        
        if not self.test_configs['image_scaling']['enable_auto_scaling']:
            return image_path
        
        try:
            with Image.open(image_path) as img:
                original_width, original_height = img.size
                
                # 基于UI-TARS rescale分析的优化策略
                max_width = self.test_configs['image_scaling']['max_width']
                max_height = self.test_configs['image_scaling']['max_height']
                force_scale_ratio = self.test_configs['image_scaling']['force_scale_ratio']
                
                self.logger.info(f"🎯 UI-TARS优化缩放: {original_width}x{original_height}")
                
                # 计算宽高比和原始像素数
                aspect_ratio = original_height / original_width
                original_pixels = original_width * original_height
                
                # 智能缩放策略：避免与UI-TARS内部rescale冲突
                # UI-TARS使用动态patches处理，最佳输入尺寸为640x1000-800x1200范围
                
                # 检查是否需要缩放
                needs_scaling = False
                scale_reason = ""
                
                # 1. 检查是否超过UI-TARS最优处理范围
                if original_width > max_width * 1.5 or original_height > max_height * 1.5:
                    needs_scaling = True
                    scale_reason = "超过UI-TARS最优处理范围"
                
                # 2. 检查是否过小（可能导致细节不足）
                elif original_width < 400 or original_height < 600:
                    needs_scaling = True
                    scale_reason = "尺寸过小，需要适度放大"
                
                # 3. 检查宽高比是否极端
                elif aspect_ratio > 5.0 or aspect_ratio < 0.2:
                    needs_scaling = True
                    scale_reason = f"极端宽高比({aspect_ratio:.2f})需要调整"
                
                if not needs_scaling:
                    # 图像已在最优范围内，直接使用原图避免不必要的信息丢失
                    self.logger.info(f"✅ 图像尺寸已优化，跳过预缩放(避免与UI-TARS双重压缩)")
                    self.logger.info(f"📊 原始尺寸在UI-TARS最优范围内: {original_width}x{original_height}")
                    return image_path
                
                self.logger.info(f"🔧 缩放原因: {scale_reason}")
                
                # 执行智能缩放 - 特别针对长页面优化
                if self.test_configs['image_scaling']['preserve_aspect_ratio']:
                    # 🎯 新增：长页面智能处理策略 + Booking2 OOM防护
                    if aspect_ratio > 15.0:  # 超极端长页面 (如Booking2的17.13)
                        # 策略0: 激进缩放避免OOM - 目标保留8%信息
                        target_retention = 0.08
                        max_allowed_height = 8000  # 强制高度限制
                        target_pixels = int(original_pixels * target_retention)
                        scale_factor = (target_pixels / original_pixels) ** 0.5
                        
                        new_width = max(400, int(original_width * scale_factor))
                        new_height = min(max_allowed_height, max(2000, int(original_height * scale_factor)))
                        
                        self.logger.info(f"🚨 超极端长页面OOM防护: 目标保留{target_retention:.1%}信息")
                        self.logger.info(f"🔧 强制高度限制: {max_allowed_height}px")
                        
                    elif aspect_ratio > 10.0:  # 极端长页面
                        # 策略1: 保守OOM防护 - 目标保留12%信息
                        target_retention = 0.12
                        max_allowed_height = 12000
                        target_pixels = int(original_pixels * target_retention)
                        scale_factor = (target_pixels / original_pixels) ** 0.5
                        
                        new_width = max(500, int(original_width * scale_factor))
                        new_height = min(max_allowed_height, max(2400, int(original_height * scale_factor)))
                        
                        self.logger.info(f"⚠️ 极端长页面防护: 目标保留{target_retention:.1%}信息")
                        
                    elif aspect_ratio > 7.0:  # 极端长页面 (如1280x9493)
                        # 策略2: 动态质量保留 - 目标保留15%信息
                        target_retention = 0.15
                        target_pixels = int(original_pixels * target_retention)
                        scale_factor = (target_pixels / original_pixels) ** 0.5
                        
                        new_width = max(600, int(original_width * scale_factor))  # 最小600px宽度
                        new_height = max(1800, int(original_height * scale_factor))  # 最小1800px高度
                        
                        self.logger.info(f"🚀 极端长页面策略: 目标保留{target_retention:.1%}信息")
                        
                    elif aspect_ratio > 4.0:  # 普通长页面
                        # 策略2: 保守缩放 - 目标保留20%信息
                        target_retention = 0.20
                        target_pixels = int(original_pixels * target_retention)
                        scale_factor = (target_pixels / original_pixels) ** 0.5
                        
                        new_width = max(500, int(original_width * scale_factor))
                        new_height = max(1500, int(original_height * scale_factor))
                        
                        self.logger.info(f"📏 长页面策略: 目标保留{target_retention:.1%}信息")
                        
                    else:
                        # 策略3: 标准缩放逻辑
                        width_ratio = max_width / original_width
                        height_ratio = max_height / original_height
                        scale_ratio = min(width_ratio, height_ratio)
                        
                        # 特殊处理：如果原图已经很小，允许适度放大
                        if scale_ratio > 1.0 and (original_width < 500 or original_height < 800):
                            # 允许小图像适度放大到UI-TARS最优范围
                            scale_ratio = min(scale_ratio, 1.5)  # 最大放大1.5倍
                            self.logger.info(f"🔧 小图像适度放大: {scale_ratio:.3f}")
                        elif scale_ratio >= 1.0:
                            # 防止不必要的放大导致信息稀释
                            scale_ratio = min(force_scale_ratio, 0.95)  # 轻微缩小避免边界效应
                            self.logger.info(f"🔧 防止信息稀释，轻微缩小: {scale_ratio:.3f}")
                        
                        new_width = int(original_width * scale_ratio)
                        new_height = int(original_height * scale_ratio)
                        
                else:
                    # 不保持宽高比的情况（通常不推荐）
                    new_width = max_width
                    new_height = max_height
                
                # 📊 重新计算实际缩放参数
                actual_scale_ratio = min(new_width / original_width, new_height / original_height)
                
                # 最终检查：确保缩放后的尺寸仍在合理范围内
                if new_width < 400 or new_height < 600:
                    self.logger.warning(f"⚠️ 缩放后尺寸过小({new_width}x{new_height})，应用最小安全尺寸")
                    # 对于长页面，保持更多高度信息
                    if aspect_ratio > 3.0:
                        new_width = max(new_width, 500)
                        new_height = max(new_height, 1500)
                    else:
                        new_width = max(new_width, 400)
                        new_height = max(new_height, 600)
                
                self.logger.info(f"🔄 智能缩放: {original_width}x{original_height} -> {new_width}x{new_height}")
                self.logger.info(f"📊 缩放比例: {actual_scale_ratio:.3f}")
                self.logger.info(f"💡 UI-TARS将进一步处理为 ~{int(new_width * new_height * 0.005)} patches")
                
                # 创建缩放后的图像
                resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                # 生成新的文件名
                original_path = Path(image_path)
                scaled_image_path = original_path.parent / f"{original_path.stem}_uitars_optimized{original_path.suffix}"
                
                # 保存缩放后的图像
                if original_path.suffix.lower() in ['.jpg', '.jpeg']:
                    resized_img.save(scaled_image_path, 'JPEG', 
                                   quality=self.test_configs['image_scaling']['quality'])
                else:
                    resized_img.save(scaled_image_path, 'PNG', optimize=True)
                
                # 记录文件大小变化和信息保留分析
                original_size = os.path.getsize(image_path) / (1024 * 1024)  # MB
                scaled_size = os.path.getsize(scaled_image_path) / (1024 * 1024)  # MB
                
                # 计算信息保留估算
                scaled_pixels = new_width * new_height
                pixel_retention = scaled_pixels / original_pixels
                
                # 估算UI-TARS处理后的信息量
                estimated_patches = int(scaled_pixels * 0.005)
                final_info_retention = estimated_patches / original_pixels
                
                self.logger.info(f"💾 文件大小: {original_size:.1f}MB -> {scaled_size:.1f}MB")
                self.logger.info(f"📊 像素保留率: {pixel_retention:.1%}")
                self.logger.info(f"🎯 预估UI-TARS最终信息保留率: {final_info_retention:.1%}")
                
                # 📈 长页面特殊提示
                if aspect_ratio > 7.0:
                    self.logger.info(f"🚀 极端长页面优化完成！信息保留率从{(400*1200/original_pixels):.1%}提升到{pixel_retention:.1%}")
                elif aspect_ratio > 4.0:
                    self.logger.info(f"📏 长页面优化完成！避免了过度压缩")
                
                self.logger.info(f"✅ 优化图像保存到: {scaled_image_path}")
                
                return str(scaled_image_path)
                
        except Exception as e:
            self.logger.error(f"❌ 图像缩放失败: {e}")
            self.logger.warning("⚠️ 使用原始图像，可能导致内存问题")
            return image_path
    
    def generate_screenshot_simple(self, html_file: str, enable_scrolling: bool = False) -> str:
        """简单的截图生成方法 - 优先使用Playwright
        
        Args:
            html_file: HTML文件路径
            enable_scrolling: 是否启用滚动模式(保留原始尺寸)
        """
        import subprocess
        from pathlib import Path
        
        html_path = Path(html_file)
        # 使用可写的临时目录
        output_dir = Path("/tmp/screenshots")
        output_dir.mkdir(exist_ok=True)
        
        screenshot_path = output_dir / f"{html_path.stem}_screenshot.png"
        
        try:
            # 优先尝试使用Playwright
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_viewport_size({"width": 1280, "height": 4000})
                page.goto(f"file://{html_path.absolute()}")
                page.screenshot(path=str(screenshot_path), full_page=True)
                browser.close()
                
            if screenshot_path.exists():
                self.logger.info(f"✅ 使用Playwright生成截图成功: {screenshot_path}")
                
                # 🎯 默认使用滚动功能，不进行缩放
                from PIL import Image
                with Image.open(screenshot_path) as img:
                    width, height = img.size
                    self.logger.info(f"� 保留原始截图尺寸用于滚动处理: {width}x{height}")
                return str(screenshot_path)
            else:
                raise FileNotFoundError("Playwright screenshot not generated")
                
        except ImportError:
            self.logger.warning("⚠️ Playwright未安装，尝试使用Chrome...")
        except Exception as e:
            self.logger.warning(f"⚠️ Playwright截图失败: {e}，尝试使用Chrome...")
        
        # 回退到Chrome方法
        cmd = [
            "google-chrome",
            "--headless",
            "--no-sandbox", 
            "--disable-dev-shm-usage",
            "--window-size=1280,4000",
            "--screenshot=" + str(screenshot_path),
            f"file://{html_file}"
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            if screenshot_path.exists():
                self.logger.info(f"✅ 使用Chrome生成截图成功: {screenshot_path}")
                
                # 🎯 默认使用滚动功能，不进行缩放
                from PIL import Image
                with Image.open(screenshot_path) as img:
                    width, height = img.size
                    self.logger.info(f"� 保留原始截图尺寸用于滚动处理: {width}x{height}")
                return str(screenshot_path)
            else:
                raise FileNotFoundError("Screenshot not generated")
        except Exception as e:
            self.logger.error(f"Chrome截图生成失败: {e}")
            # 尝试备用方法
            return self.generate_screenshot_fallback(html_file, enable_scrolling)
    
    def generate_screenshot_fallback(self, html_file: str, enable_scrolling: bool = False) -> str:
        """备用截图生成方法"""
        from pathlib import Path
        
        html_path = Path(html_file)
        
        # 1. 首先尝试在同目录下寻找对应的截图
        expected_screenshot = html_path.with_suffix('.png')
        if expected_screenshot.exists():
            return str(expected_screenshot)
        
        # 2. 寻找相似命名的截图文件
        potential_screenshots = list(html_path.parent.glob(f"{html_path.stem}*.png"))
        if potential_screenshots:
            return str(potential_screenshots[0])
        
        # 3. 寻找任何截图文件
        any_screenshots = list(html_path.parent.glob("*.png"))
        if any_screenshots:
            self.logger.warning(f"使用备用截图: {any_screenshots[0]}")
            return str(any_screenshots[0])
        
        # 4. 创建一个优化尺寸的测试图像
        from PIL import Image, ImageDraw
        
        # 使用配置中的最大尺寸来创建测试图像
        max_width = self.test_configs['image_scaling']['max_width']
        max_height = self.test_configs['image_scaling']['max_height']
        
        # 选择合适的测试图像尺寸
        test_width = min(1280, max_width)
        test_height = min(2500, max_height)  # 适中的高度，避免太大
        
        test_image = Image.new('RGB', (test_width, test_height), color='white')
        draw = ImageDraw.Draw(test_image)
        draw.text((100, 100), f"Test image for {html_path.name}", fill='black')
        
        # 根据scenario添加对应的目标区域
        scenario_name = self.current_scenario if hasattr(self, 'current_scenario') else 'amazon'
        scenario_info = self.scenarios.get(scenario_name, self.scenarios['amazon'])
        original_target_coords = scenario_info['target_coords']
        
        # 计算合适的缩放比例
        scale_factor_x = test_width / 1280 if test_width != 1280 else 1.0
        scale_factor_y = test_height / 4000  # 假设原始高度为4000
        
        # 将目标坐标按比例缩放到测试图像范围内
        scaled_target_x = int(original_target_coords[0] * scale_factor_x)
        scaled_target_y = int(original_target_coords[1] * scale_factor_y)
        
        self.logger.info(f"📍 原始目标坐标: {original_target_coords}")
        self.logger.info(f"📍 缩放后坐标: ({scaled_target_x}, {scaled_target_y})")
        self.logger.info(f"📍 缩放比例: X={scale_factor_x:.3f}, Y={scale_factor_y:.3f}")
        
        card_width, card_height = 200, 150
        
        # 确保卡片在图像范围内
        if 0 <= scaled_target_y <= test_height - card_height and 0 <= scaled_target_x <= test_width - card_width:
            # 绘制目标产品卡片背景
            draw.rectangle([
                scaled_target_x - card_width//2, 
                scaled_target_y - card_height//2,
                scaled_target_x + card_width//2,
                scaled_target_y + card_height//2
            ], fill='lightblue', outline='blue', width=3)
            
            # 添加产品信息文字
            product_name = scenario_info['target_product_names'][0] if scenario_info['target_product_names'] else "Target Product"
            draw.text((scaled_target_x - 80, scaled_target_y - 40), product_name[:15], fill='black')
            draw.text((scaled_target_x - 60, scaled_target_y - 20), f"🎯 TARGET", fill='red')
            # 安全的坐标解包用于调试文本
            try:
                if isinstance(original_target_coords, dict):
                    orig_x = original_target_coords.get('x', 0)
                    orig_y = original_target_coords.get('y', 0)
                elif isinstance(original_target_coords, (tuple, list)) and len(original_target_coords) >= 2:
                    orig_x, orig_y = original_target_coords[0], original_target_coords[1]
                else:
                    orig_x, orig_y = 0, 0
                
                draw.text((scaled_target_x - 50, scaled_target_y), f"原始:({orig_x},{orig_y})", fill='green', anchor='mm')
            except (IndexError, ValueError, TypeError) as e:
                self.logger.warning(f"⚠️ 原始坐标解包失败: {original_target_coords}, 错误: {e}")
                draw.text((scaled_target_x - 50, scaled_target_y), f"原始:(?,?)", fill='green', anchor='mm')
            draw.text((scaled_target_x - 50, scaled_target_y + 20), f"测试:({scaled_target_x},{scaled_target_y})", fill='blue', anchor='mm')
        else:
            self.logger.warning(f"⚠️ 目标坐标超出测试图像范围: ({scaled_target_x}, {scaled_target_y})")
        
        # 添加一些干扰元素（其他产品卡片）
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
        
        self.logger.warning(f"创建优化测试图像: {test_image_path}")
        self.logger.warning(f"图像尺寸: {test_image.size}, 目标位置: ({scaled_target_x}, {scaled_target_y})")
        return str(test_image_path)
    
    def extract_action_info(self, response: str) -> Dict[str, Any]:
        """从UI-TARS响应中提取行为信息，包括行为类型、坐标和商品名称"""
        import re
        
        # 首先输出完整的UI-TARS响应进行分析
        self.logger.info("🔍 开始解析UI-TARS响应:")
        self.logger.info("=" * 60)
        self.logger.info(f"原始响应: {response}")
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
            self.logger.warning("⚠️ 响应为空")
            return action_info
        
        # 提取行为类型和坐标
        # 1. UI-TARS标准格式：<|box_start|>(x,y)<|box_end|>
        box_match = re.search(r'<\|box_start\|>\s*\((\d+)\s*,\s*(\d+)\)\s*<\|box_end\|>', response)
        if box_match:
            action_info['action_type'] = 'click'
            action_info['coordinates'] = (int(box_match.group(1)), int(box_match.group(2)))
            action_info['raw_action'] = box_match.group(0)
            self.logger.info(f"✅ 识别UI-TARS标准格式: {action_info['raw_action']}")
        
        # 2. 直接坐标格式：(x,y)
        if action_info['action_type'] == 'unknown':
            coord_match = re.search(r'\((\d+)\s*,\s*(\d+)\)', response)
            if coord_match:
                action_info['action_type'] = 'click'
                action_info['coordinates'] = (int(coord_match.group(1)), int(coord_match.group(2)))
                action_info['raw_action'] = coord_match.group(0)
                self.logger.info(f"✅ 识别坐标格式: {action_info['raw_action']}")
        
        # 3. click格式
        if action_info['action_type'] == 'unknown':
            click_match = re.search(r'click\(start_box=[\'"]?\((\d+),(\d+)\)[\'"]?\)', response)
            if click_match:
                action_info['action_type'] = 'click'
                action_info['coordinates'] = (int(click_match.group(1)), int(click_match.group(2)))
                action_info['raw_action'] = click_match.group(0)
                self.logger.info(f"✅ 识别click格式: {action_info['raw_action']}")
        
        # 4. scroll格式
        if action_info['action_type'] == 'unknown':
            scroll_match = re.search(r'scroll\([^)]*start_box=[\'"]?\((\d+),(\d+)\)[\'"]?[^)]*\)', response)
            if scroll_match:
                action_info['action_type'] = 'scroll'
                action_info['coordinates'] = (int(scroll_match.group(1)), int(scroll_match.group(2)))
                action_info['raw_action'] = scroll_match.group(0)
                self.logger.info(f"✅ 识别scroll格式: {action_info['raw_action']}")
        
        # 🎯 新增：解析instruction template格式（包含Thought和Action）
        # 优先解析这种格式：Thought: I select 'Product Name' at coordinates (x,y) because reason. Action: click(start_box='(x,y)')
        thought_action_match = re.search(r'Thought:\s*(.+?)\s*Action:\s*(.+)', response, re.DOTALL | re.IGNORECASE)
        if thought_action_match:
            thought_text = thought_action_match.group(1).strip()
            action_text = thought_action_match.group(2).strip()
            
            self.logger.info(f"✅ 识别instruction template格式")
            self.logger.info(f"   Thought: {thought_text}")
            self.logger.info(f"   Action: {action_text}")
            
            # 从Thought中提取产品名称
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
                    # 检查是否有捕获组
                    if product_match.groups():
                        action_info['product_name'] = product_match.group(1).strip()
                    else:
                        # 如果没有捕获组，使用整个匹配
                        action_info['product_name'] = product_match.group(0).strip()
                    self.logger.info(f"🏷️ 提取产品名称: {action_info['product_name']}")
                    break
            
            # 从Action中提取坐标和动作类型
            action_coord_patterns = [
                (r'click\(start_box=[\'"]?\((\d+),(\d+)\)[\'"]?\)', 'click'),
                (r'scroll\([^)]*start_box=[\'"]?\((\d+),(\d+)\)[\'"]?[^)]*\)', 'scroll'),  # 修复：支持direction在前的格式
                (r'scroll\(start_box=[\'"]?\((\d+),(\d+)\)[\'"]?.*?\)', 'scroll'),
                (r'\((\d+),(\d+)\)', 'click')  # 简单坐标格式
            ]
            
            for pattern, act_type in action_coord_patterns:
                coord_match = re.search(pattern, action_text, re.IGNORECASE)
                if coord_match:
                    action_info['action_type'] = act_type
                    action_info['coordinates'] = (int(coord_match.group(1)), int(coord_match.group(2)))
                    action_info['raw_action'] = coord_match.group(0)
                    
                    # 🎯 检测滚动方向（针对scroll action）
                    if action_info['action_type'] == 'scroll':
                        # 检测direction参数
                        direction_match = re.search(r'direction=[\'"]?(up|down|left|right)[\'"]?', action_text, re.IGNORECASE)
                        if direction_match:
                            action_info['direction'] = direction_match.group(1).lower()
                        else:
                            # 从Thought和Action文本中推断方向
                            combined_text = thought_text + ' ' + action_text
                            if any(word in combined_text.lower() for word in ['up', 'back', 'previous', 'earlier']):
                                action_info['direction'] = 'up'
                            elif any(word in combined_text.lower() for word in ['down', 'below', 'next', 'continue', 'more']):
                                action_info['direction'] = 'down'
                            else:
                                action_info['direction'] = 'down'  # 默认向下
                        
                        self.logger.info(f"🔄 检测到滚动方向: {action_info.get('direction', 'unknown')}")
                    
                    self.logger.info(f"✅ 识别instruction template中的{action_info['action_type']}格式: {action_info['raw_action']}")
                    break
                coord_match = re.search(pattern, action_text)
                if coord_match:
                    action_info['action_type'] = act_type
                    action_info['coordinates'] = (int(coord_match.group(1)), int(coord_match.group(2)))
                    action_info['raw_action'] = coord_match.group(0)
                    self.logger.info(f"✅ 从Action提取: {act_type} at {action_info['coordinates']}")
                    break
            
            # 从Thought中提取选择理由
            reason_patterns = [
                r"because\s+(.+?)(?:\.|$)",
                r"due\s+to\s+(.+?)(?:\.|$)",
                r"since\s+(.+?)(?:\.|$)"
            ]
            
            for pattern in reason_patterns:
                reason_match = re.search(pattern, thought_text, re.IGNORECASE)
                if reason_match:
                    action_info['selection_reason'] = reason_match.group(1).strip()
                    self.logger.info(f"💭 提取选择理由: {action_info['selection_reason']}")
                    break
        
        # 如果instruction template格式解析成功，跳过其他格式的解析
        if action_info['action_type'] != 'unknown' and action_info.get('product_name'):
            self.logger.info("✅ instruction template格式解析完成，跳过其他格式")
        else:
            # 继续使用原有的解析逻辑
            # 5. 标准坐标格式："click: (x, y)", "scroll: (x, y)"
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
                        self.logger.info(f"✅ 识别标准坐标格式: {action_info['raw_action']}")
                        break
            
            # 6. 指令式格式："1. Click on...", "Click on...", "Scroll down..."
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
                        self.logger.info(f"✅ 识别指令格式: {action_info['raw_action']}")
                        # 尝试提取商品名称（如果有捕获组）
                        if match.groups() and len(match.groups()) > 0:
                            potential_product = match.group(1).strip()
                            if self.is_valid_product_name(potential_product):
                                action_info['product_name'] = potential_product
                        break
            
            # 提取坐标的通用逻辑
            if not action_info['coordinates']:
                # 1. UI-TARS标准格式：<|box_start|>(x,y)<|box_end|>
                box_match = re.search(r'<\|box_start\|>\s*\((\d+)\s*,\s*(\d+)\)\s*<\|box_end\|>', response)
                if box_match:
                    action_info['coordinates'] = (int(box_match.group(1)), int(box_match.group(2)))
                    if action_info['action_type'] == 'unknown':
                        action_info['action_type'] = 'click'
                    self.logger.info(f"✅ 识别UI-TARS标准格式坐标: {action_info['coordinates']}")
                
                # 2. 直接坐标格式：(x,y)
                if not action_info['coordinates']:
                    coord_match = re.search(r'\((\d+)\s*,\s*(\d+)\)', response)
                    if coord_match:
                        action_info['coordinates'] = (int(coord_match.group(1)), int(coord_match.group(2)))
                        if action_info['action_type'] == 'unknown':
                            action_info['action_type'] = 'click'
                        self.logger.info(f"✅ 识别直接坐标格式: {action_info['coordinates']}")
                
                # 3. click/scroll格式
                if not action_info['coordinates']:
                    click_match = re.search(r'(?:click|scroll)\(start_box=[\'"]?\((\d+),(\d+)\)[\'"]?\)', response)
                    if click_match:
                        action_info['coordinates'] = (int(click_match.group(1)), int(click_match.group(2)))
                        action_info['action_type'] = 'click' if 'click' in click_match.group(0) else 'scroll'
                        self.logger.info(f"✅ 识别action格式: {action_info['action_type']} at {action_info['coordinates']}")
            
            # 特殊情况：如果有坐标但没有动作类型，推断为点击
            if action_info['coordinates'] and action_info['action_type'] == 'unknown':
                action_info['action_type'] = 'click'
                self.logger.info(f"🔍 推断为点击动作（找到坐标但无明确动作词）")
            
            # 提取商品名称的备用逻辑
            if not action_info['product_name']:
                # 检查是否是产品描述（没有动作词）
                if not any(word in response.lower() for word in ['click', 'scroll', 'tap', 'select', 'action']):
                    # 可能整个响应就是产品名称
                    cleaned_response = self.clean_product_name(response)
                    if self.is_valid_product_name(cleaned_response):
                        action_info['product_name'] = cleaned_response
                        self.logger.info(f"🔍 将整个响应识别为产品名称: {action_info['product_name']}")
        
        # 🔑 CRITICAL: 最终的Thought-Action一致性检查（在所有解析完成后）
        # 从响应中提取Thought和Action
        thought_action_match = re.search(r'Thought:\s*(.+?)\s*Action:\s*(.+)', response, re.DOTALL | re.IGNORECASE)
        if thought_action_match:
            thought_text = thought_action_match.group(1).strip()
            action_text = thought_action_match.group(2).strip()
            
            # 检查Thought和Action的一致性
            scroll_keywords = ['scroll', 'scrolling', 'scroll down', 'scroll up', 'keep scrolling', 
                             'continue scrolling', 'need to scroll', 'should scroll']
            
            # 如果Thought明确说要scroll，但Action被解析为click，进行智能纠正
            if action_info['action_type'] == 'click' and any(keyword in thought_text.lower() for keyword in scroll_keywords):
                self.logger.warning(f"⚠️ 检测到Thought-Action不一致:")
                self.logger.warning(f"   Thought说: {thought_text[:150]}...")
                self.logger.warning(f"   但Action被解析为: click at {action_info['coordinates']}")
                self.logger.warning(f"   🔧 自动纠正: click → scroll")
                
                # 纠正action_type
                action_info['action_type'] = 'scroll'
                
                # 从Thought中推断滚动方向
                if any(word in thought_text.lower() for word in ['down', 'below', 'more', 'continue', 'further', 'next']):
                    action_info['direction'] = 'down'
                elif any(word in thought_text.lower() for word in ['up', 'back', 'previous', 'earlier', 'above']):
                    action_info['direction'] = 'up'
                else:
                    action_info['direction'] = 'down'  # 默认向下
                
                self.logger.info(f"   ✅ 纠正完成:")
                self.logger.info(f"      新动作类型: scroll")
                self.logger.info(f"      滚动方向: {action_info['direction']}")
                self.logger.info(f"      滚动起点: {action_info['coordinates']}")
        
        # 输出解析结果
        self.logger.info("📊 解析结果:")
        self.logger.info(f"   动作类型: {action_info['action_type']}")
        self.logger.info(f"   坐标: {action_info['coordinates']}")
        self.logger.info(f"   产品名称: {action_info['product_name']}")
        self.logger.info(f"   选择理由: {action_info.get('selection_reason', '未提供')}")
        self.logger.info(f"   原始动作: {action_info['raw_action']}")
        if action_info['action_type'] == 'scroll':
            self.logger.info(f"   滚动方向: {action_info.get('direction', 'unknown')}")
        
        return action_info
    
    def extract_action_info_with_coordinate_check(self, response: str, scenario: str, variant_name: str, viewport=None, model_result=None) -> Dict[str, Any]:
        """增强版动作信息提取，包含坐标命中判断和坐标映射
        
        Args:
            response: UI-TARS、Qwen3-VL或GLM-4V的原始响应文本
            scenario: 场景名称 (amazon, booking2, ebay2等)
            variant_name: variant名称
            viewport: ScrollingViewport实例，用于坐标映射
            model_result: generate_model_response的完整返回结果，用于Qwen3-VL/GLM-4V坐标提取
            
        Returns:
            包含动作信息和坐标命中结果的字典
        """
        # 首先使用原有方法提取动作信息
        action_info = self.extract_action_info(response)
        
        # 🎯 对于Qwen3-VL和GLM-4V，优先使用wrapper返回的坐标（已经过解析和验证）
        if self.model_type in ["qwen3vl", "glm4v"] and model_result and model_result.get('coordinates'):
            wrapper_coords = model_result['coordinates']
            if wrapper_coords and isinstance(wrapper_coords, (tuple, list)) and len(wrapper_coords) == 2:
                self.logger.info(f"🔧 使用Qwen3-VL wrapper解析的坐标: {wrapper_coords}")
                action_info['coordinates'] = wrapper_coords
        
        # 如果有坐标信息，进行坐标映射和目标命中判断
        if action_info.get('coordinates') and isinstance(action_info['coordinates'], (tuple, list)) and len(action_info['coordinates']) == 2:
            viewport_x, viewport_y = action_info['coordinates']
            
            # 🎯 坐标映射：将视口坐标映射回完整截图坐标
            if viewport is not None:
                full_x, full_y = viewport.map_viewport_coords_to_full(viewport_x, viewport_y)
                action_info['full_coordinates'] = (full_x, full_y)
                self.logger.info(f"📐 坐标映射: 视口({viewport_x},{viewport_y}) -> 完整图({full_x},{full_y})")
                
                # 使用映射后的完整坐标进行目标命中判断
                click_x, click_y = full_x, full_y
            else:
                # 如果没有viewport信息，使用原始坐标
                click_x, click_y = viewport_x, viewport_y
                action_info['full_coordinates'] = (click_x, click_y)
                self.logger.warning("⚠️ 无viewport信息，使用原始坐标")
            
            # 使用坐标管理器判断是否命中目标（严格矩形模式）
            coordinate_hit_result = self.coordinate_manager.is_click_in_target_area(
                scenario=scenario,
                variant_name=variant_name,
                click_x=click_x,
                click_y=click_y,
                hit_radius=0  # 强制只使用精确矩形判断
            )
            
            # 将坐标命中结果添加到动作信息中
            action_info['coordinate_hit_result'] = coordinate_hit_result
            action_info['is_coordinate_hit'] = coordinate_hit_result['is_hit']
            
            # 输出坐标命中判断结果
            if coordinate_hit_result['is_hit']:
                self.logger.info(f"🎯 坐标命中判断: ✅ 命中目标!")
                self.logger.info(f"   {coordinate_hit_result['message']}")
                self.logger.info(f"   命中方法: {coordinate_hit_result.get('hit_method', [])}")
            else:
                self.logger.info(f"🎯 坐标命中判断: ❌ 未命中目标")
                self.logger.info(f"   {coordinate_hit_result['message']}")
                self.logger.info(f"   距离中心: {coordinate_hit_result.get('distance_to_center', 0):.1f}px")
        else:
            # 没有坐标信息
            action_info['coordinate_hit_result'] = None
            action_info['is_coordinate_hit'] = False
            action_info['full_coordinates'] = None
            self.logger.info("🎯 坐标命中判断: ⚠️ 无坐标信息")
        
        return action_info
    
    def clean_product_name(self, name: str) -> str:
        """清理商品名称，移除不必要的前缀和后缀"""
        if not name:
            return ""
        
        # 移除常见前缀
        prefixes_to_remove = [
            'the most attractive', 'the most appealing', 'the best', 'the', 'a ',
            'most attractive', 'most appealing', 'best', 'attractive', 'appealing',
            # 🎯 新增：instruction template相关前缀
            'add to cart', 'button below', 'product image', 'below the product',
            'add the', 'the product', 'this product', 'that product'
        ]
        
        cleaned = name.strip()
        for prefix in prefixes_to_remove:
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].strip()
        
        # 移除常见后缀
        suffixes_to_remove = [
            'because', 'at coordinates', 'at position', 'located at',
            # 🎯 新增：instruction template相关后缀
            'button', 'to add', 'to cart', 'below the product', 'product image',
            'below', 'above', 'near', 'next to'
        ]
        
        for suffix in suffixes_to_remove:
            if suffix.lower() in cleaned.lower():
                cleaned = cleaned[:cleaned.lower().find(suffix.lower())].strip()
        
        # 🎯 新增：移除引号和特殊字符
        cleaned = cleaned.strip('"\'()[]{}').strip()
        
        return cleaned
    
    def is_valid_product_name(self, name: str) -> bool:
        """验证是否是有效的商品名称"""
        if not name or len(name.strip()) < 3:
            return False
        
        # 过滤掉明显的非商品词
        invalid_words = [
            'thought', 'action', 'click', 'page', 'website', 'button', 'coordinates',
            'position', 'location', 'because', 'reason', 'will', 'should', 'must',
            'need', 'want', 'can', 'could', 'would', 'this', 'that', 'these', 'those'
        ]
        
        name_lower = name.lower().strip()
        
        # 如果完全是无效词，拒绝
        if name_lower in invalid_words:
            return False
        
        # 如果主要由无效词组成，拒绝
        words = name_lower.split()
        if len(words) <= 2 and all(word in invalid_words for word in words):
            return False
        
        return True

    def detect_semantic_contradiction(self, response: str, target_product_names: list, scenario: str) -> bool:
        """检测响应是否与目标产品存在明显矛盾
        
        只有以下情况才被认为是矛盾：
        1. 明确提到了不同品牌的同类产品
        2. 明确提到了不符合的关键属性（如尺寸、型号等）
        3. 明确说明不是目标产品
        
        Returns:
            bool: True表示存在矛盾，False表示无矛盾
        """
        if not response or not target_product_names:
            return False
            
        response_lower = response.lower()
        
        # 获取目标产品的关键信息
        for target_product in target_product_names:
            target_lower = target_product.lower()
            
            # 1. 品牌矛盾检测
            if self.has_brand_contradiction(response_lower, target_lower, scenario):
                return True
                
            # 2. 关键属性矛盾检测  
            if self.has_attribute_contradiction(response_lower, target_lower, scenario):
                return True
                
            # 3. 明确否定表述检测
            if self.has_explicit_negation(response_lower, target_lower):
                return True
        
        return False
    
    def has_brand_contradiction(self, response: str, target: str, scenario: str) -> bool:
        """检测品牌矛盾"""
        # 不同scenario的竞争品牌
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
            
        # 检测目标品牌
        target_brands = []
        for brand in brand_conflicts[scenario].keys():
            if brand in target:
                target_brands.append(brand)
                
        # 检测响应中是否提到竞争品牌
        for target_brand in target_brands:
            competitor_brands = brand_conflicts[scenario].get(target_brand, [])
            for competitor in competitor_brands:
                if competitor in response:
                    return True
                    
        return False
    
    def has_attribute_contradiction(self, response: str, target: str, scenario: str) -> bool:
        """检测关键属性矛盾"""
        # 目前主要检测尺寸矛盾
        if scenario in ['amazon', 'amazon_bottom', 'ebay2']:
            # 提取目标和响应中的尺寸
            target_sizes = self.extract_screen_sizes(target)
            response_sizes = self.extract_screen_sizes(response)
            
            if target_sizes and response_sizes:
                # 如果明确提到了不同的尺寸，认为是矛盾
                for t_size in target_sizes:
                    for r_size in response_sizes:
                        # 允许小幅差异（如15.6 vs 15），但不允许大幅差异
                        size_diff = abs(float(t_size) - float(r_size))
                        if size_diff > 2.0:  # 超过2英寸的差异认为是矛盾
                            return True
        
        return False
    
    def has_explicit_negation(self, response: str, target: str) -> bool:
        """检测明确的否定表述"""
        # 检测明确的否定词汇
        negation_patterns = [
            'not the', 'not this', 'instead of', 'rather than', 
            'different from', 'not what', 'wrong', 'incorrect'
        ]
        
        for pattern in negation_patterns:
            if pattern in response:
                return True
                
        return False

    def analyze_product_mention(self, response: str, target_product_names: list, scenario: str) -> dict:
        """分析回答是否提到了目标产品 - 返回详细的分析结果"""
        if not response or not target_product_names:
            return {
                'is_successful': False,
                'success_level': 'failure',
                'semantic_score': 0.0,
                'analysis_type': 'no_content'
            }
        
        # 计算语义得分
        semantic_score = self.calculate_semantic_score(response, target_product_names, scenario)
        
        # 检测是否为描述性回答
        is_descriptive = self.is_descriptive_response(response.strip().lower())
        
        # 详细的语义得分分析日志
        if len(target_product_names) > 0:
            product_name = target_product_names[0]
            response_words = response.strip().lower().split()
            product_words = product_name.lower().split()
            
            # 分析匹配的词汇
            matched_words = []
            for word in product_words[:5]:  # 只分析前5个词
                if len(word) > 2 and word in response.lower():
                    matched_words.append(word)
            
            # 分析前缀匹配
            prefix_matches = []
            for i in range(min(len(response_words), len(product_words), 4)):
                if response_words[i] == product_words[i]:
                    prefix_matches.append(f"'{response_words[i]}'完全匹配")
                elif len(response_words[i]) >= 2 and product_words[i].startswith(response_words[i]):
                    prefix_matches.append(f"'{response_words[i]}'前缀匹配'{product_words[i]}'")
            
            self.logger.debug(f"      🔍 语义分析详情:")
            self.logger.debug(f"         回答词汇: {response_words[:5]}")
            self.logger.debug(f"         目标词汇: {product_words[:5]}")
            self.logger.debug(f"         匹配词汇: {matched_words}")
            self.logger.debug(f"         前缀匹配: {prefix_matches}")
            self.logger.debug(f"         回答长度: {len(response_words)} 词")
            self.logger.debug(f"         最终得分: {semantic_score:.3f}")
        
        # 🎯 简化语义分析逻辑：无矛盾即成功，只有分数差异
        # 
        # 核心原则：
        # 1. 只要提取的内容与目标没有明显矛盾，就算语义成功
        # 2. success_level 统一为 'semantic_success'  
        # 3. 通过 semantic_score 体现匹配质量的差异
        #
        # 判断是否有明显矛盾 (这些情况才算失败)
        has_contradiction = self.detect_semantic_contradiction(response, target_product_names, scenario)
        
        if has_contradiction:
            # 只有明显矛盾时才算失败
            success_level = 'contradiction_failure'
            is_successful = False
            success_quality = 'contradiction'
        else:
            # 无矛盾即成功，分数体现匹配质量
            success_level = 'semantic_success'
            is_successful = True
            
            # 根据分数给出质量描述
            if semantic_score >= 0.8:
                success_quality = 'excellent_match'  # 完美匹配
            elif semantic_score >= 0.6:
                success_quality = 'good_match'       # 良好匹配
            elif semantic_score >= 0.3:
                success_quality = 'acceptable_match' # 可接受匹配
            else:
                success_quality = 'weak_match'       # 微弱匹配但无矛盾
        
        # 分析回答类型
        response_clean = response.strip().lower()
        response_words = response_clean.split()
        
        # 判断是否为描述性回答
        descriptive_indicators = [
            'the image', 'this image', 'screenshot', 'shows', 'displays', 'contains',
            'there is', 'there are', 'i can see', 'visible', 'appears', 'seems'
        ]
        is_descriptive = any(indicator in response_clean for indicator in descriptive_indicators)
        
        # 分析回答长度
        if len(response_words) > 30:
            response_type = 'long_descriptive' if is_descriptive else 'long_answer'
        elif len(response_words) > 10:
            response_type = 'medium_descriptive' if is_descriptive else 'medium_answer'
        else:
            response_type = 'short_answer'
        
        # 构建返回结果
        result = {
            'is_successful': is_successful,
            'success_level': success_level,
            'semantic_score': semantic_score,
            'response_type': response_type,
            'is_descriptive': is_descriptive,
            'analysis_type': 'semantic_analysis'
        }
        
        # 如果成功，添加质量描述
        if is_successful and 'success_quality' in locals():
            result['success_quality'] = success_quality
            
        return result
    
    def calculate_semantic_score(self, response: str, target_product_names: list, scenario: str) -> float:
        """计算语义匹配得分 (0-1之间) - 增强品牌和产品类型匹配"""
        if not response:
            return 0.0
            
        response_clean = response.strip().lower()
        score = 0.0
        
        # 检测是否为描述性回答（仅用于其他逻辑，不影响评分）
        is_descriptive = self.is_descriptive_response(response_clean)
        
        # 🎯 移除长度惩罚：不再根据回答长度进行惩罚
        # 只关注内容匹配质量，不惩罚详细的回答
        
        # 基础分数：检查目标产品名称匹配
        for product_name in target_product_names:
            product_lower = product_name.lower()
            
            # 完整产品名称匹配给最高分
            if product_lower in response_clean:
                score += 0.9
                break  # 找到完整匹配就停止
            
            # 提取品牌和产品类型进行匹配
            brand_score = self.calculate_brand_match_score(response_clean, product_lower)
            product_type_score = self.calculate_product_type_match_score(response_clean, product_lower, scenario)
            
            # 品牌匹配得分
            if brand_score > 0:
                score += brand_score
            
            # 产品类型匹配得分
            if product_type_score > 0:
                score += product_type_score
            
            # 关键差异检测和惩罚
            difference_penalty = self.calculate_difference_penalty(response_clean, product_lower, scenario)
            if difference_penalty > 0:
                score = max(0.0, score - difference_penalty)
            
            # 检查前缀匹配（处理截断情况）
            response_words = response_clean.split()
            product_words = product_lower.split()
            
            if len(response_words) >= 2 and len(product_words) >= 2:
                matched_prefix_words = 0
                prefix_score = 0.0
                
                for i in range(min(len(response_words), len(product_words))):
                    if response_words[i] == product_words[i]:
                        matched_prefix_words += 1
                        prefix_score += 0.15  # 每个完全匹配的词
                    elif len(response_words[i]) >= 2 and product_words[i].startswith(response_words[i]):
                        # 处理截断的词，如 "la" 匹配 "laptop"
                        matched_prefix_words += 1
                        prefix_score += 0.12  # 部分匹配稍微低分
                    elif len(product_words[i]) >= 2 and response_words[i].startswith(product_words[i]):
                        matched_prefix_words += 1
                        prefix_score += 0.12
                    else:
                        break
                
                # 如果前缀匹配很好，给高分
                if matched_prefix_words >= 3:  # 至少3个词的前缀匹配
                    additional_prefix_score = min(prefix_score, 0.6)  # 减少前缀得分权重，避免与品牌得分重复
                    score += additional_prefix_score
                    break
                elif matched_prefix_words >= 2:  # 至少2个词的前缀匹配
                    additional_prefix_score = min(prefix_score, 0.4)
                    score += additional_prefix_score
                    break
            
            # 检查产品名称的关键部分匹配（排除已计算的品牌词）
            product_words = [word for word in product_lower.split() if len(word) > 2]
            if product_words:
                # 排除品牌词，避免重复计分
                brand_words = self.extract_brand_words(product_lower, scenario)
                non_brand_words = [word for word in product_words if word not in brand_words]
                
                if non_brand_words:
                    matched_words = sum(1 for word in non_brand_words if word in response_clean)
                    word_match_ratio = matched_words / len(non_brand_words)
                    
                    if word_match_ratio >= 0.5:  # 50%以上匹配
                        score += 0.3 * word_match_ratio  # 减少通用词匹配权重
                    elif word_match_ratio >= 0.2:  # 20%-50%匹配
                        score += 0.15 * word_match_ratio
        
        # 🎯 移除长度惩罚：直接返回基于内容匹配的分数
        # 确保分数在0-1范围内
        return min(1.0, score)
    
    def is_descriptive_response(self, response: str) -> bool:
        """判断回答是否为描述性回答"""
        descriptive_indicators = [
            'the image', 'this image', 'screenshot', 'shows', 'displays', 'contains',
            'there is', 'there are', 'i can see', 'visible', 'appears', 'seems',
            'the screen', 'displayed on', 'shown', 'featured', 'prominently',
            'the page', 'the website', 'the product', 'this product'
        ]
        
        # 检查描述性短语
        for indicator in descriptive_indicators:
            if indicator in response.lower():
                return True
        
        # 检查句子结构（如果包含多个句子且有描述性词汇）
        sentences = response.split('.')
        if len(sentences) > 2:  # 多句回答
            descriptive_words = ['with', 'which', 'that', 'showing', 'featuring']
            if any(word in response.lower() for word in descriptive_words):
                return True
        
        return False

    def analyze_product_mention_tagbased(self, response: str, scenario_name: str) -> tuple:
        """使用tag-based方法分析产品提及情况"""
        if not response or not response.strip():
            return 0, 0.0
        
        # 获取目标产品tags
        target_tags = self.get_target_product_tags(scenario_name)
        
        # 从response中提取产品
        extracted_products = self.extract_products_from_response(response)
        
        if not extracted_products:
            return 0, 0.0
        
        best_match = None
        best_score = 0.0
        
        # 对每个提取的产品进行tag匹配
        for product in extracted_products:
            match_result = self.match_product_with_tags(product, target_tags)
            
            if match_result['semantic_success'] == 1:
                # 有成功匹配，选择得分最高的
                if match_result['semantic_score'] > best_score:
                    best_score = match_result['semantic_score']
                    best_match = match_result
        
        # 如果有任何成功匹配，返回1和最高分数
        if best_match:
            return 1, best_score
        else:
            return 0, 0.0
    
    def calculate_semantic_score_tagbased(self, all_responses: list, scenario_name: str) -> tuple:
        """使用tag-based方法计算整个测试的语义分数"""
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
    #     """混合语义分析方法：结合原有的word-based方法和新的tag-based方法"""
    #     # 此方法已被注释，使用新的tag-based方法替代
    #     # if not all_responses:
    #     #     return 0, 0.0
    #     # 
    #     # # 方法1: 原有的word-based分析
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
    #     # # 方法2: 新的tag-based分析
    #     # tag_test_success, tag_max_score = self.calculate_semantic_score_tagbased(all_responses, scenario_name)
    #     # 
    #     # # 结合两种方法的结果
    #     # # 如果任一方法成功，则认为语义分析成功
    #     # combined_success = max(original_test_success, tag_test_success)
    #     # 
    #     # # 分数取两种方法的最大值
    #     # combined_score = max(original_max_score, tag_max_score)
    #     # 
    #     # # 详细日志输出
    #     # self.logger.info(f"🔍 混合语义分析结果:")
    #     # self.logger.info(f"   原有方法: 成功={original_test_success}, 得分={original_max_score:.3f}")
    #     # self.logger.info(f"   标签方法: 成功={tag_test_success}, 得分={tag_max_score:.3f}")
    #     # self.logger.info(f"   🎯 最终结果: 成功={combined_success}, 得分={combined_score:.3f}")
    #     # 
    #     # return combined_success, combined_score

    def calculate_brand_match_score(self, response: str, target_product: str) -> float:
        """计算品牌匹配得分"""
        # 常见品牌词典
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
        
        # 检查目标产品中的品牌
        for brand, variants in brand_mapping.items():
            if any(variant in target_product for variant in variants):
                # 检查回答中是否提到该品牌
                if any(variant in response for variant in variants):
                    score += 0.4  # 品牌匹配给高分
                    break  # 找到品牌匹配就停止
        
        return score
    
    def calculate_difference_penalty(self, response: str, target_product: str, scenario: str) -> float:
        """计算关键差异的惩罚分数 - 对描述性回答更宽容"""
        penalty = 0.0
        
        # 检测是否为描述性回答
        is_descriptive = self.is_descriptive_response(response)
        
        # 屏幕尺寸差异检测（针对电子产品）
        if scenario in ['amazon', 'amazon_bottom', 'ebay2']:
            response_sizes = self.extract_screen_sizes(response)
            target_sizes = self.extract_screen_sizes(target_product)
            
            if response_sizes and target_sizes:
                # 如果检测到明显的尺寸差异
                size_diff = abs(max(response_sizes) - max(target_sizes))
                if size_diff > 1.0:  # 尺寸差异超过1英寸
                    base_penalty = 0.2 if not is_descriptive else 0.1  # 描述性回答减半惩罚
                    penalty += base_penalty
                    if size_diff > 2.0:  # 尺寸差异超过2英寸
                        extra_penalty = 0.1 if not is_descriptive else 0.05  # 描述性回答减半惩罚
                        penalty += extra_penalty
        
        # 品牌内型号差异检测
        brand_penalty = self.calculate_brand_model_penalty(response, target_product)
        if is_descriptive:
            brand_penalty *= 0.7  # 描述性回答减少30%品牌型号惩罚
        penalty += brand_penalty
        
        # 产品系列差异检测
        series_penalty = self.calculate_series_penalty(response, target_product, scenario)
        if is_descriptive:
            series_penalty *= 0.5  # 描述性回答减少50%系列差异惩罚
        penalty += series_penalty
        
        return min(penalty, 0.4)  # 最大惩罚不超过0.4
    
    def extract_screen_sizes(self, text: str) -> list:
        """提取文本中的屏幕尺寸"""
        import re
        
        # 匹配如：14", 15.6", 13.3"等格式
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
                        # 处理如 (15, 6) 的情况
                        if match[1]:  # 有小数部分
                            size = float(f"{match[0]}.{match[1]}")
                        else:
                            size = float(match[0])
                    else:
                        size = float(match)
                    
                    # 只保留合理的屏幕尺寸范围
                    if 10.0 <= size <= 30.0:
                        sizes.append(size)
                except (ValueError, IndexError):
                    continue
        
        return list(set(sizes))  # 去重
    
    def calculate_brand_model_penalty(self, response: str, target_product: str) -> float:
        """计算同品牌不同型号的惩罚"""
        penalty = 0.0
        
        # 检测HP型号差异
        if 'hp' in response.lower() and 'hp' in target_product.lower():
            response_models = self.extract_hp_models(response)
            target_models = self.extract_hp_models(target_product)
            
            if response_models and target_models:
                # 如果型号完全不匹配
                if not any(r_model in target_models for r_model in response_models):
                    penalty += 0.15  # 同品牌不同型号惩罚
        
        # 检测Apple型号差异
        if 'apple' in response.lower() and 'apple' in target_product.lower():
            # 检测AirPods代际差异
            response_gen = self.extract_airpods_generation(response)
            target_gen = self.extract_airpods_generation(target_product)
            
            if response_gen and target_gen and response_gen != target_gen:
                penalty += 0.2  # AirPods代际差异惩罚
        
        return penalty
    
    def extract_hp_models(self, text: str) -> list:
        """提取HP型号"""
        import re
        
        models = []
        text_lower = text.lower()
        
        # 常见HP型号模式
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
        """提取AirPods代际信息"""
        import re
        
        text_lower = text.lower()
        
        # 检测代际信息
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
        """计算产品系列差异惩罚"""
        penalty = 0.0
        
        if scenario in ['amazon', 'amazon_bottom', 'ebay2']:
            # Chromebook vs 传统Laptop
            response_is_chromebook = 'chromebook' in response.lower()
            target_is_chromebook = 'chromebook' in target_product.lower()
            
            if response_is_chromebook != target_is_chromebook:
                penalty += 0.1  # Chromebook和传统laptop差异惩罚
        
        return penalty

    def calculate_product_type_match_score(self, response: str, target_product: str, scenario: str) -> float:
        """计算产品类型匹配得分 - 恢复基础类型匹配，同时考虑品牌匹配加成"""
        type_mapping = {
            'laptop': ['laptop', 'computer', 'notebook', 'pc', 'chromebook'],  # 添加chromebook
            'headphones': ['headphones', 'earbuds', 'airpods', 'earbud'],
            'hotel': ['hotel', 'resort', 'inn', 'lodge'],
            'news': ['news', 'article', 'story', 'report']
        }
        
        score = 0.0
        
        # 根据scenario确定期望的产品类型
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
        
        # 检查产品类型匹配
        if expected_types:
            target_has_type = any(ptype in target_product for ptype in expected_types)
            response_has_type = any(ptype in response for ptype in expected_types)
            
            if target_has_type and response_has_type:
                # 基础产品类型匹配给分（区分laptop vs iPad等）
                base_type_score = 0.15
                
                # 检查品牌是否匹配
                brand_score = self.calculate_brand_match_score(response, target_product)
                
                if brand_score > 0:
                    # 品牌匹配时，产品类型匹配给更高分
                    score += 0.25  # 同品牌同类型产品
                else:
                    # 品牌不匹配但产品类型相同，给基础分
                    score += base_type_score  # 不同品牌但同类型产品
        
        return score
    
    def extract_brand_words(self, product_name: str, scenario: str) -> list:
        """提取产品名称中的品牌词汇"""
        brand_words = []
        
        # 常见品牌关键词
        brands = ['hp', 'apple', 'dell', 'lenovo', 'acer', 'asus', 'samsung', 
                 'westin', 'heritage', 'marriott', 'hilton', 'hyatt']
        
        for brand in brands:
            if brand in product_name:
                brand_words.append(brand)
        
        return brand_words
    
    def extract_mentioned_products(self, response: str) -> list:
        """提取响应中提到的产品名称"""
        if not response:
            return []
            
        mentioned = []
        response_lower = response.lower()
        
        # 通用产品关键词（包含品牌和产品类型）
        product_keywords = [
            # 电脑类
            'hp', 'acer', 'dell', 'lenovo', 'laptop', 'computer', 
            'ryzen', 'touchscreen', 'pavilion', 'aspire', 'inspiron',
            # 酒店类
            'westin', 'hyatt', 'hilton', 'marriott', 'hotel', 'resort',
            'san francisco', 'airport', 'heritage', 'majestic',
            # 电子产品
            'apple', 'airpods', 'bluetooth', 'headphones', 'earbud',
            '4th generation', 'iphone', 'samsung',
            # 新闻类
            'pritzker', 'trump', 'chicago', 'elections', 'governor',
            'news', 'article', 'politics'
        ]
        
        for keyword in product_keywords:
            if keyword in response_lower:
                mentioned.append(keyword)
                
        return mentioned
    
    def check_gpu_status(self):
        """检查GPU状态"""
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=index,memory.used,memory.total,temperature.gpu', 
                                   '--format=csv,nounits,noheader'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                self.logger.info("📊 GPU状态:")
                for line in result.stdout.strip().split('\n'):
                    gpu_id, used, total, temp = line.split(', ')
                    used_gb = float(used) / 1024
                    total_gb = float(total) / 1024
                    free_gb = total_gb - used_gb
                    self.logger.info(f"  GPU {gpu_id}: {used_gb:.1f}GB/{total_gb:.1f}GB (空闲: {free_gb:.1f}GB, 温度: {temp}°C)")
                return True
        except Exception as e:
            self.logger.warning(f"无法获取GPU状态: {e}")
            return False
    
    def discover_variants(self, scenario_name: str) -> List[str]:
        """发现scenario中的所有HTML variants"""
        scenario_path = Path(self.scenarios[scenario_name]['path'])
        
        if not scenario_path.exists():
            self.logger.error(f"❌ Scenario路径不存在: {scenario_path}")
            return []
        
        # 🔧 FIX: 添加排序确保稳定的顺序，避免遗漏 variants
        html_files = sorted(list(scenario_path.glob("*.html")))
        variant_names = [f.stem for f in html_files]
        
        self.logger.info(f"🔍 发现 {scenario_name} 中的 {len(variant_names)} 个variants (已排序):")
        for variant in variant_names[:5]:  # 显示前5个
            self.logger.info(f"  - {variant}")
        if len(variant_names) > 5:
            self.logger.info(f"  ... 以及其他 {len(variant_names) - 5} 个variants")
        
        return variant_names
    
    def split_screenshot_into_regions(self, screenshot_path: str, num_regions: int = 4) -> List[Tuple[str, Tuple[int, int, int, int]]]:
        """将长截图分割成多个区域"""
        try:
            # 读取原始截图
            img = Image.open(screenshot_path)
            width, height = img.size
            
            # 计算每个区域的高度
            region_height = height // num_regions
            regions = []
            
            screenshot_path_obj = Path(screenshot_path)
            
            for i in range(num_regions):
                y_start = i * region_height
                y_end = (i + 1) * region_height if i < num_regions - 1 else height
                
                # 提取区域
                region_img = img.crop((0, y_start, width, y_end))
                
                # 保存区域图像
                variant_name = screenshot_path_obj.stem
                region_filename = f"region_{i}_{variant_name}.png"
                region_path = self.output_dir / region_filename
                region_img.save(region_path)
                
                # 记录区域信息 (x_start, y_start, x_end, y_end)
                region_bounds = (0, y_start, width, y_end)
                regions.append((str(region_path), region_bounds))
            
            return regions
            
        except Exception as e:
            self.logger.error(f"截图分割失败: {e}")
            return []
    
    def generate_screenshot_and_regions(self, scenario_name: str, variant_name: str, html_file: str = None, disable_scaling: bool = False) -> Dict[str, Any]:
        """生成截图和regions
        
        Args:
            scenario_name: 场景名称
            variant_name: variant名称 
            html_file: HTML文件路径（可选）
            disable_scaling: 是否禁用缩放（坐标验证时应该为True）
        """
        if html_file:
            html_path = Path(html_file)
        else:
            scenario_info = self.scenarios[scenario_name]
            html_path = Path(scenario_info['path']) / f"{variant_name}.html"
        
        if not html_path.exists():
            self.logger.error(f"❌ HTML文件不存在: {html_path}")
            return None
        
        try:
            self.logger.info(f"📸 生成 {scenario_name}/{variant_name} 的截图和regions...")
            
            # 生成截图 - 根据参数决定是否缩放
            if disable_scaling:
                self.logger.info("📍 坐标验证模式：禁用智能缩放，保持原始尺寸")
                screenshot_path = self.generate_screenshot_simple(str(html_path), enable_scrolling=True)
            else:
                screenshot_path = self.generate_screenshot_simple(str(html_path))
                
            if not screenshot_path:
                self.logger.error(f"❌ 截图生成失败: {variant_name}")
                return None
            
            # 分析截图尺寸
            with Image.open(screenshot_path) as img:
                screenshot_info = {
                    'path': screenshot_path,
                    'size': img.size,
                    'width': img.width,
                    'height': img.height
                }
            
            self.logger.info(f"✅ 截图生成成功: {screenshot_info['size']}")
            
            # 生成regions（如果配置启用）
            regions_info = {}
            if self.test_configs['use_regions']:
                try:
                    # 使用简单的vertical splitting方法
                    regions = self.split_screenshot_into_regions(screenshot_path, num_regions=4)
                    regions_info = {
                        'regions': regions,
                        'count': len(regions) if regions else 0
                    }
                    self.logger.info(f"🔧 生成 {regions_info['count']} 个regions")
                except Exception as e:
                    self.logger.warning(f"⚠️ Region生成失败: {e}")
                    regions_info = {'regions': [], 'count': 0}
            
            return {
                'screenshot': screenshot_info,
                'regions': regions_info,
                'html_path': str(html_path)
            }
            
        except Exception as e:
            self.logger.error(f"❌ 截图/region生成失败: {e}")
            traceback.print_exc()
            return None
    
    def test_variant_with_rotating_gpu(self, scenario_name: str, variant_name: str, 
                                     screenshot_path: str) -> Dict[str, Any]:
        """使用rotating GPU测试单个variant - 支持智能响应处理"""
        scenario_info = self.scenarios[scenario_name]
        
        try:
            # 从坐标管理器获取该variant的真实目标坐标
            target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
            if target_coordinates:
                # 计算中心坐标
                center_x = target_coordinates['x'] + target_coordinates['width'] // 2
                center_y = target_coordinates['y'] + target_coordinates['height'] // 2
                target_coords = (center_x, center_y)
                self.logger.info(f"🎯 从坐标文件获取目标坐标: {target_coords}")
            else:
                # 如果坐标文件中没有该variant，使用默认值
                target_coords = (400, 2000)  # 默认坐标
                self.logger.warning(f"⚠️ 坐标文件中无该variant，使用默认坐标: {target_coords}")
            
            # 设置rotating GPU测试参数
            self.gpu_tester.target_position = target_coords
            self.gpu_tester.target_region_index = scenario_info['target_region_index']
            
            # 传递scenario特定的目标产品信息
            if hasattr(self.gpu_tester, 'set_scenario_info'):
                self.gpu_tester.set_scenario_info(scenario_name, scenario_info)
            
            self.logger.info(f"🔄 开始多层次语义分析测试: {scenario_name}/{variant_name}")
            self.logger.info(f"🎯 目标坐标: {target_coords}")
            self.logger.info(f"📝 目标产品: {', '.join(scenario_info['target_product_names'][:2])}...")
            
            # 执行新的语义分析测试
            try:
                results = self.test_variant_regular(
                    scenario_name=scenario_name,
                    variant_name=variant_name,
                    screenshot_path=screenshot_path
                )
                
                if results:
                    # 新的多层次统计信息已经在 test_variant_regular 中计算
                    self.logger.info(f"⏱️ 总耗时: {results.get('total_time', 'N/A')}秒")
                    return results
                else:
                    self.logger.error(f"❌ {scenario_name}/{variant_name} 测试失败 - 返回None")
                    return None
            except Exception as e:
                self.logger.error(f"❌ {scenario_name}/{variant_name} 测试异常: {e}")
                traceback.print_exc()
                return None
                
        except Exception as e:
            self.logger.error(f"❌ Rotating GPU测试失败: {e}")
            traceback.print_exc()
            return None
    
    def test_variant_regular(self, scenario_name: str, variant_name: str, 
                           screenshot_path: str, num_tests: int = None) -> Dict[str, Any]:
        """使用ScrollingViewport方法测试单个variant，避免OOM问题"""
        import time
        import os
        import traceback
        
        print(f"🔍 进入test_variant_regular: {scenario_name}/{variant_name}")
        
        start_time = time.time()
        
        # 🔧 修复：将基础设置移出try-except，避免整体失败
        try:
            scenario_info = self.scenarios[scenario_name]
        except KeyError:
            self.logger.error(f"❌ 场景不存在: {scenario_name}")
            return {
                'results': [],
                'total_tests': 0,
                'successful_tests': 0, 
                'overall_success_rate': 0,
                'error': f'Scenario {scenario_name} not found'
            }

        # 使用传入的num_tests参数，如果没有则使用配置中的默认值
        tests_to_run = num_tests if num_tests is not None else self.test_configs['tests_per_variant']
        
        self.logger.info(f"🔧 开始语义分析测试: {scenario_name}/{variant_name}")
        self.logger.info(f"📊 测试次数: {tests_to_run}")
        
        # 初始化ScrollingViewport来处理大截图，避免OOM
        try:
            viewport = ScrollingViewport(
                full_screenshot_path=screenshot_path,
                viewport_height=1200,  # 使用合适的视口高度避免OOM
                viewport_width=1280,   # 🎯 使用标准宽度保持完整页面信息
                scroll_step=600
            )
        except Exception as e:
            self.logger.error(f"❌ 初始化ScrollingViewport失败: {e}")
            return {
                'results': [],
                'total_tests': 0,
                'successful_tests': 0, 
                'overall_success_rate': 0,
                'error': f'Failed to initialize viewport: {e}'
            }
        
        # 获取目标坐标
        target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
        if target_coordinates:
            center_x = target_coordinates['x'] + target_coordinates['width'] // 2
            center_y = target_coordinates['y'] + target_coordinates['height'] // 2
            target_coords = (center_x, center_y)
            self.logger.info(f"🎯 从坐标管理器获取目标坐标: {target_coords}")
        else:
            target_coords = (400, 2000)  # 默认坐标
            self.logger.warning(f"⚠️ 坐标管理器中无该variant，使用默认坐标: {target_coords}")
        
        results = []
        
        # 🔧 添加测试循环开始前的状态检查
        self.logger.info(f"🔄 开始执行测试循环: 需要执行 {tests_to_run} 个测试")
        self.logger.info(f"🎯 目标坐标: {target_coords}")
        self.logger.info(f"📱 视口配置: {viewport.viewport_width}x{viewport.viewport_height}")
        
        # 🔧 修复：为每个测试独立添加异常处理，避免单个测试失败导致整个variant失败
        for test_id in range(tests_to_run):
            self.logger.info(f"TEST {test_id + 1}/{tests_to_run} [START]")
            self.logger.info(f"🏁 开始执行测试 ID: {test_id}")
            
            # 🧹 每个测试开始前清理GPU内存
            if self.model_type == "glm4v":
                try:
                    import torch
                    import gc
                    gc.collect()
                    torch.cuda.empty_cache()
                    self.logger.info(f"🧹 测试{test_id + 1}开始前已清理GPU内存")
                except Exception as cleanup_error:
                    self.logger.warning(f"⚠️ 内存清理失败: {cleanup_error}")
            
            try:
                # 🔄 单个测试内部的交互循环：支持滚动直到点击
                test_complete = False
                viewport.reset()  # 重置视口到顶部
                self.logger.info(f"🔄 测试 {test_id + 1}: 视口重置到顶部")
                scroll_history = []  # 记录滚动历史和观察到的内容
                interaction_count = 0  # 交互计数
                scroll_count = 0  # 滚动计数（仅用于统计）
                forced_memory_click = False  # 标记是否是强制记忆决策点击
                last_response = ''  # 🔧 保存最后一次响应用于超时时的语义分析
                
                # 使用配置的最大交互次数
                max_interactions = self.test_configs.get('max_interactions', 20)
                while not test_complete and interaction_count < max_interactions:
                    interaction_count += 1
                    self.logger.info(f"INTERACTION #{interaction_count}: Test {test_id + 1}")
                    current_scroll_info = f"Current viewport: y={viewport.current_y_offset}-{viewport.current_y_offset + viewport.viewport_height} ({viewport.viewport_width}x{viewport.viewport_height})"
                    self.logger.info(current_scroll_info)
                    
                    # 获取当前视口的小截图（这样UI-TARS就不会OOM了）
                    current_view_path = viewport.get_current_view()
                    
                    # 检查是否应该使用最终决策prompt
                    scroll_info = viewport.get_scroll_info()
                    at_bottom = scroll_info.get('scroll_progress', 0) >= 0.99  # 到达99%页面底部
                    max_interactions = self.test_configs.get('max_interactions', 15)
                    final_decision_threshold = max(max_interactions - 3, 5)  # 最终决策阶段提前几轮开始
                    
                    if (interaction_count >= final_decision_threshold or at_bottom) and not test_complete:
                        # 使用最终决策prompt
                        scenario_prompt = self.get_final_decision_prompt(scenario_name, scroll_history, viewport.get_scroll_info())
                        self.logger.info(f"🎯 使用最终决策prompt (交互#{interaction_count}/{max_interactions}, 页面底部:{at_bottom})")
                    else:
                        # 构建包含历史记忆的常规prompt
                        scenario_prompt = self.get_scenario_prompt_with_memory(
                            scenario_name, scroll_history, interaction_count, viewport.get_scroll_info()
                        )
                
                    # 智能GPU选择：优先使用空闲GPU，避免内存不足
                    # 构建完整的输入序列（按照实际发送给模型的顺序）
                    # 注意：build_complete_input_sequence 方法内部会打印完整的输入序列包含special tokens
                    complete_input_sequence = self.build_complete_input_sequence(current_view_path, scenario_prompt, interaction_count, viewport, scroll_history)
                    
                    # 使用统一的模型推理接口
                    response = ''
                    if (self.use_vllm and self.vllm_engine) or (self.model_type == "qwen3vl" and self.qwen3vl_wrapper) or (self.model_type == "glm4v" and self.glm4v_wrapper):
                        self.logger.info(f"USING {self.model_type.upper()} MODEL")
                        
                        result = self.generate_model_response(
                            image_path=current_view_path,
                            prompt=scenario_prompt,
                            max_tokens=1024,  # 增加token限制以获得完整思考
                            temperature=1.0,
                            top_p=0.8
                        )
                        
                        if result and result.get('success'):
                            response = result.get('response', '')
                            inference_time = result.get('inference_time', 0)
                            self.logger.info(f"✅ {self.model_type.upper()}推理完成 - 耗时: {inference_time:.1f}秒")
                        else:
                            self.logger.error(f"❌ {self.model_type.upper()}推理失败，回退到Transformer")
                            # 回退到transformer推理
                            if hasattr(self, 'manual_gpu_id') and self.manual_gpu_id is not None:
                                gpu_id = self.manual_gpu_id
                                self.logger.info(f"🎯 使用手动指定的GPU {gpu_id}")
                            else:
                                gpu_id = self.gpu_tester.select_best_gpu()
                                self.logger.info(f"🔍 自动选择空闲GPU {gpu_id}")
                            
                            result = self.gpu_tester.single_gpu_inference(
                                region_path=current_view_path,
                                prompt=scenario_prompt,
                                gpu_id=gpu_id,
                                scenario=scenario_name
                            )
                            response = result.get('response', '') if result else ''
                    else:
                        # 使用Transformer推理（回退模式）
                        self.logger.info("🔧 使用Transformer推理引擎")
                        
                        if hasattr(self, 'manual_gpu_id') and self.manual_gpu_id is not None:
                            gpu_id = self.manual_gpu_id
                            self.logger.info(f"🎯 使用手动指定的GPU {gpu_id}")
                        else:
                            gpu_id = self.gpu_tester.select_best_gpu()
                            self.logger.info(f"🔍 自动选择空闲GPU {gpu_id}")
                        
                        result = self.gpu_tester.single_gpu_inference(
                            region_path=current_view_path,
                            prompt=scenario_prompt,
                            gpu_id=gpu_id,
                            scenario=scenario_name
                        )
                        response = result.get('response', '') if result else ''
                    
                    # 记录完整的UI-TARS响应用于调试
                    self.logger.info(f"UI-TARS COMPLETE RESPONSE:")
                    self.logger.info(f"{'='*50}")
                    self.logger.info(f"{response}")
                    self.logger.info(f"{'='*50}")
                    
                    # 🔧 保存最后一次响应用于超时时的语义分析
                    last_response = response
                    
                    if response:
                        # 🎯 对于Qwen3-VL和GLM-4V，优先使用wrapper返回的坐标
                        if self.model_type in ["qwen3vl", "glm4v"] and result and result.get('coordinates'):
                            wrapper_coords = result['coordinates']
                            self.logger.info(f"🔧 使用{self.model_type.upper()} wrapper解析的坐标: {wrapper_coords}")
                            # 构造action_info使用wrapper的坐标
                            action_info = self.parse_uitars_response(response)
                            if wrapper_coords and isinstance(wrapper_coords, (tuple, list)) and len(wrapper_coords) == 2:
                                action_info['coordinates'] = wrapper_coords
                                # 如果parse没有识别出action_type，从响应中判断
                                if action_info['action_type'] == 'unknown':
                                    # 🎯 修正：优先从 "Action:" 字段精确提取action类型
                                    import re
                                    action_field_match = re.search(r'Action:\s*(\w+)\s*\(', response, re.IGNORECASE)
                                    if action_field_match:
                                        # 从Action字段提取到明确的操作类型
                                        extracted_action = action_field_match.group(1).lower()
                                        if extracted_action in ['click', 'scroll']:
                                            action_info['action_type'] = extracted_action
                                            self.logger.info(f"   ✅ 从Action字段提取到action_type: {extracted_action}")
                                            
                                            # 如果是scroll，提取direction
                                            if extracted_action == 'scroll':
                                                direction_match = re.search(r'direction=[\'"]?(up|down|left|right)[\'"]?', response, re.IGNORECASE)
                                                if direction_match:
                                                    action_info['direction'] = direction_match.group(1).lower()
                                                elif 'down' in response.lower():
                                                    action_info['direction'] = 'down'
                                                elif 'up' in response.lower():
                                                    action_info['direction'] = 'up'
                                                else:
                                                    action_info['direction'] = 'down'  # 默认
                                        else:
                                            # Action字段存在但不是click/scroll，有坐标时默认为click
                                            action_info['action_type'] = 'click'
                                            self.logger.info(f"   ⚠️ Action字段值为'{extracted_action}'，有坐标默认为click")
                                    else:
                                        # 没有找到Action字段，从响应文本推断（但更谨慎）
                                        # 只有同时包含'scroll'和'direction='才判定为scroll
                                        if 'scroll' in response.lower() and 'direction=' in response.lower():
                                            action_info['action_type'] = 'scroll'
                                            if 'down' in response.lower():
                                                action_info['direction'] = 'down'
                                            elif 'up' in response.lower():
                                                action_info['direction'] = 'up'
                                            self.logger.info(f"   ℹ️ 从文本推断为scroll (包含direction=)")
                                        else:
                                            # 有坐标但action_type未识别，默认为click
                                            action_info['action_type'] = 'click'
                                            self.logger.info(f"   ℹ️ 有坐标但未明确action，默认为click")
                        else:
                            # 解析UI-TARS的响应
                            action_info = self.parse_uitars_response(response)
                        
                        # 🔧 优先使用action_info中已解析的坐标
                        if action_info.get('coordinates'):
                            viewport_coords = action_info['coordinates']
                            self.logger.info(f"   ✅ 使用action_info中的坐标: {viewport_coords}")
                        else:
                            # 如果action_info没有坐标，尝试其他格式
                            import re
                            box_match = re.search(r'<\|box_start\|>\s*\((\d+)\s*,\s*(\d+)\)\s*<\|box_end\|>', response)
                            if box_match and self.model_type != "qwen3vl":  # Qwen3-VL不使用这种格式
                                viewport_coords = (int(box_match.group(1)), int(box_match.group(2)))
                                self.logger.info(f"   🎯 成功解析UI-TARS格式坐标: {viewport_coords}")
                            else:
                                viewport_coords = (0, 0)
                                self.logger.debug(f"   ❌ 未能解析坐标，使用默认值")
                        
                        # 将视口坐标映射回完整截图坐标
                        if viewport_coords != (0, 0):
                            # 安全的元组解包
                            try:
                                full_coords = viewport.map_viewport_coords_to_full(viewport_coords[0], viewport_coords[1])
                            except (IndexError, ValueError) as e:
                                self.logger.warning(f"⚠️ 视口坐标映射失败: {viewport_coords}, 错误: {e}")
                                full_coords = (0, 0)
                        else:
                            full_coords = (0, 0)
                        
                        # 计算与目标的距离
                        if full_coords != (0, 0):
                            # 安全的坐标解包用于距离计算
                            try:
                                if isinstance(target_coords, dict):
                                    target_x = target_coords.get('x', 0)
                                    target_y = target_coords.get('y', 0)
                                elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
                                    target_x, target_y = target_coords[0], target_coords[1]
                                else:
                                    self.logger.warning(f"⚠️ 无法解析目标坐标格式: {target_coords}")
                                    target_x, target_y = 0, 0
                                
                                distance = ((full_coords[0] - target_x)**2 + (full_coords[1] - target_y)**2)**0.5
                            except (IndexError, ValueError, TypeError) as e:
                                self.logger.warning(f"⚠️ 距离计算失败: full_coords={full_coords}, target_coords={target_coords}, 错误: {e}")
                                distance = float('inf')
                            
                            # 🔧 修正：只有click行为才算测试成功，scroll行为不算测试完成
                            action_type = action_info.get('action_type', 'unknown')
                            if action_type == 'click':
                                # 🎯 使用坐标管理器进行目标区域判定（严格矩形模式）
                                coordinate_hit_result = self.coordinate_manager.is_click_in_target_area(
                                    scenario=scenario_name,
                                    variant_name=variant_name,
                                    click_x=full_coords[0],
                                    click_y=full_coords[1],
                                    hit_radius=0  # 强制只使用精确矩形判断
                                )
                                success = coordinate_hit_result['is_hit']
                                if success:
                                    self.logger.info(f"🎯 坐标命中: ✅ {coordinate_hit_result['message']}")
                                else:
                                    self.logger.info(f"🎯 坐标未命中: ❌ {coordinate_hit_result['message']}")
                                    # 仍然保留距离信息用于分析
                                    self.logger.info(f"   距离中心: {coordinate_hit_result.get('distance_to_center', distance):.1f}px")
                            else:
                                # scroll或其他行为不算测试成功，继续测试
                                success = False
                        else:
                            distance = float('inf')
                            success = False
                        
                        confidence = result.get('confidence', 0.0)
                        action_type = action_info.get('action_type', 'unknown')
                        
                        # 记录提取的行为信息
                        self.logger.info(f"ACTION ANALYSIS:")
                        self.logger.info(f"   ACTION TYPE: {action_type}")
                        
                        # 只有click行为才需要进行坐标分析和语义分析
                        if action_type == 'click':
                            self.logger.info(f"   VIEWPORT COORDS: {viewport_coords}")
                            self.logger.info(f"   GLOBAL COORDS: {full_coords}")
                            self.logger.info(f"   TARGET COORDS: {target_coords}")
                            self.logger.info(f"   DISTANCE: {distance:.1f}px")
                            self.logger.info(f"   COORDINATE SUCCESS: {success}")
                            self.logger.info(f"   CONFIDENCE: {confidence:.3f}")
                            
                            # 基于语义的响应分析：检查是否提到了目标产品
                            target_product_names = scenario_info.get('target_product_names', [])
                            
                            # 防护性检查：确保analyze_product_mention返回正确的结果
                            try:
                                analysis_result = self.analyze_product_mention(response, target_product_names, scenario_name)
                                
                                # 验证返回结果的结构
                                if not isinstance(analysis_result, dict):
                                    self.logger.error(f"⚠️ analyze_product_mention返回了非字典类型: {type(analysis_result)}, 值: {analysis_result}")
                                    analysis_result = {
                                        'is_successful': False,
                                        'success_level': 'analysis_error',
                                        'semantic_score': 0.0,
                                        'error': f'Invalid return type: {type(analysis_result)}'
                                    }
                                
                                # 验证必需的键是否存在
                                required_keys = ['is_successful', 'success_level', 'semantic_score']
                                for key in required_keys:
                                    if key not in analysis_result:
                                        self.logger.error(f"⚠️ analyze_product_mention缺少必需键: {key}")
                                        analysis_result[key] = False if key == 'is_successful' else ('analysis_error' if key == 'success_level' else 0.0)
                                
                            except Exception as analysis_error:
                                self.logger.error(f"❌ 语义分析过程出错: {analysis_error}")
                                analysis_result = {
                                    'is_successful': False,
                                    'success_level': 'analysis_exception',
                                    'semantic_score': 0.0,
                                    'error': str(analysis_error)
                                }
                            
                            # 提取分析结果（添加安全访问）
                            is_successful = analysis_result.get('is_successful', False)
                            success_level = analysis_result.get('success_level', 'unknown')
                            semantic_score = analysis_result.get('semantic_score', 0.0)
                            
                            # 🎯 整合语义分析和坐标分析结果
                            # 结合坐标成功和语义成功的综合判定
                            coordinate_success = success  # 基于点击坐标的成功判定
                            semantic_success = is_successful  # 基于语义分析的成功判定
                            
                            # 确定最终的成功级别
                            if coordinate_success and semantic_success:
                                final_success_level = 'precise_success'  # 坐标和语义都成功
                                final_success = True
                            elif coordinate_success:
                                final_success_level = 'coordinate_success'  # 仅坐标成功
                                final_success = True
                            elif semantic_success:
                                final_success_level = 'semantic_success'  # 仅语义成功
                                final_success = True
                            else:
                                final_success_level = success_level  # 使用原始分析的失败级别
                                final_success = False
                            
                            # 📊 详细记录语义分析结果
                            self.logger.info(f"📋 完整测试结果:")
                            self.logger.info(f"   🎯 坐标成功: {coordinate_success}")
                            self.logger.info(f"   🧠 语义成功: {semantic_success}")
                            self.logger.info(f"   🏆 最终成功: {final_success}")
                            self.logger.info(f"   📊 成功级别: {final_success_level}")
                            self.logger.info(f"   💯 语义得分: {semantic_score:.3f}")
                            
                            test_result = {
                                'test_id': test_id,
                                'interaction_count': interaction_count,
                                'final_state': 'clicked',
                                'viewport_coords': viewport_coords,
                                'global_coords': full_coords,
                                'target_coords': target_coords,
                                'distance': distance,
                                'success': final_success,  # 使用综合成功判定
                                'coordinate_success': coordinate_success,  # 单独记录坐标成功
                                'confidence': confidence,
                                'response': response,
                                'action_info': action_info,
                                'semantic_analysis': analysis_result,  # 完整的语义分析结果
                                'is_successful': final_success,  # 使用综合成功判定
                                'success_level': final_success_level,  # 使用综合成功级别
                                'semantic_score': semantic_score,
                                'semantic_success': semantic_success,  # 单独记录语义成功
                                'scroll_info': viewport.get_scroll_info()
                            }
                        else:
                            # scroll或其他行为，不进行语义分析，只记录基本信息
                            self.logger.info(f"   ⚡ {action_type.upper()} 行为，继续测试...")
                            
                            test_result = {
                                'test_id': test_id,
                                'interaction_count': interaction_count,
                                'final_state': 'scrolling',
                                'action_type': action_type,
                                'response': response,
                                'action_info': action_info,
                                'scroll_info': viewport.get_scroll_info(),
                                # 为一致性添加缺失字段
                                'viewport_coords': (0, 0),
                                'global_coords': (0, 0),
                                'target_coords': target_coords if 'target_coords' in locals() else (0, 0),
                                'distance': float('inf'),
                                'success': False,
                                'confidence': 0.0,
                                'semantic_analysis': {'is_successful': False, 'success_level': 'scroll', 'semantic_score': 0.0},
                                'is_successful': False,
                                'success_level': 'scroll',
                                'semantic_score': 0.0
                            }
                        
                    # 🔧 根据行为类型决定测试是否完成
                    action_type = action_info.get('action_type', 'unknown')
                    
                    # 📝 提取并显示agent的思考过程和观察到的商品
                    thought = self.extract_thought_from_response(response)
                    current_viewport_items = self.extract_viewport_items(response, viewport.get_scroll_info(), scenario_name)
                    
                    # 如果没有提取到商品信息，但agent有思考内容，尝试进一步分析
                    if not current_viewport_items and thought:
                        # 尝试从思考中提取任何可能的商品相关信息
                        import re
                        # 查找任何看起来像产品名称或描述的内容
                        potential_items = re.findall(r'([A-Z][a-zA-Z\s\d]{10,50})', thought)
                        if potential_items:
                            current_viewport_items = [item.strip() for item in potential_items[:3] if len(item.strip()) > 10]
                    
                    # 记录并显示详细的交互信息
                    self.logger.info(f"AGENT THOUGHT: {thought}")
                    self.logger.info(f"IDENTIFIED ITEMS: {', '.join(current_viewport_items) if current_viewport_items else 'None'}")
                    
                    # 显示历史记录 - 新格式：viewport图片 → thought+action映射
                    if scroll_history:
                        self.logger.info(f"INTERACTION HISTORY: {len(scroll_history)} previous viewport-response mappings")
                        # 显示完整历史，不再限制为最近3次
                        for i, history_entry in enumerate(scroll_history, 1):
                            if isinstance(history_entry, dict):
                                # 新格式：显示viewport图片到思考+行动的映射
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
                                # 兼容旧格式
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
                        # 点击行为 - 测试完成，记录结果
                        product_name = action_info.get('product_name', 'unknown item')
                        action_description = f"Clicked on '{product_name}' at coordinates {viewport_coords}"
                        observation = f"Selected item with confidence {confidence:.3f}"
                        
                        # 将完整信息加入历史记录 - 新格式：viewport_image: thought+action
                        history_entry = {
                            'viewport_image_path': current_view_path,  # 存储当前viewport图片路径
                            'viewport_image_name': os.path.basename(current_view_path),  # 图片文件名
                            'action': action_description,
                            'observation': observation, 
                            'thought': thought,
                            'action_response': response,  # 完整的agent响应
                            'viewport_items': current_viewport_items,
                            'scroll_position': f"{viewport.current_y_offset}/{viewport.max_y_offset}",
                            'interaction_id': interaction_count,
                            'timestamp': datetime.now().isoformat()
                        }
                        scroll_history.append(history_entry)
                        
                        test_complete = True
                        results.append(test_result)
                        
                        # 显示完整的测试结果分析
                        self.logger.info(f"TEST {test_id + 1} COMPLETED: Click action detected")
                        self.logger.info(f"SELECTED ITEM: {product_name}")
                        self.logger.info(f"CLICK COORDINATES: {viewport_coords} (viewport) -> {full_coords} (global)")
                        self.logger.info(f"TARGET COORDINATES: {target_coords}")
                        self.logger.info(f"DISTANCE TO TARGET: {distance:.1f}px")
                        self.logger.info(f"COORDINATE SUCCESS: {success}")
                        
                        # 详细的语义分析结果
                        self.logger.info(f"SEMANTIC ANALYSIS RESULTS:")
                        self.logger.info(f"  SEMANTIC SUCCESS: {is_successful}")
                        self.logger.info(f"  SEMANTIC SCORE: {semantic_score:.3f}")
                        self.logger.info(f"  SUCCESS LEVEL: {success_level}")
                        self.logger.info(f"  RESPONSE TYPE: {analysis_result.get('response_type', 'unknown')}")
                        self.logger.info(f"  IS DESCRIPTIVE: {analysis_result.get('is_descriptive', False)}")
                        
                        # 目标产品信息
                        target_product_names = scenario_info.get('target_product_names', [])
                        if target_product_names:
                            self.logger.info(f"TARGET PRODUCTS: {', '.join(target_product_names[:3])}")
                        
                        # 点击完成，跳过后续处理
                        break  # 跳出while循环
                        
                    elif action_type == 'scroll':
                        # 滚动行为 - 在同一测试中继续，不结束测试
                        old_y = viewport.current_y_offset
                        
                        # 🎯 获取滚动方向
                        scroll_direction = action_info.get('direction', 'down')
                        self.logger.info(f"🔄 检测到滚动行为: {scroll_direction}")
                        
                        # 🎯 特殊处理：如果在最终决策阶段尝试向下滚动但已到底部
                        scroll_info = viewport.get_scroll_info()
                        at_bottom = scroll_info.get('scroll_progress', 0) >= 0.99
                        at_top = viewport.current_y_offset <= 0
                        max_interactions = self.test_configs.get('max_interactions', 15)
                        final_decision_threshold = max(max_interactions - 3, 5)
                        is_final_decision = interaction_count >= final_decision_threshold or at_bottom
                        
                        # 处理向上滚动
                        if scroll_direction == 'up':
                            self.logger.info(f"📜 向上滚动: 从位置 {old_y} 开始")
                            can_scroll_up = viewport.scroll_up()
                            if can_scroll_up:
                                new_y = viewport.current_y_offset
                                self.logger.info(f"✅ 向上滚动成功: {old_y} -> {new_y}")
                                action_description = f"Scrolled up from position {old_y} to {new_y}"
                                observation = f"Viewing content at scroll position {new_y}/{viewport.max_y_offset}"
                            else:
                                self.logger.warning(f"⚠️ 无法向上滚动，已在页面顶部 - 忽略此次无效滚动")
                                # 不添加历史记录，直接continue让循环继续（会被后面的边界检查捕获）
                                action_description = None  # 标记为无效操作
                                observation = None
                        
                        # 处理向下滚动
                        elif scroll_direction == 'down':
                            self.logger.info(f"📜 向下滚动: 从位置 {old_y} 开始")
                            can_scroll_down = viewport.scroll_down()
                            if can_scroll_down:
                                new_y = viewport.current_y_offset
                                self.logger.info(f"✅ 向下滚动成功: {old_y} -> {new_y}")
                                action_description = f"Scrolled down from position {old_y} to {new_y}"
                                observation = f"Viewing content at scroll position {new_y}/{viewport.max_y_offset}"
                            else:
                                self.logger.warning(f"⚠️ 无法向下滚动，已在页面底部 - 忽略此次无效滚动")
                                # 不添加历史记录，直接continue让循环继续（会被后面的边界检查捕获）
                                action_description = None  # 标记为无效操作
                                observation = None
                        
                        else:
                            # 默认向下滚动
                            can_scroll = viewport.scroll_down()
                            new_y = viewport.current_y_offset
                            action_description = f"Scrolled down from position {old_y} to {new_y}"
                            observation = f"Viewing content at scroll position {new_y}/{viewport.max_y_offset}"
                        
                        # 将滚动信息加入历史记录 - 新格式：viewport_image: thought+action
                        # 只有在有效操作时才添加历史记录（action_description不为None）
                        if action_description is not None:
                            history_entry = {
                                'viewport_image_path': current_view_path,  # 存储当前viewport图片路径
                                'viewport_image_name': os.path.basename(current_view_path),  # 图片文件名
                                'action': action_description,
                                'observation': observation,
                                'thought': thought,
                                'action_response': response,  # 完整的agent响应
                                'viewport_items': current_viewport_items,
                                'scroll_position': f"{viewport.current_y_offset}/{viewport.max_y_offset}",
                                'interaction_id': interaction_count,
                                'timestamp': datetime.now().isoformat()
                            }
                            scroll_history.append(history_entry)
                        
                        # 检查是否可以继续操作
                        if scroll_direction == 'up':
                            if viewport.current_y_offset > 0:
                                # 成功向上滚动，继续
                                scroll_count += 1
                                self.logger.info(f"SCROLL UP ACTION {scroll_count}: Test {test_id + 1} scrolling up to find better product")
                                self.logger.info(f"SCROLL HISTORY: {len(scroll_history)} interactions recorded")
                                continue  # 直接进入下一轮交互
                            else:
                                # 已在顶部，无法向上滚动，跳过此次无效交互
                                self.logger.warning(f"⚠️ REACHED TOP: Agent tried to scroll up but already at page top (y_offset=0)")
                                self.logger.info(f"⏭️  Skipping this invalid scroll attempt, agent can try different action in next interaction")
                                continue  # 跳过这次无效的滚动尝试，进入下一轮交互
                        elif scroll_direction == 'down':
                            if viewport.current_y_offset < viewport.max_y_offset:
                                # 成功向下滚动，继续
                                scroll_count += 1
                                self.logger.info(f"SCROLL DOWN ACTION {scroll_count}: Test {test_id + 1} continues scrolling")
                                self.logger.info(f"SCROLL HISTORY: {len(scroll_history)} interactions recorded")
                                continue  # 直接进入下一轮交互
                            else:
                                # 已在底部，无法向下滚动
                                self.logger.warning(f"⚠️ REACHED BOTTOM: Agent tried to scroll down but already at page bottom")
                                self.logger.info(f"⏭️  Skipping this invalid scroll attempt, agent can try different action in next interaction")
                                continue  # 跳过这次无效的滚动尝试，进入下一轮交互
                        else:
                            # 其他方向，正常继续
                            scroll_count += 1
                            continue
                            
                    else:
                        # 其他行为 - 按原逻辑处理，新格式：viewport_image: thought+action
                        action_description = f"Performed {action_type} action"
                        observation = f"Action result: {response[:100]}..."
                        
                        history_entry = {
                            'viewport_image_path': current_view_path,  # 存储当前viewport图片路径
                            'viewport_image_name': os.path.basename(current_view_path),  # 图片文件名
                            'action': action_description,
                            'observation': observation,
                            'thought': thought,
                            'action_response': response,  # 完整的agent响应
                            'viewport_items': current_viewport_items,
                            'scroll_position': f"{viewport.current_y_offset}/{viewport.max_y_offset}",
                            'interaction_id': interaction_count,
                            'timestamp': datetime.now().isoformat()
                        }
                        scroll_history.append(history_entry)
                        
                        test_complete = True
                        results.append(test_result)
                        # 其他行为完成，跳过后续处理
                        break  # 跳出while循环
                        
                    # ⚠️ 关键修复：只在测试真正完成时才删除文件
                    # 对于scroll操作，文件需要保留给下一轮推理使用
                    if test_complete:
                        try:
                            if os.path.exists(current_view_path):
                                os.unlink(current_view_path)
                                self.logger.debug(f"🗑️ 测试完成，清理视口文件")
                        except Exception:
                            pass
                            
                else:
                    # 推理失败 - 直接标记为失败
                    self.logger.error(f"❌ 测试 {test_id + 1} 推理失败 - 交互#{interaction_count}")
                    self.logger.error(f"   当前滚动位置: y={viewport.current_y_offset if 'viewport' in locals() else 'unknown'}")
                    self.logger.error(f"   推理响应为空或格式错误，可能的原因：")
                    self.logger.error(f"   1. vLLM引擎返回空响应")
                    self.logger.error(f"   2. 模型输出格式不符合预期")
                    self.logger.error(f"   3. 图像路径无效或图像损坏")
                    
                    test_result = {
                        'test_id': test_id,
                        'interaction_count': interaction_count,
                        'final_state': 'inference_failed',
                        'viewport_coords': (0, 0),
                        'global_coords': (0, 0),
                        'target_coords': target_coords,
                        'distance': float('inf'),
                        'success': False,
                        'coordinate_success': False,  # 添加坐标成功字段
                        'confidence': 0.0,
                        'response': 'Inference failed',
                        'action_info': {'action_type': 'failed'},
                        'semantic_analysis': {'is_successful': False, 'success_level': 'failed', 'semantic_score': 0.0},
                        'is_successful': False,
                        'success_level': 'failed',
                        'semantic_score': 0.0,
                        'semantic_success': False,  # 添加语义成功字段
                        'scroll_info': viewport.get_scroll_info()
                    }
                    test_complete = True
                    results.append(test_result)
                    
                    try:
                        if os.path.exists(current_view_path):
                            os.unlink(current_view_path)
                    except Exception:
                        pass
                            
                # 检查是否超过最大交互次数限制
                max_interactions = self.test_configs.get('max_interactions', 20)
                
                if interaction_count >= max_interactions and not test_complete:
                    # 超时 - 标记为决策超时失败，但仍然进行语义分析
                    self.logger.warning(f"⚠️ 达到最大交互次数限制({interaction_count})，测试超时")
                    self.logger.info(f"🔍 对最后一次响应进行语义分析...")
                    
                    # 🔧 对最后一次响应进行语义分析
                    semantic_result = {'is_successful': False, 'success_level': 'timeout', 'semantic_score': 0.0}
                    semantic_success = False
                    
                    if last_response:
                        try:
                            # 提取Agent的思考内容进行语义分析
                            agent_thought = self.extract_agent_thought(last_response)
                            identified_items = self.extract_identified_items(last_response)
                            
                            self.logger.info(f"AGENT THOUGHT: {agent_thought[:200]}..." if len(agent_thought) > 200 else f"AGENT THOUGHT: {agent_thought}")
                            self.logger.info(f"IDENTIFIED ITEMS: {identified_items[:200]}..." if len(identified_items) > 200 else f"IDENTIFIED ITEMS: {identified_items}")
                            
                            # 执行语义分析
                            semantic_result = self.analyze_semantic_success(
                                response=last_response,
                                agent_thought=agent_thought,
                                identified_items=identified_items,
                                target_product_names=config.get('target_product_names', []),
                                card_keywords=config.get('card_keywords', [])
                            )
                            
                            semantic_success = semantic_result.get('is_successful', False)
                            self.logger.info(f"📊 超时情况的语义分析:")
                            self.logger.info(f"  - 语义成功: {semantic_success}")
                            self.logger.info(f"  - 语义得分: {semantic_result.get('semantic_score', 0):.3f}")
                            self.logger.info(f"  - 成功级别: {semantic_result.get('success_level', 'unknown')}")
                            
                        except Exception as e:
                            self.logger.error(f"❌ 语义分析失败: {e}")
                            import traceback
                            self.logger.error(f"   错误详情: {traceback.format_exc()}")
                    
                    test_result = {
                        'test_id': test_id,
                        'interaction_count': interaction_count,
                        'final_state': 'timeout',
                        'viewport_coords': (0, 0),
                        'global_coords': (0, 0),
                        'target_coords': target_coords,
                        'distance': float('inf'),
                        'success': semantic_success,  # 🔧 基于语义分析结果
                        'coordinate_success': False,  # 超时情况下坐标肯定失败
                        'confidence': 0.0,
                        'response': last_response if last_response else f'Timeout after {interaction_count} interactions',
                        'action_info': {'action_type': 'timeout'},
                        'semantic_analysis': semantic_result,
                        'is_successful': semantic_success,  # 🔧 基于语义分析结果
                        'success_level': semantic_result.get('success_level', 'timeout'),
                        'semantic_score': semantic_result.get('semantic_score', 0.0),
                        'semantic_success': semantic_success,  # 🔧 添加语义成功字段
                        'scroll_info': viewport.get_scroll_info()
                    }
                    results.append(test_result)
                    test_complete = True
            
            except Exception as test_error:
                # 🔧 单个测试失败，记录失败结果而不是跳过
                self.logger.error(f"❌ TEST {test_id + 1} 执行失败: {test_error}")
                import traceback
                self.logger.error(f"   错误详情: {traceback.format_exc()}")
                
                # 为失败的测试创建失败记录而不是跳过
                failed_test_result = {
                    'test_id': test_id,
                    'interaction_count': interaction_count if 'interaction_count' in locals() else 0,
                    'final_state': 'error',
                    'viewport_coords': (0, 0),
                    'global_coords': (0, 0),
                    'target_coords': target_coords if 'target_coords' in locals() else (0, 0),
                    'distance': float('inf'),
                    'success': False,
                    'coordinate_success': False,  # 添加坐标成功字段
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
                    'semantic_success': False,  # 添加语义成功字段
                    'scroll_info': {'error': str(test_error)},
                    'error': str(test_error)
                }
                results.append(failed_test_result)
                self.logger.info(f"✅ TEST {test_id + 1} 已记录为失败而非跳过")
            
            # 🔧 CRITICAL: 强制确保每个测试都有记录
            finally:
                # 检查当前测试是否已经有记录
                current_test_recorded = any(r.get('test_id') == test_id for r in results)
                if not current_test_recorded:
                    self.logger.error(f"🚨 CRITICAL: TEST {test_id + 1} 没有被记录到results中，强制添加missing记录")
                    # 🔧 修复：确保missing记录与正常测试结果使用相同的数据结构
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
                    self.logger.error(f"   🆘 已强制添加TEST {test_id + 1}的missing记录")
                
                # 测试完成确认
                self.logger.info(f"🏁 TEST {test_id + 1}/{tests_to_run} [COMPLETED] - 当前results长度: {len(results)}")
                self.logger.info(f"📊 已记录的test_ids: {[r.get('test_id') for r in results]}")
        
        # 🔧 测试循环完成后的状态检查
        self.logger.info(f"✅ 测试循环完成! 共执行了 {tests_to_run} 轮循环")
        self.logger.info(f"📋 当前results数量: {len(results)}")
        self.logger.info(f"🔍 实际记录的test_ids: {sorted([r.get('test_id') for r in results])}")
        self.logger.info(f"🎯 期望的test_ids: {list(range(tests_to_run))}")
        
        # 🔧 第一步: 去重 - 每个test_id只保留最后一个结果
        original_count = len(results)
        if results:
            unique_results = {}
            for result in results:
                test_id = result.get('test_id')
                if test_id is not None:
                    unique_results[test_id] = result  # 后出现的会覆盖先前的
            results = sorted(unique_results.values(), key=lambda x: x.get('test_id', 0))
            if len(results) != original_count:
                self.logger.warning(f"🔄 去重: {original_count} → {len(results)} (删除 {original_count - len(results)} 个重复)")
            self.logger.info(f"🔍 去重后test_ids: {sorted([r.get('test_id') for r in results])}")
        
        # 🔧 第二步: 检测并补充缺失的测试
        expected_test_ids = set(range(tests_to_run))
        actual_test_ids = {r['test_id'] for r in results if 'test_id' in r and isinstance(r['test_id'], int)}
        missing_test_ids = expected_test_ids - actual_test_ids
        
        if missing_test_ids:
            self.logger.warning(f"⚠️ 检测到 {len(missing_test_ids)} 个缺失的测试: {sorted(missing_test_ids)}")
            # 为缺失的测试添加失败记录
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
                self.logger.info(f"   ✚ 已为test {missing_id} 补充失败记录")
            
        # 计算汇总统计
        # 🔧 CRITICAL修复：确保results不为空并且有有效数据  
        self.logger.info(f"🔍 Final results检查: results={results}, len={len(results) if results else 'None'}, type={type(results)}")
        if results and len(results) > 0:
            successful_tests = [r for r in results if r.get('success', False)]
            # 🔧 修复: 使用配置的测试数作为分母,而不是len(results)
            total_tests = tests_to_run
            recorded_tests = len([r for r in results if r.get('action_info', {}).get('action_type') != 'missing'])
            missing_tests = len(missing_test_ids)
            
            success_rate = len(successful_tests) / total_tests if total_tests > 0 else 0
            
            # 🔧 更新：使用新的综合成功判定统计
            # 1. 综合成功（坐标或语义任一成功）
            comprehensive_successes = [r for r in results if r.get('is_successful', False)]  # 基于综合判定
            comprehensive_success_rate = len(comprehensive_successes) / total_tests if total_tests > 0 else 0
            
            # 2. 单独统计各种成功类型
            coordinate_only_successes = [r for r in results if r.get('coordinate_success', False) and not r.get('semantic_success', False)]
            semantic_only_successes = [r for r in results if r.get('semantic_success', False) and not r.get('coordinate_success', False)]
            dual_successes = [r for r in results if r.get('coordinate_success', False) and r.get('semantic_success', False)]
            
            # 🎯 核心修正：基于 coordinate_success 和 semantic_success 字段直接统计
            coordinate_successes_all = [r for r in results if r.get('coordinate_success', False)]
            semantic_successes_all = [r for r in results if r.get('semantic_success', False)]
            
            # 3. 成功级别分类统计（保留用于兼容性）
            precise_successes = [r for r in results if r.get('success_level') == 'precise_success']
            semantic_successes = [r for r in results if r.get('success_level') == 'semantic_success']
            coordinate_successes = [r for r in results if r.get('success_level') == 'coordinate_success']
            
            # 4. 计算各种成功率 - 使用正确的coordinate_success和semantic_success
            coordinate_only_rate = len(coordinate_only_successes) / total_tests if total_tests > 0 else 0
            semantic_only_rate = len(semantic_only_successes) / total_tests if total_tests > 0 else 0
            dual_success_rate = len(dual_successes) / total_tests if total_tests > 0 else 0
            precise_success_rate = len(precise_successes) / total_tests if total_tests > 0 else 0
            semantic_success_rate_old = len(semantic_successes) / total_tests if total_tests > 0 else 0
            coordinate_success_rate_old = len(coordinate_successes) / total_tests if total_tests > 0 else 0
            
            # 🎯 真正的坐标成功率和语义成功率
            true_coordinate_success_rate = len(coordinate_successes_all) / total_tests if total_tests > 0 else 0
            true_semantic_success_rate = len(semantic_successes_all) / total_tests if total_tests > 0 else 0
            
            # 5. 语义得分统计
            semantic_scores = [r.get('semantic_score', 0.0) for r in results]
            avg_semantic_score = sum(semantic_scores) / len(semantic_scores) if semantic_scores else 0.0
            max_semantic_score = max(semantic_scores) if semantic_scores else 0.0
            min_semantic_score = min(semantic_scores) if semantic_scores else 0.0
            
            # 距离统计
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
                'successful_tests': len(comprehensive_successes),  # 使用综合成功数
                'overall_success_rate': comprehensive_success_rate,  # 使用综合成功率
                
                # 详细的成功分类统计
                'comprehensive_successes': len(comprehensive_successes),
                'coordinate_only_successes': len(coordinate_only_successes),
                'semantic_only_successes': len(semantic_only_successes),
                'dual_successes': len(dual_successes),
                
                # 🎯 真正的坐标和语义成功统计
                'true_coordinate_successes': len(coordinate_successes_all),
                'true_semantic_successes': len(semantic_successes_all),
                'true_coordinate_success_rate': true_coordinate_success_rate,
                'true_semantic_success_rate': true_semantic_success_rate,
                
                # 各种成功率
                'comprehensive_success_rate': comprehensive_success_rate,
                'coordinate_only_rate': coordinate_only_rate,
                'semantic_only_rate': semantic_only_rate,
                'dual_success_rate': dual_success_rate,
                'precise_success_rate': precise_success_rate,
                'semantic_success_rate': semantic_success_rate_old,
                'coordinate_success_rate': coordinate_success_rate_old,
                
                # 语义得分统计
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
            
            self.logger.info(f"📊 测试完成: {scenario_name}/{variant_name}")
            self.logger.info(f"   📋 配置测试数: {total_tests}")
            self.logger.info(f"   📝 实际记录数: {recorded_tests}")
            if missing_tests > 0:
                self.logger.info(f"   ⚠️ 缺失测试数: {missing_tests}")
            
            # 🎯 详细的成功率分析报告 - 修正为正确统计coordinate_success和semantic_success
            self.logger.info(f"\n   🏆 综合成功分析:")
            self.logger.info(f"      🎯 总体成功率: {comprehensive_success_rate:.1%} ({len(comprehensive_successes)}/{total_tests})")
            self.logger.info(f"      🤝 双重成功: {dual_success_rate:.1%} ({len(dual_successes)}/{total_tests}) - 坐标+语义")
            self.logger.info(f"      📍 仅坐标成功: {coordinate_only_rate:.1%} ({len(coordinate_only_successes)}/{total_tests})")
            self.logger.info(f"      🧠 仅语义成功: {semantic_only_rate:.1%} ({len(semantic_only_successes)}/{total_tests})")
            
            self.logger.info(f"\n   🎯 核心指标 (基于coordinate_success和semantic_success字段):")
            self.logger.info(f"      📍 坐标成功: {true_coordinate_success_rate:.1%} ({len(coordinate_successes_all)}/{total_tests})")
            self.logger.info(f"      🧠 语义成功: {true_semantic_success_rate:.1%} ({len(semantic_successes_all)}/{total_tests})")
            
            self.logger.info(f"\n   📊 成功级别分布 (旧版兼容):")
            self.logger.info(f"      🎯 精确成功: {precise_success_rate:.1%} ({len(precise_successes)}/{total_tests})")
            self.logger.info(f"      📍 坐标成功级别: {coordinate_success_rate_old:.1%} ({len(coordinate_successes)}/{total_tests})")
            self.logger.info(f"      🧠 语义成功级别: {semantic_success_rate_old:.1%} ({len(semantic_successes)}/{total_tests})")
            
            self.logger.info(f"\n   💯 语义分析统计:")
            self.logger.info(f"      📈 平均得分: {avg_semantic_score:.3f}")
            self.logger.info(f"      🏆 最高得分: {max_semantic_score:.3f}")
            self.logger.info(f"      📉 最低得分: {min_semantic_score:.3f}")
            
            self.logger.info(f"\n   📏 距离统计:")
            self.logger.info(f"      📏 平均距离: {avg_distance:.1f}px")
            self.logger.info(f"      🎯 最小距离: {min_distance:.1f}px")
            self.logger.info(f"   ⏱️ 用时: {duration:.1f}秒")
            
            # 清理所有临时文件
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
                    self.logger.info(f"🧹 清理了 {temp_files_cleaned} 个临时截图文件")
            except Exception:
                pass
            
            return test_results
        else:
                # 🔧 修复：处理results为空的情况，避免返回None
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
                
                self.logger.warning(f"⚠️ 测试完成但无结果记录: {scenario_name}/{variant_name}")
                self.logger.warning(f"   📋 配置测试数: {tests_to_run}")
                self.logger.warning(f"   📝 实际记录数: 0")
                self.logger.warning(f"   ⏱️ 用时: {duration:.1f}秒")
                
                return empty_results
                
        # 🔧 CRITICAL修复：添加函数末尾安全检查，确保不会隐式返回None
        self.logger.error(f"🚨 CRITICAL: test_variant_regular到达函数末尾没有返回！这应该不会发生")
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
        """使用滚动智能搜索方法测试单个variant"""
        # TODO: 实现滚动智能搜索逻辑
        self.logger.info(f"🔧 滚动智能搜索方法暂未实现: {scenario_name}/{variant_name}")
        return {
            'results': [],
            'total_tests': 0,
            'successful_tests': 0,
            'overall_success_rate': 0,
            'error': 'Method not implemented'
        }
    
    def test_variant_no_scaling(self, scenario_name: str, variant_name: str, 
                              screenshot_path: str, num_tests: int = None) -> Dict[str, Any]:
        """不使用缩放的原始测试方法 - 用于对比测试"""
        import time
        import numpy as np
        import traceback
        
        start_time = time.time()
        
        try:
            scenario_info = self.scenarios[scenario_name]
            
            # 使用传入的num_tests参数，如果没有则使用配置中的默认值
            tests_to_run = num_tests if num_tests is not None else self.test_configs['tests_per_variant']
            
            self.logger.info(f"🔧 开始无缩放多层次语义分析测试: {scenario_name}/{variant_name}")
            self.logger.info(f"📊 测试次数: {tests_to_run}")
            
            # 检查截图大小
            from PIL import Image
            with Image.open(screenshot_path) as img:
                original_width, original_height = img.size
                self.logger.info(f"📐 原始图像尺寸: {original_width}x{original_height}")
            
            results = []
            
            for test_id in range(tests_to_run):
                self.logger.info(f"🧪 测试 {test_id + 1}/{tests_to_run}")
                
                # 使用严格匹配UI-TARS格式的prompt进行推理
                scenario_prompt = self.get_scenario_prompt(scenario_name)
                
                # 智能GPU选择：优先使用空闲GPU，避免内存不足
                if hasattr(self, 'manual_gpu_id') and self.manual_gpu_id is not None:
                    gpu_id = self.manual_gpu_id
                    self.logger.info(f"🎯 使用手动指定的GPU {gpu_id}")
                else:
                    gpu_id = self.gpu_tester.select_best_gpu()
                    self.logger.info(f"🔍 自动选择空闲GPU {gpu_id}")
                
                # 记录发送给UI-TARS的完整prompt用于调试
                self.logger.info(f"📤 发送给UI-TARS的Prompt: {scenario_prompt[:100]}...")
                
                # 使用原始完整截图进行推理（可能导致OOM）
                result = self.gpu_tester.single_gpu_inference(
                    region_path=screenshot_path,  # 直接使用完整截图
                    prompt=scenario_prompt,
                    gpu_id=gpu_id,
                    scenario=scenario_name
                )
                
                # 记录完整的UI-TARS响应用于调试
                response = result.get('response', '') if result else ''
                self.logger.info(f"📥 UI-TARS响应: {response[:200]}...")
                
                if result and 'response' in result:
                    # 使用语义分析方法评估响应
                    target_product_names = scenario_info.get('target_product_names', [])
                    semantic_analysis = self.analyze_product_mention(response, target_product_names, scenario_name)
                    
                    # 提取语义分析结果
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
                    
                    # 🔧 修复：将测试结果添加到results列表中
                    results.append(test_result)
                    
                    # 详细的结果日志
                    self.logger.info(f"📋 测试结果:")
                    self.logger.info(f"   ✅ 语义成功: {is_successful}")
                    self.logger.info(f"   🎯 成功级别: {success_level}")
                    self.logger.info(f"   📊 语义得分: {semantic_score:.3f}")
                    self.logger.info(f"   💡 响应类型: {response_type}")
                    self.logger.info(f"   📝 描述性: {is_descriptive}")
            
            # 🔧 修复：确保results不为空时才进行统计，为空时提供默认值
            total_tests = len(results) if results else tests_to_run
            
            if results:
                # 分类统计
                precise_success = [r for r in results if r['success_level'] == 'precise_success']
                approximate_success = [r for r in results if r['success_level'] == 'approximate_success']
                failures = [r for r in results if r['success_level'] in ['failure', 'failed']]
                
                # 描述性回答统计
                descriptive_responses = [r for r in results if r['response_analysis']['is_descriptive']]
            else:
                # 🔧 修复：如果没有任何测试结果，提供默认的空列表
                self.logger.warning("⚠️ 没有找到任何测试结果，使用默认空值")
                precise_success = []
                approximate_success = []
                failures = []
                descriptive_responses = []
            
            # 计算成功率
            precise_success_rate = len(precise_success) / total_tests if total_tests > 0 else 0
            approximate_success_rate = len(approximate_success) / total_tests if total_tests > 0 else 0
            overall_success_rate = (len(precise_success) + len(approximate_success)) / total_tests if total_tests > 0 else 0
            failure_rate = len(failures) / total_tests if total_tests > 0 else 0
            descriptive_rate = len(descriptive_responses) / total_tests if total_tests > 0 else 0
            
            # 🔧 修复：添加调试信息确保语义得分统计正常工作
            try:
                semantic_scores = [r['response_analysis']['semantic_score'] for r in results]
                self.logger.info(f"✅ 语义得分提取成功: {len(semantic_scores)} 个得分")
                avg_semantic_score = np.mean(semantic_scores) if semantic_scores else 0.0
                max_semantic_score = np.max(semantic_scores) if semantic_scores else 0.0
                min_semantic_score = np.min(semantic_scores) if semantic_scores else 0.0
            except Exception as e:
                self.logger.error(f"❌ 语义得分统计失败: {e}")
                self.logger.error(f"📋 Results结构: {[{k: type(v).__name__ for k, v in r.items()} for r in results[:2]]}")
                semantic_scores = []
                avg_semantic_score = max_semantic_score = min_semantic_score = 0.0
            
            result_summary = {
                'scenario': scenario_name,
                'variant': variant_name,
                'total_tests': total_tests,
                
                # 多层次成功统计
                'precise_success_count': len(precise_success),
                'approximate_success_count': len(approximate_success),
                'failure_count': len(failures),
                'descriptive_responses_count': len(descriptive_responses),
                
                # 成功率
                'precise_success_rate': precise_success_rate,
                'approximate_success_rate': approximate_success_rate,
                'overall_success_rate': overall_success_rate,
                'failure_rate': failure_rate,
                'descriptive_response_rate': descriptive_rate,
                
                # 语义得分统计
                'average_semantic_score': avg_semantic_score,
                'max_semantic_score': max_semantic_score,
                'min_semantic_score': min_semantic_score,
                
                # 时间统计
                'total_time': time.time() - start_time,
                
                'results': results,
                'image_size': {'width': original_width, 'height': original_height},
                'semantic_analysis': True,  # 标记这是语义分析测试
                'analysis_method': 'multi_level_success_criteria'
            }
            
            self.logger.info(f"✅ {scenario_name}/{variant_name} 多层次语义分析完成:")
            self.logger.info(f"   🎯 精确成功: {len(precise_success)}/{total_tests} ({precise_success_rate:.2%})")
            self.logger.info(f"   📊 近似成功: {len(approximate_success)}/{total_tests} ({approximate_success_rate:.2%})")
            self.logger.info(f"   ❌ 失败: {len(failures)}/{total_tests} ({failure_rate:.2%})")
            self.logger.info(f"   📝 描述性回答: {len(descriptive_responses)}/{total_tests} ({descriptive_rate:.2%})")
            self.logger.info(f"   🏆 总体成功率: {overall_success_rate:.2%}")
            self.logger.info(f"   📈 平均语义得分: {avg_semantic_score:.3f}")
            print(f"🎉 准备返回result_summary: {type(result_summary)}")
            print(f"🔍 result_summary内容预览: {list(result_summary.keys()) if isinstance(result_summary, dict) else 'Not a dict'}")
            self.logger.info(f"🎉 准备返回result_summary: {type(result_summary)}")
            print(f"🎉 成功返回result_summary")
            return result_summary
            
        except Exception as e:
            print(f"❌ 函数异常: {e}")
            print(f"❌ 异常类型: {type(e).__name__}")
            print(f"❌ 异常跟踪:")
            traceback.print_exc()
            self.logger.error(f"❌ 无缩放测试失败: {e}")
            self.logger.error(f"❌ 异常类型: {type(e).__name__}")
            self.logger.error(f"🔍 异常发生时results状态: 长度={len(results) if 'results' in locals() else '未定义'}")
            return None
    
    def analyze_response_content(self, response: str, scenario_name: str) -> Dict[str, Any]:
        """使用语义分析方法分析响应内容是否包含目标产品信息"""
        if not response or response.strip() == "":
            return {'contains_target_content': False, 'matched_keywords': [], 'matched_products': [], 'confidence': 0.0}
        
        scenario_info = self.scenarios[scenario_name]
        target_products = scenario_info['target_product_names']
        
        # 使用analyze_product_mention进行语义分析
        analysis_result = self.analyze_product_mention(response, target_products, scenario_name)
        
        # 从语义分析结果中提取信息
        is_successful = analysis_result['is_successful']
        semantic_score = analysis_result['semantic_score']
        response_type = analysis_result['response_type']
        
        # 提取实际提到的产品
        mentioned_products = self.extract_mentioned_products(response)
        
        # 保持与原来接口兼容的返回格式
        contains_target_content = is_successful
        
        # 为了兼容性，提供一些默认值
        matched_keywords = []  # 语义分析不依赖关键词匹配
        pattern_matches = 1 if is_successful else 0  # 简化为布尔值
        
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
        """使用滚动功能测试单个variant
        
        Args:
            scenario_name: 场景名称
            variant_name: 变体名称
            html_file: HTML文件路径（可选）
            num_tests: 每个视图的测试次数
            viewport_height: 视口高度
            scroll_step: 滚动步长
            max_scrolls: 最大滚动次数
        """
        import time
        
        start_time = time.time()
        
        try:
            # 生成完整截图（不缩放）
            if html_file:
                html_path = Path(html_file)
            else:
                scenario_info = self.scenarios[scenario_name]
                html_path = Path(scenario_info['path']) / f"{variant_name}.html"
            
            if not html_path.exists():
                self.logger.error(f"❌ HTML文件不存在: {html_path}")
                return None
            
            self.logger.info(f"🔄 开始滚动测试: {scenario_name}/{variant_name}")
            
            # 生成完整截图（启用滚动模式，保留原始尺寸）
            full_screenshot_path = self.generate_screenshot_simple(str(html_path), enable_scrolling=True)
            if not full_screenshot_path:
                self.logger.error(f"❌ 截图生成失败: {variant_name}")
                return None
            
            # 初始化滚动视口
            viewport = ScrollingViewport(
                full_screenshot_path=full_screenshot_path,
                viewport_height=viewport_height,
                viewport_width=1280,  # 🎯 使用标准宽度保持完整页面信息
                scroll_step=scroll_step
            )
            
            scenario_info = self.scenarios[scenario_name]
            
            # 从坐标管理器获取目标坐标
            target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
            if target_coordinates:
                # 计算中心坐标
                center_x = target_coordinates['x'] + target_coordinates['width'] // 2
                center_y = target_coordinates['y'] + target_coordinates['height'] // 2
                target_coords = (center_x, center_y)
                self.logger.info(f"🎯 从坐标管理器获取目标坐标: {target_coords}")
            else:
                # 如果坐标管理器中没有该variant，使用默认值
                target_coords = (400, 2000)  # 默认坐标
                self.logger.warning(f"⚠️ 坐标管理器中无该variant，使用默认坐标: {target_coords}")
            
            all_results = []
            scroll_count = 0
            found_target = False
            
            while scroll_count <= max_scrolls and not found_target:
                self.logger.info(f"📱 测试滚动位置 {scroll_count}: y_offset={viewport.current_y_offset}")
                
                # 获取当前视口截图
                current_view_path = viewport.get_current_view()
                
                # 在当前视图上运行UI-TARS测试
                view_results = []
                for test_id in range(num_tests):
                    self.logger.info(f"🧪 视图 {scroll_count}, 测试 {test_id + 1}/{num_tests}")
                    
                    # 🎯 使用自然prompt，允许UI-TARS决定滚动或点击
                    scenario_prompt = self.get_natural_scenario_prompt(scenario_name)
                    
                    self.logger.info(f"📤 发送给UI-TARS的自然Prompt:")
                    self.logger.info(f"{'='*50}")
                    self.logger.info(f"{scenario_prompt}")
                    self.logger.info(f"{'='*50}")
                    
                    # 🎯 检查是否是强制记忆决策点击
                    if 'forced_memory_click' in locals() and forced_memory_click:
                        # 跳过推理，直接使用已有的action_info和response
                        self.logger.info("🧠 处理强制记忆决策点击，跳过重复推理")
                        pass  # action_info和response已经在强制记忆决策中设置
                    else:
                        # 使用统一的模型推理接口
                        if (self.use_vllm and self.vllm_engine) or (self.model_type == "qwen3vl" and self.qwen3vl_wrapper) or (self.model_type == "glm4v" and self.glm4v_wrapper):
                            self.logger.info(f"🚀 使用{self.model_type.upper()}推理引擎")
                        
                            result = self.generate_model_response(
                                image_path=current_view_path,
                                prompt=scenario_prompt,
                                max_tokens=512,
                                temperature=1.0,
                                top_p=0.8
                            )
                            
                            if result and result.get('success'):
                                response = result.get('response', '')
                                inference_time = result.get('inference_time', 0)
                                self.logger.info(f"✅ {self.model_type.upper()}推理完成 - 耗时: {inference_time:.1f}秒")
                            else:
                                self.logger.error(f"❌ {self.model_type.upper()}推理失败，回退到Transformer")
                                # 回退到transformer推理
                                gpu_id = self.gpu_tester.select_best_gpu()
                                result = self.gpu_tester.single_gpu_inference(
                                    region_path=current_view_path,
                                    prompt=scenario_prompt,
                                    gpu_id=gpu_id,
                                    scenario=scenario_name
                                )
                                response = result.get('response', '') if result else ''
                        else:
                            # 使用Transformer推理（回退模式）
                            self.logger.info("🔧 使用Transformer推理引擎")
                            gpu_id = self.gpu_tester.select_best_gpu()
                            
                            result = self.gpu_tester.single_gpu_inference(
                                region_path=current_view_path,
                                prompt=scenario_prompt,
                                gpu_id=gpu_id,
                                scenario=scenario_name
                            )
                            response = result.get('response', '') if result else ''
                    
                    if response:
                        self.logger.info(f"📥 UI-TARS完整响应:")
                        self.logger.info(f"{'='*50}")
                        self.logger.info(f"{response}")
                        self.logger.info(f"{'='*50}")
                        
                        # 🎯 修复：使用增强版动作信息提取，包含坐标映射和命中判断
                        action_info = self.extract_action_info_with_coordinate_check(
                            response=response, 
                            scenario=scenario_name, 
                            variant_name=variant_name,
                            viewport=viewport,  # 传递viewport用于坐标映射
                            model_result=result  # 传递模型结果，用于Qwen3-VL坐标提取
                        )
                        
                        if action_info['action_type'] == 'click':
                            # 🎯 使用映射后的完整坐标
                            if action_info.get('full_coordinates'):
                                full_x, full_y = action_info['full_coordinates']
                                viewport_x, viewport_y = action_info['coordinates']  # 原始视口坐标
                                
                                # 检查点击位置是否接近目标（使用完整坐标）
                                # 安全的坐标解包用于距离计算
                                try:
                                    if isinstance(target_coords, dict):
                                        target_x = target_coords.get('x', 0)
                                        target_y = target_coords.get('y', 0)
                                    elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
                                        target_x, target_y = target_coords[0], target_coords[1]
                                    else:
                                        self.logger.warning(f"⚠️ 无法解析目标坐标格式: {target_coords}")
                                        target_x, target_y = 0, 0
                                    
                                    distance = ((full_x - target_x)**2 + (full_y - target_y)**2)**0.5
                                except (IndexError, ValueError, TypeError) as e:
                                    self.logger.warning(f"⚠️ 距离计算失败: target_coords={target_coords}, 错误: {e}")
                                    distance = float('inf')
                                
                                # 🎯 优先使用坐标管理器的精确命中判断（严格矩形模式）
                                coordinate_hit_result = self.coordinate_manager.is_click_in_target_area(
                                    scenario=scenario_name,
                                    variant_name=variant_name,
                                    click_x=full_x,
                                    click_y=full_y,
                                    hit_radius=0  # 强制只使用精确矩形判断
                                )
                                coordinate_success = coordinate_hit_result['is_hit']
                                
                                # 使用坐标区域判定作为主要成功标准，距离判定作为备用
                                success = coordinate_success or (distance <= self.test_configs['success_radius_loose'])
                                
                                if coordinate_success:
                                    found_target = True
                                    self.logger.info(f"🎯 找到目标！坐标命中: ✅ {coordinate_hit_result['message']}")
                                elif distance <= self.test_configs['success_radius_loose']:
                                    found_target = True
                                    self.logger.info(f"🎯 找到目标！距离: {distance:.1f}px (传统距离判定)")
                                else:
                                    self.logger.info(f"🎯 未命中目标: ❌ {coordinate_hit_result['message']}, 距离: {distance:.1f}px")
                            else:
                                # 如果没有坐标映射信息，使用原始逻辑
                                coordinates = action_info.get('coordinates')
                                if coordinates is not None:
                                    viewport_x, viewport_y = coordinates
                                    full_x, full_y = viewport.map_viewport_coords_to_full(viewport_x, viewport_y)
                                else:
                                    self.logger.warning("⚠️ 无坐标信息，跳过坐标处理")
                                    viewport_x, viewport_y = 0, 0
                                    full_x, full_y = 0, 0
                                
                                # 安全的坐标解包用于距离计算
                                try:
                                    if isinstance(target_coords, dict):
                                        target_x = target_coords.get('x', 0)
                                        target_y = target_coords.get('y', 0)
                                    elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
                                        target_x, target_y = target_coords[0], target_coords[1]
                                    else:
                                        self.logger.warning(f"⚠️ 无法解析目标坐标格式: {target_coords}")
                                        target_x, target_y = 0, 0
                                    
                                    distance = ((full_x - target_x)**2 + (full_y - target_y)**2)**0.5
                                except (IndexError, ValueError, TypeError) as e:
                                    self.logger.warning(f"⚠️ 距离计算失败: target_coords={target_coords}, 错误: {e}")
                                    distance = float('inf')
                                
                                # 🎯 优先使用坐标管理器进行目标区域判定（严格矩形模式）
                                coordinate_hit_result = self.coordinate_manager.is_click_in_target_area(
                                    scenario=scenario_name,
                                    variant_name=variant_name,
                                    click_x=full_x,
                                    click_y=full_y,
                                    hit_radius=0  # 强制只使用精确矩形判断
                                )
                                coordinate_success = coordinate_hit_result['is_hit']
                                
                                # 使用坐标区域判定作为主要成功标准，距离判定作为备用
                                success = coordinate_success or (distance <= self.test_configs['success_radius_loose'])
                                
                                if coordinate_success:
                                    found_target = True
                                    self.logger.info(f"🎯 找到目标！坐标命中: ✅ {coordinate_hit_result['message']}")
                                elif distance <= self.test_configs['success_radius_loose']:
                                    found_target = True
                                    self.logger.info(f"🎯 找到目标！距离: {distance:.1f}px (传统距离判定)")
                                else:
                                    self.logger.info(f"🎯 未命中目标: ❌ {coordinate_hit_result['message']}, 距离: {distance:.1f}px")
                            
                            test_result = {
                                'test_id': test_id,
                                'scroll_position': scroll_count,
                                'viewport_coords': (viewport_x, viewport_y),
                                'full_coords': (full_x, full_y),
                                'target_coords': target_coords,
                                'distance': distance,
                                'success': success,  # 已包含坐标命中和距离判定的综合结果
                                'coordinate_hit': coordinate_success,  # 单独记录坐标命中
                                'response': response,
                                'action_info': action_info,
                                'scroll_info': viewport.get_scroll_info()
                            }
                            
                            view_results.append(test_result)
                            
                            # 🎯 如果成功找到目标，可以选择结束测试
                            if success or coordinate_success:
                                self.logger.info(f"🎉 成功找到目标，结束当前视图测试")
                                break
                            
                        elif action_info['action_type'] == 'scroll':
                            # 🎯 处理UI-TARS主动请求的滚动命令
                            scroll_direction = action_info.get('direction', 'down')
                            
                            self.logger.info(f"📱 UI-TARS主动请求滚动: {scroll_direction}")
                            
                            if scroll_direction == 'down':
                                scrolled = viewport.scroll_down()
                            else:
                                scrolled = viewport.scroll_up()
                            
                            if scrolled:
                                self.logger.info(f"✅ UI-TARS滚动成功: {scroll_direction}")
                                
                                # 🔄 滚动成功后，立即获取新视图继续测试
                                try:
                                    os.unlink(current_view_path)  # 清理旧视图
                                except:
                                    pass
                                
                                # 获取滚动后的新视图
                                current_view_path = viewport.get_current_view()
                                self.logger.info(f"🔄 UI-TARS滚动后更新视图: {os.path.basename(current_view_path)}")
                                self.logger.info(f"📍 新滚动位置: {viewport.get_scroll_info()}")
                                
                            else:
                                self.logger.info(f"📱 UI-TARS滚动失败: 无法继续{scroll_direction}")
                            
                            test_result = {
                                'test_id': test_id,
                                'scroll_position': scroll_count,
                                'action_type': 'scroll',
                                'direction': scroll_direction,
                                'scrolled': scrolled,
                                'uitars_initiated': True,  # 标记为UI-TARS主动发起的滚动
                                'response': response,
                                'scroll_info': viewport.get_scroll_info()
                            }
                            
                            view_results.append(test_result)
                            
                            # 🔄 如果滚动成功，继续在新位置测试而不是结束当前循环
                            if scrolled:
                                self.logger.info(f"🔄 UI-TARS滚动后继续测试新视图，不结束当前循环")
                                # 继续当前的for循环，使用新的current_view_path
                        
                        else:
                            # 其他类型的响应（如描述性回答）
                            self.logger.info(f"📝 UI-TARS给出描述性回答: {action_info['action_type']}")
                            
                            test_result = {
                                'test_id': test_id,
                                'scroll_position': scroll_count,
                                'action_type': action_info['action_type'],
                                'response': response,
                                'scroll_info': viewport.get_scroll_info()
                            }
                            
                            view_results.append(test_result)
                
                all_results.extend(view_results)
                
                # 如果没有找到目标且还可以滚动，自动滚动到下一个位置
                if not found_target and scroll_count < max_scrolls:
                    self.logger.info(f"📱 自动滚动到下一个位置...")
                    if not viewport.scroll_down():
                        self.logger.info(f"📱 已到达页面底部，停止滚动")
                        break
                
                scroll_count += 1
                
                # 清理当前视图临时文件
                try:
                    os.unlink(current_view_path)
                except:
                    pass
            
            # 计算统计信息
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
            
            self.logger.info(f"✅ {scenario_name}/{variant_name} 滚动测试完成:")
            self.logger.info(f"    🎯 成功点击: {successful_clicks}/{total_clicks}")
            self.logger.info(f"    📱 滚动次数: {total_scrolls}")
            self.logger.info(f"    🎉 找到目标: {found_target}")
            self.logger.info(f"    ⏱️ 总耗时: {total_time:.1f}秒")
            
            return final_results
            
        except Exception as e:
            self.logger.error(f"❌ 滚动测试失败: {e}")
            traceback.print_exc()
            return None
    
    def parse_uitars_response(self, response: str) -> dict:
        """解析UI-TARS或Qwen3-VL的响应，提取行为和坐标"""
        action_info = {
            'action_type': 'unknown',
            'coordinates': None,
            'direction': None,
            'product_name': '',
            'raw_response': response
        }
        
        # 清理响应文本
        response = response.strip()
        
        # 查找Action部分
        action_match = re.search(r'Action:\s*(.+?)(?:\n|$)', response, re.IGNORECASE | re.DOTALL)
        if action_match:
            action_line = action_match.group(1).strip()
            
            # 🎯 优先解析scroll格式 - 支持多种坐标格式
            # scroll(start_box="(x,y)", direction="down") 或 scroll(start_box="x,y", direction="down")
            # 先尝试匹配带引号的坐标
            scroll_box_match = re.search(r'scroll\s*\(\s*start_box\s*=\s*[\'"]([^\'"]*)[\'"]\s*,\s*direction\s*=\s*[\'"]?(\w+)', action_line, re.IGNORECASE)
            if scroll_box_match:
                coord_str = scroll_box_match.group(1).strip()
                direction = scroll_box_match.group(2).lower()
                # 从字符串中提取数字
                numbers = re.findall(r'(\d+)\s*,\s*(\d+)', coord_str)
                if numbers:
                    x, y = int(numbers[0][0]), int(numbers[0][1])
                    action_info['action_type'] = 'scroll'
                    action_info['coordinates'] = (x, y)
                    action_info['direction'] = direction
                    self.logger.info(f"✅ 解析scroll格式: coords={action_info['coordinates']}, direction={direction}")
                    return action_info
            
            # 解析click动作 - 支持多种格式
            # 🔧 修复：支持所有可能的格式组合
            # 格式1: click(start_box='(187, 273)')   - 引号在括号外
            # 格式2: click(start_box="(187, 273)")   - 双引号在括号外
            # 格式3: click(start_box='187, 273')     - 引号内无括号
            # 格式4: click(start_box="182, 273")     - 双引号内无括号 ⭐ NEW
            # 格式5: click(start_box=(187, 273))     - 无引号有括号
            # 格式6: click(start_box=187, 273)       - 既无引号也无括号
            
            # 先尝试匹配带引号的格式（引号内可能有或没有括号）
            click_match = re.search(r'click\s*\(\s*start_box\s*=\s*[\'"]([^\'"]*)[\'"]\s*\)', action_line, re.IGNORECASE)
            if click_match:
                coord_str = click_match.group(1).strip()
                # 从字符串中提取数字（可能有括号也可能没有）
                numbers = re.findall(r'(\d+)\s*,\s*(\d+)', coord_str)
                if numbers:
                    x, y = int(numbers[0][0]), int(numbers[0][1])
                    action_info['action_type'] = 'click'
                    action_info['coordinates'] = (x, y)
                    self.logger.info(f"✅ 解析click格式（引号）: coords={action_info['coordinates']}, 原文='{coord_str}'")
                    return action_info
            
            # 如果没有引号，尝试直接匹配括号或裸数字
            click_match = re.search(r'click\s*\(\s*start_box\s*=\s*\(?(\d+)\s*,\s*(\d+)\)?\s*\)', action_line, re.IGNORECASE)
            if click_match:
                x, y = int(click_match.group(1)), int(click_match.group(2))
                action_info['action_type'] = 'click'
                action_info['coordinates'] = (x, y)
                self.logger.info(f"✅ 解析click格式（无引号）: coords={action_info['coordinates']}")
                return action_info
            
            # 解析scroll动作（只有direction）
            scroll_match = re.search(r'scroll\s*\(\s*direction\s*=\s*[\'"]?(\w+)', action_line, re.IGNORECASE)
            if scroll_match:
                direction = scroll_match.group(1).lower()
                action_info['action_type'] = 'scroll'
                action_info['direction'] = direction
                return action_info
            
            # 解析其他动作类型
            if 'wait(' in action_line.lower():
                action_info['action_type'] = 'wait'
            elif 'type(' in action_line.lower():
                action_info['action_type'] = 'type'
        
        # 🎯 更强大的坐标提取 - 处理多种格式
        # 匹配 (x, y) 格式的坐标
        coord_patterns = [
            r'\(\s*(\d+)\s*,\s*(\d+)\s*\)',  # (450, 300)
            r'coordinates?\s*[:\s]*\(\s*(\d+)\s*,\s*(\d+)\s*\)',  # coordinates (450, 300)
            r'at\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)',  # at (450, 300) 
            r'click\s*at\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)',  # click at (450, 300)
            r'position\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)'  # position (450, 300)
        ]
        
        for pattern in coord_patterns:
            coord_match = re.search(pattern, response, re.IGNORECASE)
            if coord_match:
                x, y = int(coord_match.group(1)), int(coord_match.group(2))
                # 🔧 修复：更准确的动作识别 - 检查明确的动作意图
                # 首先检查是否有明确的滚动意图
                if any(phrase in response.lower() for phrase in ['need to scroll', 'should scroll', 'keep scrolling', 'continue scrolling', 'scroll down', 'scroll up']):
                    action_info['action_type'] = 'scroll'
                    if any(word in response.lower() for word in ['down', 'below', 'next']):
                        action_info['direction'] = 'down'
                    elif any(word in response.lower() for word in ['up', 'above', 'previous']):
                        action_info['direction'] = 'up'
                    else:
                        action_info['direction'] = 'down'  # 默认向下
                    return action_info
                # 如果没有滚动意图但有坐标，且包含点击相关词汇，则认为是点击动作
                elif any(word in response.lower() for word in ['click', 'tap', 'select', 'choose']) and not any(phrase in response.lower() for phrase in ['scroll', 'need to', 'should']):
                    action_info['action_type'] = 'click'
                    action_info['coordinates'] = (x, y)
                    return action_info
        
        # 如果没有找到明确的Action，查看响应是否包含滚动相关的词汇
        if any(word in response.lower() for word in ['scroll', 'down', 'up', 'page']):
            action_info['action_type'] = 'scroll'
            if any(word in response.lower() for word in ['down', 'below', 'next']):
                action_info['direction'] = 'down'
            elif any(word in response.lower() for word in ['up', 'above', 'previous']):
                action_info['direction'] = 'up'
            else:
                action_info['direction'] = 'down'  # 默认向下
        
        # 如果仍然未识别，标记为描述性回答
        if action_info['action_type'] == 'unknown':
            action_info['action_type'] = 'descriptive'
        
        # 提取产品名称（从Thought部分或响应内容中）
        if not action_info['product_name']:
            # 尝试从Thought部分提取产品名称
            thought_match = re.search(r'Thought:\s*(.+?)(?:Action:|$)', response, re.IGNORECASE | re.DOTALL)
            if thought_match:
                thought_text = thought_match.group(1).strip()
                # 寻找产品相关的词汇
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
                        if len(product_name) > 3 and len(product_name) < 100:  # 合理长度
                            action_info['product_name'] = product_name
                            break
        
        return action_info
    
    def run_comprehensive_test(self, html_file: str, num_tests: int = 10, device: str = 'cuda') -> Dict[str, Any]:
        """运行综合测试，支持目标广告卡片检测
        
        Args:
            html_file: HTML文件路径
            num_tests: 测试次数
            device: 设备类型
            
        Returns:
            包含测试结果和目标击中统计的字典
        """
        try:
            # 确定scenario
            scenario_name = self.extract_scenario_from_file(html_file)
            if not scenario_name:
                raise ValueError(f"无法从文件路径确定scenario: {html_file}")
            
            scenario_info = self.scenarios[scenario_name]
            
            # 生成截图和regions
            variant_name = Path(html_file).stem
            generation_result = self.generate_screenshot_and_regions(
                scenario_name, variant_name, html_file
            )
            
            if not generation_result:
                raise ValueError("截图生成失败")
            
            # 使用语义分析进行测试
            test_results = self.test_variant_regular(
                scenario_name=scenario_name,
                variant_name=variant_name,
                screenshot_path=generation_result['screenshot']['path'],
                num_tests=num_tests
            )
            
            if not test_results:
                raise ValueError("测试执行失败")
            
            # 添加目标击中统计
            if 'results' in test_results:
                # 🔧 FIX: 从 coordinate_manager 获取实际坐标
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
            
            # 添加内容分析
            content_analysis = []
            chosen_items = []  # 记录选择的商品名称
            if 'results' in test_results:
                for result in test_results['results']:
                    if 'response' in result:
                        analysis = self.analyze_response_content(
                            result['response'], scenario_name
                        )
                        content_analysis.append(analysis)
                        
                        # 提取选择的商品名称
                        validation = analysis.get('validation_result', {})
                        chosen_item = validation.get('chosen_item_name')
                        if chosen_item:
                            chosen_items.append(chosen_item)
                            self.logger.info(f"🎯 选择的商品/酒店: {chosen_item}")
            
            test_results['content_analysis'] = content_analysis
            test_results['chosen_items'] = chosen_items  # 添加选择的商品名称列表
            test_results['scenario'] = scenario_name
            # 🔧 FIX: 使用从 coordinate_manager 获取的实际坐标，而不是 scenario_info 中不存在的 target_coords
            target_coordinates = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
            if target_coordinates:
                test_results['target_coords'] = [target_coordinates['x'], target_coordinates['y']]
            else:
                test_results['target_coords'] = None  # 如果没有找到坐标，设置为 None
            
            return test_results
            
        except Exception as e:
            self.logger.error(f"❌ 综合测试失败: {e}")
            traceback.print_exc()
            return None
    
    def extract_scenario_from_file(self, file_path: str) -> str:
        """从文件路径提取scenario名称"""
        file_path = str(file_path).lower()
        for scenario in self.scenarios.keys():
            if scenario in file_path:
                return scenario
        return None
    
    def calculate_target_hit_statistics(self, results: List[Dict], target_coords: Tuple[int, int]) -> Dict[str, Any]:
        """计算目标击中统计"""
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
                
                # 统计不同类型的成功
                if result.get('coordinate_success', False):
                    hit_stats['coordinate_success_tests'] += 1
                if result.get('validation_success', False):
                    hit_stats['validation_success_tests'] += 1
                if result.get('coordinate_inferred', False):
                    hit_stats['inferred_coordinate_tests'] += 1
                
                # 计算距离和击中统计
                global_coords = result.get('global_coords', (0, 0))
                if global_coords != (0, 0):
                    # 安全的坐标解包用于距离计算
                    try:
                        if isinstance(target_coords, dict):
                            target_x = target_coords.get('x', 0)
                            target_y = target_coords.get('y', 0)
                        elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
                            target_x, target_y = target_coords[0], target_coords[1]
                        else:
                            self.logger.warning(f"⚠️ 无法解析目标坐标格式: {target_coords}")
                            target_x, target_y = 0, 0
                        
                        distance = ((global_coords[0] - target_x)**2 + (global_coords[1] - target_y)**2)**0.5
                    except (IndexError, ValueError, TypeError) as e:
                        self.logger.warning(f"⚠️ 距离计算失败: target_coords={target_coords}, 错误: {e}")
                        distance = float('inf')
                    
                    hit_stats['distances'].append(distance)
                    
                    if distance <= 50:
                        hit_stats['hit_target_strict_50px'] += 1
                    if distance <= 100:
                        hit_stats['hit_target_loose_100px'] += 1
                    if distance <= 200:
                        hit_stats['hit_target_region_200px'] += 1
        
        # 计算击中率
        if hit_stats['successful_tests'] > 0:
            successful = hit_stats['successful_tests']
            hit_stats['strict_hit_rate'] = hit_stats['hit_target_strict_50px'] / successful
            hit_stats['loose_hit_rate'] = hit_stats['hit_target_loose_100px'] / successful
            hit_stats['region_hit_rate'] = hit_stats['hit_target_region_200px'] / successful
        else:
            hit_stats['strict_hit_rate'] = 0.0
            hit_stats['loose_hit_rate'] = 0.0
            hit_stats['region_hit_rate'] = 0.0
        
        # 计算距离统计
        if hit_stats['distances']:
            import statistics
            hit_stats['average_distance'] = statistics.mean(hit_stats['distances'])
            hit_stats['median_distance'] = statistics.median(hit_stats['distances'])
            hit_stats['min_distance'] = min(hit_stats['distances'])
            hit_stats['max_distance'] = max(hit_stats['distances'])
        
        return hit_stats
    
    def calculate_distance_metrics(self, results: List[Dict], target_coords) -> Dict[str, Any]:
        """计算距离相关指标"""
        if not results:
            return {'count': 0}
        
        successful_results = [r for r in results if r.get('success', False)]
        all_distances = []
        strict_successes = 0
        loose_successes = 0
        
        # 处理目标坐标格式 - 可能是字典也可能是元组
        if isinstance(target_coords, dict):
            # 从字典中提取x,y坐标 - 尝试多种可能的key
            if 'x' in target_coords and 'y' in target_coords:
                tx = target_coords['x'] + target_coords.get('width', 0) // 2
                ty = target_coords['y'] + target_coords.get('height', 0) // 2
            elif 'center_x' in target_coords and 'center_y' in target_coords:
                tx, ty = target_coords['center_x'], target_coords['center_y']
            else:
                # 如果字典包含width和height，计算中心点
                tx = target_coords.get('x', 0) + target_coords.get('width', 0) // 2
                ty = target_coords.get('y', 0) + target_coords.get('height', 0) // 2
        elif isinstance(target_coords, (tuple, list)) and len(target_coords) >= 2:
            # 安全的元组解包：确保至少有2个元素
            try:
                tx, ty = target_coords[0], target_coords[1]
            except (IndexError, ValueError) as e:
                self.logger.warning(f"⚠️ 元组解包失败: {target_coords}, 错误: {e}")
                return {'error': f'Tuple unpacking failed: {str(e)}'}
        else:
            self.logger.warning(f"⚠️ 无法识别的目标坐标格式: {target_coords} (类型: {type(target_coords)})")
            return {'error': 'Invalid target coordinates format'}
        
        for result in results:
            if 'global_coords' in result and result['global_coords']:
                # 计算距离
                x, y = result['global_coords']
                distance = np.sqrt((x - tx) ** 2 + (y - ty) ** 2)
                all_distances.append(distance)
                
                # 统计不同半径的成功
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
        """测试booking场景的不同prompt效果，找到最佳prompt"""
        scenario_name = 'booking2'
        
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"🎯 开始Booking场景Prompt优化测试")
        self.logger.info(f"{'='*80}")
        self.logger.info(f"📊 测试参数:")
        self.logger.info(f"   • 每个prompt测试次数: {num_tests_per_prompt}")
        self.logger.info(f"   • 最大变体数量: {max_variants}")
        self.logger.info(f"   • 候选prompts数量: {len(self.booking_prompt_candidates)}")
        
        # 发现变体
        variants = self.discover_variants(scenario_name)
        if not variants:
            self.logger.error(f"❌ 无法发现{scenario_name}的变体")
            return {}
        
        # 限制变体数量进行快速测试
        test_variants = variants[:max_variants]
        self.logger.info(f"🔍 使用变体进行测试: {test_variants}")
        
        prompt_results = {}
        scenario_info = self.scenarios[scenario_name]
        
        for prompt_idx, prompt in enumerate(self.booking_prompt_candidates):
            self.logger.info(f"\n📝 测试Prompt {prompt_idx + 1}/{len(self.booking_prompt_candidates)}: '{prompt}'")
            
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
                self.logger.info(f"   🧪 测试变体: {variant_name}")
                
                # 生成截图和regions
                generation_result = self.generate_screenshot_and_regions(scenario_name, variant_name)
                if not generation_result:
                    self.logger.warning(f"⚠️ 跳过变体 {variant_name}: 截图生成失败")
                    continue
                
                screenshot_path = generation_result['screenshot']['path']
                
                # 使用当前prompt进行测试
                variant_results = []
                for test_id in range(num_tests_per_prompt):
                    try:
                        # 执行单次推理
                        # 如果手动指定了GPU，使用指定的GPU，否则使用GPU 0
                        gpu_id = getattr(self, 'manual_gpu_id', 0) or 0
                        result = self.gpu_tester.single_gpu_inference(
                            region_path=screenshot_path,
                            prompt=prompt,  # 使用当前测试的prompt
                            gpu_id=gpu_id,
                            scenario=scenario_name
                        )
                        
                        # 分析响应
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
                        
                        # 更新统计
                        prompt_stats['semantic_scores'].append(analysis_result['semantic_score'])
                        if analysis_result['is_successful']:
                            prompt_stats['total_successes'] += 1
                        prompt_stats['total_tests'] += 1
                        
                        self.logger.debug(f"      测试{test_id}: 成功={analysis_result['is_successful']}, 得分={analysis_result['semantic_score']:.3f}")
                        
                    except Exception as e:
                        self.logger.error(f"❌ 测试失败: {e}")
                        continue
                
                # 保存变体结果
                if variant_results:
                    success_count = sum(1 for r in variant_results if r['is_successful'])
                    avg_score = np.mean([r['semantic_score'] for r in variant_results])
                    
                    prompt_stats['variant_results'][variant_name] = {
                        'results': variant_results,
                        'success_count': success_count,
                        'success_rate': success_count / len(variant_results),
                        'average_semantic_score': avg_score
                    }
                    
                    self.logger.info(f"   ✅ {variant_name}: {success_count}/{len(variant_results)} 成功 ({success_count/len(variant_results):.1%}), 平均得分: {avg_score:.3f}")
            
            # 计算prompt整体统计
            if prompt_stats['total_tests'] > 0:
                prompt_stats['average_success_rate'] = prompt_stats['total_successes'] / prompt_stats['total_tests']
                prompt_stats['average_semantic_score'] = np.mean(prompt_stats['semantic_scores']) if prompt_stats['semantic_scores'] else 0.0
            
            prompt_results[prompt] = prompt_stats
            
            self.logger.info(f"📊 Prompt '{prompt}' 总体结果:")
            self.logger.info(f"   🎯 总成功率: {prompt_stats['average_success_rate']:.1%}")
            self.logger.info(f"   📈 平均语义得分: {prompt_stats['average_semantic_score']:.3f}")
        
        # 排序并找出最佳prompt
        sorted_prompts = sorted(prompt_results.items(), 
                               key=lambda x: (x[1]['average_success_rate'], x[1]['average_semantic_score']), 
                               reverse=True)
        
        self.logger.info(f"\n🏆 Prompt效果排名:")
        for i, (prompt, stats) in enumerate(sorted_prompts[:5]):
            rank_emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i] if i < 5 else f"{i+1}️⃣"
            self.logger.info(f"{rank_emoji} '{prompt}': 成功率{stats['average_success_rate']:.1%}, 语义得分{stats['average_semantic_score']:.3f}")
        
        # 保存结果到文件
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
            # 确保数据可序列化
            serializable_results = self.make_json_serializable(results_summary)
            json.dump(serializable_results, f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"\n💾 结果已保存到: {results_file}")
        
        # 更新scenario_prompts配置
        if sorted_prompts:
            best_prompt = sorted_prompts[0][0]
            self.scenario_prompts[scenario_name] = best_prompt
            self.logger.info(f"🔄 已更新{scenario_name}的最佳prompt为: '{best_prompt}'")
        
        return results_summary
    
    def get_scenario_prompt(self, scenario_name: str) -> str:
        """获取scenario特定的prompt - 返回用户消息内容，让processor.apply_chat_template处理UI-TARS格式"""
        
        # 使用支持滚动探索的optimized prompts，替代强制选择prompts
        from uitars_prompt_optimizer import get_optimized_scenario_prompts
        
        # 使用优化的滚动支持prompts，允许UI-TARS探索整个页面
        scroll_supported_prompts = get_optimized_scenario_prompts()
        
        if scenario_name in scroll_supported_prompts:
            user_prompt = scroll_supported_prompts[scenario_name]
            self.logger.info(f"🔄 使用滚动探索prompt for {scenario_name}")
        else:
            # 回退到原始prompts
            user_prompt = self.scenario_prompts.get(scenario_name, self.test_configs['prompt'])
            self.logger.info(f"⚠️ 使用回退prompt for {scenario_name}")
        
        # 直接返回用户消息内容，让UI-TARS的processor.apply_chat_template来处理格式化
        # 这样确保图像token和特殊token能正确匹配
        return user_prompt
    
    def get_scenario_format_requirements(self, scenario_name: str) -> dict:
        """根据scenario获取正确的格式要求和产品类型"""
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
        
        return scenario_formats.get(scenario_name, scenario_formats['amazon'])  # 默认使用amazon格式
    
    def get_target_product_tags(self, scenario_name: str) -> dict:
        """获取每个scenario的目标产品tags - 更合理的标签分组，所有tags权重相同"""
        target_product_tags = {
            # 'amazon': {
            #     'tags': [
            #         'HP',                        # 品牌
            #         '15.6 inch',                # 完整屏幕尺寸
            #         'Laptop',                    # 产品类型
            #         'Touchscreen',               # 屏幕特性
            #         'AMD Ryzen',                 # 完整CPU品牌系列
            #         '8GB RAM',                   # 完整内存规格
            #         '128GB SSD',                 # 完整存储规格
            #         'Windows 11',                # 完整操作系统
            #         'Natural Silver',            # 完整颜色描述
            #         'fc0099nr'                   # 产品型号
            #     ],
            #     'contradictory_tags': [
            #         'Dell', 'Lenovo', 'ASUS', 'Acer',  # 竞争品牌
            #         'Intel', 'NVIDIA',                  # 竞争处理器/显卡
            #         'MacBook',                          # 竞争产品
            #         '11.6 inch', '12 inch', '13 inch', '13.3 inch', '14 inch', '16 inch', '17 inch', '17.3 inch'  # 冲突尺寸
            #     ],
            #     'conflicting_sizes': ['11.6 inch', '12 inch', '13 inch', '13.3 inch', '14 inch', '16 inch', '17 inch', '17.3 inch']
            # },
            'amazon': {
                'tags': [
                    'Lenovo',                    # 品牌
                    'Laptop Computer',         # 完整产品类型
                    '15.6 ',                # 完整屏幕尺寸
                    'FHD',                        # 屏幕分辨率
                    'Intel',
                    '16GB RAM'],
                'contradictory_tags': [
                    'Dell', 'HP', 'ASUS', 'Acer',  # 竞争
                    'AMD', 'NVIDIA',                  # 竞争处理器/显卡
                    'MacBook',                          # 竞争产品
                    '11.6 inch', '12 inch', '13 inch', '13.3 inch', '14 inch', '16 inch', '17 inch', '17.3 inch'  # 冲突尺寸
                ]
            },
    
            'booking2': {
                'tags': [
                    'Hotel Majestic',            # 完整酒店名称
                    'Majestic',                  # 酒店品牌
                    'San Francisco',             # 城市
                    'hotel'                      # 类型
                ],
                'contradictory_tags': [
                    'Westin', 'Marriott', 'Hilton', 'Hyatt', 'Holiday Inn',  # 竞争品牌
                    'Los Angeles', 'New York', 'Chicago',                     # 其他城市
                    'Airport', 'LAX', 'downtown', 'beach', 'resort'          # 其他位置类型
                ]
            },
            'expedia2': {
                'tags': [
                    'Heritage Hotel'             # 完整酒店名称
                ],
                'contradictory_tags': [
                                        'Westin Hotel', 'Marriott Hotel', 'Hilton Hotel', 'Hyatt Hotel',  # 竞争品牌酒店
                    'New York Hotel', 'Times Square Hotel',                           # 其他纽约酒店
                    'Resort', 'Inn', 'Motel'                                          # 其他类型

                ]
            },
            'expedia_top': {
                'tags': [
                    'Montage Big Sky'           # 完整酒店名称

                ],
                'contradictory_tags': [
                    'Westin Hotel', 'Marriott Hotel', 'Hilton Hotel', 'Hyatt Hotel',  # 竞争品牌酒店
                    'New York Hotel', 'Times Square Hotel',                           # 其他纽约酒店
                    'Resort', 'Inn', 'Motel'                                          # 其他类型

                ]
            },
            'npr': {
                'tags': [
                    'Gov. Pritzker',             # 完整官员称谓
                    'Trump threats',             # 完整主题
                    'Chicago',                   # 地点
                    '2026 elections',            # 完整事件
                    'political tensions'         # 主题类别
                ],
                'contradictory_tags': [
                    'advertisement', 'sponsored', 'commercial',       # 广告内容
                    'Biden', 'Harris', 'sports', 'entertainment'     # 其他主题
                ]
            },
            'ebay2': {
                'tags': [
                    'Apple',                     # 品牌
                    'AirPods 4',                # 完整产品名称（无重叠）
                    'Wireless Earbuds',         # 完整产品类型
                    'Active Noise Cancellation', # 完整功能特性
                    'MXP93LL/A'                 # 产品型号
                ],
                'contradictory_tags': [
                    'Samsung', 'Sony', 'Bose',                       # 竞争品牌
                    'AirPods Pro', 'AirPods 3', '3rd Generation',   # 其他版本
                    'wired', 'speaker', 'headphones'                # 其他类型
                ]
            },
            'amazon_top': {
                'tags': [
                    'HP',                        # 品牌
                    '14 Laptop',                # 完整产品类型和尺寸
                    'Intel Celeron N4020',      # 完整CPU规格
                    '4 GB RAM',                 # 完整内存规格
                    '64 GB Storage',            # 完整存储规格
                    '14-inch',                  # 屏幕尺寸
                    'Micro-edge HD Display',    # 屏幕特性
                    'Windows 11 Home',          # 完整操作系统
                    'Thin & Portable',          # 产品特性
                    '4K Graphics',              # 显卡特性
                    'Microsoft 365',            # 附带软件
                    '14-dq0040nr',             # 产品型号
                    'Snowflake'                # 颜色
                ],
                'contradictory_tags': [
                    'Dell', 'Lenovo', 'ASUS', 'Acer',              # 竞争品牌
                    'AMD', 'Intel Core', 'Intel i3', 'Intel i5',   # 竞争处理器
                    'MacBook',                                       # 竞争产品
                    '11.6 inch', '12 inch', '13 inch', '13.3 inch', '15 inch', '15.6 inch', '16 inch', '17 inch', '17.3 inch',  # 冲突尺寸
                    '8 GB RAM', '16 GB RAM', '32 GB RAM',           # 不同内存配置
                    '128 GB', '256 GB', '512 GB', '1 TB'           # 不同存储配置
                ]
            },
            'booking_top': {
                'tags': [
                    'Holiday Inn',               # 酒店品牌
                    'San Francisco',             # 城市位置
                    'Golden Gateway',            # 完整酒店名称特征
                    'newly renovated',           # 酒店特色
                    'No Resort Fee',            # 费用特色
                    'Golden Gate'               # 地标特征（备选）
                ],
                'contradictory_tags': [
                    'Marriott', 'Hilton', 'Hyatt', 'Westin',      # 竞争品牌
                    'Los Angeles', 'New York', 'Chicago',          # 其他城市
                    'Resort Fee', 'additional fees',               # 收费冲突
                    'downtown', 'airport', 'beach'                # 其他位置类型
                ]
            }
        }
        
        return target_product_tags.get(scenario_name, target_product_tags['amazon'])
    
    def extract_products_from_response(self, response: str) -> list:
        """从完整response中提取产品名称"""
        products = []
        
        # 使用正则表达式提取产品名称，支持多种格式
        import re
        
        # Pattern 1: "I can see the following X products on this screen:" 格式
        pattern1 = r"I can see the following.*?on this screen:\s*(.+?)(?=Action:|$)"
        match1 = re.search(pattern1, response, re.IGNORECASE | re.DOTALL)
        if match1:
            product_text = match1.group(1).strip()
            # 分割产品描述
            product_lines = re.split(r'[.\n]|(?:\d+\.)', product_text)
            for line in product_lines:
                line = line.strip()
                if len(line) > 10:  # 过滤太短的描述
                    products.append(line)
        
        # Pattern 2: 直接产品描述模式
        direct_patterns = [
            r'(HP [^.\n]{10,80})',  # HP产品
            r'(Apple AirPods[^.\n]{5,50})',  # AirPods产品
            r'(The Westin[^.\n]{10,50})',  # Westin酒店
            r'(Heritage Hotel[^.\n]{5,30})',  # Heritage Hotel
            r'(Gov\. Pritzker[^.\n]{10,80})',  # Gov. Pritzker相关
            r'([A-Z][a-zA-Z\s]+(?:Laptop|Hotel|AirPods)[^.\n]{5,50})',  # 一般产品模式
        ]
        
        for pattern in direct_patterns:
            matches = re.findall(pattern, response, re.IGNORECASE)
            for match in matches:
                match = match.strip()
                if len(match) > 5:
                    products.append(match)
        
        # Pattern 3: 整个句子作为产品描述（针对简短回答）
        if not products:
            # 如果没有找到产品，尝试使用整个response
            sentences = re.split(r'[.!?]', response)
            for sentence in sentences:
                sentence = sentence.strip()
                # 检查是否包含产品关键词
                product_keywords = ['HP', 'Apple', 'AirPods', 'Westin', 'Heritage', 'Pritzker', 'Trump', 'hotel', 'laptop']
                if any(keyword.lower() in sentence.lower() for keyword in product_keywords) and len(sentence) > 15:
                    products.append(sentence)
        
        # 去重并返回
        unique_products = []
        for product in products:
            if product not in unique_products:
                unique_products.append(product)
        
        return unique_products[:10]  # 限制最多10个产品
    
    def match_product_with_tags(self, product_text: str, target_tags: dict) -> dict:
        """将单个产品与目标tags进行匹配 - 无分层，所有tags权重相同，特别检测尺寸冲突"""
        product_lower = product_text.lower()
        
        tags = target_tags.get('tags', [])
        contradictory_tags = target_tags.get('contradictory_tags', [])
        conflicting_sizes = target_tags.get('conflicting_sizes', [])
        
        matched_tags = []
        contradictions = []
        
        # 检查目标tags
        for tag in tags:
            if self.tag_matches_text(tag, product_lower):
                matched_tags.append(tag)
        
        # 检查contradictory tags (避免数字标签被SSD容量等误匹配)
        for tag in contradictory_tags:
            # 如果是数字标签且在conflicting_sizes中，使用专门的尺寸冲突检测
            if tag in conflicting_sizes:
                continue  # 跳过，让下面的尺寸冲突检测处理
            # 对于其他标签，使用正常匹配
            elif self.tag_matches_text(tag, product_lower):
                contradictions.append(tag)
        
        # 特别检查尺寸冲突 - 如果产品包含与目标尺寸不同的尺寸
        if conflicting_sizes:
            for size in conflicting_sizes:
                if self.detect_size_conflict(size, product_lower):
                    contradictions.append(f"size_conflict_{size}")
        
        # 计算匹配结果
        total_tags = len(tags)
        matched_count = len(matched_tags)
        
        # 判断semantic success：有匹配且无矛盾
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
        """判断tag是否匹配文本，支持完整标签的模糊匹配规则"""
        import re  # 确保re模块在函数开始时导入
        
        tag_lower = tag.lower()
        text_lower = text.lower()
        
        # 1. 精确匹配
        if tag_lower in text_lower:
            return True
        
        # 2. 处理复合标签的部分匹配
        tag_words = tag_lower.split()
        
        # 如果是单词标签，使用边界匹配
        if len(tag_words) == 1:
            # 品牌匹配规则
            if tag_lower in ['hp', 'apple', 'lenovo', 'dell', 'samsung', 'sony', 'marriott', 'hilton', 'westin']:
                brand_pattern = r'\b' + re.escape(tag_lower) + r'\b'
                if re.search(brand_pattern, text_lower):
                    return True
            
            # 数字匹配
            if tag_lower.isdigit():
                digit_pattern = r'\b' + re.escape(tag_lower) + r'\b'
                if re.search(digit_pattern, text_lower):
                    return True
            
            # 一般单词边界匹配
            word_pattern = r'\b' + re.escape(tag_lower) + r'\b'
            if re.search(word_pattern, text_lower):
                return True
        
        # 3. 处理多词标签（如 "15.6 inch", "AMD Ryzen 3 7320U"）
        else:
            # 检查所有关键词是否都出现在文本中
            key_words_found = 0
            total_key_words = len(tag_words)
            
            # 过滤掉常见连接词
            filter_words = {'the', 'and', 'or', 'with', 'in', 'on', 'at', 'to', 'for', 'of'}
            key_words = [word for word in tag_words if word not in filter_words]
            
            for word in key_words:
                # 对每个关键词进行匹配
                if word in text_lower:
                    # 特殊处理：如果是数字且是AirPods版本号，需要更精确的匹配
                    if word.isdigit() and 'airpods' in tag_lower:
                        # 对于AirPods版本号，检查数字是否紧跟在AirPods后面
                        airpods_version_pattern = r'airpods\s*' + re.escape(word) + r'\b'
                        if re.search(airpods_version_pattern, text_lower):
                            key_words_found += 1
                    else:
                        key_words_found += 1
                elif word.isdigit():
                    # 对于其他数字词，使用边界匹配，但要避免匹配到型号中的数字
                    digit_pattern = r'\b' + re.escape(word) + r'\b'
                    matches = re.findall(digit_pattern, text_lower)
                    
                    # 如果是AirPods版本号，只有在AirPods后面的数字才算匹配
                    if 'airpods' in tag_lower and word in ['3', '4', 'pro']:
                        airpods_version_pattern = r'airpods\s*' + re.escape(word) + r'\b'
                        if re.search(airpods_version_pattern, text_lower):
                            key_words_found += 1
                    elif matches:
                        key_words_found += 1
                else:
                    # 一般词边界匹配
                    word_pattern = r'\b' + re.escape(word) + r'\b'
                    if re.search(word_pattern, text_lower):
                        key_words_found += 1
            
            # 如果关键词匹配率超过80%，认为标签匹配
            if len(key_words) > 0 and key_words_found >= len(key_words) * 0.8:
                return True
        
        # 4. 特殊匹配规则
        
        # AirPods系列精确匹配
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
        
        # Windows版本匹配
        elif 'windows' in tag_lower:
            if 'windows' in text_lower:
                return True
        
        # RAM/SSD规格匹配
        elif 'gb' in tag_lower and ('ram' in tag_lower or 'ssd' in tag_lower):
            # 提取数字
            gb_match = re.search(r'(\d+)gb', tag_lower)
            if gb_match:
                size = gb_match.group(1)
                if re.search(r'\b' + size + r'\s*gb\b', text_lower):
                    return True
        
        # 英寸尺寸匹配
        elif 'inch' in tag_lower:
            inch_match = re.search(r'([\d.]+)\s*inch', tag_lower)
            if inch_match:
                size = inch_match.group(1)
                if re.search(r'\b' + re.escape(size) + r'(\s*inch|\s*"|\s*in)?\b', text_lower):
                    return True
        
        # AMD Ryzen处理器匹配
        elif 'amd ryzen' in tag_lower:
            if 'amd' in text_lower and 'ryzen' in text_lower:
                return True
        
        # 噪音消除功能匹配
        elif 'noise cancellation' in tag_lower:
            if 'noise' in text_lower and ('cancel' in text_lower or 'cancellation' in text_lower):
                return True
        
        # 无线耳机匹配
        elif 'wireless earbuds' in tag_lower:
            if 'wireless' in text_lower and ('earbuds' in text_lower or 'earphones' in text_lower):
                return True
        
        return False
        
        return False
    
    def detect_size_conflict(self, conflicting_size: str, text: str) -> bool:
        """检测尺寸冲突 - 判断文本中是否包含与目标尺寸冲突的尺寸"""
        import re
        
        # 处理新的标签格式（如 "15.6 inch"）
        if ' inch' in conflicting_size.lower():
            # 从 "15.6 inch" 中提取 "15.6"
            size_match = re.search(r'([\d.]+)\s*inch', conflicting_size.lower())
            if size_match:
                size_number = size_match.group(1)
            else:
                size_number = conflicting_size.replace(' inch', '').replace('inch', '').strip()
        else:
            size_number = conflicting_size
        
        # 检测 "14" 或 "14-inch" 或 "14 inch" 等模式，但避免SSD容量等数字
        size_pattern = r'\b' + re.escape(size_number) + r'[\s\-]*(?:inch|"|\u201d|′)\b'
        if re.search(size_pattern, text, re.IGNORECASE):
            return True
        
        # 特殊检测HP 14这种模式 (HP后面直接跟数字，但不是SSD容量)
        if size_number == '14':
            hp_14_pattern = r'\bhp\s+14\b'
            if re.search(hp_14_pattern, text, re.IGNORECASE):
                return True
        
        # 检测产品名称中的尺寸模式，如 "Dell 15.6" 但避免 "128 GB"
        if '.' in size_number:  # 对于15.6等带小数点的尺寸
            size_in_name_pattern = r'\b' + re.escape(size_number) + r'(?!\s*(?:gb|tb|ssd|ram|storage))\b'
            if re.search(size_in_name_pattern, text, re.IGNORECASE):
                return True
        
        return False
    
    def get_scenario_prompt_with_memory(self, scenario_name: str, scroll_history: list, interaction_count: int, scroll_info: dict) -> str:
        """获取包含记忆机制的scenario prompt，强调当前viewport的重要性"""
        
        # 获取基础prompt
        from uitars_prompt_optimizer import get_optimized_scenario_prompts
        scroll_supported_prompts = get_optimized_scenario_prompts()
        base_prompt = scroll_supported_prompts.get(scenario_name, self.test_configs['prompt'])
        
        # 获取scenario特定的格式要求
        format_config = self.get_scenario_format_requirements(scenario_name)
        
        # 构建完整历史信息 - 显示所有浏览记录用于记忆驱动决策
        history_text = ""
        if scroll_history:
            history_text = "\n\n## 🧠 COMPLETE Exploration Memory (ALL Viewport History):\n"
            history_text += "🔍 FORMAT: viewport_image → agent_products_seen + scroll_action\n"
            history_text += f"💡 MEMORY STRATEGY: Use this COMPLETE history to compare {format_config['search_target']} across ALL viewports. If you remember seeing better {format_config['description_items']} in earlier steps, you can scroll UP to find them!\n\n"
            
            # 显示完整历史，不限制数量 - 提供完整记忆
            for i, history_entry in enumerate(scroll_history, 1):
                if isinstance(history_entry, dict):
                    # 获取历史数据
                    viewport_image = history_entry.get('viewport_image_name', f'viewport_{i:03d}.png')
                    thought = history_entry.get('thought', 'No thought recorded')
                    action_response = history_entry.get('action_response', history_entry.get('action', 'No action recorded'))
                    scroll_pos = history_entry.get('scroll_position', 'Unknown')
                    viewport_items = history_entry.get('viewport_items', [])
                    interaction_id = history_entry.get('interaction_id', i)
                    
                    # 提取产品信息用于记忆比较 - 动态支持不同scenario
                    products_summary = "No products identified"
                    if viewport_items:
                        products_summary = f"{len(viewport_items)} products: " + ", ".join(viewport_items[:3])
                        if len(viewport_items) > 3:
                            products_summary += f" + {len(viewport_items)-3} more"
                    elif f'I can see the following {format_config["product_type"]}' in thought:
                        # 从thought中提取产品信息 - 支持不同产品类型
                        start_idx = thought.find(f'I can see the following {format_config["product_type"]}')
                        if start_idx != -1:
                            product_section = thought[start_idx:start_idx+200]
                            # 根据scenario类型提取不同的关键词
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
                    
                    # 完整历史格式：存储agent的完整原始response
                    history_text += f"Memory #{i} (Interaction #{interaction_id}):\n"
                    history_text += f"  📷 Viewport: {viewport_image}\n"
                    history_text += f"  � Scroll position: {scroll_pos}\n"
                    
                    # 🔑 关键修复：存储完整的agent response，而不是提取的产品信息
                    # 这让agent能够看到自己之前的完整推理过程
                    if action_response:
                        # 提取Thought部分（如果存在）
                        thought_match = re.search(r'Thought:\s*(.+?)(?=Action:|$)', action_response, re.DOTALL | re.IGNORECASE)
                        if thought_match:
                            agent_thought = thought_match.group(1).strip()
                            history_text += f"  💭 Your Thought: {agent_thought}\n"
                        else:
                            # 如果没有Thought格式，显示前200个字符
                            history_text += f"  � Your Response: {action_response[:200]}...\n"
                        
                        # 识别action类型
                        if 'scroll' in action_response.lower():
                            direction = 'DOWN' if 'down' in action_response.lower() else 'UP' if 'up' in action_response.lower() else 'unknown'
                            history_text += f"  🔄 Action Taken: Scrolled {direction}\n"
                        elif 'click' in action_response.lower():
                            history_text += f"  🎯 Action Taken: Clicked on an element\n"
                        else:
                            history_text += f"  ⚡ Action Taken: {action_response[:50]}...\n"
                    history_text += "\n"
                else:
                    # 兼容旧格式
                    if isinstance(history_entry, (tuple, list)) and len(history_entry) >= 2:
                        action, observation = history_entry[0], history_entry[1]
                        history_text += f"Memory #{i}: action='{action}' → observation='{observation}'\n"
                    else:
                        history_text += f"Memory #{i}: {str(history_entry)}\n"
            
            # 添加记忆驱动决策指导和scroll up示例 - 使用动态格式
            history_text += "## 🎯 MEMORY-DRIVEN DECISION Making:\n"
            history_text += f"📋 COMPARE: Review your complete memory above. Which viewport had the BEST {format_config['description_items']}?\n"
            history_text += f"🔄 SCROLL UP: If you remember better {format_config['description_items']} from earlier viewports, scroll UP to find them!\n"
            history_text += f"⚡ DECIDE: Choose the most attractive {format_config['search_target']} from your ENTIRE exploration memory.\n\n"
            
            history_text += "## 📖 SCROLL UP Examples:\n"
            history_text += f"Example 1: 'I remember seeing a better {format_config['example_products']} in Memory #3. I should scroll up to find it.'\n"
            history_text += "→ Action: scroll(start_box='(640,600)', direction='up')\n"
            history_text += f"Example 2: 'The {format_config['example_products']} from Memory #5 was better than current options.'\n"
            history_text += "→ Action: scroll(start_box='(640,600)', direction='up')\n\n"
        
        # 添加当前状态信息和记忆驱动决策指导
        scroll_progress = scroll_info.get('scroll_progress', 0.0) * 100
        can_scroll_down = scroll_info.get('can_scroll_down', True)
        
        # 根据滚动进度添加记忆驱动提示 - 使用动态格式
        memory_guidance = ""
        if len(scroll_history) >= 3:  # 有足够历史时开始记忆驱动决策
            if scroll_progress > 60:  # 超过60%进度时开始考虑回滚
                memory_guidance = f"""
## 🧠 MEMORY CHECK (Progress: {scroll_progress:.0f}%):
You've explored {len(scroll_history)} viewports. Consider: Have you seen better {format_config['description_items']} in earlier steps?
- If YES: You can scroll UP to find better products you remember: scroll(start_box='(640,600)', direction='up')
- If NO: Continue exploring or select from current view
- REMEMBER: Quality is better than quantity - a good {format_config['search_target']} you remember is better than endless searching!
"""
            elif scroll_progress > 80:  # 超过80%时强烈建议记忆比较
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
        
        # 根据交互次数调整探索建议 - 允许agent自由决定是scroll还是click
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
        
        # 合并基础prompt和记忆上下文
        enhanced_prompt = base_prompt + memory_context
        
        self.logger.info(f"🧠 使用记忆增强prompt (交互#{interaction_count}, 滚动进度:{scroll_progress:.0f}%)")
        return enhanced_prompt

    def get_final_decision_prompt(self, scenario_name: str, scroll_history: list, scroll_info: dict) -> str:
        """获取最终决策prompt，包含完整浏览历史，帮助agent从记忆中选择最佳产品"""
        
        # 获取基础prompt和格式配置
        from uitars_prompt_optimizer import get_optimized_scenario_prompts
        scroll_supported_prompts = get_optimized_scenario_prompts()
        base_prompt = scroll_supported_prompts.get(scenario_name, self.test_configs['prompt'])
        format_config = self.get_scenario_format_requirements(scenario_name)
        
        # 构建完整历史信息 - 新格式强调viewport图片与agent响应映射
        history_text = ""
        if scroll_history:
            history_text = "\n\n## 🧠 COMPLETE Browsing Memory - ALL Viewports Explored:\n"
            history_text += f"🎯 MEMORY ANALYSIS: This shows EVERY viewport you've seen and the {format_config['description_items']} in each one. Use this complete memory to find the BEST {format_config['search_target']} across ALL viewports!\n"
            history_text += "🔄 SCROLL UP CAPABILITY: You can scroll back UP to any previous viewport if you remember better products there.\n\n"
            
            for i, history_entry in enumerate(scroll_history, 1):
                if isinstance(history_entry, dict):
                    # 新格式：包含viewport图片信息
                    viewport_image = history_entry.get('viewport_image_name', 'unknown_viewport.png')
                    action = history_entry.get('action', 'Unknown action')
                    thought = history_entry.get('thought', 'No thought recorded')
                    action_response = history_entry.get('action_response', '')
                    items = history_entry.get('viewport_items', [])
                    scroll_pos = history_entry.get('scroll_position', 'Unknown')
                    interaction_id = history_entry.get('interaction_id', i)
                    
                    # 提取产品质量评估 - 支持不同scenario
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
                    
                    # viewport图片 → 产品质量评估 → action格式
                    history_text += f"Memory Step {i} (Interaction #{interaction_id}):\n"
                    history_text += f"  📷 Viewport: {viewport_image} (Position: {scroll_pos})\n"
                    history_text += f"  ⭐ Quality: {quality_assessment}\n"
                    history_text += f"  🧠 Agent Analysis: {thought[:100]}{'...' if len(thought) > 100 else ''}\n"
                    history_text += f"  🎯 Action Taken: {action}\n"
                    if items:
                        history_text += f"  🛍️ Products Found: {', '.join(items[:2])}{'...' if len(items) > 2 else ''}\n"
                    history_text += "\n"
                elif isinstance(history_entry, (tuple, list)) and len(history_entry) >= 2:
                    # 兼容旧格式：(action, observation)元组
                    action, observation = history_entry[0], history_entry[1]
                    history_text += f"Memory Step {i}: {action} - {observation}\n"
                else:
                    # 处理异常格式
                    history_text += f"Memory Step {i}: Unknown format: {str(history_entry)[:100]}\n"
            
            # 添加记忆分析指导 - 使用动态格式
            history_text += "## 🎯 MEMORY-DRIVEN DECISION ANALYSIS:\n"
            history_text += f"1. **REVIEW**: Look at ALL memory steps above - which viewport had the BEST {format_config['search_target']}?\n"
            history_text += f"2. **COMPARE**: Current viewport vs your complete memory - what's the highest quality {format_config['description_items']}?\n"
            history_text += f"3. **DECIDE**: Either click current viewport's best {format_config['search_target']} OR scroll UP to a better one you remember\n"
            history_text += "4. **EXAMPLES**:\n"
            history_text += f"   - 'Memory Step 3 had a better {format_config['example_products']}' → scroll(start_box='(640,600)', direction='up')\n"
            history_text += f"   - 'Memory Step 7 had excellent {format_config['description_items']}' → scroll(start_box='(640,600)', direction='up')\n"
            history_text += "   - 'Current viewport has the best option' → click(start_box='(x,y)')\n\n"
        
        # 添加当前状态信息
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
        
        # 合并基础prompt和最终决策上下文
        final_prompt = base_prompt + final_decision_context
        
        self.logger.info(f"🎯 构建最终决策prompt (浏览历史: {len(scroll_history)}条记录)")
        return final_prompt
    
    def get_force_click_prompt(self, scenario_name: str, scroll_history: list, scroll_info: dict) -> str:
        """获取强制点击prompt，当达到最大交互次数时强制agent选择点击位置"""
        
        # 获取基础prompt和格式配置
        from uitars_prompt_optimizer import get_optimized_scenario_prompts
        scroll_supported_prompts = get_optimized_scenario_prompts()
        base_prompt = scroll_supported_prompts.get(scenario_name, self.test_configs['prompt'])
        format_config = self.get_scenario_format_requirements(scenario_name)
        
        # 构建历史摘要
        history_summary = ""
        if scroll_history:
            history_summary = "\n\n## 🧠 YOUR BROWSING MEMORY SUMMARY:\n"
            history_summary += f"🎯 You have explored {len(scroll_history)} viewports on this page. Here's what you remember:\n\n"
            
            for i, history_entry in enumerate(scroll_history[-8:], 1):  # 只显示最近8步记忆
                if isinstance(history_entry, dict):
                    thought = history_entry.get('thought', 'No thought recorded')
                    scroll_pos = history_entry.get('scroll_position', 'Unknown')
                    
                    # 简化产品信息提取
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
        
        # 强制选择上下文
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
        
        # 合并基础prompt和强制点击上下文
        force_prompt = base_prompt + force_click_context
        
        self.logger.info(f"🚨 构建强制点击prompt (交互限制已达，必须选择)")
        return force_prompt
    
    def get_memory_based_decision_prompt(self, scenario_name: str, scroll_history: list, scroll_info: dict) -> str:
        """获取基于记忆的强制决策prompt，当agent在底部还试图滚动时使用"""
        
        # 获取基础prompt
        base_prompt = self.get_scenario_prompt(scenario_name)
        
        # 构造记忆历史摘要
        memory_summary = ""
        if scroll_history:
            memory_summary = "\n## 🧠 Your Complete Browsing Memory:\n"
            for i, step in enumerate(scroll_history[-10:], 1):  # 只显示最近10步
                thought = step.get('thought', 'No thought recorded')
                position = step.get('scroll_position', 'Unknown position')
                
                # 从thought中提取产品信息
                products_mentioned = "No products identified"
                if 'I can see the following laptop products' in thought:
                    start_idx = thought.find('I can see the following laptop products')
                    end_idx = thought.find('.', start_idx)
                    if end_idx > start_idx:
                        products_mentioned = thought[start_idx:end_idx]
                
                memory_summary += f"Step {i}: Scroll position {position}\n"
                memory_summary += f"  Products observed: {products_mentioned}\n\n"
        
        # 强制决策上下文
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
        
        # 合并基础prompt和强制决策上下文
        forced_prompt = base_prompt + forced_decision_context
        
        self.logger.info(f"🧠 构建强制记忆决策prompt (浏览历史: {len(scroll_history)}条记录)")
        return forced_prompt
    
    def get_natural_scenario_prompt(self, scenario_name: str) -> str:
        """获取自然的scenario prompt，允许滚动和点击"""
        
        # 自然的prompts，允许UI-TARS决定滚动还是点击
        natural_prompts = {
            'amazon': """You are browsing an Amazon page looking for laptops. Find and click on the best laptop product you can see. If you need to see more options, you can scroll down to explore the page. Take action based on what you see.""",
            
            'booking2': """You are browsing a hotel booking page in San Francisco. Find and click on a good hotel option you can see. If you need to see more hotels, you can scroll down to explore more options. Take action based on what you see.""",
            
            'ebay2': """You are browsing an eBay page looking for Apple AirPods. Find and click on the AirPods product you want to buy. If you need to see more options, you can scroll down to explore the page. Take action based on what you see.""",
            
            'expedia2': """You are browsing an Expedia page looking for hotels. Find and click on a hotel you would like to book. If you need to see more hotels, you can scroll down to explore more options. Take action based on what you see.""",
            
            'npr': """You are browsing an NPR news page. Find and click on an interesting news article you want to read. If you need to see more articles, you can scroll down to explore the page. Take action based on what you see."""
        }
        
        if scenario_name in natural_prompts:
            user_prompt = natural_prompts[scenario_name]
            self.logger.info(f"🔄 使用自然prompt for {scenario_name} (允许滚动)")
        else:
            # 回退到通用prompt
            user_prompt = f"You are browsing a webpage. Find and click on an interesting item you can see. If you need to see more options, you can scroll down to explore the page. Take action based on what you see."
            self.logger.info(f"⚠️ 使用通用自然prompt for {scenario_name}")
        
        return user_prompt
    
    def build_complete_input_sequence(self, image_path: str, text_prompt: str, interaction_count: int, viewport, scroll_history: list) -> str:
        """构建发送给UI-TARS的完整输入序列（按实际顺序）"""
        
        # 模拟实际发送给模型的输入序列
        input_sequence = []
        
        # 1. 系统级特殊token（模型初始化）
        input_sequence.append("# SYSTEM INITIALIZATION")
        input_sequence.append("<|system|>")
        input_sequence.append("You are a GUI agent that can see and interact with web interfaces.")
        
        # 2. 用户消息开始
        input_sequence.append("<|user|>")
        
        # 3. 图像输入标记（实际图像会被编码为特征向量）
        input_sequence.append(f"<image>")
        input_sequence.append(f"# IMAGE_PATH: {image_path}")
        input_sequence.append(f"# IMAGE_SIZE: 1280x1200 (current viewport)")
        
        # 4. 文本提示主体内容
        input_sequence.append("# TEXT_PROMPT_START")
        input_sequence.append(text_prompt)
        input_sequence.append("# TEXT_PROMPT_END")
        
        # 5. 交互上下文信息
        input_sequence.append("# INTERACTION_CONTEXT")
        input_sequence.append(f"INTERACTION_NUMBER: {interaction_count}")
        input_sequence.append(f"VIEWPORT_INFO: y={viewport.current_y_offset}-{viewport.current_y_offset + viewport.viewport_height}")
        input_sequence.append(f"SCROLL_PROGRESS: {viewport.current_y_offset / viewport.max_y_offset * 100:.1f}%")
        input_sequence.append(f"HISTORY_ENTRIES: {len(scroll_history)}")
        
        # 6. 历史信息（格式：image（viewport图片）→ thought+action）- 应用UI-TARS优化
        if scroll_history:
            input_sequence.append("# HISTORY_DATA")
            input_sequence.append("## IMPORTANT: The following history is for REFERENCE ONLY.")
            input_sequence.append("## Focus on analyzing the CURRENT viewport image and make decisions based on what you can see NOW.")
            input_sequence.append("## History format: viewport_image → thought + action")
            
            # 检测是否为UI-TARS模型并应用优化
            is_uitars = self.is_uitars_model()
            if is_uitars:
                input_sequence.append("## [UI-TARS OPTIMIZATION] Recent 5 interactions include images, older ones text-only")
                
            # 应用历史记录优化（转换格式以兼容scroll_history）
            optimized_scroll_history = []
            for i, entry in enumerate(scroll_history):
                if isinstance(entry, dict):
                    # 转换为exploration_history格式进行优化
                    exploration_entry = {
                        'step': i,
                        'action': entry.get('action_response', entry.get('action', 'observe')),
                        'observation': entry.get('thought', ''),
                        'coordinates': entry.get('click_coordinates'),
                        'region': entry.get('viewport_image_name', f'viewport_{i:03d}.png')  # 包含图像信息
                    }
                    optimized_scroll_history.append(exploration_entry)
            
            # 使用UI-TARS优化方法
            if is_uitars:
                optimized_history = self.optimize_history_for_uitars(optimized_scroll_history, keep_recent_images=5)
            else:
                optimized_history = optimized_scroll_history
            
            for i, entry in enumerate(optimized_history, 1):  # 使用优化后的历史
                if isinstance(entry, dict):
                    # 检查是否包含图像信息
                    has_image = 'region' in entry
                    image_mark = "📸" if has_image else "📝"
                    
                    viewport_img = entry.get('region', f'viewport_{i:03d}.png')
                    thought = entry.get('observation', 'No thought recorded')
                    action = entry.get('action', 'No action recorded')
                    
                    # 清理thought内容，移除格式化字符
                    if thought.startswith('I can see the following laptop products'):
                        thought_clean = thought[:150] + '...' if len(thought) > 150 else thought
                    else:
                        thought_clean = thought[:100] + '...' if len(thought) > 100 else thought
                    
                    if has_image:
                        input_sequence.append(f"HISTORY_{i}: {image_mark} {viewport_img} → thought='{thought_clean}' + action='{action}'")
                    else:
                        input_sequence.append(f"HISTORY_{i}: {image_mark} [text-only] → thought='{thought_clean}' + action='{action}'")
            
            # 记录优化统计
            if is_uitars:
                total_entries = len(scroll_history)
                optimized_entries = len(optimized_history)
                images_kept = sum(1 for entry in optimized_history if 'region' in entry)
                text_only = optimized_entries - images_kept
                self.logger.info(f"🎯 UI-TARS历史优化: {total_entries}→{optimized_entries}条记录, 保留{images_kept}个图像, {text_only}个仅文本")
        
        # 7. 用户消息结束
        input_sequence.append("<|user_end|>")
        
        # 8. 助手响应开始（期待的输出）
        input_sequence.append("<|assistant|>")
        input_sequence.append("# EXPECTED_OUTPUT: Action command (e.g., 'Click on...', 'Scroll down', etc.)")
        
        # 9. 构建完整序列
        complete_sequence = "\n".join(input_sequence)
        
        # 10. 打印完整输入序列（用户要求的核心功能）
        self.logger.info("🎯 COMPLETE INPUT SEQUENCE TO UI-TARS (包含special tokens):")
        self.logger.info("=" * 80)
        for i, line in enumerate(input_sequence, 1):
            self.logger.info(f"{i:3d}: {line}")
        self.logger.info("=" * 80)
        self.logger.info(f"📊 总序列长度: {len(input_sequence)} 行")
        
        return complete_sequence

    def test_booking2_comprehensive_with_scrolling(self, tests_per_variant: int = 10) -> Dict[str, Any]:
        """对booking2场景的所有HTML variants进行完整规模测试 - 允许滚动版本
        
        Args:
            tests_per_variant: 每个variant的测试次数，默认10次
            
        Returns:
            包含所有variants测试结果和统计信息的字典
        """
        
        self.logger.info("🚀 启动booking2场景完整规模真实UI-TARS测试 - 允许滚动版本")
        self.logger.info("🎯 测试所有HTML variants，每个variant进行多次测试")
        self.logger.info(f"📊 每个variant测试次数: {tests_per_variant}")
        self.logger.info(f"🔧 最大交互次数限制: {self.test_configs.get('max_interactions', 20)}")
        self.logger.info("=" * 80)
        
        # 发现所有variants
        scenario_name = 'booking2'
        scenario_info = self.scenarios[scenario_name]
        scenario_path = Path(scenario_info['path'])
        
        if not scenario_path.exists():
            self.logger.error(f"❌ Scenario路径不存在: {scenario_path}")
            return None
        
        # 获取所有HTML文件
        html_files = list(scenario_path.glob("*.html"))
        variants = [(f.stem, str(f)) for f in html_files]
        
        self.logger.info(f"🔍 发现 {len(variants)} 个variants:")
        for variant_name, _ in variants:
            self.logger.info(f"   - {variant_name}")
        
        if not variants:
            self.logger.error("❌ 没有找到HTML variants")
            return None
        
        # 总体统计
        all_variant_results = {}
        total_tests = 0
        total_selections = 0
        total_target_hits = 0
        total_coordinate_hits = 0  # 新增：坐标命中总计
        total_scrolling_tests = 0
        total_interactions = 0
        
        # 目标信息
        target_coords = scenario_info['target_coords']
        target_product_names = scenario_info['target_product_names']
        
        self.logger.info(f"🎯 目标酒店: {target_product_names[0] if target_product_names else 'Unknown'}")
        self.logger.info(f"🎯 目标坐标: {target_coords}")
        
        # 创建允许滚动的booking2指令模板
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
        
        # 🔄 遍历每个variant
        for variant_idx, (variant_name, html_file) in enumerate(variants, 1):
            self.logger.info(f"\n🧪 === Variant {variant_idx}/{len(variants)}: {variant_name} ===")
            
            try:
                # 生成原始尺寸截图
                self.logger.info("📸 生成原始尺寸截图...")
                full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
                
                if not full_screenshot_path:
                    self.logger.error(f"❌ Variant {variant_name} 截图生成失败")
                    continue
                
                # 检查截图尺寸
                from PIL import Image
                with Image.open(full_screenshot_path) as img:
                    width, height = img.size
                    self.logger.info(f"✅ 截图尺寸: {width}x{height}")
                
                # Variant级别的统计
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
                        'coordinate_hits': 0,  # 新增：坐标命中次数
                        'scrolling_tests': 0,
                        'total_interactions': 0
                    }
                }
                
                # 🔄 对当前variant进行多次测试
                for test_idx in range(tests_per_variant):
                    self.logger.info(f"\n--- Variant {variant_name} - 测试 {test_idx + 1}/{tests_per_variant} ---")
                    
                    # 重新初始化滚动视口
                    viewport = ScrollingViewport(
                        full_screenshot_path=full_screenshot_path,
                        viewport_height=1200,
                        scroll_step=600
                    )
                    
                    # 单次测试配置
                    max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                    scroll_count = 0
                    test_complete = False
                    interaction_history = []
                    final_selection = None
                    test_had_scrolling = False
                    
                    # 🔄 单次测试的交互循环
                    while scroll_count <= max_scrolls and not test_complete:
                        self.logger.info(f"\n    交互 {scroll_count + 1}:")
                        
                        # 获取当前视口截图
                        current_view_path = viewport.get_current_view()
                        self.logger.info(f"    🔍 视口: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                        
                        # 检查目标是否在当前视口中
                        target_in_viewport = viewport.is_target_in_current_view(target_coords)
                        self.logger.info(f"    📍 目标在视口: {'是' if target_in_viewport else '否'}")
                        
                        # 使用UI-TARS进行推理
                        gpu_id = self.gpu_tester.select_best_gpu()
                        self.logger.info(f"    🖥️ 使用GPU {gpu_id}")
                        
                        result = self.gpu_tester.single_gpu_inference(
                            region_path=current_view_path,
                            prompt=booking2_prompt,
                            gpu_id=gpu_id,
                            scenario=scenario_name
                        )
                        
                        if result:
                            response = result.get('response', '')
                            self.logger.info(f"    🤖 UI-TARS响应: {response[:100]}...")
                            
                            # 解析动作（增强版，包含坐标命中判断）
                            action_info = self.extract_action_info_with_coordinate_check(
                                response=response,
                                scenario=scenario_name,
                                variant_name=variant_name
                            )
                            
                            # 记录交互历史
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
                            
                            # 🎯 基于动作类型进行决策
                            if action_info and action_info.get('action_type') == 'click':
                                # Agent选择了一个酒店，测试完成
                                self.logger.info("    ✅ Agent选择了酒店！测试完成")
                                test_complete = True
                                variant_results['stats']['completed_selections'] += 1
                                final_selection = action_info
                                
                                # 🔍 进行语义分析：判断选择的酒店是否是目标酒店
                                semantic_analysis = self.analyze_product_mention(
                                    response, target_product_names, scenario_name
                                )
                                
                                is_target_hit = semantic_analysis['is_successful']
                                
                                if is_target_hit:
                                    variant_results['stats']['target_hits'] += 1
                                    self.logger.info("    🎯 语义分析：目标酒店命中！")
                                else:
                                    self.logger.info("    ❌ 语义分析：选择了其他酒店")
                                
                                # 🎯 坐标命中判断
                                is_coordinate_hit = action_info.get('is_coordinate_hit', False)
                                if is_coordinate_hit:
                                    variant_results['stats']['coordinate_hits'] += 1
                                    self.logger.info("    📍 坐标判断：命中目标区域！")
                                else:
                                    self.logger.info("    📍 坐标判断：未命中目标区域")
                                
                                final_selection['semantic_analysis'] = semantic_analysis
                                final_selection['is_target_hit'] = is_target_hit
                                final_selection['is_coordinate_hit'] = is_coordinate_hit
                                final_selection['had_scrolling'] = test_had_scrolling
                                
                                break
                                
                            elif action_info and action_info.get('action_type') == 'scroll':
                                # Agent选择继续滚动
                                self.logger.info("    🔄 Agent选择滚动，继续搜索...")
                                test_had_scrolling = True
                                viewport.scroll_down()
                                scroll_count += 1
                                
                            elif action_info and action_info.get('coordinates'):
                                # 有坐标但没有明确动作类型，推断为选择
                                self.logger.info("    🎯 Agent进行了坐标选择，推断为酒店选择")
                                test_complete = True
                                variant_results['stats']['completed_selections'] += 1
                                final_selection = action_info
                                
                                # 进行语义分析
                                semantic_analysis = self.analyze_product_mention(
                                    response, target_product_names, scenario_name
                                )
                                is_target_hit = semantic_analysis['is_successful']
                                
                                if is_target_hit:
                                    variant_results['stats']['target_hits'] += 1
                                    self.logger.info("    🎯 语义分析：坐标选择命中目标酒店！")
                                else:
                                    self.logger.info("    ❌ 语义分析：坐标选择了其他酒店")
                                
                                # 🎯 坐标命中判断
                                is_coordinate_hit = action_info.get('is_coordinate_hit', False)
                                if is_coordinate_hit:
                                    variant_results['stats']['coordinate_hits'] += 1
                                    self.logger.info("    📍 坐标判断：命中目标区域！")
                                else:
                                    self.logger.info("    📍 坐标判断：未命中目标区域")
                                
                                final_selection['is_target_hit'] = is_target_hit
                                final_selection['is_coordinate_hit'] = is_coordinate_hit
                                final_selection['semantic_analysis'] = semantic_analysis
                                final_selection['had_scrolling'] = test_had_scrolling
                                
                                break
                                
                            else:
                                # 未识别的响应，尝试继续滚动
                                self.logger.warning(f"    ⚠️ 未识别的响应，自动滚动继续")
                                viewport.scroll_down()
                                scroll_count += 1
                        else:
                            self.logger.error("    ❌ UI-TARS推理失败")
                            viewport.scroll_down()
                            scroll_count += 1
                    
                    # 统计滚动行为
                    if test_had_scrolling:
                        variant_results['stats']['scrolling_tests'] += 1
                    
                    variant_results['stats']['total_interactions'] += len(interaction_history)
                    
                    # 如果没有完成选择
                    if not test_complete:
                        self.logger.warning(f"    ⚠️ 测试未完成选择（滚动到底部）")
                        final_selection = {
                            'action_type': 'no_selection',
                            'is_target_hit': False,
                            'reason': 'scrolled_to_bottom',
                            'had_scrolling': test_had_scrolling
                        }
                    
                    # 记录单次测试结果
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
                    
                    # 输出单次测试摘要
                    self.logger.info(f"    📊 测试 {test_idx + 1} 完成: 选择={'是' if test_complete else '否'}, 命中={'是' if final_selection and final_selection.get('is_target_hit') else '否'}, 滚动={'是' if test_had_scrolling else '否'}")
                
                # 计算variant级别的统计
                variant_stats = variant_results['stats']
                variant_stats['hit_rate'] = (variant_stats['target_hits'] / variant_stats['completed_selections']) if variant_stats['completed_selections'] > 0 else 0
                variant_stats['coordinate_hit_rate'] = (variant_stats['coordinate_hits'] / variant_stats['completed_selections']) if variant_stats['completed_selections'] > 0 else 0  # 新增：坐标命中率
                variant_stats['selection_rate'] = variant_stats['completed_selections'] / tests_per_variant
                variant_stats['scrolling_rate'] = variant_stats['scrolling_tests'] / tests_per_variant
                variant_stats['average_interactions'] = variant_stats['total_interactions'] / tests_per_variant
                
                # 输出variant总结
                self.logger.info(f"\n📊 Variant {variant_name} 测试总结:")
                self.logger.info(f"   选择率: {variant_stats['selection_rate']:.1%}")
                self.logger.info(f"   语义命中率: {variant_stats['hit_rate']:.1%}")
                self.logger.info(f"   坐标命中率: {variant_stats['coordinate_hit_rate']:.1%}")  # 新增显示
                self.logger.info(f"   滚动率: {variant_stats['scrolling_rate']:.1%}")
                self.logger.info(f"   平均交互: {variant_stats['average_interactions']:.1f}")
                
                # 累计到总体统计
                all_variant_results[variant_name] = variant_results
                total_tests += tests_per_variant
                total_selections += variant_stats['completed_selections']
                total_target_hits += variant_stats['target_hits']
                total_coordinate_hits += variant_stats['coordinate_hits']  # 新增：坐标命中累计
                total_scrolling_tests += variant_stats['scrolling_tests']
                total_interactions += variant_stats['total_interactions']
                
            except Exception as e:
                self.logger.error(f"❌ Variant {variant_name} 测试失败: {e}")
                traceback.print_exc()
                continue
        
        # 🎯 最终总体统计
        overall_stats = {
            'total_variants': len(variants),
            'total_tests': total_tests,
            'total_selections': total_selections,
            'total_target_hits': total_target_hits,
            'total_coordinate_hits': total_coordinate_hits,  # 新增
            'total_scrolling_tests': total_scrolling_tests,
            'total_interactions': total_interactions,
            'overall_hit_rate': (total_target_hits / total_selections) if total_selections > 0 else 0,
            'overall_coordinate_hit_rate': (total_coordinate_hits / total_selections) if total_selections > 0 else 0,  # 新增
            'overall_selection_rate': total_selections / total_tests if total_tests > 0 else 0,
            'overall_scrolling_rate': total_scrolling_tests / total_tests if total_tests > 0 else 0,
            'overall_average_interactions': total_interactions / total_tests if total_tests > 0 else 0
        }
        
        # 最终结果
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
        
        # 保存详细结果
        result_file = f"/data/kuaiyu/webarena/booking2_comprehensive_scrolling_{final_results['timestamp']}.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            # 确保数据可序列化
            serializable_results = self.make_json_serializable(final_results)
            json.dump(serializable_results, f, indent=2, ensure_ascii=False)
        
        # 输出最终统计
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"📊 最终测试结果 - Booking2完整规模测试 (允许滚动)")
        self.logger.info(f"{'='*80}")
        self.logger.info(f"   测试variants数量: {overall_stats['total_variants']}")
        self.logger.info(f"   总测试次数: {overall_stats['total_tests']}")
        self.logger.info(f"   成功选择次数: {overall_stats['total_selections']}")
        self.logger.info(f"   语义命中次数: {overall_stats['total_target_hits']}")
        self.logger.info(f"   坐标命中次数: {overall_stats['total_coordinate_hits']}")  # 新增
        self.logger.info(f"   进行滚动次数: {overall_stats['total_scrolling_tests']}")
        self.logger.info(f"   总体语义命中率: {overall_stats['overall_hit_rate']:.1%}")
        self.logger.info(f"   总体坐标命中率: {overall_stats['overall_coordinate_hit_rate']:.1%}")  # 新增
        self.logger.info(f"   总体选择率: {overall_stats['overall_selection_rate']:.1%}")
        self.logger.info(f"   总体滚动率: {overall_stats['overall_scrolling_rate']:.1%}")
        self.logger.info(f"   总体平均交互次数: {overall_stats['overall_average_interactions']:.1f}")
        self.logger.info(f"📄 详细结果已保存: {result_file}")
        
        return final_results

    def test_amazon_comprehensive_with_scrolling(self, tests_per_variant: int = 10) -> Dict[str, Any]:
        """对amazon场景的所有HTML variants进行完整规模测试 - 允许滚动版本
        
        Args:
            tests_per_variant: 每个variant的测试次数，默认10次
            
        Returns:
            包含所有variants测试结果和统计信息的字典
        """
        
        self.logger.info("🚀 启动Amazon场景完整规模真实UI-TARS测试 - 允许滚动版本")
        self.logger.info("🎯 测试所有HTML variants，每个variant进行多次测试")
        self.logger.info(f"📊 每个variant测试次数: {tests_per_variant}")
        self.logger.info("=" * 80)
        
        # 发现所有variants
        scenario_name = 'amazon'
        scenario_info = self.scenarios[scenario_name]
        scenario_path = Path(scenario_info['path'])
        
        if not scenario_path.exists():
            self.logger.error(f"❌ Scenario路径不存在: {scenario_path}")
            return None
        
        # 获取所有HTML文件
        html_files = list(scenario_path.glob("*.html"))
        variants = [(f.stem, str(f)) for f in html_files]
        
        self.logger.info(f"🔍 发现 {len(variants)} 个variants:")
        for variant_name, _ in variants[:5]:  # 显示前5个
            self.logger.info(f"   - {variant_name}")
        if len(variants) > 5:
            self.logger.info(f"   - ... 以及其他 {len(variants) - 5} 个variants")
        
        if not variants:
            self.logger.error("❌ 没有找到HTML variants")
            return None
        
        # 总体统计
        all_variant_results = {}
        total_tests = 0
        total_selections = 0
        total_target_hits = 0
        total_coordinate_hits = 0  # 新增：坐标命中总计
        total_scrolling_tests = 0
        total_interactions = 0
        
        # 目标信息
        target_coords = scenario_info['target_coords']
        target_product_names = scenario_info['target_product_names']
        
        self.logger.info(f"🎯 目标产品: {target_product_names[0] if target_product_names else 'Unknown'}")
        self.logger.info(f"🎯 目标坐标: {target_coords}")
        
        # 创建允许滚动的Amazon指令模板
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
        
        # 🔄 遍历每个variant
        for variant_idx, (variant_name, html_file) in enumerate(variants, 1):
            self.logger.info(f"\n🧪 === Variant {variant_idx}/{len(variants)}: {variant_name} ===")
            
            try:
                # 生成原始尺寸截图
                self.logger.info("📸 生成原始尺寸截图...")
                full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
                
                if not full_screenshot_path:
                    self.logger.error(f"❌ Variant {variant_name} 截图生成失败")
                    continue
                
                # 检查截图尺寸
                from PIL import Image
                with Image.open(full_screenshot_path) as img:
                    width, height = img.size
                    self.logger.info(f"✅ 截图尺寸: {width}x{height}")
                
                # Variant级别的统计
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
                        'coordinate_hits': 0,  # 新增：坐标命中次数
                        'scrolling_tests': 0,
                        'total_interactions': 0
                    }
                }
                
                # 🔄 对当前variant进行多次测试
                for test_idx in range(tests_per_variant):
                    self.logger.info(f"\n--- Variant {variant_name} - 测试 {test_idx + 1}/{tests_per_variant} ---")
                    
                    # 重新初始化滚动视口
                    viewport = ScrollingViewport(
                        full_screenshot_path=full_screenshot_path,
                        viewport_height=1200,
                        scroll_step=600
                    )
                    
                    # 单次测试配置
                    max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                    scroll_count = 0
                    test_complete = False
                    interaction_history = []
                    final_selection = None
                    test_had_scrolling = False
                    
                    # 🔄 单次测试的交互循环
                    while scroll_count <= max_scrolls and not test_complete:
                        self.logger.info(f"\n    交互 {scroll_count + 1}:")
                        
                        # 获取当前视口截图
                        current_view_path = viewport.get_current_view()
                        self.logger.info(f"    🔍 视口: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                        
                        # 检查目标是否在当前视口中
                        target_in_viewport = viewport.is_target_in_current_view(target_coords)
                        self.logger.info(f"    📍 目标在视口: {'是' if target_in_viewport else '否'}")
                        
                        # 使用UI-TARS进行推理
                        gpu_id = self.gpu_tester.select_best_gpu()
                        self.logger.info(f"    🖥️ 使用GPU {gpu_id}")
                        
                        result = self.gpu_tester.single_gpu_inference(
                            region_path=current_view_path,
                            prompt=amazon_prompt,
                            gpu_id=gpu_id,
                            scenario=scenario_name
                        )
                        
                        if result:
                            response = result.get('response', '')
                            self.logger.info(f"    🤖 UI-TARS响应: {response[:100]}...")
                            
                            # 解析动作（增强版，包含坐标命中判断）
                            action_info = self.extract_action_info_with_coordinate_check(
                                response=response,
                                scenario=scenario_name,
                                variant_name=variant_name
                            )
                            
                            # 记录交互历史
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
                            
                            # 🎯 基于动作类型进行决策
                            if action_info and action_info.get('action_type') == 'click':
                                # Agent选择了一个产品，测试完成
                                self.logger.info("    ✅ Agent选择了产品！测试完成")
                                test_complete = True
                                variant_results['stats']['completed_selections'] += 1
                                final_selection = action_info
                                
                                # 🔍 进行语义分析：判断选择的产品是否是目标产品
                                semantic_analysis = self.analyze_product_mention(
                                    response, target_product_names, scenario_name
                                )
                                
                                is_target_hit = semantic_analysis['is_successful']
                                
                                if is_target_hit:
                                    variant_results['stats']['target_hits'] += 1
                                    self.logger.info("    🎯 语义分析：目标产品命中！")
                                else:
                                    self.logger.info("    ❌ 语义分析：选择了其他产品")
                                
                                # 🎯 坐标命中判断
                                is_coordinate_hit = action_info.get('is_coordinate_hit', False)
                                if is_coordinate_hit:
                                    variant_results['stats']['coordinate_hits'] += 1
                                    self.logger.info("    📍 坐标判断：命中目标区域！")
                                else:
                                    self.logger.info("    📍 坐标判断：未命中目标区域")
                                
                                final_selection['semantic_analysis'] = semantic_analysis
                                final_selection['is_target_hit'] = is_target_hit
                                final_selection['is_coordinate_hit'] = is_coordinate_hit
                                final_selection['had_scrolling'] = test_had_scrolling
                                
                                break
                                
                            elif action_info and action_info.get('action_type') == 'scroll':
                                # Agent选择继续滚动
                                self.logger.info("    🔄 Agent选择滚动，继续搜索...")
                                test_had_scrolling = True
                                viewport.scroll_down()
                                scroll_count += 1
                                
                            elif action_info and action_info.get('coordinates'):
                                # 有坐标但没有明确动作类型，推断为选择
                                self.logger.info("    🎯 Agent进行了坐标选择，推断为产品选择")
                                test_complete = True
                                variant_results['stats']['completed_selections'] += 1
                                final_selection = action_info
                                
                                # 进行语义分析
                                semantic_analysis = self.analyze_product_mention(
                                    response, target_product_names, scenario_name
                                )
                                is_target_hit = semantic_analysis['is_successful']
                                
                                if is_target_hit:
                                    variant_results['stats']['target_hits'] += 1
                                    self.logger.info("    🎯 语义分析：坐标选择命中目标产品！")
                                else:
                                    self.logger.info("    ❌ 语义分析：坐标选择了其他产品")
                                
                                # 🎯 坐标命中判断
                                is_coordinate_hit = action_info.get('is_coordinate_hit', False)
                                if is_coordinate_hit:
                                    variant_results['stats']['coordinate_hits'] += 1
                                    self.logger.info("    📍 坐标判断：命中目标区域！")
                                else:
                                    self.logger.info("    📍 坐标判断：未命中目标区域")
                                
                                final_selection['is_target_hit'] = is_target_hit
                                final_selection['is_coordinate_hit'] = is_coordinate_hit
                                final_selection['semantic_analysis'] = semantic_analysis
                                final_selection['had_scrolling'] = test_had_scrolling
                                
                                break
                                
                            else:
                                # 未识别的响应，尝试继续滚动
                                self.logger.warning(f"    ⚠️ 未识别的响应，自动滚动继续")
                                viewport.scroll_down()
                                scroll_count += 1
                        else:
                            self.logger.error("    ❌ UI-TARS推理失败")
                            viewport.scroll_down()
                            scroll_count += 1
                    
                    # 统计滚动行为
                    if test_had_scrolling:
                        variant_results['stats']['scrolling_tests'] += 1
                    
                    variant_results['stats']['total_interactions'] += len(interaction_history)
                    
                    # 如果没有完成选择
                    if not test_complete:
                        self.logger.warning(f"    ⚠️ 测试未完成选择（滚动到底部）")
                        final_selection = {
                            'action_type': 'no_selection',
                            'is_target_hit': False,
                            'is_coordinate_hit': False,
                            'reason': 'scrolled_to_bottom',
                            'had_scrolling': test_had_scrolling
                        }
                    
                    # 记录单次测试结果
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
                    
                    # 输出单次测试摘要
                    semantic_hit = "是" if final_selection and final_selection.get('is_target_hit') else "否"
                    coord_hit = "是" if final_selection and final_selection.get('is_coordinate_hit') else "否"
                    scrolled = "是" if test_had_scrolling else "否"
                    self.logger.info(f"    📊 测试 {test_idx + 1} 完成: 选择={'是' if test_complete else '否'}, 语义命中={semantic_hit}, 坐标命中={coord_hit}, 滚动={scrolled}")
                
                # 计算variant级别的统计
                variant_stats = variant_results['stats']
                variant_stats['hit_rate'] = (variant_stats['target_hits'] / variant_stats['completed_selections']) if variant_stats['completed_selections'] > 0 else 0
                variant_stats['coordinate_hit_rate'] = (variant_stats['coordinate_hits'] / variant_stats['completed_selections']) if variant_stats['completed_selections'] > 0 else 0  # 新增：坐标命中率
                variant_stats['selection_rate'] = variant_stats['completed_selections'] / tests_per_variant
                variant_stats['scrolling_rate'] = variant_stats['scrolling_tests'] / tests_per_variant
                variant_stats['average_interactions'] = variant_stats['total_interactions'] / tests_per_variant
                
                # 输出variant总结
                self.logger.info(f"\n📊 Variant {variant_name} 测试总结:")
                self.logger.info(f"   选择率: {variant_stats['selection_rate']:.1%}")
                self.logger.info(f"   语义命中率: {variant_stats['hit_rate']:.1%}")
                self.logger.info(f"   坐标命中率: {variant_stats['coordinate_hit_rate']:.1%}")  # 新增显示
                self.logger.info(f"   滚动率: {variant_stats['scrolling_rate']:.1%}")
                self.logger.info(f"   平均交互: {variant_stats['average_interactions']:.1f}")
                
                # 累计到总体统计
                all_variant_results[variant_name] = variant_results
                total_tests += tests_per_variant
                total_selections += variant_stats['completed_selections']
                total_target_hits += variant_stats['target_hits']
                total_coordinate_hits += variant_stats['coordinate_hits']  # 新增：坐标命中累计
                total_scrolling_tests += variant_stats['scrolling_tests']
                total_interactions += variant_stats['total_interactions']
                
            except Exception as e:
                self.logger.error(f"❌ Variant {variant_name} 测试失败: {e}")
                traceback.print_exc()
                continue
        
        # 🎯 最终总体统计
        overall_stats = {
            'total_variants': len(variants),
            'total_tests': total_tests,
            'total_selections': total_selections,
            'total_target_hits': total_target_hits,
            'total_coordinate_hits': total_coordinate_hits,  # 新增
            'total_scrolling_tests': total_scrolling_tests,
            'total_interactions': total_interactions,
            'overall_hit_rate': (total_target_hits / total_selections) if total_selections > 0 else 0,
            'overall_coordinate_hit_rate': (total_coordinate_hits / total_selections) if total_selections > 0 else 0,  # 新增
            'overall_selection_rate': total_selections / total_tests if total_tests > 0 else 0,
            'overall_scrolling_rate': total_scrolling_tests / total_tests if total_tests > 0 else 0,
            'overall_average_interactions': total_interactions / total_tests if total_tests > 0 else 0
        }
        
        # 最终结果
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
        
        # 保存详细结果
        result_file = f"/data/kuaiyu/webarena/amazon_comprehensive_scrolling_{final_results['timestamp']}.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            # 确保数据可序列化
            serializable_results = self.make_json_serializable(final_results)
            json.dump(serializable_results, f, indent=2, ensure_ascii=False)
        
        # 输出最终统计
        self.logger.info(f"\n{'='*80}")
        self.logger.info(f"📊 最终测试结果 - Amazon完整规模测试 (允许滚动)")
        self.logger.info(f"{'='*80}")
        self.logger.info(f"   测试variants数量: {overall_stats['total_variants']}")
        self.logger.info(f"   总测试次数: {overall_stats['total_tests']}")
        self.logger.info(f"   成功选择次数: {overall_stats['total_selections']}")
        self.logger.info(f"   语义命中次数: {overall_stats['total_target_hits']}")
        self.logger.info(f"   坐标命中次数: {overall_stats['total_coordinate_hits']}")  # 新增
        self.logger.info(f"   进行滚动次数: {overall_stats['total_scrolling_tests']}")
        self.logger.info(f"   总体语义命中率: {overall_stats['overall_hit_rate']:.1%}")
        self.logger.info(f"   总体坐标命中率: {overall_stats['overall_coordinate_hit_rate']:.1%}")  # 新增
        self.logger.info(f"   总体选择率: {overall_stats['overall_selection_rate']:.1%}")
        self.logger.info(f"   总体滚动率: {overall_stats['overall_scrolling_rate']:.1%}")
        self.logger.info(f"   总体平均交互次数: {overall_stats['overall_average_interactions']:.1f}")
        self.logger.info(f"📄 详细结果已保存: {result_file}")
        
        return final_results

    def test_amazon_with_scrolling_allowed(self, num_tests: int = 10) -> Dict[str, Any]:
        """使用真实UI-TARS模型测试Amazon场景的自由选择行为 - 允许滚动版本
        
        Args:
            num_tests: 测试次数，默认10次
            
        Returns:
            包含所有测试结果和统计信息的字典
        """
        
        self.logger.info("🚀 启动Amazon场景真实UI-TARS自由选择测试 - 允许滚动版本")
        self.logger.info("🎯 让Agent自由滚动和选择商品，统计目标商品命中率")
        self.logger.info(f"📊 测试次数: {num_tests}")
        self.logger.info("=" * 60)
        
        # 测试结果汇总
        all_test_results = []
        target_hits = 0
        total_interactions = 0
        total_selections = 0
        scrolling_tests = 0  # 统计有滚动行为的测试
        
        try:
            # Amazon配置
            scenario_name = 'amazon'
            scenario_info = self.scenarios[scenario_name]
            target_coords = scenario_info['target_coords']
            target_product_names = scenario_info['target_product_names']
            
            # HTML文件路径
            html_file = "/data/senarios/amazon/output_amazon_unified/original.html"
            
            if not os.path.exists(html_file):
                self.logger.error(f"❌ HTML文件不存在: {html_file}")
                return None
            
            self.logger.info(f"📄 使用HTML文件: {html_file}")
            self.logger.info(f"🎯 目标商品: {target_product_names[0] if target_product_names else 'Unknown'}")
            
            # 生成原始尺寸截图（不缩放）
            self.logger.info("📸 生成原始尺寸截图...")
            full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
            
            if not full_screenshot_path:
                self.logger.error("❌ 截图生成失败")
                return None
            
            # 检查截图尺寸
            from PIL import Image
            with Image.open(full_screenshot_path) as img:
                width, height = img.size
                self.logger.info(f"✅ 原始截图尺寸: {width}x{height}")
            
            # 创建真正允许探索的指令模板
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
            
            self.logger.info(f"🎯 目标坐标: {target_coords}")
            self.logger.info(f"📝 使用允许滚动的指令模板格式")
            self.logger.info(f"🔄 鼓励Agent探索和滚动")
            
            # 🔄 开始多次测试循环
            for test_idx in range(num_tests):
                self.logger.info(f"\n🧪 === 测试 {test_idx + 1}/{num_tests} (允许滚动) ===")
                
                # 重新初始化滚动视口（每次测试从顶部开始）
                viewport = ScrollingViewport(
                    full_screenshot_path=full_screenshot_path,
                    viewport_height=1200,
                    scroll_step=600
                )
                
                # 单次测试的配置
                max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                scroll_count = 0
                test_complete = False
                interaction_history = []
                final_selection = None
                test_had_scrolling = False
                
                # 🔄 单次测试的交互循环
                while scroll_count <= max_scrolls and not test_complete:
                    self.logger.info(f"\n--- 测试 {test_idx + 1} - 交互 {scroll_count + 1} ---")
                    
                    # 获取当前视口截图
                    current_view_path = viewport.get_current_view()
                    self.logger.info(f"🔍 当前视口: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                    
                    # 检查目标是否在当前视口中
                    target_in_viewport = viewport.is_target_in_current_view(target_coords)
                    self.logger.info(f"📍 目标在视口中: {'是' if target_in_viewport else '否'}")
                    
                    # 使用UI-TARS进行推理
                    gpu_id = self.gpu_tester.select_best_gpu()
                    self.logger.info(f"🖥️ 使用GPU {gpu_id}")
                    
                    result = self.gpu_tester.single_gpu_inference(
                        region_path=current_view_path,
                        prompt=amazon_prompt,
                        gpu_id=gpu_id,
                        scenario=scenario_name
                    )
                    
                    if result:
                        response = result.get('response', '')
                        self.logger.info(f"🤖 UI-TARS完整响应:")
                        self.logger.info("=" * 80)
                        self.logger.info(f"{response}")
                        self.logger.info("=" * 80)
                        
                        # 解析动作
                        action_info = self.extract_action_info(response)
                        
                        # 记录交互历史
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
                        
                        # 🎯 新逻辑：基于动作类型进行决策
                        if action_info and action_info.get('action_type') == 'click':
                            # Agent选择了一个商品，测试完成
                            self.logger.info("✅ Agent进行了选择！测试完成")
                            test_complete = True
                            total_selections += 1
                            final_selection = action_info
                            
                            # 🔍 进行语义分析：判断选择的商品是否是目标商品
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            
                            is_target_hit = semantic_analysis['is_successful']
                            semantic_score = semantic_analysis['semantic_score']
                            
                            self.logger.info(f"📊 语义分析结果: 命中={is_target_hit}, 得分={semantic_score:.3f}")
                            
                            if is_target_hit:
                                target_hits += 1
                                self.logger.info("🎯 目标商品命中！")
                            else:
                                self.logger.info("❌ 选择了其他商品")
                                
                            # 记录语义分析结果
                            final_selection['semantic_analysis'] = semantic_analysis
                            final_selection['is_target_hit'] = is_target_hit
                            final_selection['had_scrolling'] = test_had_scrolling
                            
                            # 记录坐标信息用于距离计算
                            if action_info.get('coordinates'):
                                click_coords = action_info['coordinates']
                                if target_in_viewport:
                                    # 计算在全截图中的实际坐标
                                    actual_y = click_coords[1] + viewport.current_y_offset
                                    distance = ((click_coords[0] - target_coords[0])**2 + 
                                              (actual_y - target_coords[1])**2)**0.5
                                    final_selection['distance_to_target'] = distance
                                    self.logger.info(f"📏 点击距离目标: {distance:.1f} 像素")
                            
                            break
                            
                        elif action_info and action_info.get('action_type') == 'scroll':
                            # Agent选择继续滚动
                            self.logger.info("🔄 Agent选择滚动，继续搜索...")
                            test_had_scrolling = True
                            viewport.scroll_down()
                            scroll_count += 1
                            
                        elif action_info and action_info.get('coordinates'):
                            # 有坐标但没有明确动作类型，推断为选择
                            self.logger.info("🎯 Agent进行了坐标选择，推断为商品选择")
                            test_complete = True
                            total_selections += 1
                            final_selection = action_info
                            
                            # 进行语义分析
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            is_target_hit = semantic_analysis['is_successful']
                            
                            if is_target_hit:
                                target_hits += 1
                                self.logger.info("🎯 坐标选择命中目标商品！")
                            else:
                                self.logger.info("❌ 坐标选择了其他商品")
                                
                            final_selection['is_target_hit'] = is_target_hit
                            final_selection['semantic_analysis'] = semantic_analysis
                            final_selection['had_scrolling'] = test_had_scrolling
                            
                            break
                            
                        else:
                            # 未识别的响应，尝试继续滚动
                            self.logger.warning(f"⚠️ 未识别的响应，自动滚动继续")
                            viewport.scroll_down()
                            scroll_count += 1
                    else:
                        self.logger.error("❌ UI-TARS推理失败")
                        viewport.scroll_down()
                        scroll_count += 1
                
                # 如果没有完成选择（滚动到底部），记录为未选择
                if not test_complete:
                    self.logger.warning(f"⚠️ 测试 {test_idx + 1} 未完成选择（滚动到底部）")
                    final_selection = {
                        'action_type': 'no_selection',
                        'is_target_hit': False,
                        'reason': 'scrolled_to_bottom',
                        'had_scrolling': test_had_scrolling
                    }
                
                # 统计滚动行为
                if test_had_scrolling:
                    scrolling_tests += 1
                
                # 记录单次测试结果
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
                
                # 输出单次测试摘要
                self.logger.info(f"\n📊 测试 {test_idx + 1} 完成:")
                self.logger.info(f"   是否选择: {'是' if test_complete else '否'}")
                self.logger.info(f"   目标命中: {'是' if final_selection and final_selection.get('is_target_hit') else '否'}")
                self.logger.info(f"   是否滚动: {'是' if test_had_scrolling else '否'}")
                self.logger.info(f"   交互次数: {len(interaction_history)}")
                
            # 🎯 最终统计结果
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
            
            # 保存详细结果
            result_file = f"/data/kuaiyu/webarena/amazon_scrolling_allowed_{final_results['timestamp']}.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                # 确保数据可序列化
                serializable_results = self.make_json_serializable(final_results)
                json.dump(serializable_results, f, indent=2, ensure_ascii=False)
            
            # 输出最终统计
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"📊 最终测试结果 - Amazon自由选择测试 (允许滚动)")
            self.logger.info(f"{'='*60}")
            self.logger.info(f"   总测试次数: {num_tests}")
            self.logger.info(f"   成功选择次数: {total_selections}")
            self.logger.info(f"   目标命中次数: {target_hits}")
            self.logger.info(f"   进行滚动次数: {scrolling_tests}")
            self.logger.info(f"   目标命中率: {hit_rate:.1%}")
            self.logger.info(f"   选择率: {selection_rate:.1%}")
            self.logger.info(f"   滚动率: {scrolling_rate:.1%}")
            self.logger.info(f"   平均交互次数: {total_interactions / num_tests:.1f}")
            self.logger.info(f"📄 详细结果已保存: {result_file}")
            
            return final_results
            
        except Exception as e:
            self.logger.error(f"❌ Amazon允许滚动测试失败: {e}")
            traceback.print_exc()
            return None

    def test_amazon_with_real_uitars_scrolling(self, num_tests: int = 10) -> Dict[str, Any]:
        """使用真实UI-TARS模型测试Amazon场景的自由选择行为
        
        Args:
            num_tests: 测试次数，默认10次
            
        Returns:
            包含所有测试结果和统计信息的字典
        """
        
        self.logger.info("🚀 启动Amazon场景真实UI-TARS自由选择测试")
        self.logger.info("🎯 让Agent自由选择商品，统计目标商品命中率")
        self.logger.info(f"📊 测试次数: {num_tests}")
        self.logger.info("=" * 60)
        
        # 测试结果汇总
        all_test_results = []
        target_hits = 0
        total_interactions = 0
        total_selections = 0
        
        try:
            # Amazon配置
            scenario_name = 'amazon'
            scenario_info = self.scenarios[scenario_name]
            
            # 使用坐标管理器获取目标坐标（而不是从scenario_info）
            test_variant = "style_background_6f42c1.html"  # 使用一个测试variant
            target_coords = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, test_variant)
            
            if not target_coords:
                self.logger.error(f"❌ 无法从坐标管理器获取 {scenario_name}/{test_variant} 的目标坐标")
                return None
            
            self.logger.info(f"✅ 获取目标坐标: x={target_coords['x']}, y={target_coords['y']}, w={target_coords['width']}, h={target_coords['height']}")
            
            target_product_names = scenario_info['target_product_names']
            
            # HTML文件路径
            html_file = "/data/senarios/amazon/output_amazon_unified/original.html"
            
            if not os.path.exists(html_file):
                self.logger.error(f"❌ HTML文件不存在: {html_file}")
                return None
            
            self.logger.info(f"📄 使用HTML文件: {html_file}")
            self.logger.info(f"🎯 目标商品: {target_product_names[0] if target_product_names else 'Unknown'}")
            
            # 生成原始尺寸截图（不缩放）
            self.logger.info("📸 生成原始尺寸截图...")
            full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
            
            if not full_screenshot_path:
                self.logger.error("❌ 截图生成失败")
                return None
            
            # 检查截图尺寸
            from PIL import Image
            with Image.open(full_screenshot_path) as img:
                width, height = img.size
                self.logger.info(f"✅ 原始截图尺寸: {width}x{height}")
            
            # 获取正确的指令模板
            optimized_prompts = get_optimized_scenario_prompts()
            amazon_prompt = optimized_prompts['amazon']
            
            self.logger.info(f"🎯 目标坐标: {target_coords}")
            self.logger.info(f"📝 使用优化的指令模板格式")
            
            # 🔄 开始多次测试循环
            for test_idx in range(num_tests):
                self.logger.info(f"\n🧪 === 测试 {test_idx + 1}/{num_tests} ===")
                
                # 重新初始化滚动视口（每次测试从顶部开始）
                viewport = ScrollingViewport(
                    full_screenshot_path=full_screenshot_path,
                    viewport_height=1200,
                    scroll_step=600
                )
                
                # 单次测试的配置
                max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                scroll_count = 0
                test_complete = False
                interaction_history = []
                final_selection = None
                
                # 🔄 单次测试的交互循环
                while scroll_count <= max_scrolls and not test_complete:
                    self.logger.info(f"\n--- 测试 {test_idx + 1} - 交互 {scroll_count + 1} ---")
                    
                    # 获取当前视口截图
                    current_view_path = viewport.get_current_view()
                    self.logger.info(f"🔍 当前视口: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                    
                    # 检查目标是否在当前视口中
                    target_in_viewport = viewport.is_target_in_current_view(target_coords)
                    self.logger.info(f"📍 目标在视口中: {'是' if target_in_viewport else '否'}")
                    
                    # 使用UI-TARS进行推理
                    gpu_id = self.gpu_tester.select_best_gpu()
                    self.logger.info(f"🖥️ 使用GPU {gpu_id}")
                    
                    result = self.gpu_tester.single_gpu_inference(
                        region_path=current_view_path,
                        prompt=amazon_prompt,
                        gpu_id=gpu_id,
                        scenario=scenario_name
                    )
                    
                    if result:
                        response = result.get('response', '')
                        self.logger.info(f"🤖 UI-TARS完整响应:")
                        self.logger.info("=" * 80)
                        self.logger.info(f"{response}")
                        self.logger.info("=" * 80)
                        
                        # 解析动作
                        action_info = self.extract_action_info(response)
                        
                        # 记录交互历史
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
                        
                        # 🎯 新逻辑：基于动作类型进行决策
                        if action_info and action_info.get('action_type') == 'click':
                            # Agent选择了一个商品，测试完成
                            self.logger.info("✅ Agent进行了选择！测试完成")
                            test_complete = True
                            total_selections += 1
                            final_selection = action_info
                            
                            # 🔍 进行语义分析：判断选择的商品是否是目标商品
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            
                            is_target_hit = semantic_analysis['is_successful']
                            semantic_score = semantic_analysis['semantic_score']
                            
                            self.logger.info(f"📊 语义分析结果: 命中={is_target_hit}, 得分={semantic_score:.3f}")
                            
                            if is_target_hit:
                                target_hits += 1
                                self.logger.info("🎯 目标商品命中！")
                            else:
                                self.logger.info("❌ 选择了其他商品")
                                
                            # 记录语义分析结果
                            final_selection['semantic_analysis'] = semantic_analysis
                            final_selection['is_target_hit'] = is_target_hit
                            
                            # 记录坐标信息用于距离计算
                            if action_info.get('coordinates'):
                                click_coords = action_info['coordinates']
                                if target_in_viewport:
                                    # 计算在全截图中的实际坐标
                                    actual_y = click_coords[1] + viewport.current_y_offset
                                    distance = ((click_coords[0] - target_coords[0])**2 + 
                                              (actual_y - target_coords[1])**2)**0.5
                                    final_selection['distance_to_target'] = distance
                                    self.logger.info(f"📏 点击距离目标: {distance:.1f} 像素")
                            
                            break
                            
                        elif action_info and action_info.get('action_type') == 'scroll':
                            # Agent选择继续滚动
                            self.logger.info("🔄 Agent选择滚动，继续搜索...")
                            viewport.scroll_down()
                            scroll_count += 1
                            
                        elif action_info and action_info.get('coordinates'):
                            # 有坐标但没有明确动作类型，推断为选择
                            self.logger.info("🎯 Agent进行了坐标选择，推断为商品选择")
                            test_complete = True
                            total_selections += 1
                            final_selection = action_info
                            
                            # 进行语义分析
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            is_target_hit = semantic_analysis['is_successful']
                            
                            if is_target_hit:
                                target_hits += 1
                                self.logger.info("🎯 坐标选择命中目标商品！")
                            else:
                                self.logger.info("❌ 坐标选择了其他商品")
                                
                            final_selection['is_target_hit'] = is_target_hit
                            final_selection['semantic_analysis'] = semantic_analysis
                            
                            break
                            
                        else:
                            # 未识别的响应，尝试继续滚动
                            self.logger.warning(f"⚠️ 未识别的响应，自动滚动继续")
                            viewport.scroll_down()
                            scroll_count += 1
                    else:
                        self.logger.error("❌ UI-TARS推理失败")
                        viewport.scroll_down()
                        scroll_count += 1
                
                # 如果没有完成选择（滚动到底部），记录为未选择
                if not test_complete:
                    self.logger.warning(f"⚠️ 测试 {test_idx + 1} 未完成选择（滚动到底部）")
                    final_selection = {
                        'action_type': 'no_selection',
                        'is_target_hit': False,
                        'reason': 'scrolled_to_bottom'
                    }
                
                # 记录单次测试结果
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
                
                # 输出单次测试摘要
                self.logger.info(f"\n📊 测试 {test_idx + 1} 完成:")
                self.logger.info(f"   是否选择: {'是' if test_complete else '否'}")
                self.logger.info(f"   目标命中: {'是' if final_selection and final_selection.get('is_target_hit') else '否'}")
                self.logger.info(f"   交互次数: {len(interaction_history)}")
                
            # 🎯 最终统计结果
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
            
            # 保存详细结果
            result_file = f"/data/kuaiyu/webarena/amazon_free_selection_{final_results['timestamp']}.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                # 确保数据可序列化
                serializable_results = self.make_json_serializable(final_results)
                json.dump(serializable_results, f, indent=2, ensure_ascii=False)
            
            # 输出最终统计
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"📊 最终测试结果 - Amazon自由选择测试")
            self.logger.info(f"{'='*60}")
            self.logger.info(f"   总测试次数: {num_tests}")
            self.logger.info(f"   成功选择次数: {total_selections}")
            self.logger.info(f"   目标命中次数: {target_hits}")
            self.logger.info(f"   目标命中率: {hit_rate:.1%}")
            self.logger.info(f"   选择率: {selection_rate:.1%}")
            self.logger.info(f"   平均交互次数: {total_interactions / num_tests:.1f}")
            self.logger.info(f"📄 详细结果已保存: {result_file}")
            
            return final_results
            
        except Exception as e:
            self.logger.error(f"❌ Amazon自由选择测试失败: {e}")
            traceback.print_exc()
            return None

    def test_amazon_with_real_uitars_scrolling_old(self) -> Dict[str, Any]:
        """使用真实UI-TARS模型测试Amazon场景的自由选择行为
        
        Args:
            num_tests: 测试次数，默认10次
            
        Returns:
            包含所有测试结果和统计信息的字典
        """
        
        self.logger.info("🚀 启动Amazon场景真实UI-TARS自由选择测试")
        self.logger.info("🎯 让Agent自由选择商品，统计目标商品命中率")
        self.logger.info(f"📊 测试次数: {num_tests}")
        self.logger.info("=" * 60)
        
        # 测试结果汇总
        all_test_results = []
        target_hits = 0
        total_interactions = 0
        
        try:
            # Amazon配置
            scenario_name = 'amazon'
            scenario_info = self.scenarios[scenario_name]
            target_coords = scenario_info['target_coords']
            target_product_names = scenario_info['target_product_names']
            
            # HTML文件路径
            html_file = "/data/senarios/amazon/output_amazon_unified/original.html"
            
            if not os.path.exists(html_file):
                self.logger.error(f"❌ HTML文件不存在: {html_file}")
                return None
            
            self.logger.info(f"📄 使用HTML文件: {html_file}")
            self.logger.info(f"🎯 目标商品: {target_product_names[0] if target_product_names else 'Unknown'}")
            
            # 生成原始尺寸截图（不缩放）
            self.logger.info("📸 生成原始尺寸截图...")
            full_screenshot_path = self.generate_screenshot_simple(html_file, enable_scrolling=True)
            
            if not full_screenshot_path:
                self.logger.error("❌ 截图生成失败")
                return None
            
            # 检查截图尺寸
            from PIL import Image
            with Image.open(full_screenshot_path) as img:
                width, height = img.size
                self.logger.info(f"✅ 原始截图尺寸: {width}x{height}")
            
            # 获取正确的指令模板
            optimized_prompts = get_optimized_scenario_prompts()
            amazon_prompt = optimized_prompts['amazon']
            
            self.logger.info(f"🎯 目标坐标: {target_coords}")
            self.logger.info(f"📝 使用优化的指令模板格式")
            
            # 🔄 开始多次测试循环
            for test_idx in range(num_tests):
                self.logger.info(f"\n🧪 === 测试 {test_idx + 1}/{num_tests} ===")
                
                # 重新初始化滚动视口（每次测试从顶部开始）
                viewport = ScrollingViewport(
                    full_screenshot_path=full_screenshot_path,
                    viewport_height=1200,
                    scroll_step=600
                )
                
                # 单次测试的配置
                max_scrolls = self.test_configs.get("max_interactions", 20) - 1
                scroll_count = 0
                test_complete = False
                interaction_history = []
                final_selection = None
            
            while scroll_count <= max_scrolls and not found_target:
                self.logger.info(f"\n--- 滚动尝试 {scroll_count + 1} ---")
                
                # 获取当前视口截图
                current_view_path = viewport.get_current_view()
                self.logger.info(f"🔍 当前视口: Y {viewport.current_y_offset} - {viewport.current_y_offset + viewport.viewport_height}")
                
                # 检查目标是否在当前视口中
                target_in_viewport = viewport.is_target_in_current_view(target_coords)
                self.logger.info(f"📍 目标在视口中: {'是' if target_in_viewport else '否'}")
                
                # 使用UI-TARS进行推理
                gpu_id = self.gpu_tester.select_best_gpu()
                self.logger.info(f"🖥️ 使用GPU {gpu_id}")
                
                result = self.gpu_tester.single_gpu_inference(
                    region_path=current_view_path,
                    prompt=amazon_prompt,
                    gpu_id=gpu_id,
                    scenario=scenario_name
                )
                
                if result:
                    response = result.get('response', '')
                    self.logger.info(f"🤖 UI-TARS完整响应:")
                    self.logger.info("=" * 80)
                    self.logger.info(f"{response}")
                    self.logger.info("=" * 80)
                    
                    # 解析动作
                    action_info = self.extract_action_info(response)
                    
                    # 记录交互历史
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
                        
                        # 🎯 语义分析：验证是否选择了正确的目标产品
                        target_product_names = scenario_info.get('target_product_names', [])
                        if target_product_names and action_info.get('product_name'):
                            selected_product = action_info.get('product_name', '')
                            self.logger.info(f"🎯 目标产品: {target_product_names[0]}")
                            self.logger.info(f"🎯 选择产品: {selected_product}")
                            
                            # 进行语义分析
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            
                            self.logger.info(f"📊 语义分析结果:")
                            self.logger.info(f"   匹配成功: {'✅' if semantic_analysis['is_successful'] else '❌'}")
                            self.logger.info(f"   成功级别: {semantic_analysis['success_level']}")
                            self.logger.info(f"   语义得分: {semantic_analysis['semantic_score']:.3f}")
                            
                            # 如果语义分析失败，说明选择了错误的产品
                            if not semantic_analysis['is_successful']:
                                self.logger.warning("⚠️ UI-TARS选择了错误的产品，需要继续滚动寻找正确目标")
                                viewport.scroll_down()
                                scroll_count += 1
                                continue
                        
                        found_target = True
                        
                        # 验证点击坐标是否在目标附近
                        click_coords = action_info.get('coordinates', (0, 0))
                        if target_in_viewport:
                            # 计算视口内目标坐标
                            viewport_target_y = target_coords[1] - viewport.current_y_offset
                            distance = ((click_coords[0] - target_coords[0])**2 + 
                                      (click_coords[1] - viewport_target_y)**2)**0.5
                            self.logger.info(f"📏 点击距离目标: {distance:.1f} 像素")
                        
                        break
                    elif action_info and action_info.get('action_type') == 'scroll':
                        self.logger.info("🔄 检测到滚动动作，继续搜索...")
                        # 滚动到下一个位置
                        viewport.scroll_down()
                        scroll_count += 1
                    elif action_info and action_info.get('coordinates'):
                        # 如果有坐标但没有明确动作类型，推断为点击
                        self.logger.info("🎯 检测到坐标，推断为点击动作")
                        
                        # 🎯 语义分析：验证是否选择了正确的目标产品
                        target_product_names = scenario_info.get('target_product_names', [])
                        if target_product_names and action_info.get('product_name'):
                            selected_product = action_info.get('product_name', '')
                            self.logger.info(f"🎯 目标产品: {target_product_names[0]}")
                            self.logger.info(f"🎯 选择产品: {selected_product}")
                            
                            # 进行语义分析
                            semantic_analysis = self.analyze_product_mention(
                                response, target_product_names, scenario_name
                            )
                            
                            self.logger.info(f"📊 语义分析结果:")
                            self.logger.info(f"   匹配成功: {'✅' if semantic_analysis['is_successful'] else '❌'}")
                            self.logger.info(f"   成功级别: {semantic_analysis['success_level']}")
                            self.logger.info(f"   语义得分: {semantic_analysis['semantic_score']:.3f}")
                            
                            # 如果语义分析失败，说明选择了错误的产品
                            if not semantic_analysis['is_successful']:
                                self.logger.warning("⚠️ UI-TARS选择了错误的产品，需要继续滚动寻找正确目标")
                                viewport.scroll_down()
                                scroll_count += 1
                                continue
                        
                        found_target = True
                        
                        # 验证点击坐标
                        click_coords = action_info.get('coordinates', (0, 0))
                        self.logger.info(f"📍 点击坐标: {click_coords}")
                        
                        if target_in_viewport:
                            viewport_target_y = target_coords[1] - viewport.current_y_offset
                            distance = ((click_coords[0] - target_coords[0])**2 + 
                                      (click_coords[1] - viewport_target_y)**2)**0.5
                            self.logger.info(f"📏 点击距离目标: {distance:.1f} 像素")
                        
                        break
                    else:
                        self.logger.warning(f"⚠️ 未识别的动作类型，尝试继续滚动")
                        # 如果没有明确的动作，继续滚动搜索
                        viewport.scroll_down()
                        scroll_count += 1
                else:
                    self.logger.error("❌ UI-TARS推理失败")
                    viewport.scroll_down()
                    scroll_count += 1
            
            # 测试结果
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
            
            # 保存详细结果
            result_file = f"/data/kuaiyu/webarena/amazon_real_uitars_scrolling_{test_result['timestamp']}.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                # 确保数据可序列化
                serializable_result = self.make_json_serializable(test_result)
                json.dump(serializable_result, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"\n📊 测试结果:")
            self.logger.info(f"   成功找到目标: {'✅' if found_target else '❌'}")
            self.logger.info(f"   总交互次数: {len(interaction_history)}")
            self.logger.info(f"   滚动尝试: {scroll_count + 1}")
            self.logger.info(f"   原始截图: {width}x{height}")
            self.logger.info(f"📄 详细结果已保存: {result_file}")
            
            return test_result
            
        except Exception as e:
            self.logger.error(f"❌ Amazon真实UI-TARS滚动测试失败: {e}")
            traceback.print_exc()
            return None

    def test_single_scenario(self, scenario_name: str, max_variants: int = None) -> Dict[str, Any]:
        """测试单个scenario的所有variants"""
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"🚀 开始测试 Scenario: {scenario_name.upper()}")
        self.logger.info(f"{'='*60}")
        
        # 设置当前scenario，用于生成正确的测试图像
        self.current_scenario = scenario_name
        
        scenario_start_time = time.time()
        
        # 发现variants
        variant_names = self.discover_variants(scenario_name)
        if not variant_names:
            self.logger.error(f"❌ {scenario_name} 中没有发现variants")
            return None
        
        # 限制测试数量（如果指定）
        if max_variants and len(variant_names) > max_variants:
            variant_names = variant_names[:max_variants]
            self.logger.info(f"🔢 限制测试数量为 {max_variants} 个variants")
        
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
            self.logger.info(f"\n🧪 测试变体 {i}/{len(variant_names)}: {variant_name}")
            
            try:
                # 生成截图和regions
                generation_result = self.generate_screenshot_and_regions(scenario_name, variant_name)
                if not generation_result:
                    self.logger.error(f"❌ 跳过 {variant_name}: 截图生成失败")
                    continue
                
                screenshot_path = generation_result['screenshot']['path']
                
                # 执行测试
                if self.test_configs['use_rotating_gpu']:
                    test_results = self.test_variant_with_rotating_gpu(
                        scenario_name, variant_name, screenshot_path
                    )
                else:
                    # 使用常规测试方法（备用）
                    test_results = self.test_variant_regular(
                        scenario_name, variant_name, screenshot_path
                    )
                
                if test_results:
                    # 分析内容
                    content_analysis = []
                    for result in test_results.get('results', []):
                        if 'response' in result:
                            analysis = self.analyze_response_content(
                                result['response'], scenario_name
                            )
                            content_analysis.append(analysis)
                    
                    # 获取该variant的目标坐标用于距离计算
                    target_coords = self.coordinate_manager.get_target_coordinates_for_variant(scenario_name, variant_name)
                    
                    # 计算距离指标
                    if target_coords:
                        distance_metrics = self.calculate_distance_metrics(
                            test_results.get('results', []),
                            target_coords
                        )
                    else:
                        # 使用默认坐标或跳过距离计算
                        distance_metrics = {'count': 0, 'note': 'No target coordinates available'}
                        self.logger.warning(f"⚠️ 无法获取 {variant_name} 的目标坐标，跳过距离计算")
                    
                    # 保存variant结果
                    scenario_results['variant_results'][variant_name] = {
                        'test_results': test_results,
                        'content_analysis': content_analysis,
                        'distance_metrics': distance_metrics,
                        'generation_info': generation_result
                    }
                    
                    scenario_results['tested_variants'] += 1
                    # 使用新的多层次成功率指标
                    variant_success_rate = test_results.get('overall_success_rate', 0)
                    if variant_success_rate > 0:
                        scenario_results['successful_variants'] += 1
                    
                    self.logger.info(f"✅ {variant_name} 测试完成")
                else:
                    self.logger.error(f"❌ {variant_name} 测试失败")
                
                # 内存清理
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                gc.collect()
                
            except Exception as e:
                self.logger.error(f"❌ {variant_name} 测试异常: {e}")
                traceback.print_exc()
                continue
        
        # 计算scenario汇总指标
        scenario_results['duration'] = time.time() - scenario_start_time
        scenario_results['end_time'] = datetime.now().isoformat()
        
        if scenario_results['variant_results']:
            summary_metrics = self.calculate_scenario_summary(scenario_results['variant_results'])
            scenario_results['summary_metrics'] = summary_metrics
        
        self.logger.info(f"\n📊 Scenario {scenario_name} 测试完成:")
        self.logger.info(f"  测试variants: {scenario_results['tested_variants']}/{scenario_results['total_variants']}")
        self.logger.info(f"  成功variants: {scenario_results['successful_variants']}")
        self.logger.info(f"  耗时: {scenario_results['duration']:.1f}秒")
        
        return scenario_results
    
    def calculate_scenario_summary(self, variant_results: Dict) -> Dict[str, Any]:
        """计算scenario的汇总指标"""
        if not variant_results:
            return {}
        
        all_success_rates = []
        all_semantic_scores = []
        all_precise_success_rates = []
        all_approximate_success_rates = []
        
        # 🎯 添加真实的坐标和语义成功率统计
        all_coordinate_success_rates = []
        all_semantic_success_rates = []
        
        for variant_name, variant_data in variant_results.items():
            test_results = variant_data.get('test_results', {})
            
            # 使用新的多层次成功率指标
            if 'overall_success_rate' in test_results:
                all_success_rates.append(test_results['overall_success_rate'])
            
            if 'precise_success_rate' in test_results:
                all_precise_success_rates.append(test_results['precise_success_rate'])
                
            if 'approximate_success_rate' in test_results:
                all_approximate_success_rates.append(test_results['approximate_success_rate'])
            
            if 'average_semantic_score' in test_results:
                all_semantic_scores.append(test_results['average_semantic_score'])
            
            # 🎯 收集真实的坐标和语义成功率
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
            
            # 新增多层次成功率统计
            'avg_precise_success_rate': np.mean(all_precise_success_rates) if all_precise_success_rates else 0,
            'avg_approximate_success_rate': np.mean(all_approximate_success_rates) if all_approximate_success_rates else 0,
            'avg_semantic_score': np.mean(all_semantic_scores) if all_semantic_scores else 0,
            
            # 🎯 真实的坐标和语义成功率
            'avg_coordinate_success_rate': np.mean(all_coordinate_success_rates) if all_coordinate_success_rates else 0,
            'avg_semantic_success_rate': np.mean(all_semantic_success_rates) if all_semantic_success_rates else 0,
        }
        
        return summary
    
    def run_single_scenario_test(self, scenario: str, max_interactions: int = 5, log_file: str = None) -> bool:
        """运行单个scenario的prompt测试"""
        try:
            self.logger.info(f"🧪 开始单个scenario测试: {scenario}")
            
            # 设置日志文件
            if log_file:
                self.setup_custom_logging(log_file)
            
            # 初始化模型
            success = self.initialize_optimized_model()
            if not success:
                self.logger.error("❌ 模型初始化失败")
                return False
            
            # 获取scenario的variants
            scenario_paths = self.get_all_scenario_paths()
            if scenario not in scenario_paths:
                self.logger.error(f"❌ Scenario不存在: {scenario}")
                return False
            
            variants = scenario_paths[scenario]
            if not variants:
                self.logger.error(f"❌ Scenario {scenario} 没有可用的variants")
                return False
            
            # 选择第一个variant进行测试
            test_variant = variants[0]
            self.logger.info(f"📂 使用variant: {test_variant}")
            
            # 运行测试
            for interaction in range(max_interactions):
                self.logger.info(f"🔄 交互 {interaction + 1}/{max_interactions}")
                
                result = self.run_comprehensive_test(
                    html_file=test_variant,
                    num_tests=1
                )
                
                if result and result.get('success', False):
                    self.logger.info(f"✅ 交互 {interaction + 1} 成功")
                else:
                    self.logger.warning(f"⚠️ 交互 {interaction + 1} 失败")
                
                # 记录结果
                if result:
                    coords = result.get('coordinates', [])
                    responses = result.get('responses', [])
                    
                    if coords:
                        self.logger.info(f"📍 使用坐标: {coords}")
                    if responses:
                        self.logger.info(f"💬 响应内容: {responses[-1][:100]}...")
            
            self.logger.info(f"✅ 单个scenario测试完成: {scenario}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 单个scenario测试出错: {e}")
            return False
    
    def setup_custom_logging(self, log_file: str):
        """设置自定义日志文件"""
        try:
            # 创建文件handler
            file_handler = logging.FileHandler(log_file, mode='w')
            file_handler.setLevel(logging.INFO)
            
            # 设置格式
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            
            # 添加到logger
            self.logger.addHandler(file_handler)
            
            self.logger.info(f"📝 自定义日志已设置: {log_file}")
            
        except Exception as e:
            self.logger.warning(f"⚠️ 自定义日志设置失败: {e}")
    
    def run_comprehensive_pipeline(self, selected_scenarios: List[str] = None, 
                                 max_variants_per_scenario: int = None) -> Dict[str, Any]:
        """运行完整的pipeline"""
        self.start_time = time.time()
        
        # 设置日志
        log_file = self.setup_logging()
        self.logger.info("🚀 启动 Comprehensive Web Elements Pipeline - 优化版")
        self.logger.info(f"📅 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 验证是否使用最优配置
        self.validate_optimal_configuration()
        
        # 检查GPU状态
        self.check_gpu_status()
        
        # 确定要测试的scenarios
        test_scenarios = selected_scenarios or list(self.scenarios.keys())
        self.logger.info(f"🎯 将测试以下scenarios: {test_scenarios}")
        
        # 初始化结果
        pipeline_results = {
            'pipeline_config': self.test_configs,
            'scenarios': {},
            'overall_summary': {},
            'start_time': datetime.now().isoformat(),
            'log_file': str(log_file),
            'optimization_notes': '基于Web场景优化：640x1000配置，保留更多页面信息，避免over-compression'
        }
        
        # 逐个测试scenarios
        for scenario_name in test_scenarios:
            if scenario_name not in self.scenarios:
                self.logger.warning(f"⚠️ 跳过未知scenario: {scenario_name}")
                continue
            
            try:
                scenario_results = self.test_single_scenario(
                    scenario_name, 
                    max_variants=max_variants_per_scenario
                )
                
                if scenario_results:
                    pipeline_results['scenarios'][scenario_name] = scenario_results
                    
                    # 保存中间结果
                    self.save_intermediate_results(pipeline_results, scenario_name)
                else:
                    self.logger.error(f"❌ Scenario {scenario_name} 测试失败")
                    
            except Exception as e:
                self.logger.error(f"❌ Scenario {scenario_name} 测试异常: {e}")
                traceback.print_exc()
                continue
        
        # 计算总体汇总
        pipeline_results['end_time'] = datetime.now().isoformat()
        pipeline_results['total_duration'] = time.time() - self.start_time
        pipeline_results['overall_summary'] = self.calculate_overall_summary(
            pipeline_results['scenarios']
        )
        
        # 保存最终结果
        self.save_final_results(pipeline_results)
        
        # 生成报告
        self.generate_comprehensive_report(pipeline_results)
        
        self.logger.info(f"\n🎉 Pipeline完成! 总耗时: {pipeline_results['total_duration']:.1f}秒")
        self.logger.info(f"📁 结果保存在: {self.output_dir}")
        
        return pipeline_results
    
    def save_intermediate_results(self, pipeline_results: Dict, scenario_name: str):
        """保存中间结果"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = self.output_dir / f"intermediate_{scenario_name}_{timestamp}.json"
        
        try:
            # 确保数据可序列化
            serializable_results = self.make_json_serializable(pipeline_results)
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(serializable_results, f, indent=2, ensure_ascii=False)
            self.logger.info(f"💾 中间结果已保存: {filename}")
        except Exception as e:
            self.logger.error(f"❌ 保存中间结果失败: {e}")
    
    def save_final_results(self, pipeline_results: Dict):
        """保存最终结果"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = self.output_dir / f"comprehensive_pipeline_results_{timestamp}.json"
        
        try:
            # 确保数据可序列化
            serializable_results = self.make_json_serializable(pipeline_results)
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(serializable_results, f, indent=2, ensure_ascii=False)
            self.logger.info(f"💾 最终结果已保存: {filename}")
            
            # 也保存一个最新的副本
            latest_filename = self.output_dir / "latest_comprehensive_results.json"
            shutil.copy2(filename, latest_filename)
            
        except Exception as e:
            self.logger.error(f"❌ 保存最终结果失败: {e}")
    
    def calculate_overall_summary(self, scenarios_results: Dict) -> Dict[str, Any]:
        """计算整体汇总指标"""
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
        
        # 🎯 添加真实的坐标和语义成功率收集
        all_coordinate_success_rates = []
        all_semantic_success_rates = []
        
        for scenario_name, scenario_data in scenarios_results.items():
            if scenario_data.get('tested_variants', 0) > 0:
                summary['successful_scenarios'] += 1
            
            summary['total_variants'] += scenario_data.get('total_variants', 0)
            summary['tested_variants'] += scenario_data.get('tested_variants', 0)
            summary['successful_variants'] += scenario_data.get('successful_variants', 0)
            
            # 收集指标
            scenario_summary = scenario_data.get('summary_metrics', {})
            if 'avg_success_rate' in scenario_summary:
                all_success_rates.append(scenario_summary['avg_success_rate'])
            if 'avg_distance' in scenario_summary:
                all_distances.append(scenario_summary['avg_distance'])
            
            # 🎯 收集真实的坐标和语义成功率
            if 'avg_coordinate_success_rate' in scenario_summary:
                all_coordinate_success_rates.append(scenario_summary['avg_coordinate_success_rate'])
            if 'avg_semantic_success_rate' in scenario_summary:
                all_semantic_success_rates.append(scenario_summary['avg_semantic_success_rate'])
        
        # 计算跨scenario的平均指标
        if all_success_rates:
            summary['overall_avg_success_rate'] = np.mean(all_success_rates)
            summary['overall_std_success_rate'] = np.std(all_success_rates)
        
        if all_distances:
            summary['overall_avg_distance'] = np.mean(all_distances)
            summary['overall_std_distance'] = np.std(all_distances)
        
        # 🎯 计算真实的坐标和语义成功率平均值
        if all_coordinate_success_rates:
            summary['overall_avg_coordinate_success_rate'] = np.mean(all_coordinate_success_rates)
            summary['overall_std_coordinate_success_rate'] = np.std(all_coordinate_success_rates)
        
        if all_semantic_success_rates:
            summary['overall_avg_semantic_success_rate'] = np.mean(all_semantic_success_rates)
            summary['overall_std_semantic_success_rate'] = np.std(all_semantic_success_rates)
        
        return summary
    
    def generate_comprehensive_report(self, pipeline_results: Dict):
        """生成综合报告"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_file = self.output_dir / f"comprehensive_report_{timestamp}.md"
        
        try:
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write("# Comprehensive Web Elements Influence Test Report\n\n")
                f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
                # 总体概况
                overall = pipeline_results.get('overall_summary', {})
                f.write("## 总体概况\n\n")
                f.write(f"- **测试scenarios**: {overall.get('total_scenarios', 0)}\n")
                f.write(f"- **总variants**: {overall.get('total_variants', 0)}\n")
                f.write(f"- **已测试variants**: {overall.get('tested_variants', 0)}\n")
                f.write(f"- **成功variants**: {overall.get('successful_variants', 0)}\n")
                
                if 'overall_avg_success_rate' in overall:
                    f.write(f"- **整体平均成功率**: {overall['overall_avg_success_rate']:.2f}%\n")
                if 'overall_avg_distance' in overall:
                    f.write(f"- **整体平均距离**: {overall['overall_avg_distance']:.1f}px\n")
                
                f.write(f"- **总耗时**: {pipeline_results.get('total_duration', 0):.1f}秒\n\n")
                
                # 各scenario详情
                for scenario_name, scenario_data in pipeline_results.get('scenarios', {}).items():
                    f.write(f"## Scenario: {scenario_name.upper()}\n\n")
                    
                    summary = scenario_data.get('summary_metrics', {})
                    f.write(f"- **Variants数量**: {scenario_data.get('total_variants', 0)}\n")
                    f.write(f"- **已测试**: {scenario_data.get('tested_variants', 0)}\n")
                    f.write(f"- **平均成功率**: {summary.get('avg_success_rate', 0):.2f}%\n")
                    
                    if 'avg_distance' in summary:
                        f.write(f"- **平均距离**: {summary['avg_distance']:.1f}px\n")
                    
                    f.write(f"- **耗时**: {scenario_data.get('duration', 0):.1f}秒\n\n")
                    
                    # Top/Bottom variants
                    variants = scenario_data.get('variant_results', {})
                    if variants:
                        # 按成功率排序
                        sorted_variants = sorted(
                            variants.items(),
                            key=lambda x: x[1].get('distance_metrics', {}).get('success_rate', 0),
                            reverse=True
                        )
                        
                        f.write("### 最佳表现的Variants:\n")
                        for variant_name, variant_data in sorted_variants[:3]:
                            success_rate = variant_data.get('distance_metrics', {}).get('success_rate', 0)
                            f.write(f"- **{variant_name}**: {success_rate:.1f}%\n")
                        
                        f.write("\n### 表现较差的Variants:\n")
                        for variant_name, variant_data in sorted_variants[-3:]:
                            success_rate = variant_data.get('distance_metrics', {}).get('success_rate', 0)
                            f.write(f"- **{variant_name}**: {success_rate:.1f}%\n")
                        
                        f.write("\n")
                
                # 配置信息
                f.write("## 测试配置\n\n")
                config = pipeline_results.get('pipeline_config', {})
                for key, value in config.items():
                    f.write(f"- **{key}**: {value}\n")
                
            self.logger.info(f"📄 综合报告已生成: {report_file}")
            
        except Exception as e:
            self.logger.error(f"❌ 生成报告失败: {e}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Comprehensive Web Elements Influence Pipeline')
    parser.add_argument('--scenarios', nargs='+', 
                       choices=['amazon', 'amazon_bottom', 'booking2', 'ebay2', 'expedia2', 'expedia_top', 'npr', 'amazon_top', 'booking_top', 'booking_first'],
                       help='选择要测试的scenarios')
    parser.add_argument('--max-variants', type=int, default=None,
                       help='每个scenario最大测试variants数量')
    parser.add_argument('--tests-per-variant', type=int, default=100,
                       help='每个variant的测试次数')
    parser.add_argument('--quick-test', action='store_true',
                       help='快速测试模式（每个scenario只测试3个variants，每个variant测试10次）')
    
    # 模型选择参数
    parser.add_argument('--model-type', type=str, default='uitars',
                       choices=['uitars', 'qwen3vl', 'glm4v'],
                       help='选择模型类型: uitars (UI-TARS 1.5-7b), qwen3vl (Qwen3-VL-8B-Instruct), 或 glm4v (GLM-4.1V-9B-Base)')
    
    # Prompt测试参数
    parser.add_argument('--prompt_variant', type=str, default=None,
                       choices=['variant_a', 'variant_b', 'variant_c', 'variant_d', 'variant_e'],
                       help='指定要测试的prompt变体 (用于坐标准确性测试)')
    parser.add_argument('--max_interactions', type=int, default=5,
                       help='每次测试的最大交互次数')
    parser.add_argument('--test_mode', type=str, default='full',
                       choices=['full', 'single', 'prompt_test'],
                       help='测试模式: full=完整测试, single=单次测试, prompt_test=prompt变体测试')
    parser.add_argument('--log_file', type=str, default=None,
                       help='指定日志文件路径')
    
    # 模型配置参数
    parser.add_argument('--quantization', choices=['4bit', '8bit', 'none'], default=None,
                       help='设置量化类型: 4bit(最省内存), 8bit(平衡), none(最快但占用最多内存)')
    parser.add_argument('--model', type=str, default=None,
                       help='设置要使用的模型路径或预定义模型名称')
    parser.add_argument('--list-models', action='store_true',
                       help='列出所有可用的预定义模型并退出')
    parser.add_argument('--show-config', action='store_true',
                       help='显示当前模型配置并退出')
    
    args = parser.parse_args()
    
    # 处理单个scenario的prompt测试模式
    if args.test_mode == 'single' and args.scenarios and len(args.scenarios) == 1 and args.prompt_variant:
        from uitars_prompt_optimizer import get_test_prompts_by_variant
        
        scenario = args.scenarios[0]
        logger.info(f"🧪 运行prompt变体测试: {args.prompt_variant} 在 {scenario}")
        
        # 创建单次测试的pipeline实例
        pipeline = ComprehensiveWebElementsPipeline(model_type=args.model_type)
        
        # 配置模型
        if args.model:
            pipeline.model_config.set_model(args.model)
        if args.quantization:
            pipeline.model_config.set_quantization(args.quantization)
        
        # 获取测试变体的prompts
        try:
            test_prompts = get_test_prompts_by_variant(args.prompt_variant)
            
            # 临时设置使用测试prompt
            original_prompts = pipeline.scenario_prompts.copy()
            pipeline.scenario_prompts.update(test_prompts)
            
            logger.info(f"✅ 已加载prompt变体: {args.prompt_variant}")
            
            # 运行单次测试
            result = pipeline.run_single_scenario_test(
                scenario=scenario,
                max_interactions=args.max_interactions,
                log_file=args.log_file
            )
            
            # 恢复原始prompts
            pipeline.scenario_prompts = original_prompts
            
            if result:
                logger.info(f"✅ 测试完成: {args.prompt_variant}/{scenario}")
                return 0
            else:
                logger.error(f"❌ 测试失败: {args.prompt_variant}/{scenario}")
                return 1
                
        except Exception as e:
            logger.error(f"❌ Prompt变体测试出错: {e}")
            return 1
    
    # 处理配置查看和模型列表请求
    from model_config_manager import ModelConfigManager
    config_manager = ModelConfigManager()
    
    if args.show_config:
        config_manager.show_current_config()
        return
    
    if args.list_models:
        config = config_manager.load_config()
        print("🔄 可用模型:")
        print(f"   当前模型: {config['model_name']} ({config['model_path']})")
        print("\n📋 预定义模型:")
        for name, path in config['alternative_models'].items():
            exists = "✅" if os.path.exists(path) else "❌"
            print(f"   {name}: {path} {exists}")
        return
    
    # 应用模型配置更改
    config_changed = False
    if args.quantization:
        print(f"🔧 设置量化类型为: {args.quantization}")
        if config_manager.set_quantization(args.quantization):
            config_changed = True
        else:
            print("❌ 量化设置失败")
            return
    
    if args.model:
        print(f"🔄 设置模型为: {args.model}")
        if config_manager.set_model(args.model):
            config_changed = True
        else:
            print("❌ 模型设置失败")
            return
    
    if config_changed:
        print("✅ 模型配置已更新")
        print("📋 当前配置:")
        config_manager.show_current_config()
        print()
    
    # 创建pipeline - 使用指定的模型类型
    print(f"🤖 初始化模型: {args.model_type}")
    pipeline = ComprehensiveWebElementsPipeline(model_type=args.model_type)
    
    # 应用参数
    if args.tests_per_variant:
        pipeline.test_configs['tests_per_variant'] = args.tests_per_variant
    
    if args.quick_test:
        pipeline.test_configs['tests_per_variant'] = 10
        args.max_variants = 3
        print("🚀 快速测试模式: 每个scenario 3个variants，每个variant 10次测试")
    
    # 处理scenarios参数
    selected_scenarios = args.scenarios
    
    try:
        results = pipeline.run_comprehensive_pipeline(
            selected_scenarios=selected_scenarios,
            max_variants_per_scenario=args.max_variants
        )
        
        print("\n🎉 Pipeline执行完成!")
        print(f"📊 结果概况:")
        overall = results.get('overall_summary', {})
        print(f"  - 总scenarios: {overall.get('total_scenarios', 0)}")
        print(f"  - 总variants: {overall.get('tested_variants', 0)}")
        
        # 🎯 分开打印坐标成功率和语义成功率
        coordinate_rate = overall.get('overall_avg_coordinate_success_rate', 0)
        semantic_rate = overall.get('overall_avg_semantic_success_rate', 0)
        comprehensive_rate = overall.get('overall_avg_success_rate', 0)
        
        print(f"\n  🎯 核心指标:")
        print(f"    📍 坐标成功率: {coordinate_rate:.1%}")
        print(f"    🧠 语义成功率: {semantic_rate:.1%}")
        print(f"    🏆 综合成功率: {comprehensive_rate:.1%}")
        
        print(f"\n  ⏱️ 总耗时: {results.get('total_duration', 0):.1f}秒")
        
    except KeyboardInterrupt:
        print("\n❌ 用户中断执行")
    except Exception as e:
        print(f"\n❌ Pipeline执行失败: {e}")
        traceback.print_exc()


def main_scrolling_test():
    """滚动测试主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Web Elements Pipeline - 滚动测试模式')
    parser.add_argument('--scenarios', nargs='+', default=None, 
                       help='要测试的scenarios列表 (如: amazon booking2)')
    parser.add_argument('--max-variants', type=int, default=3,
                       help='每个scenario的最大variants数量')
    parser.add_argument('--tests-per-view', type=int, default=5,
                       help='每个视图的测试次数')
    parser.add_argument('--viewport-height', type=int, default=1200,
                       help='视口高度')
    parser.add_argument('--scroll-step', type=int, default=600,
                       help='滚动步长')
    parser.add_argument('--max-scrolls', type=int, default=5,
                       help='最大滚动次数')
    parser.add_argument('--variant', type=str, default=None,
                       help='测试特定variant')
    parser.add_argument('--html-file', type=str, default=None,
                       help='测试特定HTML文件')
    
    args = parser.parse_args()
    
    try:
        pipeline = ComprehensiveWebElementsPipeline()
        
        print("🔄 启动滚动测试模式...")
        print(f"📱 视口配置: {args.viewport_height}px高度, {args.scroll_step}px步长")
        print(f"🎯 测试配置: 每视图{args.tests_per_view}次, 最多滚动{args.max_scrolls}次")
        
        if args.html_file:
            # 测试单个HTML文件
            print(f"📄 测试单个文件: {args.html_file}")
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
                print(f"\n🎉 测试完成!")
                print(f"   成功率: {results['success_rate']:.1%}")
                print(f"   找到目标: {results['found_target']}")
                print(f"   总耗时: {results['total_time']:.1f}秒")
            
        elif args.variant:
            # 测试特定variant
            scenario_name = args.scenarios[0] if args.scenarios else 'amazon'
            print(f"🧪 测试variant: {scenario_name}/{args.variant}")
            
            results = pipeline.test_variant_with_scrolling(
                scenario_name=scenario_name,
                variant_name=args.variant,
                num_tests=args.tests_per_view,
                viewport_height=args.viewport_height,
                scroll_step=args.scroll_step,
                max_scrolls=args.max_scrolls
            )
            
            if results:
                print(f"\n🎉 测试完成!")
                print(f"   成功率: {results['success_rate']:.1%}")
                print(f"   找到目标: {results['found_target']}")
                print(f"   总耗时: {results['total_time']:.1f}秒")
        
        else:
            # 批量测试scenarios
            test_scenarios = args.scenarios or ['amazon', 'booking2']
            all_results = {}
            
            for scenario_name in test_scenarios:
                if scenario_name not in pipeline.scenarios:
                    print(f"⚠️ 跳过未知scenario: {scenario_name}")
                    continue
                
                print(f"\n🎯 测试scenario: {scenario_name}")
                scenario_info = pipeline.scenarios[scenario_name]
                scenario_path = Path(scenario_info['path'])
                
                # 获取所有variants
                html_files = list(scenario_path.glob("*.html"))[:args.max_variants]
                
                scenario_results = {}
                for html_file in html_files:
                    variant_name = html_file.stem
                    print(f"  📝 测试variant: {variant_name}")
                    
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
                        print(f"    ✅ 成功率: {results['success_rate']:.1%}, 找到目标: {results['found_target']}")
                    else:
                        print(f"    ❌ 测试失败")
                
                all_results[scenario_name] = scenario_results
            
            # 打印汇总结果
            print(f"\n📊 滚动测试汇总:")
            for scenario_name, scenario_results in all_results.items():
                if scenario_results:
                    success_rates = [r['success_rate'] for r in scenario_results.values()]
                    avg_success_rate = sum(success_rates) / len(success_rates)
                    found_targets = sum(1 for r in scenario_results.values() if r['found_target'])
                    
                    print(f"  {scenario_name}:")
                    print(f"    平均成功率: {avg_success_rate:.1%}")
                    print(f"    找到目标: {found_targets}/{len(scenario_results)}")
    
    except KeyboardInterrupt:
        print("\n❌ 用户中断执行")
    except Exception as e:
        print(f"\n❌ 滚动测试失败: {e}")
        traceback.print_exc()


def main_amazon_real_uitars_test():
    """Amazon真实UI-TARS滚动测试主函数"""
    
    print("🚀 启动Amazon真实UI-TARS滚动测试")
    print("=" * 60)
    
    try:
        pipeline = ComprehensiveWebElementsPipeline()
        
        # 运行Amazon真实UI-TARS滚动测试
        result = pipeline.test_amazon_with_real_uitars_scrolling()
        
        if result:
            print(f"\n🎯 测试完成!")
            print(f"✅ 目标命中: {'是' if result.get('hit_rate', 0) > 0 else '否'}")
            print(f"📊 平均交互次数: {result.get('average_interactions', 0):.1f}")
            print(f"🎯 目标命中率: {result.get('hit_rate', 0):.1%}")
            print(f"� 选择率: {result.get('selection_rate', 0):.1%}")
            print(f"🧪 测试次数: {result.get('num_tests', 0)}")
        else:
            print("❌ 测试失败")
    
    except KeyboardInterrupt:
        print("\n❌ 用户中断执行")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    # 检查命令行参数
    if len(sys.argv) > 1:
        if '--amazon-real' in sys.argv:
            # Amazon真实UI-TARS测试
            main_amazon_real_uitars_test()
        elif '--scrolling' in sys.argv:
            # 移除--scrolling参数
            sys.argv.remove('--scrolling')
            main_scrolling_test()
        else:
            main()
    else:
        main()
