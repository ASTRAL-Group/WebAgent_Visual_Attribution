#!/usr/bin/env python3
"""
GLM-4V-9B 模型推理包装器
用于comprehensive_web_elements_pipeline的模型集成
"""

import torch
from PIL import Image
from transformers import AutoTokenizer, AutoModel
import json
import re
from typing import Dict, List, Any, Tuple, Optional
import logging


class GLM4VWrapper:
    """GLM-4.1V-9B-Base模型推理包装器，提供与vLLM引擎相同的接口"""
    
    def __init__(
        self,
        model_path: str = "/data/kuaiyu/GLM-4.1V-9B-Base",
        device_map: str = "auto",
        dtype: torch.dtype = torch.bfloat16,
        max_new_tokens: int = 512,
        max_memory: Optional[Dict[int, str]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        初始化GLM-4V-9B模型
        
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
        
        self.logger.info(f"🚀 加载GLM-4.1V-9B-Base模型: {model_path}")
        if max_memory:
            self.logger.info(f"💾 GPU内存限制: {max_memory}")
        
        # 加载tokenizer和model
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                trust_remote_code=True
            )
            self.logger.info("✅ Tokenizer加载成功")
            
            # 构建加载参数 - 使用AutoModel而不是AutoModelForCausalLM
            model_kwargs = {
                "torch_dtype": dtype,
                "device_map": device_map,
                "trust_remote_code": True
            }
            
            # 如果指定了max_memory，添加到参数中
            if max_memory:
                model_kwargs["max_memory"] = max_memory
            
            self.model = AutoModel.from_pretrained(
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
            image = Image.open(image_path).convert("RGB")
            
            # 构建更详细的prompt，明确要求分析图像位置
            detailed_prompt = f"""Task: {prompt}

CRITICAL INSTRUCTIONS:
1. Look at the screenshot image carefully
2. Visually locate the element described in the task
3. Determine the EXACT pixel coordinates where you see this element in the image
4. The coordinates must be based on WHERE you actually SEE the element, not a generic center point

Response format - provide ONLY the click command with the actual element's coordinates:
click(x, y)

Example: If you see a red button at the top-left area of the image around position x=250, y=180, respond with:
click(250, 180)

IMPORTANT: 
- DO NOT use generic coordinates like (640, 600)
- DO NOT copy example coordinates
- USE the actual visual position you observe in the image
- The x coordinate should match the horizontal position of the element
- The y coordinate should match the vertical position of the element

Now analyze the image and provide the click coordinates for the element:"""
            
            # 准备输入 - 使用GLM-4V的apply_chat_template
            # 注意：GLM-4V需要role, content, 以及可选的image和metadata
            inputs = self.tokenizer.apply_chat_template(
                [{"role": "user", "image": image, "content": detailed_prompt}],
                add_generation_prompt=True,
                tokenize=True,
                return_tensors="pt",
                return_dict=True
            )
            
            # 移动tensors到正确设备
            device = next(self.model.parameters()).device
            device_inputs = {}
            for k, v in inputs.items():
                if isinstance(v, torch.Tensor):
                    device_inputs[k] = v.to(device)
                else:
                    device_inputs[k] = v
            
            # 生成配置 - 与UI-TARS保持一致的参数
            gen_kwargs = {
                "max_new_tokens": self.max_new_tokens,
                "do_sample": True,  # 启用采样
                "temperature": 1.0,  # 与UI-TARS一致
                "top_p": 0.8  # 与UI-TARS一致
            }
            
            # 生成响应
            self.logger.debug("🔮 正在生成响应...")
            
            # 🧹 生成前清理一次内存
            if torch.cuda.is_available():
                import gc
                gc.collect()
                torch.cuda.empty_cache()
            
            with torch.no_grad():
                outputs = self.model.generate(**device_inputs, **gen_kwargs)
                # 只获取新生成的token（去除输入部分）
                outputs = outputs[:, device_inputs['input_ids'].shape[1]:]
                response_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            
            self.logger.debug(f"🤖 GLM-4V响应: {response_text}")
            
            # 🔧 生成后立即清理GPU内存，防止OOM
            if torch.cuda.is_available():
                import gc
                del outputs, device_inputs
                gc.collect()
                torch.cuda.empty_cache()
                self.logger.debug("🧹 已清理生成后的GPU内存")
            
            # 解析坐标
            coordinates = self._parse_coordinates(response_text)
            
            # 构建元数据
            metadata = {
                "model": "GLM-4.1V-9B-Base",
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
                import gc
                gc.collect()
                torch.cuda.empty_cache()
                self.logger.debug("🧹 异常后已清理GPU内存")
            
            return None, str(e), {"error": str(e)}
    
    def _parse_coordinates(self, response_text: str) -> Optional[Tuple[int, int]]:
        """
        从响应文本中解析坐标
        
        支持多种格式:
        - click(start_box='(x, y)')  # 标准格式
        - click(start_box="x, y")     # 无括号格式
        - [x, y]
        - (x, y)
        - x, y
        - click at x, y
        - coordinates: x, y
        """
        self.logger.debug(f"🔍 解析坐标，输入文本: {response_text[:200]}...")
        
        # 优先匹配 click(start_box='...') 格式 - 包含引号内的各种格式
        # 先尝试匹配带引号的格式（引号内可能有或没有括号）
        click_match = re.search(
            r'click\s*\(\s*start_box\s*=\s*[\'"]([^\'"]*)[\'"]\\s*\)',
            response_text,
            re.IGNORECASE
        )
        if click_match:
            coord_str = click_match.group(1).strip()
            # 从字符串中提取数字（可能有括号也可能没有）
            numbers = re.findall(r'(\d+)\s*,\s*(\d+)', coord_str)
            if numbers:
                coords = (int(numbers[0][0]), int(numbers[0][1]))
                self.logger.info(f"✅ 解析click(start_box='...') 格式: {coords}")
                return coords
        
        # 匹配 click(start_box=(x,y)) 格式（无引号）
        click_match2 = re.search(
            r'click\s*\(\s*start_box\s*=\s*\((\d+)\s*,\s*(\d+)\)\s*\)',
            response_text,
            re.IGNORECASE
        )
        if click_match2:
            coords = (int(click_match2.group(1)), int(click_match2.group(2)))
            self.logger.info(f"✅ 解析click(start_box=(...)) 格式: {coords}")
            return coords
        
        # 匹配 scroll(start_box='(x, y)', direction='...') 格式
        scroll_match = re.search(
            r'scroll\s*\(\s*start_box\s*=\s*[\'"]?\s*\((\d+)\s*,\s*(\d+)\)\s*[\'"]?',
            response_text,
            re.IGNORECASE
        )
        if scroll_match:
            coords = (int(scroll_match.group(1)), int(scroll_match.group(2)))
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
            if hasattr(self, 'tokenizer'):
                del self.tokenizer
            
            torch.cuda.empty_cache()
            self.logger.info("🧹 GLM-4V资源已清理")
        except Exception as e:
            self.logger.warning(f"⚠️ 清理资源时出错: {e}")
    
    def __del__(self):
        """析构函数"""
        self.cleanup()


# 用于测试的简单示例
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # 初始化模型
    wrapper = GLM4VWrapper()
    
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
