# backend/rag/resource_planner.py

import psutil
import os

def get_optimal_strategy(file_size_mb: float):
    """
    Decides the processing strategy based on file size and current system load.
    Returns: strategy_name, recommended_cores, batch_size
    """
    
    # 1. Check System Health
    mem = psutil.virtual_memory()
    cpu_usage = psutil.cpu_percent(interval=0.1)
    
    available_ram_gb = mem.available / (1024 ** 3)
    total_cores = psutil.cpu_count(logical=False) or 2
    
    print(f"🩺 [SYSTEM] RAM Available: {available_ram_gb:.2f}GB | CPU Usage: {cpu_usage}%")

    # 2. Critical Safety Valve (If system is dying, go slow)
    if mem.percent > 85 or cpu_usage > 90:
        print("⚠️ [SYSTEM] High Load Detected! Forcing 'Serial' mode to save crash.")
        return "serial_stream", 1, 1  # 1 core, 1 page at a time

    # 3. Strategy Selection based on File Size
    
    # TINY FILES (< 5MB) -> Just do it fast
    if file_size_mb < 5:
        return "in_memory", total_cores, 10

    # MEDIUM FILES (5MB - 50MB) -> Standard Batching
    if file_size_mb < 50:
        # Use 75% of cores, batch 5 pages
        safe_cores = max(1, int(total_cores * 0.75))
        return "batched_stream", safe_cores, 5

    # HUGE FILES (> 50MB) -> Conservative Streaming
    # Use 50% cores, process 1 page at a time to keep RAM flat
    safe_cores = max(1, int(total_cores * 0.5))
    return "serial_stream", safe_cores, 1

def limit_cpu_usage(cores=1):
    """
    Pins the current process to a subset of CPU cores (Windows/Linux).
    """
    p = psutil.Process(os.getpid())
    
    # Get list of all cores
    all_cores = list(range(psutil.cpu_count(logical=True)))
    
    # Select the last N cores (usually efficient cores on hybrid CPUs)
    selected_cores = all_cores[-cores:]
    
    try:
        p.cpu_affinity(selected_cores)
        print(f"🔧 [SYSTEM] Pinned process to Cores: {selected_cores}")
    except Exception as e:
        print(f"⚠️ [SYSTEM] Could not pin CPU affinity: {e}")