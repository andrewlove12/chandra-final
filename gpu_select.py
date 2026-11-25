"""Pick which GPUs PyTorch should see, before torch is imported in the main process.

Why: on AMD ROCm (Windows), having an unsupported GPU visible - typically the
CPU's integrated Radeon (e.g. gfx1036) - makes kernels fail on *every* device
("device kernel image is invalid"), even the supported discrete card. So we
probe the devices in a throwaway subprocess (listing properties launches no
kernels), then hide unsupported ones via HIP_VISIBLE_DEVICES /
CUDA_VISIBLE_DEVICES before the real torch import.

Run directly to see what would be selected:  python gpu_select.py
"""
import json
import os
import subprocess
import sys

VISIBILITY_VARS = ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES")


def is_usable(torch, props) -> bool:
    # Integrated GPUs report shared system RAM as "VRAM" and are usually not
    # in the PyTorch build's supported architecture list.
    if getattr(props, "is_integrated", False):
        return False
    arch_list = torch.cuda.get_arch_list()
    if not arch_list:
        return True
    arch = getattr(props, "gcnArchName", None)
    if arch:  # ROCm, e.g. "gfx1102" or "gfx1102:sramecc-:xnack-"
        return arch.split(":")[0] in arch_list
    return f"sm_{props.major}{props.minor}" in arch_list  # CUDA


def _probe():
    import torch
    devices = []
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            p = torch.cuda.get_device_properties(i)
            devices.append({"index": i, "name": p.name, "usable": is_usable(torch, p),
                            "memory_gb": round(p.total_memory / 1024 ** 3, 1)})
    return {"hip": bool(getattr(torch.version, "hip", None)), "devices": devices}


def restrict_to_usable_gpus(verbose=True):
    """Set *_VISIBLE_DEVICES so torch only sees usable GPUs. Must run before `import torch`."""
    if "torch" in sys.modules:
        raise RuntimeError("restrict_to_usable_gpus() must be called before torch is imported")
    if any(v in os.environ for v in VISIBILITY_VARS):
        return  # user already chose; respect it
    try:
        out = subprocess.run([sys.executable, os.path.abspath(__file__), "--json"],
                             capture_output=True, text=True, timeout=300, check=True).stdout
        info = json.loads(out.strip().splitlines()[-1])
    except Exception as e:
        if verbose:
            print(f"GPU probe failed ({e}); letting PyTorch use its defaults.")
        return

    devices = info["devices"]
    usable = [d["index"] for d in devices if d["usable"]]
    if not devices or len(usable) == len(devices):
        return
    if verbose:
        for d in devices:
            if not d["usable"]:
                print(f"Hiding unsupported GPU from PyTorch: {d['name']}")
    value = ",".join(map(str, usable)) if usable else "-1"
    os.environ["HIP_VISIBLE_DEVICES" if info["hip"] else "CUDA_VISIBLE_DEVICES"] = value


if __name__ == "__main__":
    result = _probe()
    if "--json" in sys.argv:
        print(json.dumps(result))
    else:
        for d in result["devices"]:
            print(f"[{d['index']}] {d['name']}  {d['memory_gb']} GB  {'usable' if d['usable'] else 'NOT usable'}")
        if not result["devices"]:
            print("No GPU visible to PyTorch (CPU mode).")
