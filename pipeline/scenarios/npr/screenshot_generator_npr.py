#!/usr/bin/env python3
"""
NPR 截图生成脚本
专门负责将HTML变体文件转换为原比例PNG截图

功能：
1. 使用Playwright将HTML文件转换为原比例PNG截图
2. 支持批量处理所有HTML变体文件
3. 生成高质量的截图用于后续坐标提取

输出：
- screenshots/ : 原比例PNG截图文件
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

class NPRScreenshotGenerator:
    """NPR 截图生成器"""
    
    def __init__(self, input_dir: str, output_dir: str):
        """
        初始化截图生成器
        
        Args:
            input_dir: HTML变体文件目录
            output_dir: 输出目录
        """
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        
        # 创建输出目录
        self.screenshots_dir = self.output_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        
        # 设置日志
        self.setup_logging()
        
        print(f"🚀 NPR 截图生成器已初始化")
        print(f"📁 输入目录: {self.input_dir}")
        print(f"📁 输出目录: {self.output_dir}")
        print(f"📁 截图目录: {self.screenshots_dir}")
    
    def setup_logging(self):
        """设置日志"""
        log_file = self.output_dir / f"screenshot_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
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
                page_size["width"] = max(page_size["width"], 1280)
                page_size["height"] = max(page_size["height"], 1200)
                
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
    
    def process_single_html(self, html_file_path: str) -> Optional[Dict]:
        """
        处理单个HTML文件生成截图
        
        Args:
            html_file_path: HTML文件路径
            
        Returns:
            Dict: 处理结果或None
        """
        html_path = Path(html_file_path)
        variant_name = html_path.stem
        
        self.logger.info(f"\n🔄 处理变体: {variant_name}")
        
        try:
            # 生成截图
            screenshot_path = self.screenshots_dir / f"{variant_name}.png"
            
            if screenshot_path.exists():
                self.logger.info(f"⏭️ 截图已存在，跳过: {screenshot_path}")
                return {
                    'variant_name': variant_name,
                    'html_file': str(html_path),
                    'screenshot_file': str(screenshot_path),
                    'processing_time': datetime.now().isoformat(),
                    'status': 'skipped'
                }
            else:
                if self.generate_screenshot_playwright(html_file_path, str(screenshot_path)):
                    return {
                        'variant_name': variant_name,
                        'html_file': str(html_path),
                        'screenshot_file': str(screenshot_path),
                        'processing_time': datetime.now().isoformat(),
                        'status': 'success'
                    }
                else:
                    return None
            
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
    
    def generate_all_screenshots(self) -> Dict[str, Any]:
        """
        为所有HTML变体生成截图
        
        Returns:
            Dict: 处理结果汇总
        """
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"🚀 开始生成所有NPR HTML变体截图")
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
                'total_files': len(html_files),
                'start_time': datetime.now().isoformat()
            },
            'variants': {},
            'summary': {
                'total_processed': 0,
                'successful': 0,
                'skipped': 0,
                'failed': 0,
                'success_rate': 0.0
            }
        }
        
        # 逐个处理HTML文件
        for html_file in tqdm(html_files, desc="生成截图"):
            result = self.process_single_html(html_file)
            
            if result:
                results['variants'][result['variant_name']] = result
                if result['status'] == 'success':
                    results['summary']['successful'] += 1
                elif result['status'] == 'skipped':
                    results['summary']['skipped'] += 1
            else:
                results['summary']['failed'] += 1
            
            results['summary']['total_processed'] += 1
        
        # 计算成功率
        if results['summary']['total_processed'] > 0:
            results['summary']['success_rate'] = (
                (results['summary']['successful'] + results['summary']['skipped']) / 
                results['summary']['total_processed']
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
        results_file = self.output_dir / "screenshot_results.json"
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        self.logger.info(f"💾 结果已保存: {results_file}")
    
    def generate_summary_report(self, results: Dict[str, Any]):
        """生成汇总报告"""
        report_file = self.output_dir / "screenshot_summary.md"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("# NPR 截图生成报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # 处理概况
            summary = results['summary']
            f.write("## 处理概况\n\n")
            f.write(f"- **总文件数**: {summary['total_processed']}\n")
            f.write(f"- **成功生成**: {summary['successful']}\n")
            f.write(f"- **跳过文件**: {summary['skipped']}\n")
            f.write(f"- **生成失败**: {summary['failed']}\n")
            f.write(f"- **成功率**: {summary['success_rate']:.1%}\n")
            f.write(f"- **处理耗时**: {results['processing_info']['duration']:.1f}秒\n\n")
            
            # 输入输出信息
            f.write("## 输入输出信息\n\n")
            f.write(f"- **输入目录**: {results['processing_info']['input_dir']}\n")
            f.write(f"- **输出目录**: {results['processing_info']['output_dir']}\n")
            f.write(f"- **截图目录**: {results['processing_info']['screenshots_dir']}\n\n")
            
            # 成功处理的变体
            f.write("## 成功处理的变体\n\n")
            for variant_name, variant_data in results['variants'].items():
                f.write(f"### {variant_name}\n")
                f.write(f"- **状态**: {variant_data['status']}\n")
                f.write(f"- **截图**: {Path(variant_data['screenshot_file']).name}\n")
                f.write(f"- **处理时间**: {variant_data['processing_time']}\n\n")
        
        self.logger.info(f"📄 汇总报告已生成: {report_file}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='NPR screenshot generation script')
    parser.add_argument('--input-dir', type=str, 
                       default='data/npr/html',
                       help='Directory containing HTML variants')
    parser.add_argument('--output-dir', type=str,
                       default='data/npr/screenshots',
                       help='Output directory for screenshots')
    
    args = parser.parse_args()
    
    # 检查输入目录
    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        print(f"❌ 输入目录不存在: {input_dir}")
        return
    
    # 创建截图生成器
    generator = NPRScreenshotGenerator(args.input_dir, args.output_dir)
    
    # 生成所有截图
    try:
        results = generator.generate_all_screenshots()
        
        print(f"\n🎉 截图生成完成!")
        print(f"📊 处理结果:")
        print(f"   - 总文件数: {results['summary']['total_processed']}")
        print(f"   - 成功生成: {results['summary']['successful']}")
        print(f"   - 跳过文件: {results['summary']['skipped']}")
        print(f"   - 生成失败: {results['summary']['failed']}")
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

