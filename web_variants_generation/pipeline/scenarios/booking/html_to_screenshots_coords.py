#!/usr/bin/env python3
"""
Booking SF HTML变体处理脚本
将HTML变体转换为截图并提取坐标

功能：
1. 使用Playwright将HTML文件转换为原比例PNG截图
2. 使用HTML DOM提取目标卡片坐标
3. 生成统一的坐标JSON文件
4. 为后续pipeline提供预处理好的数据

输出：
- screenshots/ : 原比例PNG截图文件
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

# 直接导入Playwright坐标提取函数
from test_variants_simple import extract_html_coordinates

class BookingSFHTMLProcessor:
    """Booking SF HTML变体处理器"""
    
    def __init__(self, input_dir: str, output_dir: str):
        """
        初始化处理器
        
        Args:
            input_dir: HTML变体文件目录
            output_dir: 输出目录
        """
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        
        # 创建输出目录
        self.screenshots_dir = self.output_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        # 目标文本
        self.target_text = "Holiday Inn San Francisco - Golden Gateway"
        
        # 设置日志
        self.setup_logging()
        
        print(f"🚀 Booking SF HTML处理器已初始化")
        print(f"📁 输入目录: {self.input_dir}")
        print(f"📁 输出目录: {self.output_dir}")
        print(f"📁 截图目录: {self.screenshots_dir}")
        print(f"🎯 目标文本: {self.target_text}")
    
    def setup_logging(self):
        """设置日志"""
        log_file = self.output_dir / f"processing_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
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
    
    def generate_screenshot_playwright(self, html_file_path: str, output_path: str) -> bool:
        """
        使用Playwright生成原比例截图
        
        Args:
            html_file_path: HTML文件路径
            output_path: 输出截图路径
            
        Returns:
            bool: 是否成功生成截图
        """
        try:
            from playwright.sync_api import sync_playwright
            
            html_path = Path(html_file_path)
            screenshot_path = Path(output_path)
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                
                # 加载HTML文件
                page.goto(f"file://{html_path.absolute()}")
                
                # 等待页面加载完成
                page.wait_for_load_state('networkidle')
                time.sleep(2)  # 额外等待确保渲染完成
                
                # 获取页面完整尺寸
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
                page_size["width"] = max(page_size["width"], 1200)
                page_size["height"] = max(page_size["height"], 800)
                
                # 设置视口为页面完整尺寸
                page.set_viewport_size({
                    "width": page_size["width"], 
                    "height": page_size["height"]
                })
                
                # 等待重新渲染
                time.sleep(2)
                
                # 生成完整页面截图
                page.screenshot(
                    path=str(screenshot_path),
                    full_page=True,
                    type='png'
                )
                
                browser.close()
            
            # 验证截图是否生成成功
            if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
                self.logger.info(f"✅ 截图生成成功: {screenshot_path}")
                self.logger.info(f"📏 页面尺寸: {page_size['width']}x{page_size['height']}")
                return True
            else:
                self.logger.error(f"❌ 截图生成失败: {screenshot_path}")
                return False
                
        except ImportError:
            self.logger.error("❌ Playwright未安装，请运行: pip install playwright")
            return False
        except Exception as e:
            self.logger.error(f"❌ Playwright截图生成失败: {e}")
            return False
    
    def extract_coordinates_for_html(self, html_file_path: str) -> Optional[Dict]:
        """
        为HTML文件提取坐标
        
        Args:
            html_file_path: HTML文件路径
            
        Returns:
            Dict: 坐标数据或None
        """
        try:
            self.logger.info(f"🔍 提取坐标: {Path(html_file_path).name}")
            
            # 使用现有的HTML坐标提取函数
            coordinates = extract_html_coordinates(html_file_path, self.target_text)
            
            if coordinates:
                self.logger.info(f"✅ 坐标提取成功:")
                self.logger.info(f"   位置: ({coordinates['x']}, {coordinates['y']})")
                self.logger.info(f"   大小: {coordinates['width']}x{coordinates['height']}")
                self.logger.info(f"   中心: ({coordinates['center_x']}, {coordinates['center_y']})")
                self.logger.info(f"   置信度: {coordinates['confidence']}%")
                return coordinates
            else:
                self.logger.warning(f"⚠️ 未找到目标元素: {self.target_text}")
                return None
                
        except Exception as e:
            self.logger.error(f"❌ 坐标提取失败: {e}")
            return None
    
    def process_single_html(self, html_file_path: str) -> Optional[Dict]:
        """
        处理单个HTML文件
        
        Args:
            html_file_path: HTML文件路径
            
        Returns:
            Dict: 处理结果或None
        """
        html_path = Path(html_file_path)
        variant_name = html_path.stem
        
        self.logger.info(f"\n🔄 处理变体: {variant_name}")
        
        try:
            # 1. 生成截图
            screenshot_path = self.screenshots_dir / f"{variant_name}.png"
            
            if screenshot_path.exists():
                self.logger.info(f"⏭️ 截图已存在，跳过: {screenshot_path}")
            else:
                if not self.generate_screenshot_playwright(html_file_path, str(screenshot_path)):
                    return None
            
            # 2. 提取坐标
            coordinates = self.extract_coordinates_for_html(html_file_path)
            if not coordinates:
                return None
            
            # 3. 返回处理结果
            result = {
                'variant_name': variant_name,
                'html_file': str(html_path),
                'screenshot_file': str(screenshot_path),
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
    
    def process_all_variants(self) -> Dict[str, Any]:
        """
        处理所有HTML变体
        
        Returns:
            Dict: 处理结果汇总
        """
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"🚀 开始处理所有Booking2 HTML变体")
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
                'screenshots_dir': str(self.screenshots_dir),
                'target_text': self.target_text,
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
        for html_file in tqdm(html_files, desc="处理HTML变体"):
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
        results_file = self.output_dir / "processing_results.json"
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
        report_file = self.output_dir / "processing_summary.md"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("# Booking2 HTML变体处理报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # 处理概况
            summary = results['summary']
            f.write("## 处理概况\n\n")
            f.write(f"- **总文件数**: {summary['total_processed']}\n")
            f.write(f"- **成功处理**: {summary['successful']}\n")
            f.write(f"- **处理失败**: {summary['failed']}\n")
            f.write(f"- **成功率**: {summary['success_rate']:.1%}\n")
            f.write(f"- **处理耗时**: {results['processing_info']['duration']:.1f}秒\n\n")
            
            # 目标信息
            f.write("## 目标信息\n\n")
            f.write(f"- **目标文本**: {results['processing_info']['target_text']}\n")
            f.write(f"- **输入目录**: {results['processing_info']['input_dir']}\n")
            f.write(f"- **输出目录**: {results['processing_info']['output_dir']}\n")
            f.write(f"- **截图目录**: {results['processing_info']['screenshots_dir']}\n\n")
            
            # 成功处理的变体
            f.write("## 成功处理的变体\n\n")
            for variant_name, variant_data in results['variants'].items():
                coords = variant_data['coordinates']
                f.write(f"### {variant_name}\n")
                f.write(f"- **位置**: ({coords['x']}, {coords['y']})\n")
                f.write(f"- **大小**: {coords['width']}x{coords['height']}\n")
                f.write(f"- **中心**: ({coords['center_x']}, {coords['center_y']})\n")
                f.write(f"- **置信度**: {coords['confidence']}%\n")
                f.write(f"- **截图**: {Path(variant_data['screenshot_file']).name}\n\n")
        
        self.logger.info(f"📄 汇总报告已生成: {report_file}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Booking SF HTML变体处理脚本')
    parser.add_argument('--input-dir', type=str, required=True, help='HTML variant directory')
    parser.add_argument('--output-dir', type=str, required=True, help='Output directory')
    
    args = parser.parse_args()
    
    # 检查输入目录
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"❌ 输入目录不存在: {input_dir}")
        return
    
    # 创建处理器
    processor = BookingSFHTMLProcessor(args.input_dir, args.output_dir)
    
    # 处理所有变体
    try:
        results = processor.process_all_variants()
        
        print(f"\n🎉 处理完成!")
        print(f"📊 处理结果:")
        print(f"   - 总文件数: {results['summary']['total_processed']}")
        print(f"   - 成功处理: {results['summary']['successful']}")
        print(f"   - 处理失败: {results['summary']['failed']}")
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
