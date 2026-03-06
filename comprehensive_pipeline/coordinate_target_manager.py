#!/usr/bin/env python3


import json
import os
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import logging

class CoordinateTargetManager:
   
    
    def __init__(self):
        
        self.logger = logging.getLogger(__name__)
        
        
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
        
        
        self.coordinates_data = {}
        self.load_all_coordinates()
        
       
        self.default_hit_radius = 50
        
        
        self.scenario_hit_radius = {
            'amazon': 800,      
            'booking2': 200,    
            'ebay2': 150,       
            'expedia2': 200,    
            'expedia_top': 200, 
            'npr': 150,         
            'amazon_top': 800,  
            'booking_top': 200, 
            'amazon_bottom': 800 
        }
    
    def load_all_coordinates(self):
        
        for scenario, file_path in self.coordinate_files.items():
            try:
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        self.coordinates_data[scenario] = json.load(f)
                    self.logger.info(f"✅ Scenario Coordinate: {len(self.coordinates_data[scenario])} variants")
                else:
                    self.logger.warning(f"⚠️ File not exist: {file_path}")
                    self.coordinates_data[scenario] = {}
            except Exception as e:
                self.logger.error(f"❌ Loading {scenario} Failure: {e}")
                self.coordinates_data[scenario] = {}
    
    def get_target_coordinates_for_variant(self, scenario: str, variant_name: str) -> Optional[Dict[str, Any]]:
        
        if scenario not in self.coordinates_data:
            self.logger.warning(f"⚠️ Cannot find {scenario} coordinate data")
            return None
        
        scenario_data = self.coordinates_data[scenario]
        
       
        if variant_name in scenario_data:
            return scenario_data[variant_name]
        
        
        variant_base = variant_name.replace('.html', '')
        if variant_base in scenario_data:
            return scenario_data[variant_base]
            
        
        variant_with_html = variant_name + '.html'
        if variant_with_html in scenario_data:
            return scenario_data[variant_with_html]
        
        
        if 'original' in scenario_data:
            self.logger.info(f"🔄 Use 'original' as {variant_name} config coordinate")
            return scenario_data['original']
        
        
        if scenario_data:
            first_key = list(scenario_data.keys())[0]
            self.logger.info(f"🔄 Use '{first_key}' as {variant_name} config coordinate")
            return scenario_data[first_key]
        
        self.logger.warning(f"⚠️ Cannot find {scenario}/{variant_name} target")
        return None
    
    def is_click_in_target_area(self, scenario: str, variant_name: str, click_x: int, click_y: int, 
                               hit_radius: Optional[int] = None) -> Dict[str, Any]:
       
        target_coords = self.get_target_coordinates_for_variant(scenario, variant_name)
        
        if not target_coords:
            return {
                'is_hit': False,
                'reason': 'no_target_coords',
                'message': f'未找到 {scenario}/{variant_name} 的目标坐标'
            }
        
        
        if hit_radius is None:
            hit_radius = self.scenario_hit_radius.get(scenario, self.default_hit_radius)
        
        
        target_x = target_coords.get('x', 0)
        target_y = target_coords.get('y', 0)
        target_width = target_coords.get('width', 0)
        target_height = target_coords.get('height', 0)
        
        
        target_center_x = target_coords.get('center_x', target_x + target_width // 2)
        target_center_y = target_coords.get('center_y', target_y + target_height // 2)
        
        
        is_in_rect = (target_x <= click_x <= target_x + target_width and 
                      target_y <= click_y <= target_y + target_height)
        
        
        distance_to_center = ((click_x - target_center_x) ** 2 + (click_y - target_center_y) ** 2) ** 0.5
        
        
        is_hit = is_in_rect
        hit_method = ['exact_rectangle'] if is_in_rect else []
        
        
        result = {
            'is_hit': is_hit,
            'hit_method': hit_method,
            'distance_to_center': round(distance_to_center, 2),
            'hit_radius': hit_radius,  
            'click_position': (click_x, click_y),
            'target_center': (target_center_x, target_center_y),
            'target_rectangle': {
                'x': target_x, 'y': target_y, 
                'width': target_width, 'height': target_height
            },
            'target_info': target_coords
        }
        
        if is_hit:
            result['message'] = f'✅ Shoot! Coordinate ({click_x}, {click_y})in range[{target_x}, {target_x + target_width}] x [{target_y}, {target_y + target_height}]'
        else:
            result['reason'] = 'outside_target_area'
            result['message'] = f'❌ Failure. Coordinate ({click_x}, {click_y})not in[{target_x}, {target_x + target_width}] x [{target_y}, {target_y + target_height}]，Distance: {distance_to_center:.1f}px'
        
        return result
    
    def get_all_variants_for_scenario(self, scenario: str) -> List[str]:
        
        if scenario not in self.coordinates_data:
            return []
        return list(self.coordinates_data[scenario].keys())
    
    def get_scenario_statistics(self, scenario: str) -> Dict[str, Any]:
        
        if scenario not in self.coordinates_data:
            return {'error': f' {scenario} not exist'}
        
        scenario_data = self.coordinates_data[scenario]
        
        if not scenario_data:
            return {'variants_count': 0, 'message': 'no coordinates data'}
        
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
       
        results = []
        
        for i, (click_x, click_y) in enumerate(test_clicks):
            result = self.is_click_in_target_area(scenario, variant_name, click_x, click_y)
            result['test_index'] = i + 1
            result['test_click'] = (click_x, click_y)
            results.append(result)
        
        return results


if __name__ == "__main__":
    
    logging.basicConfig(level=logging.INFO)
    

    manager = CoordinateTargetManager()
    
    
    for scenario in ['amazon', 'booking2', 'ebay2']:
        print(f"\n🔍 {scenario.upper()} :")
        stats = manager.get_scenario_statistics(scenario)
        print(f"   Variants num: {stats.get('variants_count', 0)}")
        if 'coordinate_ranges' in stats:
            print(f"   X range: {stats['coordinate_ranges']['x_range']}")
            print(f"   Y range: {stats['coordinate_ranges']['y_range']}")
            print(f"   Hit Radius: {stats['hit_radius']}px")
    

    print(f"Coordinate Success test:")
    
  
    test_clicks_amazon = [
        (1111, 5046),  
        (363, 4868),   
        (100, 100),    
        (1859, 5224)   
    ]
    
    results = manager.test_coordinate_detection('amazon', 'original', test_clicks_amazon)
    for result in results:
        status = "✅ Hit" if result['is_hit'] else "❌ Not Hit"
        print(f"   Amazon测试{result['test_index']}: {result['test_click']} - {status}")
        print(f"      {result['message']}")
