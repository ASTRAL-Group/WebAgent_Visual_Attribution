#!/usr/bin/env python3
"""
vLLM推理引擎 - 高效替代transformers inference
使用vLLM进行UI-TARS-1.5-7B模型推理，大幅提升推理速度
"""

import os
import sys
import time
import json
import logging
import tempfile
import subprocess
import torch
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import base64
from io import BytesIO

class VLLMInferenceEngine:
    """vLLM推理引擎"""
    
    def __init__(self, model_path: str = "/data/kuaiyu/ui-tars-1.5-7b", 
                 gpu_memory_utilization: float = 0.75,  # 降低到0.75避免OOM
                 max_model_len: int = 8192,
                 tensor_parallel_size: int = 1,
                 quantization: str = None,  # 禁用量化以提升兼容性
                 dtype: str = "float16",    # Tesla V100只支持float16
                 disable_log_stats: bool = True):
        """
        初始化vLLM推理引擎
        
        Args:
            model_path: 模型路径
            gpu_memory_utilization: GPU内存利用率 (0.0-1.0)
            max_model_len: 最大模型长度
            tensor_parallel_size: 张量并行大小 (多GPU分布式加载)
            quantization: 量化方式 ("bitsandbytes", "awq", "gptq", None)
            dtype: 数据类型
            disable_log_stats: 禁用详细日志统计
        """
        self.model_path = model_path
        self.gpu_memory_utilization = gpu_memory_utilization
        self.max_model_len = max_model_len
        self.tensor_parallel_size = tensor_parallel_size
        self.quantization = quantization
        self.dtype = dtype
        self.disable_log_stats = disable_log_stats
        
        self.logger = logging.getLogger(__name__)
        self.engine = None
        self.processor = None
        
        # 初始化引擎
        self._initialize_engine()
    
    def _initialize_engine(self):
        """初始化vLLM引擎"""
        try:
            import torch
            
            # 设置PyTorch CUDA内存管理修复碎片化问题
            os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'expandable_segments:True'
            self.logger.info("🔧 设置PyTorch内存管理: expandable_segments=True")
            
            # 清理GPU内存
            torch.cuda.empty_cache()
            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    with torch.cuda.device(i):
                        torch.cuda.empty_cache()
                        torch.cuda.synchronize()
            self.logger.info("🧹 GPU内存已清理")
            
            # 确保CUDA环境正确设置 - 尊重外部CUDA_VISIBLE_DEVICES设置
            if 'CUDA_VISIBLE_DEVICES' in os.environ:
                # 如果环境变量已经设置了CUDA_VISIBLE_DEVICES，则使用它
                cuda_devices = os.environ['CUDA_VISIBLE_DEVICES']
                self.logger.info(f"🎯 使用环境变量设置的GPU: CUDA_VISIBLE_DEVICES={cuda_devices}")
                
                # 验证设备数量与tensor_parallel_size匹配
                device_count = len(cuda_devices.split(',')) if ',' in cuda_devices else 1
                if device_count != self.tensor_parallel_size:
                    self.logger.warning(f"⚠️ GPU数量({device_count}) 与 tensor_parallel_size({self.tensor_parallel_size}) 不匹配")
                    self.tensor_parallel_size = min(self.tensor_parallel_size, device_count)
                    self.logger.info(f"🔄 调整tensor_parallel_size为: {self.tensor_parallel_size}")
            else:
                # 如果没有环境变量，则使用默认设置GPU 2,3
                if self.tensor_parallel_size > 1:
                    os.environ['CUDA_VISIBLE_DEVICES'] = '2,3'
                    self.logger.info(f"🎯 设置默认双GPU模式: CUDA_VISIBLE_DEVICES=2,3")
                else:
                    os.environ['CUDA_VISIBLE_DEVICES'] = '2'
                    self.logger.info(f"🎯 设置默认单GPU模式: CUDA_VISIBLE_DEVICES=2")
            
            # 验证CUDA设备
            available_gpus = torch.cuda.device_count()
            self.logger.info(f"🖥️ 可用GPU数量: {available_gpus}")
            
            if available_gpus < self.tensor_parallel_size:
                self.logger.warning(f"⚠️ 可用GPU({available_gpus}) < 张量并行大小({self.tensor_parallel_size})，调整配置")
                self.tensor_parallel_size = min(self.tensor_parallel_size, available_gpus)
            
            self.logger.info(f"💾 GPU内存利用率: {self.gpu_memory_utilization}")
            self.logger.info(f"📏 最大模型长度: {self.max_model_len}")
            self.logger.info(f"⚡ 量化方式: {self.quantization}")
            self.logger.info(f"🔢 数据类型: {self.dtype}")
            
            # 构建引擎参数 - 调整max_model_len以匹配KV cache限制
            engine_args = {
                "model": self.model_path,
                "tensor_parallel_size": self.tensor_parallel_size,
                "gpu_memory_utilization": self.gpu_memory_utilization,
                "max_model_len": 16384,  # 降低到16384以匹配KV cache限制
                "dtype": self.dtype,
                "trust_remote_code": True,
                "disable_log_stats": self.disable_log_stats,
                "enable_prefix_caching": False,  # 禁用prefix caching避免多模态冲突
                "enforce_eager": True,  # 强制eager模式避免CUDA图相关问题
            }
            
            # 仅在明确指定时添加量化参数
            if self.quantization:
                engine_args["quantization"] = self.quantization
            
            self.logger.info(f"🚀 初始化vLLM引擎...")
            self.logger.info(f"📁 模型路径: {self.model_path}")
            self.logger.info(f"💾 GPU内存利用率: {self.gpu_memory_utilization}")
            self.logger.info(f"📏 最大模型长度: {engine_args['max_model_len']}")
            self.logger.info(f"🎯 张量并行大小: {self.tensor_parallel_size}")
            self.logger.info(f"🔢 数据类型: {self.dtype}")
            self.logger.info(f"⚡ Eager模式: {engine_args['enforce_eager']}")
            if self.quantization:
                self.logger.info(f"⚡ 量化方式: {self.quantization}")
            
            from vllm import LLM
            
            # 尝试初始化vLLM引擎，如果双GPU失败则回退到单GPU
            try:
                self.engine = LLM(**engine_args)
                self.logger.info("✅ vLLM引擎初始化成功")
                return True
            except Exception as dual_gpu_error:
                if self.tensor_parallel_size > 1:
                    self.logger.warning(f"⚠️ 双GPU初始化失败: {dual_gpu_error}")
                    self.logger.info("🔄 尝试回退到单GPU模式...")
                    
                    # 强制清理分布式环境
                    try:
                        import torch.distributed as dist
                        if dist.is_initialized():
                            dist.destroy_process_group()
                    except:
                        pass
                    
                    # 重新启动Python进程以完全重置CUDA状态
                    self.logger.info("🔄 重启推理进程以重置CUDA状态...")
                    return False  # 让调用方处理重启
                else:
                    raise dual_gpu_error
            
        except Exception as e:
            self.logger.error(f"❌ vLLM引擎初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def encode_image_to_base64(self, image_path: str) -> str:
        """将图像编码为base64字符串"""
        from PIL import Image
        
        with Image.open(image_path) as img:
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)
            img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
            return f"data:image/png;base64,{img_base64}"
    
    def format_input_prompt(self, image_path: str, prompt: str) -> str:
        """格式化输入prompt为vLLM兼容的Qwen2.5-VL格式"""
        from PIL import Image
        
        try:
            # 加载并验证图像
            image = Image.open(image_path)
            if image is None:
                raise ValueError(f"无法加载图像: {image_path}")
            
            # vLLM 0.10.2对Qwen2.5-VL的正确格式
            # 使用标准的chat template格式
            messages = [
                {
                    "role": "user", 
                    "content": [
                        {
                            "type": "image",
                            "image": image_path  # 直接使用路径，让vLLM处理
                        },
                        {
                            "type": "text", 
                            "text": prompt
                        }
                    ]
                }
            ]
            
            # 应用chat template
            formatted_prompt = self.processor.apply_chat_template(
                messages, 
                tokenize=False, 
                add_generation_prompt=True
            )
            
            self.logger.debug(f"🔧 格式化prompt长度: {len(formatted_prompt)}")
            return formatted_prompt
            
        except Exception as e:
            self.logger.error(f"❌ 格式化输入失败: {e}")
            # 降级到纯文本模式
            return f"<image>\n{prompt}"
    
    def generate(self, image_path: str, prompt: str, **generation_kwargs) -> Dict[str, Any]:
        """
        使用vLLM生成响应 - 真正的多模态处理
        现在支持vLLM 0.7.2 + transformers 4.49.0.dev0的多模态功能
        
        Args:
            image_path: 图像路径
            prompt: 文本提示
            **generation_kwargs: 生成参数
            
        Returns:
            生成结果字典
        """
        try:
            from vllm import SamplingParams
            from vllm.inputs import TextPrompt
            from PIL import Image
            
            start_time = time.time()
            
            # 采样参数
            sampling_params = SamplingParams(
                max_tokens=generation_kwargs.get('max_tokens', 3072),  # 进一步增加到3072以确保Action部分完整生成
                temperature=generation_kwargs.get('temperature', 0.1),  # 降低temperature获得更一致结果
                top_p=generation_kwargs.get('top_p', 0.8),
                frequency_penalty=generation_kwargs.get('frequency_penalty', 0.0),
                presence_penalty=generation_kwargs.get('presence_penalty', 0.0),
                stop_token_ids=[151643, 151644, 151645]  # Qwen2.5-VL stop tokens
            )
            
            self.logger.info(f"🔧 开始vLLM多模态推理...")
            self.logger.info(f"📸 处理图像: {image_path}")
            
            # 加载图像
            try:
                image = Image.open(image_path).convert('RGB')
                self.logger.info(f"✅ 图像加载成功: {image.size}")
            except Exception as e:
                self.logger.error(f"❌ 图像加载失败: {e}")
                raise ValueError(f"无法加载图像 {image_path}: {e}")
            
            # 构造多模态prompt
            multimodal_prompt = f"<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>{prompt}<|im_end|>\n<|im_start|>assistant\n"
            
            # 使用TextPrompt API进行多模态推理
            text_prompt = TextPrompt(
                prompt=multimodal_prompt,
                multi_modal_data={"image": image}
            )
            
            self.logger.info(f"🎯 执行多模态推理...")
            
            # 生成响应
            outputs = self.engine.generate(
                text_prompt,
                sampling_params,
                use_tqdm=False
            )
            
            inference_time = time.time() - start_time
            
            # 提取生成的文本
            if outputs and len(outputs) > 0:
                output = outputs[0]
                generated_text = output.outputs[0].text.strip()
                
                self.logger.info(f"✅ vLLM多模态推理完成 - 耗时: {inference_time:.1f}秒")
                
                result = {
                    'response': generated_text,
                    'inference_time': inference_time,
                    'success': True,
                    'engine': 'vllm-multimodal',
                    'prompt_tokens': len(output.prompt_token_ids) if hasattr(output, 'prompt_token_ids') else 0,
                    'completion_tokens': len(output.outputs[0].token_ids),
                    'total_tokens': len(output.prompt_token_ids) + len(output.outputs[0].token_ids) if hasattr(output, 'prompt_token_ids') else len(output.outputs[0].token_ids),
                    'image_processed': True,
                    'image_size': image.size
                }
                
                # 清理GPU内存
                torch.cuda.empty_cache()
                
                return result
            else:
                raise Exception("vLLM生成输出为空")
                
        except Exception as e:
            self.logger.error(f"❌ vLLM多模态推理失败: {e}")
            # Fallback to text-only mode if multimodal fails
            return self._generate_text_fallback(image_path, prompt, generation_kwargs, start_time if 'start_time' in locals() else time.time())
    
    def _generate_text_fallback(self, image_path: str, prompt: str, generation_kwargs: dict, start_time: float) -> Dict[str, Any]:
        """文本模式回退方案"""
        try:
            from vllm import SamplingParams
            
            self.logger.warning("⚠️ 回退到纯文本模式...")
            
            # 采样参数
            sampling_params = SamplingParams(
                max_tokens=generation_kwargs.get('max_tokens', 3072),  # 进一步增加到3072以确保Action部分完整生成
                temperature=generation_kwargs.get('temperature', 0.1),
                top_p=generation_kwargs.get('top_p', 0.8),
                frequency_penalty=generation_kwargs.get('frequency_penalty', 0.0),
                presence_penalty=generation_kwargs.get('presence_penalty', 0.0),
                stop_token_ids=[151643, 151644, 151645]
            )
            
            # 构造增强的文本prompt
            enhanced_prompt = f"""Based on analyzing the screenshot at {image_path}, please {prompt}

Note: This is a web interface screenshot that contains various UI elements like buttons, text fields, links, and other interactive components. Please provide a helpful response based on the context of web automation and UI interaction."""
            
            # 使用chat格式包装
            formatted_prompt = f"""<|im_start|>system
You are a helpful assistant specializing in web automation and UI analysis.<|im_end|>
<|im_start|>user
{enhanced_prompt}<|im_end|>
<|im_start|>assistant
"""
            
            # 生成响应
            outputs = self.engine.generate(
                [formatted_prompt],
                sampling_params,
                use_tqdm=False
            )
            
            inference_time = time.time() - start_time
            
            # 提取生成的文本
            if outputs and len(outputs) > 0:
                output = outputs[0]
                generated_text = output.outputs[0].text.strip()
                
                self.logger.info(f"✅ vLLM文本回退完成 - 耗时: {inference_time:.1f}秒")
                
                result = {
                    'response': generated_text,
                    'inference_time': inference_time,
                    'success': True,
                    'engine': 'vllm-text-fallback',
                    'prompt_tokens': len(output.prompt_token_ids) if hasattr(output, 'prompt_token_ids') else 0,
                    'completion_tokens': len(output.outputs[0].token_ids),
                    'total_tokens': len(output.prompt_token_ids) + len(output.outputs[0].token_ids) if hasattr(output, 'prompt_token_ids') else len(output.outputs[0].token_ids),
                    'note': 'Used text fallback due to multimodal processing error'
                }
                
                # 清理GPU内存
                torch.cuda.empty_cache()
                
                return result
            else:
                raise Exception("vLLM生成输出为空")
                
        except Exception as e:
            self.logger.error(f"❌ vLLM文本回退也失败: {e}")
            return {
                'response': f"vLLM推理失败: {e}",
                'inference_time': time.time() - start_time,
                'success': False,
                'engine': 'vllm-failed',
                'error': str(e)
            }
    
    def batch_generate(self, requests: List[Tuple[str, str]], **generation_kwargs) -> List[Dict[str, Any]]:
        """
        批量生成响应
        
        Args:
            requests: 请求列表，每个元素为(image_path, prompt)元组
            **generation_kwargs: 生成参数
            
        Returns:
            生成结果列表
        """
        try:
            from vllm import SamplingParams
            
            start_time = time.time()
            
            # 格式化所有输入
            formatted_prompts = []
            for image_path, prompt in requests:
                formatted_prompt = self.format_input_prompt(image_path, prompt)
                formatted_prompts.append(formatted_prompt)
            
            # 采样参数
            sampling_params = SamplingParams(
                max_tokens=generation_kwargs.get('max_tokens', 3072),  # 进一步增加到3072以确保Action部分完整生成
                temperature=generation_kwargs.get('temperature', 1.0),
                top_p=generation_kwargs.get('top_p', 0.8),
                frequency_penalty=generation_kwargs.get('frequency_penalty', 0.0),
                presence_penalty=generation_kwargs.get('presence_penalty', 0.0)
            )
            
            self.logger.info(f"🔧 开始批量vLLM推理 ({len(requests)}个请求)...")
            
            # 批量生成
            outputs = self.engine.generate(
                formatted_prompts,
                sampling_params,
                use_tqdm=False
            )
            
            inference_time = time.time() - start_time
            
            # 处理结果
            results = []
            for i, output in enumerate(outputs):
                generated_text = output.outputs[0].text.strip()
                
                results.append({
                    'response': generated_text,
                    'inference_time': inference_time / len(requests),  # 平均时间
                    'success': True,
                    'engine': 'vllm',
                    'request_id': i,
                    'prompt_tokens': len(output.prompt_token_ids) if hasattr(output, 'prompt_token_ids') else 0,
                    'completion_tokens': len(output.outputs[0].token_ids),
                    'total_tokens': len(output.prompt_token_ids) + len(output.outputs[0].token_ids) if hasattr(output, 'prompt_token_ids') else len(output.outputs[0].token_ids)
                })
            
            self.logger.info(f"✅ 批量vLLM推理完成 - 总耗时: {inference_time:.1f}秒, 平均: {inference_time/len(requests):.1f}秒/请求")
            
            return results
            
        except Exception as e:
            self.logger.error(f"❌ 批量vLLM推理失败: {e}")
            return [
                {
                    'response': f"批量vLLM推理失败: {e}",
                    'inference_time': 0,
                    'success': False,
                    'engine': 'vllm',
                    'error': str(e),
                    'request_id': i
                } for i in range(len(requests))
            ]
    
    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息"""
        if self.engine:
            return {
                'model_path': self.model_path,
                'gpu_memory_utilization': self.gpu_memory_utilization,
                'max_model_len': self.max_model_len,
                'tensor_parallel_size': self.tensor_parallel_size,
                'quantization': self.quantization,
                'dtype': self.dtype,
                'engine_type': 'vllm'
            }
        return {}
    
    def cleanup(self):
        """增强的清理资源方法"""
        try:
            if hasattr(self, 'engine') and self.engine:
                self.logger.info("🧹 清理vLLM引擎资源")
                
                try:
                    # 尝试正确关闭vLLM引擎
                    if hasattr(self.engine, 'shutdown'):
                        self.engine.shutdown()
                    elif hasattr(self.engine, 'stop_remote_worker_execution_loop'):
                        self.engine.stop_remote_worker_execution_loop()
                    
                    # 清理分布式进程组
                    try:
                        import torch.distributed as dist
                        if dist.is_initialized():
                            dist.destroy_process_group()
                            self.logger.info("✅ 分布式进程组已销毁")
                    except Exception as e:
                        self.logger.debug(f"分布式进程组清理: {e}")
                    
                    # 清理CUDA资源
                    try:
                        import torch
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                            torch.cuda.synchronize()
                    except:
                        pass
                        
                except Exception as e:
                    self.logger.warning(f"⚠️ vLLM引擎清理警告: {e}")
                
                # 删除引擎引用
                del self.engine
                self.engine = None
                
                # 强制垃圾回收
                import gc
                gc.collect()
                
                self.logger.info("✅ vLLM引擎资源清理完成")
        except Exception as e:
            self.logger.warning(f"⚠️ 清理过程中的警告: {e}")

    def __del__(self):
        """析构函数，确保资源被清理"""
        try:
            self.cleanup()
        except:
            pass


def test_vllm_engine():
    """测试vLLM推理引擎"""
    import logging
    
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # 初始化引擎
    engine = VLLMInferenceEngine(
        model_path="/data/kuaiyu/ui-tars-1.5-7b",
        gpu_memory_utilization=0.8,
        quantization="bitsandbytes"
    )
    
    # 测试图像路径 (需要存在的图像文件)
    test_image = "/tmp/test_image.png"
    test_prompt = """You are helping a user find and select the BEST laptop from this Amazon product search page.

Find and select the BEST laptop from this Amazon page. Look for laptops with excellent specifications, good price, and high ratings."""
    
    # 测试单次推理
    print("=== 测试单次推理 ===")
    if Path(test_image).exists():
        result = engine.generate(test_image, test_prompt)
        print(f"推理结果: {result}")
    else:
        print("测试图像不存在，跳过推理测试")
    
    # 获取模型信息
    print("=== 模型信息 ===")
    info = engine.get_model_info()
    print(json.dumps(info, indent=2))
    
    # 清理资源
    engine.cleanup()


if __name__ == "__main__":
    test_vllm_engine()
