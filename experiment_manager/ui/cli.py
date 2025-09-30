"""EXP 命令行入口，提供调度器运行和 UI 启动功能。"""
from __future__ import annotations

import argparse
import getpass
import socket
import sys
import webbrowser
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import uvicorn

from experiment_manager.ui.server import create_app
from experiment_manager.ui.service import SchedulerUISession
from experiment_manager.utils.env_utils import (
    load_project_env,
    resolve_env_file,
    update_project_env,
)

DEFAULT_PORT = 6066

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


def build_ui_parser(parser: argparse.ArgumentParser) -> None:
    """为 see 子命令添加参数"""
    parser.add_argument(
        "logdir",
        type=Path,
        help="实验输出目录（与调度器的 base_experiment_dir 对应）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 6066")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    parser.add_argument("--open-browser", action="store_true", help="强制打开浏览器")
    parser.set_defaults(func=handle_see_ui)


def build_run_parser(parser: argparse.ArgumentParser) -> None:
    """为 run 子命令添加参数"""
    parser.add_argument(
        "config",
        type=Path,
        help="调度器配置文件路径 (TOML)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅显示执行计划，不真正启动实验",
    )
    parser.set_defaults(func=handle_run_scheduler)


def build_set_parser(parser: argparse.ArgumentParser) -> None:
    """为 set 子命令添加参数"""
    parser.add_argument("--key", help="指定单个变量名，未提供时进入交互模式")
    parser.add_argument(
        "--preset",
        choices=["lark"],
        help="使用预设变量集，目前支持 lark",
    )
    parser.add_argument(
        "--secret",
        action="store_true",
        help="使用密文输入（适用于 SECRET 变量）",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="将指定变量从 .env 中移除",
    )
    parser.set_defaults(func=handle_set_env)


def build_parser() -> argparse.ArgumentParser:
    """构建主要的参数解析器"""
    parser = argparse.ArgumentParser(description="EXP 实验管理工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # run 子命令
    run_parser = subparsers.add_parser(
        "run",
        help="运行调度器",
    )
    build_run_parser(run_parser)
    
    # see 子命令
    see_parser = subparsers.add_parser(
        "see", 
        help="启动可视化UI",
    )
    build_ui_parser(see_parser)
    
    # set 子命令 (之前的 set_env)
    set_parser = subparsers.add_parser(
        "set",
        help="交互式设置环境变量并写入项目根目录的 .env",
    )
    build_set_parser(set_parser)
    
    return parser


def pick_free_port(port: int, host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return port
        except OSError:
            sock.bind((host, 0))
            return sock.getsockname()[1]


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


def handle_set_env(args: argparse.Namespace) -> None:
    """处理环境变量设置"""
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


def handle_see_ui(args: argparse.Namespace) -> None:
    """启动 UI"""
    run_ui(args)


def run_ui(args: argparse.Namespace) -> None:
    logdir = args.logdir.expanduser().resolve()
    if not logdir.exists():
        print(f"⚠️ 指定的实验目录不存在: {logdir}", file=sys.stderr)
        sys.exit(1)

    session = SchedulerUISession(logdir)
    app = create_app(session)

    port = pick_free_port(args.port, args.host)
    url = f"http://{args.host}:{port}"
    print(f"🌐 EXP UI @ {url}")
    print(f"📁 监听实验目录: {logdir}")
    should_open = (not args.no_browser) and (args.open_browser or args.host in {"127.0.0.1", "localhost"})
    if should_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    config = uvicorn.Config(app=app, host=args.host, port=port, log_level="info", reload=False)
    server = uvicorn.Server(config)
    server.run()


def main(argv: List[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    
    # 如果没有提供子命令，显示帮助信息
    if not hasattr(args, 'func'):
        parser.print_help()
        return
    
    # 调用对应的处理函数
    args.func(args)


__all__ = ["run_ui", "main", "handle_run_scheduler", "handle_see_ui", "handle_set_env"]
