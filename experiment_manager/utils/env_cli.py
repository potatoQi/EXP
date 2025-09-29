"""Command line helpers for managing the project `.env` file.

This module backs the ``EMP`` console script declared in ``pyproject.toml``.
It provides a small interactive utility for persisting environment variables
that are later consumed by tools such as ``test_cnn.py``.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from experiment_manager.utils.env_utils import (
    load_project_env,
    resolve_env_file,
    update_project_env,
)

LARK_KEYS = [
    "LARK_APP_ID",
    "LARK_APP_SECRET",
    "LARK_APP_TOKEN",
    "LARK_TABLE_ID",
    "LARK_VIEW_ID",
]


def _collect_updates(
    requested_keys: Iterable[str],
    current_env: Dict[str, str],
    *,
    secret: bool = False,
    allow_remove: bool = False,
    interactive: bool = True,
) -> Dict[str, Optional[str]]:
    updates: Dict[str, Optional[str]] = {}
    requested = list(dict.fromkeys(requested_keys))  # preserve order, dedupe
    key_iter: List[str] = requested.copy()

    while key_iter or interactive:
        if key_iter:
            key = key_iter.pop(0)
        else:
            key = input("其余想加的变量名 (回车结束): ").strip()
            if not key:
                break
        if allow_remove:
            updates[key] = None
            print(f"🗑️ 已标记删除 {key}")
            continue
        default = current_env.get(key, "")
        prompt = f"{key} [{default}]: " if default else f"{key} (不填就回车): "
        if secret or key.endswith("SECRET"):
            value = getpass.getpass(prompt)
        else:
            value = input(prompt)
        value = value.strip()
        if not value:
            print("⚠️ 未填写，跳过该变量。")
            continue
        updates[key] = value
    return updates


def handle_set_env(args: argparse.Namespace) -> None:
    env_file = resolve_env_file()
    current_env = load_project_env(apply=False)

    print(f"📁 项目根目录: {env_file.project_root}")
    print(f"🗂️ 环境文件: {env_file.env_path}")

    if args.remove and not args.key and not args.preset:
        args.preset = "lark"

    preset_keys: List[str] = []
    if args.preset == "lark":
        preset_keys.extend(LARK_KEYS)
        print("ℹ️ 正在配置飞书相关变量 (LARK_* )。")

    if args.key:
        preset_keys.insert(0, args.key)

    updates = _collect_updates(
        preset_keys,
        current_env,
        secret=args.secret,
        allow_remove=args.remove,
        interactive=args.key is None,
    )

    if not updates:
        print("ℹ️ 未修改任何变量。")
        return

    update_project_env(updates)
    print("✅ 已更新 .env 文件。")


def handle_run_scheduler(args: argparse.Namespace) -> None:
    """运行调度器"""
    from experiment_manager.scheduler import ExperimentScheduler
    
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)
    
    print(f"🚀 启动调度器，配置文件: {config_path}")
    scheduler = ExperimentScheduler(config_path, dry_run=args.dry_run)
    scheduler.run_all()


def handle_see_ui(args: argparse.Namespace) -> None:
    """启动 UI"""
    from experiment_manager.ui.cli import run_ui
    
    # 构造 UI 需要的参数对象
    ui_args = argparse.Namespace(
        logdir=args.path,
        host=args.host,
        port=args.port,
        no_browser=args.no_browser,
        open_browser=not args.no_browser
    )
    run_ui(ui_args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="实验管理工具集")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # set_env 子命令
    set_parser = subparsers.add_parser(
        "set_env",
        help="交互式设置环境变量并写入项目根目录的 .env",
    )
    set_parser.add_argument("--key", help="指定单个变量名，未提供时进入交互模式")
    set_parser.add_argument(
        "--preset",
        choices=["lark"],
        help="使用预设变量集，目前支持 lark",
    )
    set_parser.add_argument(
        "--secret",
        action="store_true",
        help="使用密文输入（适用于 SECRET 变量）",
    )
    set_parser.add_argument(
        "--remove",
        action="store_true",
        help="将指定变量从 .env 中移除",
    )
    set_parser.set_defaults(func=handle_set_env)

    # run 子命令
    run_parser = subparsers.add_parser(
        "run",
        help="运行调度器",
    )
    run_parser.add_argument(
        "config",
        type=Path,
        help="调度器配置文件路径 (TOML)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅显示执行计划，不真正启动实验",
    )
    run_parser.set_defaults(func=handle_run_scheduler)

    # see 子命令
    see_parser = subparsers.add_parser(
        "see",
        help="启动可视化UI",
    )
    see_parser.add_argument(
        "path",
        type=Path,
        help="实验输出目录路径",
    )
    see_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址，默认 127.0.0.1",
    )
    see_parser.add_argument(
        "--port",
        type=int,
        default=6066,
        help="监听端口，默认 6066",
    )
    see_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="启动时不自动打开浏览器",
    )
    see_parser.set_defaults(func=handle_see_ui)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return
    handler(args)


__all__ = ["main", "build_parser"]


if __name__ == "__main__":  # pragma: no cover - manual invocation helper
    main()
