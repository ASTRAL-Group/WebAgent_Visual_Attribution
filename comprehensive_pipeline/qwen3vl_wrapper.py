#!/usr/bin/env python3
"""
Qwen3-VL 模型推理包装器
用于comprehensive_web_elements_pipeline的模型集成
"""

import torch
from PIL import Image
from transformers import Qwen3VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
import json
import re
from typing import Dict, List, Any, Tuple, Optional
import logging


class Qwen3VLWrapper:
    """Qwen3-VL模型推理包装器，提供与vLLM引擎相同的接口"""
    
    def __init__(
        self,
        model_path: str = "/data/kuaiyu/qwen3-vl-8b-instruct",
        device_map: str = "auto",
        dtype: torch.dtype = torch.bfloat16,
        max_new_tokens: int = 512,
        max_memory: Optional[Dict[int, str]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        初始化Qwen3-VL模型
        
        Args:
            model_path: 模型路径
            device_map: 设备映射策略（"auto"或自定义映射）
            dtype: 模型精度
            max_new_tokens: 最大生成token数
            max_memory: 每个GPU的最大内存限制，例如 {0: "14GB", 1: "14GB"}
            logger: 日志记录器
        """
        self.model_path = model_path
        self.device_map = device_map
        self.dtype = dtype
        self.max_new_tokens = max_new_tokens
        self.max_memory = max_memory
        self.logger = logger or logging.getLogger(__name__)
        
        self.logger.info(f"🚀 加载Qwen3-VL模型: {model_path}")
        if max_memory:
            self.logger.info(f"💾 GPU内存限制: {max_memory}")
        
        # 加载processor和model
        try:
            self.processor = AutoProcessor.from_pretrained(
                model_path,
                trust_remote_code=True
            )
            self.logger.info("✅ Processor加载成功")
            
            # 构建加载参数
            model_kwargs = {
                "torch_dtype": dtype,
                "device_map": device_map,
                "trust_remote_code": True
            }
            
            # 如果指定了max_memory，添加到参数中
            if max_memory:
                model_kwargs["max_memory"] = max_memory
            
            self.model = Qwen3VLForConditionalGeneration.from_pretrained(
                model_path,
                **model_kwargs
            )
            self.logger.info("✅ 模型加载成功")
            self.logger.info(f"📊 模型类型: {type(self.model).__name__}")
            self.logger.info(f"💾 数据类型: {dtype}")
            self.logger.info(f"🎯 设备映射: {device_map}")
            
            # 显示实际的设备分布
            if hasattr(self.model, 'hf_device_map'):
                self.logger.info(f"🗺️ 实际设备映射: {self.model.hf_device_map}")
            elif hasattr(self.model, 'device'):
                self.logger.info(f"🔧 模型设备: {self.model.device}")
            
        except Exception as e:
            self.logger.error(f"❌ 模型加载失败: {e}")
            raise
    
    def generate_click_coordinates(
        self,
        image_path: str,
        prompt: str,
        bbox_info: Optional[str] = None
    ) -> Tuple[Optional[Tuple[int, int]], str, Dict[str, Any]]:
        """
        生成点击坐标
        
        Args:
            image_path: 图像路径
            prompt: 提示文本
            bbox_info: bbox信息（可选，用于兼容）
            
        Returns:
            (coordinates, response_text, metadata)
            - coordinates: (x, y) 坐标或None
            - response_text: 完整响应文本
            - metadata: 元数据字典
        """
        try:
            # 加载图像
            image = Image.open(image_path)
            
            # 构建消息
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
            
            # 应用chat template
            text = self.processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
            
            # 处理视觉信息
            image_inputs, video_inputs = process_vision_info(messages)
            
            # 准备输入
            inputs = self.processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt"
            ).to(self.model.device)
            
            # 生成响应 - 与UI-TARS保持一致的参数
            with torch.no_grad():
                output_ids = self.model.generate(
                    **inputs,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=True,  # 启用采样
                    temperature=1.0,  # 与UI-TARS一致
                    top_p=0.8  # 与UI-TARS一致
                )
            
            # 解码输出
            generated_ids = [
                output_ids[len(input_ids):]
                for input_ids, output_ids in zip(inputs.input_ids, output_ids)
            ]
            
            response_text = self.processor.batch_decode(
                generated_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False
            )[0]
            
            self.logger.debug(f"🤖 Qwen3-VL响应: {response_text}")
            
            # 🔧 立即清理GPU内存，防止OOM
            del inputs
            del output_ids
            del generated_ids
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            # 解析坐标
            coordinates = self._parse_coordinates(response_text)
            
            # 构建元数据
            metadata = {
                "model": "qwen3-vl-8b-instruct",
                "raw_response": response_text,
                "has_coordinates": coordinates is not None,
                "prompt_length": len(prompt),
                "image_size": image.size
            }
            
            return coordinates, response_text, metadata
            
        except Exception as e:
            self.logger.error(f"❌ 生成失败: {e}")
            import traceback
            traceback.print_exc()
            
            # 🔧 异常时也清理GPU内存
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            
            return None, str(e), {"error": str(e)}
    
    def _parse_coordinates(self, response_text: str) -> Optional[Tuple[int, int]]:
        """
        从响应文本中解析坐标
        
        支持多种格式:
        - click(start_box='(x, y)')  # UI-TARS/Qwen3-VL标准格式
        - [x, y]
        - (x, y)
        - x, y
        - click at x, y
        - coordinates: x, y
        """
        self.logger.debug(f"🔍 解析坐标，输入文本: {response_text[:200]}...")
        
        # 优先匹配 click(start_box='(x, y)') 格式 - 最常见的输出格式
        pattern_click_box = r"click\s*\(\s*start_box\s*=\s*['\"]?\s*\((\d+)\s*,\s*(\d+)\)\s*['\"]?\s*\)"
        match = re.search(pattern_click_box, response_text, re.IGNORECASE)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ 解析click(start_box=...) 格式: {coords}")
            return coords
        
        # 匹配 scroll(start_box='(x, y)', direction='...') 格式
        pattern_scroll_box = r"scroll\s*\(\s*start_box\s*=\s*['\"]?\s*\((\d+)\s*,\s*(\d+)\)\s*['\"]?"
        match = re.search(pattern_scroll_box, response_text, re.IGNORECASE)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ 解析scroll(start_box=...) 格式: {coords}")
            return coords
        
        # 尝试匹配 [x, y] 格式
        pattern1 = r'\[(\d+),\s*(\d+)\]'
        match = re.search(pattern1, response_text)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ 解析[x, y]格式: {coords}")
            return coords
        
        # 尝试匹配 (x, y) 格式 - 优先在Action后面找
        pattern2a = r'Action:.*?\((\d+)\s*,\s*(\d+)\)'
        match = re.search(pattern2a, response_text, re.DOTALL | re.IGNORECASE)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ 解析Action后(x, y)格式: {coords}")
            return coords
        
        # 尝试匹配任意 (x, y) 格式
        pattern2 = r'\((\d+)\s*,\s*(\d+)\)'
        match = re.search(pattern2, response_text)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ 解析(x, y)格式: {coords}")
            return coords
        
        # 尝试匹配 x, y 格式
        pattern3 = r'(\d+)\s*,\s*(\d+)'
        match = re.search(pattern3, response_text)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ 解析x, y格式: {coords}")
            return coords
        
        # 尝试匹配 "click at x, y" 格式
        pattern4 = r'click\s+at\s+(\d+)\s*,\s*(\d+)'
        match = re.search(pattern4, response_text, re.IGNORECASE)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ 解析click at格式: {coords}")
            return coords
        
        # 尝试匹配 "coordinates: x, y" 格式
        pattern5 = r'coordinates?:\s*(\d+)\s*,\s*(\d+)'
        match = re.search(pattern5, response_text, re.IGNORECASE)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ 解析coordinates:格式: {coords}")
            return coords
        
        self.logger.warning(f"⚠️ 未能从响应中解析出坐标")
        return None
    
    def cleanup(self):
        """清理资源"""
        try:
            if hasattr(self, 'model'):
                del self.model
            if hasattr(self, 'processor'):
                del self.processor
            
            torch.cuda.empty_cache()
            self.logger.info("🧹 Qwen3-VL资源已清理")
        except Exception as e:
            self.logger.warning(f"⚠️ 清理资源时出错: {e}")
    
    def __del__(self):
        """析构函数"""
        self.cleanup()


# 用于测试的简单示例
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # 初始化模型
    wrapper = Qwen3VLWrapper()
    
    # 测试
    test_image = "/data/sanity_check/button/screenshots/button_large_page-top.png"
    test_prompt = "Click on the red button with the text '●'."
    
    coords, response, metadata = wrapper.generate_click_coordinates(
        test_image,
        test_prompt
    )
    
    print(f"\n📍 坐标: {coords}")
    print(f"📝 响应: {response}")
    print(f"📊 元数据: {json.dumps(metadata, indent=2)}")
