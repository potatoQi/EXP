#!/usr/bin/env python3
"""命令行入口：根据 TOML 配置批量调度实验"""
from __future__ import annotations

import argparse
from pathlib import Path

from experiment_manager.scheduler import ExperimentScheduler


DEFAULT_CONFIG_PATH = Path(__file__).with_name("example_config.toml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量运行实验调度器")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="调度器配置文件 (TOML)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅输出调度计划，不真正启动实验",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scheduler = ExperimentScheduler(args.config, dry_run=args.dry_run)
    scheduler.run_all()


if __name__ == "__main__":
    main()
