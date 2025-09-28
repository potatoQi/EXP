"""命令行入口，提供 TensorBoard 风格的 UI 启动体验。"""
from __future__ import annotations

import argparse
import socket
import sys
import webbrowser
from pathlib import Path

import uvicorn

from experiment_manager.ui.server import create_app
from experiment_manager.ui.service import SchedulerUISession

DEFAULT_PORT = 6066


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 EXP 调度器 UI")
    parser.add_argument(
        "logdir",
        type=Path,
        help="实验输出目录（与调度器的 base_experiment_dir 对应）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口，默认 6066")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    parser.add_argument("--open-browser", action="store_true", help="强制打开浏览器")
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


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    parsed = parser.parse_args(argv)
    run_ui(parsed)


__all__ = ["run_ui", "main"]
