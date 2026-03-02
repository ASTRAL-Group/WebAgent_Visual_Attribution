#!/usr/bin/env python3
"""
NPR 验证性框选脚本
专门负责根据坐标在截图上进行验证性框选

功能：
1. 读取坐标数据和截图文件
2. 在截图上绘制目标框选
3. 生成验证图片用于检查坐标准确性

输出：
- verifications/ : 带框选的验证图片
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
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

class NPRVerificationBoxer:
    """NPR 验证性框选器"""
    
    def __init__(self, screenshots_dir: str, coordinates_file: str, output_dir: str):
        """
        初始化验证性框选器
        
        Args:
            screenshots_dir: 截图目录
            coordinates_file: 坐标文件路径
            output_dir: 输出目录
        """
        self.screenshots_dir = Path(screenshots_dir)
        self.coordinates_file = Path(coordinates_file)
        self.output_dir = Path(output_dir)
        
        # 创建输出目录
        self.verifications_dir = self.output_dir / "verifications"
        self.verifications_dir.mkdir(parents=True, exist_ok=True)
        
        # 设置日志
        self.setup_logging()
        
        print(f"🚀 NPR 验证性框选器已初始化")
        print(f"📁 截图目录: {self.screenshots_dir}")
        print(f"📄 坐标文件: {self.coordinates_file}")
        print(f"📁 输出目录: {self.output_dir}")
        print(f"📁 验证目录: {self.verifications_dir}")
    
    def setup_logging(self):
        """设置日志"""
        log_file = self.output_dir / f"verification_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
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
    
    def load_coordinates(self) -> Dict[str, Any]:
        """
        加载坐标数据
        
        Returns:
            Dict: 坐标数据
        """
        try:
            with open(self.coordinates_file, 'r', encoding='utf-8') as f:
                coordinates_data = json.load(f)
            
            self.logger.info(f"✅ 坐标数据加载成功，共 {len(coordinates_data)} 个变体")
            return coordinates_data
            
        except Exception as e:
            self.logger.error(f"❌ 坐标数据加载失败: {e}")
            return {}
    
    def draw_verification_box(self, screenshot_path: str, coordinates: Dict, output_path: str) -> bool:
        """
        在截图上绘制验证框
        
        Args:
            screenshot_path: 截图文件路径
            coordinates: 坐标数据
            output_path: 输出验证图片路径
            
        Returns:
            bool: 是否成功生成验证图片
        """
        try:
            # 使用PIL加载图片
            image = Image.open(screenshot_path)
            draw = ImageDraw.Draw(image)
            
            # 获取坐标
            x = coordinates['x']
            y = coordinates['y']
            width = coordinates['width']
            height = coordinates['height']
            confidence = coordinates['confidence']
            detection_method = coordinates['detection_method']
            matched_text = coordinates.get('matched_text', 'Target News Article')
            
            # 绘制矩形框
            box_color = (255, 0, 0)  # 红色
            box_width = 3
            
            # 绘制外框
            draw.rectangle([x, y, x + width, y + height], outline=box_color, width=box_width)
            
            # 绘制中心点
            center_x = coordinates['center_x']
            center_y = coordinates['center_y']
            center_size = 5
            draw.ellipse([center_x - center_size, center_y - center_size, 
                         center_x + center_size, center_y + center_size], 
                        fill=box_color)
            
            # 添加标签
            try:
                # 尝试使用系统字体
                font = ImageFont.truetype("/System/Library/Fonts/Arial.ttf", 16)
            except:
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
                except:
                    font = ImageFont.load_default()
            
            # 标签文本
            label_text = f"Target News Article ({confidence}%)"
            label_bg_color = (255, 255, 255, 200)  # 半透明白色背景
            
            # 计算标签位置（在框的上方）
            label_x = x
            label_y = max(0, y - 25)
            
            # 获取文本尺寸
            bbox = draw.textbbox((0, 0), label_text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            
            # 绘制标签背景
            draw.rectangle([label_x, label_y, label_x + text_width + 10, label_y + text_height + 5], 
                         fill=label_bg_color)
            
            # 绘制标签文本
            draw.text((label_x + 5, label_y + 2), label_text, fill=(0, 0, 0), font=font)
            
            # 添加检测方法信息
            method_text = f"Method: {detection_method}"
            method_y = label_y + text_height + 10
            
            # 绘制方法标签背景
            method_bbox = draw.textbbox((0, 0), method_text, font=font)
            method_width = method_bbox[2] - method_bbox[0]
            method_height = method_bbox[3] - method_bbox[1]
            
            draw.rectangle([label_x, method_y, label_x + method_width + 10, method_y + method_height + 5], 
                         fill=label_bg_color)
            
            # 绘制方法文本
            draw.text((label_x + 5, method_y + 2), method_text, fill=(0, 0, 0), font=font)
            
            # 保存验证图片
            image.save(output_path, 'PNG')
            
            self.logger.info(f"✅ 验证图片生成成功: {Path(output_path).name}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ 验证图片生成失败: {e}")
            return False
    
    def process_single_verification(self, variant_name: str, coordinates: Dict) -> Optional[Dict]:
        """
        处理单个变体的验证框选
        
        Args:
            variant_name: 变体名称
            coordinates: 坐标数据
            
        Returns:
            Dict: 处理结果或None
        """
        self.logger.info(f"\n🔄 处理验证: {variant_name}")
        
        try:
            # 查找对应的截图文件
            screenshot_path = self.screenshots_dir / f"{variant_name}.png"
            if not screenshot_path.exists():
                self.logger.warning(f"⚠️ 截图文件不存在: {screenshot_path}")
                return None
            
            # 生成验证图片路径
            verification_path = self.verifications_dir / f"{variant_name}_verification.png"
            
            # 如果验证图片已存在，跳过
            if verification_path.exists():
                self.logger.info(f"⏭️ 验证图片已存在，跳过: {verification_path}")
                return {
                    'variant_name': variant_name,
                    'screenshot_file': str(screenshot_path),
                    'verification_file': str(verification_path),
                    'coordinates': coordinates,
                    'processing_time': datetime.now().isoformat(),
                    'status': 'skipped'
                }
            
            # 生成验证图片
            if self.draw_verification_box(str(screenshot_path), coordinates, str(verification_path)):
                return {
                    'variant_name': variant_name,
                    'screenshot_file': str(screenshot_path),
                    'verification_file': str(verification_path),
                    'coordinates': coordinates,
                    'processing_time': datetime.now().isoformat(),
                    'status': 'success'
                }
            else:
                return None
            
        except Exception as e:
            self.logger.error(f"❌ {variant_name} 验证处理失败: {e}")
            return None
    
    def generate_all_verifications(self) -> Dict[str, Any]:
        """
        为所有变体生成验证图片
        
        Returns:
            Dict: 处理结果汇总
        """
        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"🚀 开始生成所有NPR变体验证图片")
        self.logger.info(f"{'='*60}")
        
        start_time = time.time()
        
        # 加载坐标数据
        coordinates_data = self.load_coordinates()
        if not coordinates_data:
            self.logger.error("❌ 无法加载坐标数据")
            return {}
        
        # 处理结果
        results = {
            'processing_info': {
                'screenshots_dir': str(self.screenshots_dir),
                'coordinates_file': str(self.coordinates_file),
                'verifications_dir': str(self.verifications_dir),
                'total_variants': len(coordinates_data),
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
        
        # 逐个处理变体
        for variant_name, coordinates in tqdm(coordinates_data.items(), desc="生成验证图片"):
            result = self.process_single_verification(variant_name, coordinates)
            
            if result:
                results['variants'][variant_name] = result
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
        results_file = self.output_dir / "verification_results.json"
        with open(results_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        self.logger.info(f"💾 结果已保存: {results_file}")
    
    def generate_summary_report(self, results: Dict[str, Any]):
        """生成汇总报告"""
        report_file = self.output_dir / "verification_summary.md"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("# NPR 验证性框选报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # 处理概况
            summary = results['summary']
            f.write("## 处理概况\n\n")
            f.write(f"- **总变体数**: {summary['total_processed']}\n")
            f.write(f"- **成功生成**: {summary['successful']}\n")
            f.write(f"- **跳过文件**: {summary['skipped']}\n")
            f.write(f"- **生成失败**: {summary['failed']}\n")
            f.write(f"- **成功率**: {summary['success_rate']:.1%}\n")
            f.write(f"- **处理耗时**: {results['processing_info']['duration']:.1f}秒\n\n")
            
            # 输入输出信息
            f.write("## 输入输出信息\n\n")
            f.write(f"- **截图目录**: {results['processing_info']['screenshots_dir']}\n")
            f.write(f"- **坐标文件**: {results['processing_info']['coordinates_file']}\n")
            f.write(f"- **验证目录**: {results['processing_info']['verifications_dir']}\n\n")
            
            # 成功处理的变体
            f.write("## 成功处理的变体\n\n")
            for variant_name, variant_data in results['variants'].items():
                coords = variant_data['coordinates']
                f.write(f"### {variant_name}\n")
                f.write(f"- **状态**: {variant_data['status']}\n")
                f.write(f"- **位置**: ({coords['x']}, {coords['y']})\n")
                f.write(f"- **大小**: {coords['width']}x{coords['height']}\n")
                f.write(f"- **中心**: ({coords['center_x']}, {coords['center_y']})\n")
                f.write(f"- **置信度**: {coords['confidence']}%\n")
                f.write(f"- **检测方法**: {coords['detection_method']}\n")
                f.write(f"- **验证图片**: {Path(variant_data['verification_file']).name}\n\n")
        
        self.logger.info(f"📄 汇总报告已生成: {report_file}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='NPR verification bounding-box script')
    parser.add_argument('--screenshots-dir', type=str, 
                       default='data/npr/screenshots',
                       help='Screenshots directory')
    parser.add_argument('--coordinates-file', type=str,
                       default='data/npr/coordinates.json',
                       help='Coordinates JSON path')
    parser.add_argument('--output-dir', type=str,
                       default='data/npr/verifications',
                       help='Output directory for verification images')
    
    args = parser.parse_args()
    
    # 检查输入文件
    screenshots_dir = Path(args.screenshots_dir)
    coordinates_file = Path(args.coordinates_file)
    
    if not screenshots_dir.exists():
        print(f"❌ 截图目录不存在: {screenshots_dir}")
        return
    
    if not coordinates_file.exists():
        print(f"❌ 坐标文件不存在: {coordinates_file}")
        return
    
    # 创建验证性框选器
    boxer = NPRVerificationBoxer(str(screenshots_dir), str(coordinates_file), args.output_dir)
    
    # 生成所有验证图片
    try:
        results = boxer.generate_all_verifications()
        
        print(f"\n🎉 验证图片生成完成!")
        print(f"📊 处理结果:")
        print(f"   - 总变体数: {results['summary']['total_processed']}")
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















