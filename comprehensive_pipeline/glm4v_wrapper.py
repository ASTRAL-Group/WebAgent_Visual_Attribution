#!/usr/bin/env python3


import torch
from PIL import Image
from transformers import AutoTokenizer, AutoModel
import json
import re
from typing import Dict, List, Any, Tuple, Optional
import logging


class GLM4VWrapper:
    
    
    def __init__(
        self,
        model_path: str = "/data/kuaiyu/GLM-4.1V-9B-Base",
        device_map: str = "auto",
        dtype: torch.dtype = torch.bfloat16,
        max_new_tokens: int = 512,
        max_memory: Optional[Dict[int, str]] = None,
        logger: Optional[logging.Logger] = None
    ):
       
        self.model_path = model_path
        self.device_map = device_map
        self.dtype = dtype
        self.max_new_tokens = max_new_tokens
        self.max_memory = max_memory
        self.logger = logger or logging.getLogger(__name__)
        
        self.logger.info(f"🚀 Load GLM-4.1V-9B-Base: {model_path}")
        if max_memory:
            self.logger.info(f"💾 GPU Limit: {max_memory}")
        
        # 加载tokenizer和model
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                model_path,
                trust_remote_code=True
            )
            self.logger.info("✅ Tokenizer Loading Success")
            
            
            model_kwargs = {
                "torch_dtype": dtype,
                "device_map": device_map,
                "trust_remote_code": True
            }
            
            
            if max_memory:
                model_kwargs["max_memory"] = max_memory
            
            self.model = AutoModel.from_pretrained(
                model_path,
                **model_kwargs
            )
            self.logger.info("✅ Model load success")
            self.logger.info(f"📊 Model type: {type(self.model).__name__}")
            self.logger.info(f"💾 Data type: {dtype}")
            self.logger.info(f"🎯 Device mapping: {device_map}")
            
            # 显示实际的设备分布
            if hasattr(self.model, 'hf_device_map'):
                self.logger.info(f"🗺️ Real device mapping: {self.model.hf_device_map}")
            elif hasattr(self.model, 'device'):
                self.logger.info(f"🔧 Model device: {self.model.device}")
            
        except Exception as e:
            self.logger.error(f"❌ Model loading failure: {e}")
            raise
    
    def generate_click_coordinates(
        self,
        image_path: str,
        prompt: str,
        bbox_info: Optional[str] = None
    ) -> Tuple[Optional[Tuple[int, int]], str, Dict[str, Any]]:
       
        try:
           
            image = Image.open(image_path).convert("RGB")
            
            
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
            
           
            inputs = self.tokenizer.apply_chat_template(
                [{"role": "user", "image": image, "content": detailed_prompt}],
                add_generation_prompt=True,
                tokenize=True,
                return_tensors="pt",
                return_dict=True
            )
            
           
            device = next(self.model.parameters()).device
            device_inputs = {}
            for k, v in inputs.items():
                if isinstance(v, torch.Tensor):
                    device_inputs[k] = v.to(device)
                else:
                    device_inputs[k] = v
            
            
            gen_kwargs = {
                "max_new_tokens": self.max_new_tokens,
                "do_sample": True,  
                "temperature": 1.0,  
                "top_p": 0.8  
            }
            
            # 生成响应
            self.logger.debug("🔮 Response...")
            
            
            if torch.cuda.is_available():
                import gc
                gc.collect()
                torch.cuda.empty_cache()
            
            with torch.no_grad():
                outputs = self.model.generate(**device_inputs, **gen_kwargs)
                
                outputs = outputs[:, device_inputs['input_ids'].shape[1]:]
                response_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
            
            self.logger.debug(f"🤖 GLM-4V Response: {response_text}")
            
            
            if torch.cuda.is_available():
                import gc
                del outputs, device_inputs
                gc.collect()
                torch.cuda.empty_cache()
                self.logger.debug("🧹 Cleaning up GPU")
            
            
            coordinates = self._parse_coordinates(response_text)
            
            
            metadata = {
                "model": "GLM-4.1V-9B-Base",
                "raw_response": response_text,
                "has_coordinates": coordinates is not None,
                "prompt_length": len(prompt),
                "image_size": image.size
            }
            
            return coordinates, response_text, metadata
            
        except Exception as e:
            self.logger.error(f"❌ Generation Failure: {e}")
            import traceback
            traceback.print_exc()
            
            if torch.cuda.is_available():
                import gc
                gc.collect()
                torch.cuda.empty_cache()
                self.logger.debug("🧹 Exception GPU cleanup")
            
            return None, str(e), {"error": str(e)}
    
    def _parse_coordinates(self, response_text: str) -> Optional[Tuple[int, int]]:
       
        self.logger.debug(f"🔍 Text: {response_text[:200]}...")
        
        click_match = re.search(
            r'click\s*\(\s*start_box\s*=\s*[\'"]([^\'"]*)[\'"]\\s*\)',
            response_text,
            re.IGNORECASE
        )
        if click_match:
            coord_str = click_match.group(1).strip()
        
            numbers = re.findall(r'(\d+)\s*,\s*(\d+)', coord_str)
            if numbers:
                coords = (int(numbers[0][0]), int(numbers[0][1]))
                self.logger.info(f"✅ click(start_box='...') : {coords}")
                return coords
        
     
        click_match2 = re.search(
            r'click\s*\(\s*start_box\s*=\s*\((\d+)\s*,\s*(\d+)\)\s*\)',
            response_text,
            re.IGNORECASE
        )
        if click_match2:
            coords = (int(click_match2.group(1)), int(click_match2.group(2)))
            self.logger.info(f"✅ click(start_box=(...)) : {coords}")
            return coords
        
  
        scroll_match = re.search(
            r'scroll\s*\(\s*start_box\s*=\s*[\'"]?\s*\((\d+)\s*,\s*(\d+)\)\s*[\'"]?',
            response_text,
            re.IGNORECASE
        )
        if scroll_match:
            coords = (int(scroll_match.group(1)), int(scroll_match.group(2)))
            self.logger.info(f"✅ scroll(start_box=...) : {coords}")
            return coords
        
      
        pattern1 = r'\[(\d+),\s*(\d+)\]'
        match = re.search(pattern1, response_text)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ [x, y]: {coords}")
            return coords
        
  
        pattern2a = r'Action:.*?\((\d+)\s*,\s*(\d+)\)'
        match = re.search(pattern2a, response_text, re.DOTALL | re.IGNORECASE)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ Action(x, y): {coords}")
            return coords
        
      
        pattern2 = r'\((\d+)\s*,\s*(\d+)\)'
        match = re.search(pattern2, response_text)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ (x, y): {coords}")
            return coords
        
   
        pattern3 = r'(\d+)\s*,\s*(\d+)'
        match = re.search(pattern3, response_text)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ x, y: {coords}")
            return coords
        

        pattern4 = r'click\s+at\s+(\d+)\s*,\s*(\d+)'
        match = re.search(pattern4, response_text, re.IGNORECASE)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ click at: {coords}")
            return coords
        

        pattern5 = r'coordinates?:\s*(\d+)\s*,\s*(\d+)'
        match = re.search(pattern5, response_text, re.IGNORECASE)
        if match:
            coords = (int(match.group(1)), int(match.group(2)))
            self.logger.info(f"✅ coordinates: {coords}")
            return coords
        
        self.logger.warning(f"⚠️ Coordinate Extraction failure")
        return None
    
    def cleanup(self):
        try:
            if hasattr(self, 'model'):
                del self.model
            if hasattr(self, 'tokenizer'):
                del self.tokenizer
            
            torch.cuda.empty_cache()
            self.logger.info("🧹 GLM-4V Clean up")
        except Exception as e:
            self.logger.warning(f"⚠️ Clean up exception: {e}")
    
    def __del__(self):
        self.cleanup()



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
  
    wrapper = GLM4VWrapper()
    

    test_image = "/data/sanity_check/button/screenshots/button_large_page-top.png"
    test_prompt = "Click on the red button with the text '●'."
    
    coords, response, metadata = wrapper.generate_click_coordinates(
        test_image,
        test_prompt
    )
    
    print(f"\n📍 Coordinate: {coords}")
    print(f"📝 Response: {response}")
    print(f"📊 Metadata: {json.dumps(metadata, indent=2)}")
