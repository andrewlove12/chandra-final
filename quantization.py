"""Optional int8 weight-only quantization for Chandra OCR 2.

Plain PyTorch (no bitsandbytes/torchao), so it works the same on NVIDIA CUDA,
AMD ROCm (incl. Windows) and CPU. Weights are stored as int8 with one bf16
scale per output channel (symmetric absmax); activations stay bf16.

Only the language model's large Linear layers are quantized (~3.6B params).
The vision encoder, embeddings / lm_head (tied) and the tiny linear-attention
gate projections stay in bf16: they are small and the most precision-sensitive.
Model size goes from ~10.3 GB to ~6 GB.
"""
import torch
import torch.nn.functional as F
from torch import nn

QUANTIZED_PREFIX = "model.language_model."
SKIP_SUFFIXES = ("in_proj_a", "in_proj_b")


class Int8Linear(nn.Module):
    def __init__(self, linear: nn.Linear):
        super().__init__()
        w = linear.weight.detach().float()
        scale = (w.abs().amax(dim=1) / 127.0).clamp(min=1e-8)
        self.register_buffer("qweight", torch.round(w / scale[:, None]).clamp(-127, 127).to(torch.int8))
        self.register_buffer("scale", scale.to(linear.weight.dtype))
        self.bias = None if linear.bias is None else nn.Parameter(linear.bias.detach(), requires_grad=False)
        self.in_features, self.out_features = linear.in_features, linear.out_features

    def forward(self, x):
        # Per-output-channel scale can be applied after the matmul.
        out = F.linear(x, self.qweight.to(x.dtype)) * self.scale
        return out if self.bias is None else out + self.bias


def quantize_int8(model: nn.Module) -> int:
    """Replace eligible Linear layers in-place. Returns number of params quantized."""
    targets = [
        (name, mod) for name, mod in model.named_modules()
        if isinstance(mod, nn.Linear) and name.startswith(QUANTIZED_PREFIX) and not name.endswith(SKIP_SUFFIXES)
    ]
    count = 0
    for name, mod in targets:
        parent_name, _, child = name.rpartition(".")
        setattr(model.get_submodule(parent_name), child, Int8Linear(mod))
        count += mod.weight.numel()
    return count


def model_bytes(model: nn.Module) -> int:
    return sum(t.numel() * t.element_size() for t in list(model.parameters()) + list(model.buffers()))
