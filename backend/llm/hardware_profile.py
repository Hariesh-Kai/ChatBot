# backend/llm/hardware_profile.py

"""
Hardware-Aware Adaptive Configuration

Auto-detects system capabilities (CPU cores, RAM, GPU) and adjusts:
- Retrieval depth (candidate_k, final_k)
- Generation token limits
- Model selection preferences
- Context compaction aggressiveness

Hardware tiers:
- HIGH:   GPU available OR >=32GB RAM + >=12 cores
- MEDIUM: >=16GB RAM + >=8 cores (no GPU)
- LOW:    <16GB RAM OR <8 cores (no GPU)
"""

import os
from typing import Dict, Any, Literal

HardwareTier = Literal["high", "medium", "low"]


def _detect_system_specs() -> Dict[str, Any]:
    """Detect system hardware capabilities."""
    specs: Dict[str, Any] = {
        "gpu": False,
        "ram_gb": 0.0,
        "cpu_cores": 0,
    }

    # GPU detection
    try:
        import torch
        specs["gpu"] = torch.cuda.is_available()
        if specs["gpu"]:
            specs["gpu_name"] = torch.cuda.get_device_name(0)
            specs["gpu_vram_gb"] = torch.cuda.get_device_properties(0).total_mem / (1024 ** 3)
    except Exception:
        pass

    # RAM detection
    try:
        import psutil
        specs["ram_gb"] = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        specs["ram_gb"] = 8.0  # Conservative default

    # CPU cores
    try:
        specs["cpu_cores"] = os.cpu_count() or 4
    except Exception:
        specs["cpu_cores"] = 4

    return specs


def classify_hardware_tier(specs: Dict[str, Any] = None) -> HardwareTier:
    """
    Classify system into a hardware tier for adaptive configuration.
    """
    if specs is None:
        specs = _detect_system_specs()

    has_gpu = bool(specs.get("gpu", False))
    ram_gb = float(specs.get("ram_gb", 8.0))
    cpu_cores = int(specs.get("cpu_cores", 4))

    if has_gpu:
        return "high"

    if ram_gb >= 32 and cpu_cores >= 12:
        return "high"

    if ram_gb >= 16 and cpu_cores >= 8:
        return "medium"

    return "low"


def get_adaptive_retrieval_config(tier: HardwareTier) -> Dict[str, Any]:
    """
    Get retrieval configuration adjusted for hardware tier.

    Lower tiers get fewer candidates and final chunks to reduce
    both retrieval latency and prompt size.
    """
    configs = {
        "high": {
            # Full retrieval quality
            "fast_candidate_k": 14,
            "fast_final_k": 6,
            "balanced_candidate_k": 25,
            "balanced_final_k": 8,
            "high_fidelity_candidate_k": 40,
            "high_fidelity_final_k": 10,
            "use_parallel": True,
            "use_cache": True,
            "max_workers": 4,
        },
        "medium": {
            # Slightly reduced for 16GB RAM systems
            "fast_candidate_k": 10,
            "fast_final_k": 4,
            "balanced_candidate_k": 18,
            "balanced_final_k": 6,
            "high_fidelity_candidate_k": 30,
            "high_fidelity_final_k": 8,
            "use_parallel": True,
            "use_cache": True,
            "max_workers": 3,
        },
        "low": {
            # Aggressively reduced for low-end systems
            "fast_candidate_k": 8,
            "fast_final_k": 3,
            "balanced_candidate_k": 12,
            "balanced_final_k": 5,
            "high_fidelity_candidate_k": 20,
            "high_fidelity_final_k": 6,
            "use_parallel": False,
            "use_cache": True,
            "max_workers": 2,
        },
    }
    return configs.get(tier, configs["medium"])


def get_adaptive_generation_config(tier: HardwareTier) -> Dict[str, Any]:
    """
    Get generation configuration adjusted for hardware tier.

    Lower tiers get shorter max_tokens and more aggressive
    extractive ratios to minimize LLM generation time.
    """
    configs = {
        "high": {
            "lite_max_tokens": 512,
            "base_max_tokens": 256,
            "base_extractive_ratio": 0.5,
            "base_context_max_chars": 6000,
            "prefer_extractive_only": False,
        },
        "medium": {
            # For i7-12700 + 16GB: reduce base tokens, increase extractive ratio
            "lite_max_tokens": 512,
            "base_max_tokens": 200,
            "base_extractive_ratio": 0.65,
            "base_context_max_chars": 4000,
            "prefer_extractive_only": True,  # Skip LLM for short factual on CPU
        },
        "low": {
            "lite_max_tokens": 384,
            "base_max_tokens": 160,
            "base_extractive_ratio": 0.75,
            "base_context_max_chars": 3000,
            "prefer_extractive_only": True,
        },
    }
    return configs.get(tier, configs["medium"])


# ============================================================
# GLOBAL CACHED PROFILE
# ============================================================

_cached_profile: Dict[str, Any] = {}


def get_hardware_profile(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Get the full hardware profile with adaptive configs.
    Cached after first call for performance.
    """
    global _cached_profile

    if _cached_profile and not force_refresh:
        return _cached_profile

    specs = _detect_system_specs()
    tier = classify_hardware_tier(specs)

    _cached_profile = {
        "specs": specs,
        "tier": tier,
        "retrieval": get_adaptive_retrieval_config(tier),
        "generation": get_adaptive_generation_config(tier),
    }

    print(
        f"[HARDWARE] Tier={tier} | GPU={specs.get('gpu', False)} | "
        f"RAM={specs.get('ram_gb', 0):.1f}GB | Cores={specs.get('cpu_cores', 0)}"
    )

    return _cached_profile
