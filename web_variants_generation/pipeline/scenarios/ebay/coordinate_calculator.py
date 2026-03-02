#!/usr/bin/env python3
"""
eBay 坐标计算脚本
专门负责从HTML中提取第一个商品卡片的坐标

功能：
1. 使用Playwright从HTML中提取第一个商品卡片的精确坐标
2. 支持多种检测方法（DOM结构分析、选择器匹配等）
3. 生成统一的坐标JSON文件

输出：
- coordinates.json : 所有变体的坐标数据
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

class EbayCoordinateCalculator:
    """eBay 坐标计算器"""
    
    def __init__(self, input_dir: str, output_dir: str):
        """
        初始化坐标计算器
        
        Args:
            input_dir: HTML变体文件目录
            output_dir: 输出目录
        """
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        
        # 设置日志
        self.setup_logging()
        
        print(f"🚀 eBay 坐标计算器已初始化")
        print(f"📁 输入目录: {self.input_dir}")
        print(f"📁 输出目录: {self.output_dir}")
        print(f"🎯 目标: 目标商品卡片 (li.s-card[data-target-card=\"true\"])")
    
    def setup_logging(self):
        """设置日志"""
        log_file = self.output_dir / f"coordinate_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"📝 日志文件: {log_file}")
    
    def extract_html_coordinates(self, html_file_path: str) -> Optional[Dict]:
        """
        从HTML中提取第一个商品卡片的精确坐标（使用Playwright确保与截图一致）
        """
        self.logger.info(f"🔍 从HTML提取坐标: {Path(html_file_path).name}")
        
        try:
            from playwright.sync_api import sync_playwright
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                
                # 加载HTML文件
                page.goto(f"file://{Path(html_file_path).absolute()}")
                
                # 等待页面加载
                page.wait_for_load_state('networkidle')
                time.sleep(2)
                
                # 获取页面完整尺寸（与截图生成保持一致）
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
                
                # 确保最小尺寸
                page_size["width"] = max(page_size["width"], 1280)
                page_size["height"] = max(page_size["height"], 1200)
                
                # 设置视口为页面完整尺寸
                page.set_viewport_size({
                    "width": page_size["width"], 
                    "height": page_size["height"]
                })
                
                # 等待重新渲染
                time.sleep(2)
                
                # 尝试多种方法查找目标商品卡片
                coordinates = None
                detection_method = "unknown"
                matched_text = ""
                
                # 方法1: 查找带有 data-target-card="true" 的目标商品（最高优先级）
                try:
                    target_card = page.locator('li.s-card[data-target-card="true"]').first
                    if target_card.count() > 0:
                        bbox = target_card.bounding_box()
                        if bbox and bbox['width'] > 100 and bbox['height'] > 50:
                            # 获取商品标题作为匹配文本
                            try:
                                title_element = target_card.locator('.s-card__title, .su-styled-text').first
                                matched_text = title_element.text_content()[:100] if title_element.count() > 0 else "Target Product Card"
                            except:
                                matched_text = "Target Product Card"
                            
                            coordinates = {
                                'x': int(bbox['x']),
                                'y': int(bbox['y']),
                                'width': int(bbox['width']),
                                'height': int(bbox['height']),
                                'center_x': int(bbox['x'] + bbox['width'] / 2),
                                'center_y': int(bbox['y'] + bbox['height'] / 2),
                                'confidence': 98,
                                'detection_method': 'target_card_selector',
                                'matched_text': matched_text
                            }
                            detection_method = "target_card_selector"
                except Exception as e:
                    self.logger.warning(f"目标卡片选择器查找失败: {e}")
                
                # 方法2: 直接查找第一个 li.s-card（作为后备方法）
                if not coordinates:
                    try:
                        first_card = page.locator('ul.srp-results li.s-card').first
                        if first_card.count() > 0:
                            bbox = first_card.bounding_box()
                            if bbox and bbox['width'] > 100 and bbox['height'] > 50:
                                # 获取商品标题作为匹配文本
                                try:
                                    title_element = first_card.locator('.s-card__title, .su-styled-text').first
                                    matched_text = title_element.text_content()[:100] if title_element.count() > 0 else "First Product Card"
                                except:
                                    matched_text = "First Product Card"
                                
                                coordinates = {
                                    'x': int(bbox['x']),
                                    'y': int(bbox['y']),
                                    'width': int(bbox['width']),
                                    'height': int(bbox['height']),
                                    'center_x': int(bbox['x'] + bbox['width'] / 2),
                                    'center_y': int(bbox['y'] + bbox['height'] / 2),
                                    'confidence': 95,
                                    'detection_method': 'first_card_selector',
                                    'matched_text': matched_text
                                }
                                detection_method = "first_card_selector"
                    except Exception as e:
                        self.logger.warning(f"第一个卡片选择器查找失败: {e}")
                
                # 方法3: 查找包含 data-listingid 的第一个元素
                if not coordinates:
                    try:
                        first_listing = page.locator('li.s-card[data-listingid]').first
                        if first_listing.count() > 0:
                            bbox = first_listing.bounding_box()
                            if bbox and bbox['width'] > 100 and bbox['height'] > 50:
                                try:
                                    title_element = first_listing.locator('.s-card__title, .su-styled-text').first
                                    matched_text = title_element.text_content()[:100] if title_element.count() > 0 else "First Listing Card"
                                except:
                                    matched_text = "First Listing Card"
                                
                                coordinates = {
                                    'x': int(bbox['x']),
                                    'y': int(bbox['y']),
                                    'width': int(bbox['width']),
                                    'height': int(bbox['height']),
                                    'center_x': int(bbox['x'] + bbox['width'] / 2),
                                    'center_y': int(bbox['y'] + bbox['height'] / 2),
                                    'confidence': 90,
                                    'detection_method': 'first_listing_selector',
                                    'matched_text': matched_text
                                }
                                detection_method = "first_listing_selector"
                    except Exception as e:
                        self.logger.warning(f"第一个listing选择器查找失败: {e}")
                
                # 方法4: 查找 ul.srp-results 的第一个子元素
                if not coordinates:
                    try:
                        srp_results = page.locator('ul.srp-results').first
                        if srp_results.count() > 0:
                            first_child = srp_results.locator('> li').first
                            if first_child.count() > 0:
                                bbox = first_child.bounding_box()
                                if bbox and bbox['width'] > 100 and bbox['height'] > 50:
                                    try:
                                        title_element = first_child.locator('.s-card__title, .su-styled-text').first
                                        matched_text = title_element.text_content()[:100] if title_element.count() > 0 else "First Result Item"
                                    except:
                                        matched_text = "First Result Item"
                                    
                                    coordinates = {
                                        'x': int(bbox['x']),
                                        'y': int(bbox['y']),
                                        'width': int(bbox['width']),
                                        'height': int(bbox['height']),
                                        'center_x': int(bbox['x'] + bbox['width'] / 2),
                                        'center_y': int(bbox['y'] + bbox['height'] / 2),
                                        'confidence': 85,
                                        'detection_method': 'first_result_item',
                                        'matched_text': matched_text
                                    }
                                    detection_method = "first_result_item"
                    except Exception as e:
                        self.logger.warning(f"第一个结果项查找失败: {e}")
                
                # 方法5: 查找包含商品图片的第一个元素
                if not coordinates:
                    try:
                        # 查找包含商品图片的卡片
                        cards_with_images = page.locator('li.s-card:has(img.s-card__image)').first
                        if cards_with_images.count() > 0:
                            bbox = cards_with_images.bounding_box()
                            if bbox and bbox['width'] > 100 and bbox['height'] > 50:
                                try:
                                    title_element = cards_with_images.locator('.s-card__title, .su-styled-text').first
                                    matched_text = title_element.text_content()[:100] if title_element.count() > 0 else "Card with Image"
                                except:
                                    matched_text = "Card with Image"
                                
                                coordinates = {
                                    'x': int(bbox['x']),
                                    'y': int(bbox['y']),
                                    'width': int(bbox['width']),
                                    'height': int(bbox['height']),
                                    'center_x': int(bbox['x'] + bbox['width'] / 2),
                                    'center_y': int(bbox['y'] + bbox['height'] / 2),
                                    'confidence': 80,
                                    'detection_method': 'card_with_image',
                                    'matched_text': matched_text
                                }
                                detection_method = "card_with_image"
                    except Exception as e:
                        self.logger.warning(f"带图片的卡片查找失败: {e}")
                
                browser.close()
                
                if coordinates:
                    self.logger.info(f"✅ 坐标提取成功:")
                    self.logger.info(f"   位置: ({coordinates['x']}, {coordinates['y']})")
                    self.logger.info(f"   大小: {coordinates['width']}x{coordinates['height']}")
                    self.logger.info(f"   中心: ({coordinates['center_x']}, {coordinates['center_y']})")
                    self.logger.info(f"   置信度: {coordinates['confidence']}%")
                    self.logger.info(f"   检测方法: {coordinates['detection_method']}")
                    return coordinates
                else:
                    self.logger.warning(f"⚠️ 未找到第一个商品卡片")
                    return None
                    
        except ImportError:
            self.logger.error("❌ Playwright未安装，请运行: pip install playwright")
            return None
        except Exception as e:
            self.logger.error(f"❌ 坐标提取失败: {e}")
            return None
    
    def process_single_html(self, html_file_path: str) -> Optional[Dict]:
        """
        处理单个HTML文件提取坐标
        
        Args:
            html_file_path: HTML文件路径
            
        Returns:
            Dict: 处理结果或None
        """
        html_path = Path(html_file_path)
        variant_name = html_path.stem
        
        self.logger.info(f"\n🔄 处理变体: {variant_name}")
        
        try:
            # 提取坐标
            coordinates = self.extract_html_coordinates(html_file_path)
            if not coordinates:
                return None
            
            # 返回处理结果
            result = {
                'variant_name': variant_name,
                'html_file': str(html_path),
                'coordinates': coordinates,
                'processing_time': datetime.now().isoformat(),
                'status': 'success'
            }
            
            self.logger.info(f"✅ {variant_name} 处理完成")
            return result
            
        except Exception as e:
            self.logger.error(f"❌ {variant_name} 处理失败: {e}")
            return None
    
    def discover_html_files(self) -> List[Path]:
        """
        发现所有HTML变体文件
        
        Returns:
            List[Path]: HTML文件路径列表
        """
        html_files = list(self.input_dir.glob("*.html"))
        
        self.logger.info(f"🔍 发现 {len(html_files)} 个HTML变体文件:")
        for html_file in html_files:
            self.logger.info(f"   - {html_file.name}")
        
        return html_files
    
    def calculate_all_coordinates(self) -> Dict[str, Any]:
        """
        为所有HTML变体计算坐标
        
        Returns:
            Dict: 处理结果汇总
        """
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"🚀 开始计算所有eBay HTML变体坐标")
        self.logger.info(f"{'='*60}")
        
        start_time = time.time()
        
        # 发现HTML文件
        html_files = self.discover_html_files()
        if not html_files:
            self.logger.error("❌ 未找到HTML变体文件")
            return {}
        
        # 处理结果
        results = {
            'processing_info': {
                'input_dir': str(self.input_dir),
                'output_dir': str(self.output_dir),
                'target': 'first_product_card',
                'total_files': len(html_files),
                'start_time': datetime.now().isoformat()
            },
            'variants': {},
            'summary': {
                'total_processed': 0,
                'successful': 0,
                'failed': 0,
                'success_rate': 0.0
            }
        }
        
        # 逐个处理HTML文件
        for html_file in tqdm(html_files, desc="计算坐标"):
            result = self.process_single_html(html_file)
            
            if result:
                results['variants'][result['variant_name']] = result
                results['summary']['successful'] += 1
            else:
                results['summary']['failed'] += 1
            
            results['summary']['total_processed'] += 1
        
        # 计算成功率
        if results['summary']['total_processed'] > 0:
            results['summary']['success_rate'] = (
                results['summary']['successful'] / results['summary']['total_processed']
            )
        
        # 添加处理时间
        results['processing_info']['end_time'] = datetime.now().isoformat()
        results['processing_info']['duration'] = time.time() - start_time
        
        # 保存结果
        self.save_results(results)
        
        # 生成汇总报告
        self.generate_summary_report(results)
        
        return results
    
    def save_results(self, results: Dict[str, Any]):
        """保存处理结果"""
        # 保存详细结果
        results_file = self.output_dir / "coordinate_results.json"
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        # 保存坐标数据（简化版，供pipeline使用）
        coordinates_data = {}
        for variant_name, variant_data in results['variants'].items():
            coordinates_data[variant_name] = {
                'x': variant_data['coordinates']['x'],
                'y': variant_data['coordinates']['y'],
                'width': variant_data['coordinates']['width'],
                'height': variant_data['coordinates']['height'],
                'center_x': variant_data['coordinates']['center_x'],
                'center_y': variant_data['coordinates']['center_y'],
                'confidence': variant_data['coordinates']['confidence'],
                'detection_method': variant_data['coordinates']['detection_method'],
                'matched_text': variant_data['coordinates']['matched_text']
            }
        
        coordinates_file = self.output_dir / "coordinates.json"
        with open(coordinates_file, 'w', encoding='utf-8') as f:
            json.dump(coordinates_data, f, ensure_ascii=False, indent=2)
        
        self.logger.info(f"💾 结果已保存:")
        self.logger.info(f"   📄 详细结果: {results_file}")
        self.logger.info(f"   📄 坐标数据: {coordinates_file}")
    
    def generate_summary_report(self, results: Dict[str, Any]):
        """生成汇总报告"""
        report_file = self.output_dir / "coordinate_summary.md"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("# eBay 坐标计算报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # 处理概况
            summary = results['summary']
            f.write("## 处理概况\n\n")
            f.write(f"- **总文件数**: {summary['total_processed']}\n")
            f.write(f"- **成功计算**: {summary['successful']}\n")
            f.write(f"- **计算失败**: {summary['failed']}\n")
            f.write(f"- **成功率**: {summary['success_rate']:.1%}\n")
            f.write(f"- **处理耗时**: {results['processing_info']['duration']:.1f}秒\n\n")
            
            # 目标信息
            f.write("## 目标信息\n\n")
            f.write(f"- **目标**: {results['processing_info']['target']}\n")
            f.write(f"- **输入目录**: {results['processing_info']['input_dir']}\n")
            f.write(f"- **输出目录**: {results['processing_info']['output_dir']}\n\n")
            
            # 成功处理的变体
            f.write("## 成功处理的变体\n\n")
            for variant_name, variant_data in results['variants'].items():
                coords = variant_data['coordinates']
                f.write(f"### {variant_name}\n")
                f.write(f"- **位置**: ({coords['x']}, {coords['y']})\n")
                f.write(f"- **大小**: {coords['width']}x{coords['height']}\n")
                f.write(f"- **中心**: ({coords['center_x']}, {coords['center_y']})\n")
                f.write(f"- **置信度**: {coords['confidence']}%\n")
                f.write(f"- **检测方法**: {coords['detection_method']}\n")
                f.write(f"- **匹配文本**: {coords['matched_text'][:100]}...\n\n")
        
        self.logger.info(f"📄 汇总报告已生成: {report_file}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='eBay 坐标计算脚本')
    parser.add_argument('--input-dir', type=str, required=True, help='HTML variant directory')
    parser.add_argument('--output-dir', type=str, required=True, help='Output directory')
    
    args = parser.parse_args()
    
    # 检查输入目录
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"❌ 输入目录不存在: {input_dir}")
        return
    
    # 创建坐标计算器
    calculator = EbayCoordinateCalculator(args.input_dir, args.output_dir)
    
    # 计算所有坐标
    try:
        results = calculator.calculate_all_coordinates()
        
        print(f"\n🎉 坐标计算完成!")
        print(f"📊 处理结果:")
        print(f"   - 总文件数: {results['summary']['total_processed']}")
        print(f"   - 成功计算: {results['summary']['successful']}")
        print(f"   - 计算失败: {results['summary']['failed']}")
        print(f"   - 成功率: {results['summary']['success_rate']:.1%}")
        print(f"   - 处理耗时: {results['processing_info']['duration']:.1f}秒")
        
    except KeyboardInterrupt:
        print("\n❌ 用户中断处理")
    except Exception as e:
        print(f"\n❌ 处理失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()


