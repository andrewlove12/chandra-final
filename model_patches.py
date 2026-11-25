"""Small, exact-math model patches for GPU backends with gaps.

PatchLinear: the vision encoder's patch embedding is a Conv3d whose kernel
equals its stride (non-overlapping patches). On AMD ROCm for Windows the
MIOpen kernel for it fails to launch ("hipErrorInvalidValue"). For inputs that
are exactly one patch - which is how Qwen3.5's patch embed calls it - that
convolution is just a matrix multiply, so we compute it as one.
"""
import torch.nn.functional as F
from torch import nn


class PatchLinear(nn.Module):
    def __init__(self, conv: nn.Conv3d):
        super().__init__()
        self.conv = conv
        self.kernel = tuple(conv.kernel_size)

    # Callers inspect e.g. `proj.weight.dtype`.
    @property
    def weight(self):
        return self.conv.weight

    @property
    def bias(self):
        return self.conv.bias

    def forward(self, x):
        if tuple(x.shape[2:]) != self.kernel:  # not one-patch-per-row: use the real conv
            return self.conv(x)
        w = self.conv.weight
        out = F.linear(x.reshape(x.shape[0], -1), w.reshape(w.shape[0], -1), self.conv.bias)
        return out.view(x.shape[0], w.shape[0], 1, 1, 1)


def replace_patch_convs(model: nn.Module) -> int:
    """Swap eligible Conv3d layers (kernel == stride, no padding/dilation/groups) for PatchLinear."""
    count = 0
    for name, mod in list(model.named_modules()):
        if (isinstance(mod, nn.Conv3d) and tuple(mod.kernel_size) == tuple(mod.stride)
                and all(p == 0 for p in mod.padding) and all(d == 1 for d in mod.dilation) and mod.groups == 1):
            parent_name, _, child = name.rpartition(".")
            setattr(model.get_submodule(parent_name) if parent_name else model, child, PatchLinear(mod))
            count += 1
    return count
