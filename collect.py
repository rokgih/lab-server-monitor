"""Collect CPU/mem/GPU metrics from this master + its nodes (via SSH) and
write data/<master>.json. Designed to be run by cron every 5 min.

Per-node remote probe: a one-line python3 script via SSH stdin.
Anonymization (sha256[:6] of username) is applied to gpustat process info.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Remote probe: stdin'd into `python3 -` on the node. No psutil dependency on
# the master side; everything runs on the node. Returns one JSON line.
REMOTE_PROBE = r"""
import json, subprocess, sys, time
try:
    import psutil
except ImportError:
    print(json.dumps({"error": "psutil not installed on node"})); sys.exit(0)

# CPU% needs interval to be meaningful
cpu_pct = psutil.cpu_percent(interval=1.0)
mem = psutil.virtual_memory()
try:
    load = psutil.getloadavg()
except (AttributeError, OSError):
    load = [0.0, 0.0, 0.0]

gpu = None
gpu_err = None
try:
    out = subprocess.check_output(
        ["gpustat", "--json"], stderr=subprocess.PIPE, timeout=10, text=True
    )
    gpu = json.loads(out)
except FileNotFoundError:
    gpu_err = "gpustat-not-installed"
except subprocess.CalledProcessError as e:
    msg = (e.stderr or "").strip().splitlines()[-1] if e.stderr else "gpustat-failed"
    gpu_err = msg[:120]
except subprocess.TimeoutExpired:
    gpu_err = "gpustat-timeout"
except Exception as e:
    gpu_err = f"gpustat-error: {type(e).__name__}"[:120]

print(json.dumps({
    "cpu_percent": cpu_pct,
    "cpu_count": psutil.cpu_count(),
    "mem_total": mem.total,
    "mem_used": mem.used,
    "mem_percent": mem.percent,
    "load": list(load),
    "gpu": gpu,
    "gpu_error": gpu_err,
    "boot_time": psutil.boot_time(),
}))
"""


def hash_user(name: str) -> str:
    if not name:
        return ""
    return "u_" + hashlib.sha256(name.encode()).hexdigest()[:6]


def anonymize_gpu(gpu: dict | None) -> dict | None:
    """Replace process owner usernames with sha256[:6] hashes."""
    if not gpu or "gpus" not in gpu:
        return gpu
    for g in gpu["gpus"]:
        for p in g.get("processes", []) or []:
            if "username" in p:
                p["username"] = hash_user(p["username"])
    return gpu


def run_remote(host: str, ssh_user: str | None, timeout: int) -> dict:
    target = f"{ssh_user}@{host}" if ssh_user else host
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout={timeout}",
        target,
        "python3 -",
    ]
    try:
        result = subprocess.run(
            cmd,
            input=REMOTE_PROBE,
            capture_output=True,
            text=True,
            timeout=timeout + 15,
        )
    except subprocess.TimeoutExpired:
        return {"error": "ssh-timeout"}
    if result.returncode != 0:
        err = (result.stderr or "").strip().splitlines()[-1:] or ["ssh-failed"]
        return {"error": err[0][:160]}
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"error": "bad-output", "stdout": result.stdout[:200]}


def run_local() -> dict:
    """Run the same probe locally, in-process — used for the master itself."""
    import psutil

    cpu_pct = psutil.cpu_percent(interval=1.0)
    mem = psutil.virtual_memory()
    try:
        load = list(psutil.getloadavg())
    except (AttributeError, OSError):
        load = [0.0, 0.0, 0.0]

    gpu = None
    gpu_err = None
    try:
        out = subprocess.check_output(
            ["gpustat", "--json"], stderr=subprocess.PIPE, timeout=10, text=True
        )
        gpu = json.loads(out)
    except FileNotFoundError:
        gpu_err = "gpustat-not-installed"
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or "").strip().splitlines()[-1] if e.stderr else "gpustat-failed"
        gpu_err = msg[:120]
    except subprocess.TimeoutExpired:
        gpu_err = "gpustat-timeout"
    except Exception as e:
        gpu_err = f"gpustat-error: {type(e).__name__}"

    return {
        "cpu_percent": cpu_pct,
        "cpu_count": psutil.cpu_count(),
        "mem_total": mem.total,
        "mem_used": mem.used,
        "mem_percent": mem.percent,
        "load": load,
        "gpu": gpu,
        "gpu_error": gpu_err,
        "boot_time": psutil.boot_time(),
    }


def collect(config_path: Path, out_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    master_name = cfg.get("master_name") or socket.gethostname()
    anonymize = cfg.get("anonymize_users", True)
    ssh_timeout = int(cfg.get("ssh_timeout_sec", 10))
    nodes_cfg = cfg.get("nodes") or []

    nodes_out = []

    if cfg.get("include_master", True):
        local = run_local()
        if anonymize:
            local["gpu"] = anonymize_gpu(local.get("gpu"))
        nodes_out.append({
            "host": master_name,
            "description": cfg.get("master_description", "master"),
            "is_master": True,
            **local,
        })

    for node in nodes_cfg:
        if isinstance(node, str):
            node = {"host": node}
        host = node["host"]
        info = run_remote(host, node.get("ssh_user"), ssh_timeout)
        if anonymize and "gpu" in info:
            info["gpu"] = anonymize_gpu(info["gpu"])
        nodes_out.append({
            "host": host,
            "description": node.get("description", ""),
            "is_master": False,
            **info,
        })

    payload = {
        "master_name": master_name,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "nodes": nodes_out,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {out_path} ({len(nodes_out)} nodes)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=Path("nodes.yaml"))
    ap.add_argument("--out", type=Path, required=True,
                    help="output JSON path, e.g. data/master-1.json")
    args = ap.parse_args()
    collect(args.config, args.out)
