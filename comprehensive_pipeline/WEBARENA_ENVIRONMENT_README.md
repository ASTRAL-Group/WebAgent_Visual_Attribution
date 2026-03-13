# WebArena Environment Setup Guide

This directory contains complete environment configuration files for the WebArena project.

## File Descriptions

1. **webarena_environment.yml** - Complete Conda environment configuration (includes all dependencies)
2. **webarena_pip_requirements_in_env.txt** - Complete pip package list
3. **webarena_key_requirements.txt** - Core dependencies (simplified version)
4. **setup_webarena_environment.sh** - Automated installation script

## Quick Start

### Method 1: Using Automated Installation Script (Recommended)

```bash
# 1. Ensure all configuration files are in the same directory
cd /data/kuaiyu

# 2. Run the installation script
bash setup_webarena_environment.sh

# 3. Activate the environment
conda activate webarena
```

### Method 2: Manual Installation

```bash
# 1. Create environment from YAML file
conda env create -f webarena_environment.yml

# 2. Activate the environment
conda activate webarena

# 3. Verify installation
python -c "import torch; print(f'PyTorch: {torch.__version__}')"
python -c "import vllm; print(f'vLLM: {vllm.__version__}')"
```

### Method 3: Install Core Dependencies Only

```bash
# 1. Create base environment
conda create -n webarena python=3.10 -y
conda activate webarena

# 2. Install core dependencies
pip install -r webarena_key_requirements.txt

# 3. Install CUDA version of PyTorch (if needed)
pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu124
```

## Key Dependency Versions

| Package | Version | Description |
|---------|---------|-------------|
| Python | 3.10 | Base Python version |
| PyTorch | 2.5.1 | Deep learning framework (CUDA 12.4) |
| Transformers | 4.49.0 | HuggingFace model library |
| vLLM | 0.7.3 | High-performance inference engine |
| Pillow | 11.3.0 | Image processing |
| OpenCV | 4.12.0 | Computer vision |

##  UI-TARS Model Configuration

### Model Path
```
/data/kuaiyu/ui-tars-1.5-7b/
```

### Critical Configuration Modifications
In `config.json`:
```json
{
  "rope_scaling": {
    "rope_type": "mrope",
    "mrope_section": [16, 24, 24]
  }
}
```

### vLLM Dual-GPU Configuration
```python
from vllm import LLM

llm = LLM(
    model="/data/kuaiyu/ui-tars-1.5-7b",
    tensor_parallel_size=2,  # Use 2 GPUs
    gpu_memory_utilization=0.75,
    max_model_len=16384,
    dtype="float16",
    trust_remote_code=True,
    enforce_eager=True
)
```

##  Common Issues

### 1. NCCL Timeout Error
If you encounter GPU communication timeout, set environment variables:
```bash
export NCCL_TIMEOUT=1800000  # 30 minutes
export NCCL_ASYNC_ERROR_HANDLING=1
```

### 2. CUDA Version Mismatch
Check CUDA version:
```bash
nvidia-smi
```
Ensure PyTorch CUDA version is compatible with system CUDA version.

### 3. vLLM Out of Memory
Reduce `gpu_memory_utilization` or `max_model_len`:
```python
gpu_memory_utilization=0.6  # Reduce from 0.75 to 0.6
max_model_len=8192  # Reduce from 16384 to 8192
```

##  Environment Variables

Recommended additions to `~/.bashrc`:
```bash
# CUDA Configuration
export CUDA_VISIBLE_DEVICES=0,1  # Specify available GPUs

# NCCL Configuration
export NCCL_TIMEOUT=1800000
export NCCL_ASYNC_ERROR_HANDLING=1

# vLLM Configuration
export VLLM_LOGGING_LEVEL=INFO
```

## 🔍 Verify Installation

Run the following commands to verify your environment:
```bash
python << 'EOF'
import sys
import torch
import transformers
import vllm
from PIL import Image

print(f"Python: {sys.version}")
print(f"PyTorch: {torch.__version__}")
print(f"CUDA Available: {torch.cuda.is_available()}")
print(f"CUDA Devices: {torch.cuda.device_count()}")
print(f"Transformers: {transformers.__version__}")
print(f"vLLM: {vllm.__version__}")
print(f"Pillow: {Image.__version__}")
EOF
```

##  GPU Requirements

- **Minimum**: 2 x NVIDIA GPUs, at least 12GB VRAM each
- **Recommended**: 2 x NVIDIA A100/A6000 (40GB+)
- **CUDA Version**: 12.1 or higher

## 🔗 Related Resources

- [UI-TARS Paper](https://arxiv.org/abs/2410.17465)
- [vLLM Documentation](https://docs.vllm.ai/)
- [WebArena Project](https://github.com/web-arena-x/webarena)

##  Last Updated

Last Update: 2025-12-12
