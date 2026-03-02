#!/usr/bin/env python3
"""
简化版变体测试脚本
测试特定Booking2变体的处理效果
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, List, Any, Optional
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By

def extract_html_coordinates(html_file_path: str, target_text: str = "Holiday Inn San Francisco - Golden Gateway") -> Optional[Dict]:
    """
    从HTML中提取目标元素的精确坐标（使用Playwright确保与截图一致）
    """
    print(f"🔍 从HTML提取坐标: {Path(html_file_path).name}")
    
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
            
            # 设置视口为页面完整尺寸（与截图生成保持一致）
            page.set_viewport_size({
                "width": max(page_size["width"], 1200), 
                "height": max(page_size["height"], 800)
            })
            
            # 等待重新渲染
            time.sleep(2)
            
            # 查找包含目标文本的元素
            text_elements = page.locator(f"text={target_text}").all()
            
            if not text_elements:
                print(f"⚠️ 未找到包含文本 '{target_text}' 的元素")
                browser.close()
                return None
            
            print(f"✅ 找到 {len(text_elements)} 个包含目标文本的元素")
            
            # 选择第一个元素
            text_element = text_elements[0]
        
            # 向上遍历DOM树，查找卡片容器
            best_container = None
            best_score = 0
            
            # 使用Playwright的evaluate方法进行DOM遍历
            result = page.evaluate("""
                (targetText) => {
                    // 查找包含目标文本的元素（排除HTML和BODY元素）
                    const textElements = Array.from(document.querySelectorAll('*')).filter(el => 
                        el.textContent && 
                        el.textContent.includes(targetText) &&
                        el.tagName !== 'HTML' &&
                        el.tagName !== 'BODY' &&
                        el.textContent.trim().length < 200  // 排除包含大量文本的容器
                    );
                    
                    if (textElements.length === 0) return null;
                    
                    // 选择文本内容最接近目标文本的元素
                    let current = textElements.find(el => el.textContent.trim() === targetText) || textElements[0];
                    let bestContainer = null;
                    let bestScore = 0;
                    
                    // 向上遍历DOM树
                    for (let level = 0; level < 15; level++) {
                        const parent = current.parentElement;
                        if (!parent) break;
                        
                        const tagName = parent.tagName.toLowerCase();
                        const className = parent.className || '';
                        
                        // 计算容器评分
                        let score = 0;
                        
                        // 检查标签和类名
                        const cardTags = ['div', 'article', 'section', 'li'];
                        const cardClasses = ['card', 'hotel', 'property', 'item', 'listing', 'sr_property_block', 'sr_item'];
                        
                        if (cardTags.includes(tagName)) score += 10;
                        if (cardClasses.some(cardClass => className.toLowerCase().includes(cardClass))) score += 20;
                        
                        // 检查容器大小
                        const rect = parent.getBoundingClientRect();
                        const width = rect.width;
                        const height = rect.height;
                        
                        // 理想的卡片尺寸范围
                        if (width >= 400 && width <= 1200 && height >= 200 && height <= 600) {
                            score += 30;
                        } else if (width >= 300 && width <= 1500 && height >= 150 && height <= 800) {
                            score += 20;
                        } else if (width > 200 && height > 100) {
                            score += 10;
                        }
                        
                        // 检查是否包含图片元素
                        const images = parent.querySelectorAll('img');
                        if (images.length > 0) {
                            score += 15;
                        }
                        
                        // 如果这个容器评分更高，更新最佳容器
                        if (score > bestScore) {
                            bestScore = score;
                            bestContainer = parent;
                        }
                        
                        current = parent;
                    }
                    
                    if (bestContainer) {
                        const rect = bestContainer.getBoundingClientRect();
                        return {
                            x: Math.round(rect.left),
                            y: Math.round(rect.top),
                            width: Math.round(rect.width),
                            height: Math.round(rect.height),
                            center_x: Math.round(rect.left + rect.width / 2),
                            center_y: Math.round(rect.top + rect.height / 2),
                            score: bestScore
                        };
                    }
                    
                    return null;
                }
            """, target_text)
            
            if not result:
                print("⚠️ 未找到合适的卡片容器")
                # 添加调试信息
                debug_info = page.evaluate("""
                    () => {
                        const textElements = Array.from(document.querySelectorAll('*')).filter(el => 
                            el.textContent && el.textContent.includes('Holiday Inn San Francisco - Golden Gateway')
                        );
                        return {
                            foundElements: textElements.length,
                            firstElement: textElements[0] ? {
                                tagName: textElements[0].tagName,
                                className: textElements[0].className,
                                textContent: textElements[0].textContent.substring(0, 100)
                            } : null
                        };
                    }
                """)
                print(f"🔍 调试信息: {debug_info}")
                browser.close()
                return None
            
            # 获取页面尺寸
            page_width = page_size["width"]
            page_height = page_size["height"]
            
            # 确保坐标在页面边界内
            x = max(0, result['x'])
            y = max(0, result['y'])
            width = result['width']
            height = result['height']
            
            # 确保右边界不超出截图宽度
            if x + width > page_width:
                width = page_width - x
                print(f"⚠️ 调整宽度以避免超出截图右边界: {width} (截图宽度: {page_width})")
            
            # 确保下边界不超出截图高度
            if y + height > page_height:
                height = page_height - y
                print(f"⚠️ 调整高度以避免超出截图下边界: {height} (截图高度: {page_height})")
            
            # 确保宽度和高度为正数
            if width <= 0 or height <= 0:
                print(f"❌ 调整后的尺寸无效: {width}x{height}")
                browser.close()
                return None
            
            # 计算中心点
            center_x = x + width / 2
            center_y = y + height / 2
            
            coordinates = {
                'x': x,
                'y': y,
                'width': width,
                'height': height,
                'center_x': int(center_x),
                'center_y': int(center_y),
                'confidence': 95,
                'detection_method': 'playwright_html_dom_extraction',
                'matched_text': target_text,
                'page_width': page_width,
                'page_height': page_height
            }
            
            print(f"✅ 坐标提取成功:")
            print(f"   位置: ({coordinates['x']}, {coordinates['y']})")
            print(f"   大小: {coordinates['width']}x{coordinates['height']}")
            print(f"   中心: ({coordinates['center_x']}, {coordinates['center_y']})")
            print(f"   置信度: {coordinates['confidence']}%")
            
            browser.close()
            return coordinates
            
    except Exception as e:
        print(f"❌ 坐标提取失败: {e}")
        return None

def generate_screenshot_playwright(html_file_path: str, output_path: str) -> Optional[Dict]:
    """
    使用Playwright生成原比例截图
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
            time.sleep(2)
            
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
        
        # 验证截图并获取详细信息
        if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
            # 获取截图尺寸
            with Image.open(screenshot_path) as img:
                actual_width, actual_height = img.size
            
            screenshot_info = {
                'path': str(screenshot_path),
                'file_size': screenshot_path.stat().st_size,
                'page_size': page_size,
                'actual_size': {'width': actual_width, 'height': actual_height},
                'success': True
            }
            
            print(f"✅ 截图生成成功: {screenshot_path.name}")
            print(f"📏 页面尺寸: {page_size['width']}x{page_size['height']}")
            print(f"📏 实际尺寸: {actual_width}x{actual_height}")
            print(f"📦 文件大小: {screenshot_info['file_size']:,} bytes")
            
            return screenshot_info
        else:
            print(f"❌ 截图生成失败: {screenshot_path}")
            return None
            
    except ImportError:
        print("❌ Playwright未安装，请运行: pip install playwright")
        return None
    except Exception as e:
        print(f"❌ 截图生成异常: {e}")
        return None

def create_verification_image(screenshot_path: str, coordinates: Dict, variant_name: str, output_dir: str) -> str:
    """
    创建可视化验证图片
    """
    try:
        # 读取截图
        image = cv2.imread(screenshot_path)
        if image is None:
            print(f"❌ 无法读取截图: {screenshot_path}")
            return None
        
        # 创建验证图片副本
        verification_image = image.copy()
        
        # 提取坐标
        x = coordinates['x']
        y = coordinates['y']
        width = coordinates['width']
        height = coordinates['height']
        center_x = coordinates['center_x']
        center_y = coordinates['center_y']
        confidence = coordinates['confidence']
        
        # 确保坐标在图片范围内
        img_height, img_width = verification_image.shape[:2]
        
        # 调整坐标到图片范围内
        x_adj = max(0, min(x, img_width - 1))
        y_adj = max(0, min(y, img_height - 1))
        x2_adj = max(0, min(x + width, img_width - 1))
        y2_adj = max(0, min(y + height, img_height - 1))
        
        # 绘制边界框
        cv2.rectangle(verification_image, (x_adj, y_adj), (x2_adj, y2_adj), (0, 255, 0), 3)
        
        # 绘制中心点（确保在图片范围内）
        center_x_adj = max(0, min(center_x, img_width - 1))
        center_y_adj = max(0, min(center_y, img_height - 1))
        cv2.circle(verification_image, (center_x_adj, center_y_adj), 5, (0, 0, 255), -1)
        
        # 如果坐标被调整，添加警告信息
        if x_adj != x or y_adj != y or x2_adj != x + width or y2_adj != y + height:
            print(f"⚠️ 坐标已调整以适应图片范围:")
            print(f"   原始: ({x},{y}) 到 ({x+width},{y+height})")
            print(f"   调整: ({x_adj},{y_adj}) 到 ({x2_adj},{y2_adj})")
            print(f"   图片尺寸: {img_width}x{img_height}")
        
        # 添加文本信息
        text_info = [
            f"Variant: {variant_name}",
            f"Target: Holiday Inn San Francisco - Golden Gateway",
            f"Position: ({x}, {y})",
            f"Size: {width}x{height}",
            f"Center: ({center_x}, {center_y})",
            f"Confidence: {confidence}%",
            f"Image Size: {verification_image.shape[1]}x{verification_image.shape[0]}"
        ]
        
        # 添加页面边界信息（如果有）
        if 'page_width' in coordinates:
            text_info.append(f"Page Size: {coordinates['page_width']}x{coordinates['page_height']}")
        
        # 在图片上添加文本
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.7
        font_thickness = 2
        text_color = (255, 255, 255)  # 白色
        bg_color = (0, 0, 0)  # 黑色背景
        
        y_offset = 30
        for i, text in enumerate(text_info):
            # 获取文本尺寸
            (text_width, text_height), baseline = cv2.getTextSize(text, font, font_scale, font_thickness)
            
            # 绘制背景矩形
            cv2.rectangle(verification_image, 
                       (10, y_offset - text_height - 5), 
                       (10 + text_width, y_offset + 5), 
                       bg_color, -1)
            
            # 绘制文本
            cv2.putText(verification_image, text, (10, y_offset), 
                      font, font_scale, text_color, font_thickness)
            
            y_offset += 35
        
        # 保存验证图片
        verification_path = Path(output_dir) / f"{variant_name}_verification.png"
        cv2.imwrite(str(verification_path), verification_image)
        
        print(f"✅ 验证图片已生成: {verification_path.name}")
        return str(verification_path)
        
    except Exception as e:
        print(f"❌ 验证图片生成失败: {e}")
        return None

def test_single_variant(variant_name: str, input_dir: str, output_dir: str) -> Optional[Dict]:
    """
    测试单个变体
    """
    print(f"\n{'='*60}")
    print(f"🧪 测试变体: {variant_name}")
    print(f"{'='*60}")
    
    # 查找HTML文件
    html_file = Path(input_dir) / f"{variant_name}.html"
    if not html_file.exists():
        print(f"❌ HTML文件不存在: {html_file}")
        return None
    
    try:
        # 1. 生成截图
        screenshot_path = Path(output_dir) / f"{variant_name}.png"
        screenshot_info = generate_screenshot_playwright(str(html_file), str(screenshot_path))
        
        if not screenshot_info:
            return None
        
        # 2. 提取坐标
        coordinates = extract_html_coordinates(str(html_file), "Holiday Inn San Francisco - Golden Gateway")
        if not coordinates:
            return None
        
        # 3. 创建验证图片
        verification_path = create_verification_image(
            str(screenshot_path), coordinates, variant_name, output_dir
        )
        
        # 4. 返回测试结果
        result = {
            'variant_name': variant_name,
            'html_file': str(html_file),
            'screenshot_info': screenshot_info,
            'coordinates': coordinates,
            'verification_image': verification_path,
            'test_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'status': 'success'
        }
        
        print(f"✅ {variant_name} 测试完成")
        return result
        
    except Exception as e:
        print(f"❌ {variant_name} 测试失败: {e}")
        return None

def main():
    """主函数"""
    # 设置路径
    input_dir = "/Users/naichengyu/Desktop/Code/WebAgentsLook/senarios/booking2/output_booking2_online"
    output_dir = "/Users/naichengyu/Desktop/Code/WebAgentsLook/senarios/booking2/booking2_html_to_screenshots_coords/test_output"
    
    # 创建输出目录
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # 目标变体
    target_variants = [
        "style_card_clarity_blur_1px",
        "style_card_size_scale_1.5", 
        "position_banner",
        "position_floating", 
        "position_header"
    ]
    
    print(f"🧪 Booking2 变体测试")
    print(f"📁 输入目录: {input_dir}")
    print(f"📁 输出目录: {output_dir}")
    print(f"🎯 目标变体: {target_variants}")
    
    results = {}
    
    # 测试每个变体
    for variant_name in target_variants:
        result = test_single_variant(variant_name, input_dir, output_dir)
        if result:
            results[variant_name] = result
    
    # 保存测试结果
    results_file = Path(output_dir) / "test_results.json"
    with open(results_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n🎉 测试完成!")
    print(f"📊 测试结果:")
    print(f"   - 测试变体数: {len(target_variants)}")
    print(f"   - 成功测试: {len(results)}")
    print(f"   - 成功率: {len(results)/len(target_variants):.1%}")
    
    print(f"\n📁 输出文件:")
    print(f"   - 测试结果: {results_file}")
    print(f"   - 截图文件: {output_dir}/*.png")
    print(f"   - 验证图片: {output_dir}/*_verification.png")

if __name__ == "__main__":
    main()
