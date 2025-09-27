#!/usr/bin/env python3
"""
测试CNN训练脚本的实时输出捕获
"""
import argparse
from pathlib import Path
from experiment_manager.core.experiment import Experiment

def main():
    parser = argparse.ArgumentParser(description="测试CNN训练实时输出")
    parser.add_argument(
        "--resume",
        type=str,
        help="指定时间戳(例如 2025-09-27__15-57-54)，在已有实验目录下继续运行"
    )
    args = parser.parse_args()

    print("🧪 测试CNN训练实时输出")
    base_dir = Path("experiments")
    name = "cnn_test"
    command = "python simple_cnn_train.py"
    cwd = Path("/home/qixing.zhou/EXP")
    gpu_ids = [0]

    try:
        exp = Experiment(
            base_dir=base_dir,
            name=name,
            command=command,
            cwd=cwd,
            gpu_ids=gpu_ids,
            resume=args.resume
        )
        if args.resume:
            print(f"🔁 继续实验: {exp.work_dir}")
    except ValueError as exc:
        print(f"❌ {exc}")
        return

    print(f"📂 实验目录: {exp.work_dir}")
    print(f"🏷️  当前运行: {exp.current_run_id}")
    
    print(f"📄 日志文件: {exp.get_log_file_path()}")
    
    # 启动实验
    print("\n🚀 启动CNN训练...")
    process = exp.run(background=True)
    print(f"✅ 训练启动，PID: {process.pid}")

if __name__ == "__main__":
    main()