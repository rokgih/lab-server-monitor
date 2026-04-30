"""Generate fake snapshots so the dashboard can be tested without real masters."""
import json
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path


def fake_gpu(idx, util_range=(0, 95)):
    util = random.randint(*util_range)
    mem_total = random.choice([24576, 49140, 81920])
    mem_used = int(mem_total * random.uniform(0.05, 0.95))
    procs = []
    if util > 5:
        for _ in range(random.randint(1, 3)):
            procs.append({
                "username": f"u_{random.randint(0, 0xffffff):06x}",
                "command": random.choice(["python train.py", "python infer.py", "torchrun"]),
                "gpu_memory_usage": random.randint(800, 22000),
            })
    return {
        "index": idx,
        "name": random.choice(["NVIDIA RTX A6000", "NVIDIA GeForce RTX 3090", "NVIDIA RTX 4090"]),
        "utilization.gpu": util,
        "memory.used": mem_used,
        "memory.total": mem_total,
        "temperature.gpu": random.randint(35, 82),
        "processes": procs,
    }


def fake_node(host, desc, has_gpus, n_gpus=4):
    gpu = None
    err = None
    if has_gpus:
        gpu = {"gpus": [fake_gpu(i) for i in range(n_gpus)]}
    else:
        err = "gpustat-not-installed"
    return {
        "host": host,
        "description": desc,
        "is_master": False,
        "cpu_percent": round(random.uniform(2, 95), 1),
        "cpu_count": random.choice([16, 32, 64, 128]),
        "mem_total": 256 * 1024**3,
        "mem_used": int(random.uniform(0.1, 0.9) * 256 * 1024**3),
        "mem_percent": round(random.uniform(10, 90), 1),
        "load": [random.uniform(0, 30) for _ in range(3)],
        "gpu": gpu,
        "gpu_error": err,
        "boot_time": (datetime.now(timezone.utc) - timedelta(days=14)).timestamp(),
    }


def fake_master(name, when_minutes_ago=0):
    return {
        "master_name": name,
        "generated_at": (datetime.now(timezone.utc) - timedelta(minutes=when_minutes_ago))
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nodes": [
            {**fake_node(name, "Master — RTX A6000 ×4", True, 4), "is_master": True},
            fake_node(f"{name}-node-01", "RTX 3090 ×8", True, 8),
            fake_node(f"{name}-node-02", "RTX 4090 ×4", True, 4),
            fake_node(f"{name}-cpu-01", "CPU-only worker", False),
            # one unreachable example
            {"host": f"{name}-node-03", "description": "Down for maintenance",
             "is_master": False, "error": "ssh-timeout"},
        ],
    }


def main():
    out_dir = Path("data")
    out_dir.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    # (master_name, master_clock_skew_min, real_age_min)
    # — the "skew" is what the master's clock is off by (how far behind real
    # time it thinks it is). The "real_age" is when the file was actually
    # pushed (we fake this as the manifest's committed_at).
    masters = [
        ("master-1", 0,  0),    # in sync, fresh
        ("master-2", 0,  3),    # in sync, slightly older push
        ("master-3", 22, 1),    # clock 22 min behind, just pushed → skew badge, not stale
        ("master-4", 0, 30),    # actually stale
    ]
    files_entries = []
    for name, skew_min, age_min in masters:
        # generated_at = master's wall clock at time of push = (now - age) - skew
        gen_at = (now - timedelta(minutes=age_min + skew_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
        committed_at = (now - timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
        m = fake_master(name, age_min + skew_min)
        m["generated_at"] = gen_at
        f = out_dir / f"{name}.json"
        f.write_text(json.dumps(m, indent=2))
        files_entries.append({"name": f"{name}.json", "committed_at": committed_at})
        print(f"wrote {f}")
    Path("manifest.json").write_text(json.dumps({"files": files_entries}))
    print("wrote manifest.json")


if __name__ == "__main__":
    random.seed(42)
    main()
