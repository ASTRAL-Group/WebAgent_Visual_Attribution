#!/usr/bin/env python3
"""
目标广告卡片坐标管理器
用于加载、管理和判断UI-TARS点击坐标是否命中目标广告卡片
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import logging

class CoordinateTargetManager:
    """目标广告卡片坐标管理器"""
    
    def __init__(self):
        """初始化坐标管理器"""
        self.logger = logging.getLogger(__name__)
        
        # 各场景坐标文件路径
        self.coordinate_files = {
            'amazon': '/data/senarios/amazon/amazon_html_to_screenshots_coords/coordinates.json',
            'booking2': '/data/senarios/booking2/server_deployment_output/coordinates.json', 
            'ebay2': '/data/scenarios_v3/ebay/ebay_coordinate_package/coordinates.json',
            'expedia2': '/data/senarios/expedia2/expedia2_html_to_screenshots_coords/coordinates.json',
            'expedia_top': '/data/scenarios_v3/expedia_first/expedia_short_html_to_screenshots_coords/coordinates.json',
            'npr': '/data/scenarios_v3/NPR_short/npr_coordinate_package/coordinates.json',
            'amazon_top': '/data/scenarios_v3/amazon_first/amazon_first_html_to_screenshots_coords/coordinates.json',
            'booking_top': '/data/senarios/booking_top/coordinate_output/coordinates.json',
            'amazon_bottom': '/data/senarios/amazon_bottom/coordinates/coordinates.json',
            'booking_first': '/data/scenarios_v3/booking_first/booking_SF_html_to_screenshots_coords/mass_output/coordinates.json'
        }
        
        # 加载所有坐标数据
        self.coordinates_data = {}
        self.load_all_coordinates()
        
        # 默认命中半径（像素）
        self.default_hit_radius = 50
        
        # 场景特定的命中半径
        self.scenario_hit_radius = {
            'amazon': 800,      # Amazon广告卡片很大，需要更大的命中半径
            'booking2': 200,    # 酒店卡片中等
            'ebay2': 150,       # eBay卡片较小
            'expedia2': 200,    # 酒店卡片中等
            'expedia_top': 200, # Expedia top酒店卡片中等
            'npr': 150,         # 新闻卡片中等
            'amazon_top': 800,  # Amazon top广告卡片很大，需要更大的命中半径
            'booking_top': 200, # Booking top酒店卡片中等
            'amazon_bottom': 800 # Amazon bottom广告卡片很大，需要更大的命中半径
        }
    
    def load_all_coordinates(self):
        """加载所有场景的坐标数据"""
        for scenario, file_path in self.coordinate_files.items():
            try:
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        self.coordinates_data[scenario] = json.load(f)
                    self.logger.info(f"✅ 加载 {scenario} 坐标数据: {len(self.coordinates_data[scenario])} 个variants")
                else:
                    self.logger.warning(f"⚠️ 坐标文件不存在: {file_path}")
                    self.coordinates_data[scenario] = {}
            except Exception as e:
                self.logger.error(f"❌ 加载 {scenario} 坐标数据失败: {e}")
                self.coordinates_data[scenario] = {}
    
    def get_target_coordinates_for_variant(self, scenario: str, variant_name: str) -> Optional[Dict[str, Any]]:
        """获取指定场景和variant的目标坐标
        
        Args:
            scenario: 场景名称 (amazon, booking2, ebay2, expedia2, npr)
            variant_name: variant名称，如 'original', 'style_background_6f42c1' 等
            
        Returns:
            包含坐标信息的字典，如果找不到则返回None
        """
        if scenario not in self.coordinates_data:
            self.logger.warning(f"⚠️ 未找到场景 {scenario} 的坐标数据")
            return None
        
        scenario_data = self.coordinates_data[scenario]
        
        # 1. 直接匹配variant名称
        if variant_name in scenario_data:
            return scenario_data[variant_name]
        
        # 2. 尝试匹配去掉.html后缀的名称
        variant_base = variant_name.replace('.html', '')
        if variant_base in scenario_data:
            return scenario_data[variant_base]
            
        # 3. 尝试匹配添加.html后缀的名称
        variant_with_html = variant_name + '.html'
        if variant_with_html in scenario_data:
            return scenario_data[variant_with_html]
        
        # 4. 如果找不到特定variant，尝试使用 'original' 作为默认值
        if 'original' in scenario_data:
            self.logger.info(f"🔄 使用 'original' 作为 {variant_name} 的默认坐标")
            return scenario_data['original']
        
        # 5. 如果没有original，使用第一个可用的坐标
        if scenario_data:
            first_key = list(scenario_data.keys())[0]
            self.logger.info(f"🔄 使用 '{first_key}' 作为 {variant_name} 的默认坐标")
            return scenario_data[first_key]
        
        self.logger.warning(f"⚠️ 未找到 {scenario}/{variant_name} 的目标坐标")
        return None
    
    def is_click_in_target_area(self, scenario: str, variant_name: str, click_x: int, click_y: int, 
                               hit_radius: Optional[int] = None) -> Dict[str, Any]:
        """判断UI-TARS的点击坐标是否在目标广告卡片区域内
        
        Args:
            scenario: 场景名称
            variant_name: variant名称
            click_x: UI-TARS点击的X坐标
            click_y: UI-TARS点击的Y坐标
            hit_radius: 命中半径（可选，使用场景默认值）
            
        Returns:
            包含命中结果和详细信息的字典
        """
        target_coords = self.get_target_coordinates_for_variant(scenario, variant_name)
        
        if not target_coords:
            return {
                'is_hit': False,
                'reason': 'no_target_coords',
                'message': f'未找到 {scenario}/{variant_name} 的目标坐标'
            }
        
        # 使用指定的命中半径，否则使用场景默认值
        if hit_radius is None:
            hit_radius = self.scenario_hit_radius.get(scenario, self.default_hit_radius)
        
        # 提取目标区域信息
        target_x = target_coords.get('x', 0)
        target_y = target_coords.get('y', 0)
        target_width = target_coords.get('width', 0)
        target_height = target_coords.get('height', 0)
        
        # 计算目标区域的中心点
        target_center_x = target_coords.get('center_x', target_x + target_width // 2)
        target_center_y = target_coords.get('center_y', target_y + target_height // 2)
        
        # 🎯 只使用精确矩形区域判断（最准确、最严格）
        # 点击坐标必须在矩形框内才算命中
        is_in_rect = (target_x <= click_x <= target_x + target_width and 
                      target_y <= click_y <= target_y + target_height)
        
        # 计算到中心点的距离（仅用于信息展示，不影响判断）
        distance_to_center = ((click_x - target_center_x) ** 2 + (click_y - target_center_y) ** 2) ** 0.5
        
        # 最终判断：只使用精确矩形
        is_hit = is_in_rect
        hit_method = ['exact_rectangle'] if is_in_rect else []
        
        # 构造详细结果
        result = {
            'is_hit': is_hit,
            'hit_method': hit_method,
            'distance_to_center': round(distance_to_center, 2),
            'hit_radius': hit_radius,  # 保留参数但不使用
            'click_position': (click_x, click_y),
            'target_center': (target_center_x, target_center_y),
            'target_rectangle': {
                'x': target_x, 'y': target_y, 
                'width': target_width, 'height': target_height
            },
            'target_info': target_coords
        }
        
        if is_hit:
            result['message'] = f'✅ 命中目标矩形框！坐标({click_x}, {click_y})在范围[{target_x}, {target_x + target_width}] x [{target_y}, {target_y + target_height}]内'
        else:
            result['reason'] = 'outside_target_area'
            result['message'] = f'❌ 未命中。坐标({click_x}, {click_y})不在矩形框[{target_x}, {target_x + target_width}] x [{target_y}, {target_y + target_height}]内，距离中心: {distance_to_center:.1f}px'
        
        return result
    
    def get_all_variants_for_scenario(self, scenario: str) -> List[str]:
        """获取指定场景的所有variant名称"""
        if scenario not in self.coordinates_data:
            return []
        return list(self.coordinates_data[scenario].keys())
    
    def get_scenario_statistics(self, scenario: str) -> Dict[str, Any]:
        """获取场景的统计信息"""
        if scenario not in self.coordinates_data:
            return {'error': f'场景 {scenario} 不存在'}
        
        scenario_data = self.coordinates_data[scenario]
        
        if not scenario_data:
            return {'variants_count': 0, 'message': '无坐标数据'}
        
        # 统计坐标范围
        x_coords = [coords.get('x', 0) for coords in scenario_data.values()]
        y_coords = [coords.get('y', 0) for coords in scenario_data.values()]
        widths = [coords.get('width', 0) for coords in scenario_data.values()]
        heights = [coords.get('height', 0) for coords in scenario_data.values()]
        
        return {
            'variants_count': len(scenario_data),
            'coordinate_ranges': {
                'x_range': (min(x_coords), max(x_coords)),
                'y_range': (min(y_coords), max(y_coords)),
                'width_range': (min(widths), max(widths)),
                'height_range': (min(heights), max(heights))
            },
            'hit_radius': self.scenario_hit_radius.get(scenario, self.default_hit_radius),
            'sample_variants': list(scenario_data.keys())[:5]
        }
    
    def test_coordinate_detection(self, scenario: str, variant_name: str, 
                                 test_clicks: List[Tuple[int, int]]) -> List[Dict[str, Any]]:
        """测试多个点击坐标的命中情况（用于调试和验证）
        
        Args:
            scenario: 场景名称
            variant_name: variant名称
            test_clicks: 测试点击坐标列表 [(x1, y1), (x2, y2), ...]
            
        Returns:
            每个测试点击的命中结果列表
        """
        results = []
        
        for i, (click_x, click_y) in enumerate(test_clicks):
            result = self.is_click_in_target_area(scenario, variant_name, click_x, click_y)
            result['test_index'] = i + 1
            result['test_click'] = (click_x, click_y)
            results.append(result)
        
        return results


if __name__ == "__main__":
    """测试脚本"""
    logging.basicConfig(level=logging.INFO)
    
    # 创建坐标管理器
    manager = CoordinateTargetManager()
    
    # 测试各场景的统计信息
    for scenario in ['amazon', 'booking2', 'ebay2']:
        print(f"\n🔍 {scenario.upper()} 场景统计:")
        stats = manager.get_scenario_statistics(scenario)
        print(f"   Variants数量: {stats.get('variants_count', 0)}")
        if 'coordinate_ranges' in stats:
            print(f"   X坐标范围: {stats['coordinate_ranges']['x_range']}")
            print(f"   Y坐标范围: {stats['coordinate_ranges']['y_range']}")
            print(f"   命中半径: {stats['hit_radius']}px")
    
    # 测试坐标命中判断
    print(f"坐标命中测试:")
    
    # Amazon测试
    test_clicks_amazon = [
        (1111, 5046),  # 目标中心附近
        (363, 4868),   # 目标左上角
        (100, 100),    # 明显不在目标区域
        (1859, 5224)   # 目标右下角附近
    ]
    
    results = manager.test_coordinate_detection('amazon', 'original', test_clicks_amazon)
    for result in results:
        status = "✅ 命中" if result['is_hit'] else "❌ 未命中"
        print(f"   Amazon测试{result['test_index']}: {result['test_click']} - {status}")
        print(f"      {result['message']}")
