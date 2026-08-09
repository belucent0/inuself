"""Initialize the WSL/DXG ROCm device before vLLM imports."""

import torch


torch.cuda.init()
torch.cuda.get_device_name(0)
