"""EXP 命令行入口，提供调度器运行和 UI 启动功能。"""
from __future__ import annotations

import argparse
import socket
import sys
import webbrowser
from pathlib import Path
from typing import List

import uvicorn

from experiment_manager.ui.server import create_app
from experiment_manager.ui.service import SchedulerUISession

DEFAULT_PORT = 6066

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


__all__ = ["run_ui", "main", "handle_run_scheduler", "handle_see_ui"]
