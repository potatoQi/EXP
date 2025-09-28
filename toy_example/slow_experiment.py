#!/usr/bin/env python3
"""Toy long-running experiment that emits periodic logs and metrics.

Designed for observing the EXP dashboard behaviour. The script relies on
environment variables injected by :class:`experiment_manager.core.experiment.Experiment`:
- ``EXPERIMENT_WORK_DIR``: target directory for metrics/log artefacts
- ``EXPERIMENT_RUN_ID``: current run identifier (optional)

It periodically prints status updates, appends to ``metrics/progress.csv`` and
supports configurable duration to simulate diverse workloads.
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, TextIO

DEFAULT_INTERVAL = 15


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Long-running toy experiment")
    parser.add_argument(
        "--duration",
        type=float,
        default=180.0,
        help="总运行时长（秒），默认 180 秒",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="日志与指标更新间隔（秒），默认 15 秒",
    )
    parser.add_argument(
        "--workload",
        type=str,
        default="baseline",
        help="用于区分不同实验的自定义标签",
    )
    parser.add_argument(
        "--jitter",
        type=float,
        default=0.0,
        help="在每次 sleep 前额外增加 0~jitter 秒随机扰动，模拟不稳定工作量",
    )
    return parser.parse_args()


def get_work_dir() -> Path:
    env_path = os.environ.get("EXPERIMENT_WORK_DIR")
    if env_path:
        return Path(env_path)
    # fallback to current directory to remain usable outside scheduler
    return Path.cwd()


def ensure_csv(path: Path, fieldnames: Iterable[str]) -> tuple[csv.DictWriter, TextIO]:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    fh = open(path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
    if not file_exists:
        writer.writeheader()
        fh.flush()
    return writer, fh


def main() -> int:
    args = parse_args()
    duration = max(args.duration, args.interval)
    interval = max(args.interval, 5.0)
    jitter = max(args.jitter, 0.0)

    work_dir = get_work_dir()
    run_id = os.environ.get("EXPERIMENT_RUN_ID", "run_unknown")
    log_prefix = f"[{args.workload}]"

    metrics_path = work_dir / "metrics" / "progress.csv"
    fieldnames = ["timestamp", "run_id", "workload", "step", "elapsed", "progress", "message"]
    writer, metrics_file = ensure_csv(metrics_path, fieldnames)

    start_ts = time.monotonic()
    deadline = start_ts + duration
    step = 0

    print(f"{log_prefix} 实验开始，目标运行时长 {duration:.1f} 秒", flush=True)
    print(f"写入指标: {metrics_path}", flush=True)

    try:
        while True:
            now = time.monotonic()
            elapsed = now - start_ts
            remaining = max(deadline - now, 0.0)
            progress = min(elapsed / duration, 1.0)
            step += 1

            message = (
                f"{log_prefix} step={step} progress={progress*100:5.1f}% "
                f"elapsed={elapsed:6.1f}s remaining~{remaining:6.1f}s"
            )
            print(message, flush=True)

            writer.writerow(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "run_id": run_id,
                    "workload": args.workload,
                    "step": step,
                    "elapsed": round(elapsed, 2),
                    "progress": round(progress, 4),
                    "message": message,
                }
            )
            metrics_file.flush()

            if now >= deadline:
                break

            sleep_interval = interval
            if jitter > 0:
                sleep_interval += random.uniform(0, jitter)
            time.sleep(sleep_interval)

        print(f"{log_prefix} 实验完成，总耗时 {time.monotonic() - start_ts:.1f} 秒", flush=True)
        return 0
    except KeyboardInterrupt:
        print(f"{log_prefix} 收到中断信号，提前结束", flush=True)
        return 1
    finally:
        try:
            metrics_file.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
