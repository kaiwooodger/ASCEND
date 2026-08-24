"""Measure isolated Layer 1 wall time, peak RSS, cache latency, and output size."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # Portable fallback for a minimal ASCEND runtime.
    psutil = None


def measured(command: list[str], cwd: Path) -> dict[str, Any]:
    """Run one isolated command while sampling process-family resident memory."""
    start = time.perf_counter()
    # File-backed capture prevents a verbose DICOM warning stream from filling
    # a PIPE and deadlocking the monitored child before communicate().
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stdout_file, tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
        process = subprocess.Popen(command, cwd=cwd, stdout=stdout_file, stderr=stderr_file, text=True)
        root = psutil.Process(process.pid) if psutil is not None else None
        peak = 0
        while process.poll() is None:
            if root is not None:
                try:
                    family = [root, *root.children(recursive=True)]
                    peak = max(peak, sum(item.memory_info().rss for item in family if item.is_running()))
                except (psutil.Error, ProcessLookupError):
                    pass
            else:
                sampled = subprocess.run(["ps", "-o", "rss=", "-p", str(process.pid)], capture_output=True, text=True)
                try:
                    peak = max(peak, int(sampled.stdout.strip()) * 1024)
                except ValueError:
                    pass
            time.sleep(0.05)
        stdout_file.seek(0); stderr_file.seek(0)
        stdout, stderr = stdout_file.read(), stderr_file.read()
        if process.returncode not in (0, 3):
            raise RuntimeError(stderr or stdout)
    return {"wall_seconds": time.perf_counter() - start, "peak_rss_bytes": peak}


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize repeated runs using median time and maximum observed RSS."""
    return {
        "runs": len(records),
        "median_wall_seconds": statistics.median(item["wall_seconds"] for item in records),
        "maximum_peak_rss_bytes": max(item["peak_rss_bytes"] for item in records),
    }


def remove_scratch_tree(path: Path) -> None:
    """Remove benchmark-owned scratch, including immutable cache entries."""
    if not path.exists():
        return
    for item in path.rglob("*"):
        if item.is_dir():
            item.chmod(0o700)
        elif item.is_file():
            item.chmod(0o600)
    path.chmod(0o700)
    shutil.rmtree(path)


def main() -> None:
    """Execute the documented uncached and cache-hit benchmark protocol."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("small", "representative-eclipse", "very-large"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    args.workspace.mkdir(parents=True, exist_ok=True)
    uncached = []
    for index in range(3):
        case_root = args.workspace / f"uncached_{index}"
        command = [sys.executable, "-m", "ascend.cli", "run", str(args.source), "--case-root", str(case_root), "--config", str(args.config), "--layer1-only"]
        uncached.append(measured(command, project))
        remove_scratch_tree(case_root)
    prepared = args.workspace / "prepared"
    measured([sys.executable, "-m", "ascend.cli", "run", str(args.source), "--case-root", str(prepared), "--config", str(args.config), "--layer1-only"], project)
    cached = []
    for index in range(5):
        cached.append(measured([
            sys.executable, "-m", "ascend.cli", "resume", str(prepared / "ascend_case.json"), "--layer", "layer1",
        ], project))
        if index < 4:
            current = json.loads((prepared / "ascend_case.json").read_text(encoding="utf-8"))
            remove_scratch_tree(Path(current["layer1"]["result_path"]).parent)
    case_payload = json.loads((prepared / "ascend_case.json").read_text(encoding="utf-8"))
    formal_run = Path(case_payload["layer1"]["result_path"]).parent
    output_size = sum(path.stat().st_size for path in formal_run.rglob("*") if path.is_file())
    report = {
        "schema_version": "ASCEND-Layer1-benchmark-v1",
        "profile": args.profile,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "cpu_count": os.cpu_count(),
        "uncached": summarize(uncached),
        "cache_hit": summarize(cached),
        "formal_case_output_size_bytes": output_size,
        "source_alias_only": args.profile,
    }
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        wall_ratio = report["uncached"]["median_wall_seconds"] / baseline["uncached"]["median_wall_seconds"]
        rss_ratio = report["uncached"]["maximum_peak_rss_bytes"] / baseline["uncached"]["maximum_peak_rss_bytes"]
        report["baseline_comparison"] = {
            "wall_time_ratio": wall_ratio, "peak_rss_ratio": rss_ratio,
            "performance_regression": wall_ratio > 1.10 or rss_ratio > 1.10,
            "regression_threshold_fraction": 0.10,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
