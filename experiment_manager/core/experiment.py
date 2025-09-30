"""
实验管理核心类
提供实验的完整生命周期管理功能，包括状态管理、日志记录、指标保存等。
"""
import csv
import json
import os
import subprocess
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import pandas as pd
from experiment_manager.core.status import ExperimentStatus
from experiment_manager.integrations.lark.sync_utils import (
    coerce_lark_config_input,
    expand_lark_config,
    resolve_lark_config,
    sync_row_to_lark,
)
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Asia/Shanghai")

class Experiment:
    """实验管理类"""

    def __init__(
        self,
        name: str,                  # 实验名字
        command: str,               # 启动命令
        base_dir: Path,             # 实验根目录
        tags: List[str] = None,     # tags
        gpu_ids: List[int] = None,  # 直接指定GPU ID列表
        cwd: Path = None,           # 命令执行的工作目录
        resume: Optional[str] = None,  # 指定时间戳继续在已有目录中运行
        description: Optional[str] = None,  # 对实验的自然语言描述
        lark_config: Optional[Dict[str, str]] = None,  # 飞书多维表格（Bitable）配置 (dict or url str)
    ):
        self.name = name
        self.command = command
        self.tags = tags or []
        self.cwd = Path(cwd) if cwd else None  # 添加自定义工作目录
        self.description = description

        # 进程管理
        self.pid: Optional[int] = None
        self.gpu_ids: List[int] = gpu_ids or []  # 直接使用用户指定的GPU ID
        if base_dir is None:
            raise ValueError("base_dir 参数是必传的，请指定实验输出根目录")
        self.base_dir = Path(base_dir)
        self.current_run_id: Optional[str] = None

        provided_lark_config = coerce_lark_config_input(lark_config)    # 用户在 Experiment init 中提供的飞书配置

        self.lark_config: Optional[Dict[str, str]] = None

        # 如果提供 resume，尝试加载已有实验目录
        if resume:
            resume_str = str(resume)
            self.work_dir = self.base_dir / f"{self.name}_{resume_str}"
            if not self.work_dir.exists():
                raise ValueError(f"未找到可继续的实验目录: {self.work_dir}")

            resume_exp = Experiment.load_from_dir(self.work_dir)    # 拿到 resume Experiment 实例
            self.timestamp = resume_exp.timestamp
            self.status = ExperimentStatus.PENDING
            self.pid = None
            if self.description is None:
                self.description = getattr(resume_exp, "description", None)

            # 如果未显式指定 cwd 或 gpu，则沿用原实验配置
            if self.cwd is None and getattr(resume_exp, "cwd", None):
                self.cwd = resume_exp.cwd
            if not self.gpu_ids:
                self.gpu_ids = resume_exp.gpu_ids
            if tags is None:
                self.tags = resume_exp.tags

            existing_config = getattr(resume_exp, "lark_config", None) or {}    # 拿到 resume Experiment 的飞书配置
            merged_config: Dict[str, str] = dict(existing_config)
            merged_config.update(provided_lark_config)  # 用户在 Experiment init 中提供的飞书配置更新进去
            expanded_config = expand_lark_config(merged_config) # 解析配置中的 url 并展开为 app_token, table_id, view_id
            self.lark_config = expanded_config or None  # 得到最终飞书配置
        else:
            self.timestamp = datetime.now(LOCAL_TZ)         # 实验创建时间戳
            if self.timestamp.tzinfo is None:
                self.timestamp = self.timestamp.replace(tzinfo=LOCAL_TZ)
            self.status = ExperimentStatus.PENDING  # 实验状态
            timestamp_str = self.timestamp.strftime("%Y-%m-%d__%H-%M-%S")
            self.work_dir = self.base_dir / f"{self.name}_{timestamp_str}"
            merged_config: Dict[str, str] = dict(provided_lark_config)
            expanded_config = expand_lark_config(merged_config) # 解析配置中的 url 并展开为 app_token, table_id, view_id
            self.lark_config = expanded_config or None  # 得到最终飞书配置
            self._init_directories()

        # 确保关键目录存在
        (self.work_dir / "terminal_logs").mkdir(parents=True, exist_ok=True)
        (self.work_dir / "metrics").mkdir(parents=True, exist_ok=True)

        # 智能确定下一个运行ID并自动开始
        self.current_run_id = self._smart_start_next_run()
        self._save_metadata()
    
    def _smart_start_next_run(self) -> str:
        """智能确定并开始下一个运行
        
        自动检测现有的运行ID，确定下一个可用的运行编号
        使用4位数字格式：run_0001, run_0002, run_0003...
        
        Returns:
            str: 新的运行ID
        """
        # 检查现有的日志文件，找到最大的运行编号
        log_dir = self.work_dir / "terminal_logs"
        if not log_dir.exists():
            log_dir.mkdir(parents=True, exist_ok=True)
        
        max_run_num = 0
        for log_file in log_dir.glob("run_*.log"):
            try:
                # 从文件名提取运行编号：run_0001.log -> 1
                run_num_str = log_file.stem.split('_')[1]  # run_0001 -> 0001
                run_num = int(run_num_str)
                max_run_num = max(max_run_num, run_num)
            except (ValueError, IndexError):
                # 忽略无法解析的文件名
                continue
        
        # 下一个运行编号
        next_run_num = max_run_num + 1
        run_id = f"run_{next_run_num:04d}"  # 格式化为4位数字
        
        # 确保日志文件目录存在
        log_file = self.get_log_file_path(run_id)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 记录初始化日志
        self.append_log(f"实验初始化完成", run_id)
        self.append_log(f"运行编号: {run_id}", run_id)
        self.append_log(f"实验名称: {self.name}", run_id)
        self.append_log(f"启动命令: {self.command}", run_id)
        if self.gpu_ids:
            self.append_log(f"分配GPU: {self.gpu_ids}", run_id)
        if self.description:
            self.append_log(f"实验描述: {self.description}", run_id)
        self.append_log(f"工作目录: {self.work_dir}", run_id)
        
        return run_id
    
    def _init_directories(self):
        """初始化实验目录结构"""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        (self.work_dir / "terminal_logs").mkdir(exist_ok=True)
        (self.work_dir / "metrics").mkdir(exist_ok=True)
        self._save_metadata()
    
    def _save_metadata(self):
        """保存实验元信息到 metadata.json"""
        metadata = {
            "name": self.name,
            "command": self.command,
            "tags": self.tags,
            "timestamp": self.timestamp.astimezone(LOCAL_TZ).isoformat(),
            "status": self.status.value,
            "pid": self.pid,
            "gpu_ids": self.gpu_ids,
            "current_run_id": self.current_run_id,
            "cwd": str(self.cwd) if self.cwd else None,
            "description": self.description,
            "lark_config": self.lark_config,
        }
        with open(self.work_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load_from_dir(cls, work_dir: Path) -> "Experiment":
        """从目录加载实验
        
        Args:
            work_dir: 实验工作目录
            
        Returns:
            Experiment: 加载的实验实例
            
        Raises:
            FileNotFoundError: metadata.json 文件不存在
            ValueError: 元数据格式错误
        """
        metadata_file = work_dir / "metadata.json"
        if not metadata_file.exists():
            raise FileNotFoundError(f"Metadata file not found: {metadata_file}")
            
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        # 绕过 __init__ 创建实例
        exp = cls.__new__(cls)
        exp.name = metadata["name"]
        exp.command = metadata["command"]
        exp.tags = metadata["tags"]
        exp.timestamp = datetime.fromisoformat(metadata["timestamp"])
        exp.status = ExperimentStatus(metadata["status"])
        exp.pid = metadata.get("pid")
        exp.gpu_ids = metadata.get("gpu_ids", [])
        exp.current_run_id = metadata.get("current_run_id")
        exp.work_dir = work_dir
        exp.base_dir = work_dir.parent
        cwd_value = metadata.get("cwd")
        exp.cwd = Path(cwd_value) if cwd_value else None
        exp.description = metadata.get("description")
        existing_lark = metadata.get("lark_config") or {}
        expanded_config = expand_lark_config(existing_lark) if existing_lark else None
        exp.lark_config = expanded_config or None   # 得到最终飞书配置

        return exp
    
    # ============ 状态管理 ============
    
    def set_status(self, status: ExperimentStatus):
        """更新实验状态
        
        Args:
            status: 新的实验状态
        """
        self.status = status
        self._save_metadata()
    
    def set_running(self, pid: int):
        """标记为运行状态
        
        Args:
            pid: 进程ID
        """
        self.status = ExperimentStatus.RUNNING
        self.pid = pid
        # GPU已经在初始化时分配好了，不需要重新设置
        self.append_log(f"实验开始运行，进程ID: {pid}")
        if self.gpu_ids:
            self.append_log(f"使用GPU: {self.gpu_ids}")
        self._save_metadata()
    
    def set_finished(self):
        """标记为完成状态"""
        self.status = ExperimentStatus.FINISHED
        self.pid = None
        self._save_metadata()
    
    def set_error(self, error_msg: str = None):
        """标记为错误状态
        
        Args:
            error_msg: 错误信息，会记录到日志中
        """
        self.status = ExperimentStatus.ERROR
        self.pid = None
        if error_msg:
            self.append_log(f"ERROR: {error_msg}")
        self._save_metadata()
    
    def run(self, background: bool = True, extra_env: Optional[Dict[str, str]] = None):
        """运行实验
        
        Args:
            background: 是否在后台运行，True为非阻塞，False为阻塞等待完成
            extra_env: 额外注入的环境变量，覆盖默认设置
            
        Returns:
            subprocess.Popen: 如果background=True，返回进程对象
            int: 如果background=False，返回退出码
        """
        # 准备环境变量
        env = os.environ.copy()
        if self.gpu_ids:
            env['CUDA_VISIBLE_DEVICES'] = ','.join(map(str, self.gpu_ids))

        # 传递日志目录信息
        env['EXPERIMENT_WORK_DIR'] = str(self.work_dir)
        env['EXPERIMENT_NAME'] = self.name
        if self.current_run_id:
            env['EXPERIMENT_RUN_ID'] = self.current_run_id
        env.setdefault('PYTHONUNBUFFERED', '1')

        # 合并额外环境变量
        if extra_env:
            for key, value in extra_env.items():
                if value is None:
                    env.pop(key, None)
                else:
                    env[key] = str(value)
        
        # 准备命令
        full_command = self.command

        # 如果使用 conda run，确保关闭输出捕获以支持实时流式输出
        if "conda run" in full_command:
            # 仅在未显式指定 --no-capture-output 时插入
            if "--no-capture-output" not in full_command:
                full_command = full_command.replace(
                    "conda run",
                    "conda run --no-capture-output",
                    1
                )
        
        # 确定执行目录
        execution_cwd = str(self.cwd) if self.cwd else str(self.work_dir)
        
        # 启动进程
        try:
            popen_kwargs = dict(
                shell=True,
                cwd=execution_cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                bufsize=0,
            )

            if os.name != "nt":
                # 在 POSIX 系统中创建新的会话，确保进程组可以被整体终止
                popen_kwargs["preexec_fn"] = os.setsid
            else:  # pragma: no cover - Windows 特殊逻辑
                raise NotImplementedError("Windows 系统暂不支持实验运行")
                creation_flags = popen_kwargs.get("creationflags", 0)
                creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                popen_kwargs["creationflags"] = creation_flags

            self.append_log(f"开始执行: {full_command}")
            self.append_log(f"执行目录: {execution_cwd}")
            if self.gpu_ids:
                self.append_log(f"CUDA_VISIBLE_DEVICES: {env['CUDA_VISIBLE_DEVICES']}")
            
            process = subprocess.Popen(
                full_command,
                **popen_kwargs,
            )
            
            # 标记为运行状态
            self.set_running(process.pid)
            
            if background:
                # 非阻塞模式，启动后台线程实时记录输出  
                def stream_output():
                    """后台线程实时记录进程输出"""
                    try:
                        self.append_log("开始监听进程输出...")
                        
                        # 实时读取标准输出
                        while True:
                            line = process.stdout.readline()
                            if not line:
                                break
                            line = line.rstrip()
                            if line:
                                self.append_log(line)
                        
                        # 读取错误输出
                        stderr_content = process.stderr.read()
                        if stderr_content:
                            self.append_log("=== stderr 输出 ===")
                            for line in stderr_content.strip().split('\n'):
                                text = line.strip()
                                if text:
                                    self.append_log(f"STDERR: {text}")
                        
                        # 等待进程结束并记录最终状态
                        return_code = process.wait()
                        self.append_log(f"进程结束，退出码: {return_code}")
                        
                        if return_code == 0:
                            self.set_finished()
                            self.append_log(f"实验完成，退出码: {return_code}")
                        else:
                            self.set_error(f"实验失败，退出码: {return_code}")
                            
                    except Exception as e:
                        error_msg = f"输出流记录错误: {str(e)}"
                        self.append_log(error_msg)
                        self.set_error(f"输出流记录失败: {str(e)}")
                        self.append_log(f"错误堆栈: {traceback.format_exc()}")
                
                # 启动后台线程
                output_thread = threading.Thread(target=stream_output, daemon=False)  # 不使用daemon
                output_thread.start()
                
                # 保存线程引用，以便后续可以查询状态
                self._output_thread = output_thread
                
                # 返回进程对象（非阻塞）
                return process
            else:
                # 阻塞模式，等待完成并实时输出日志
                for line in process.stdout:
                    line = line.rstrip()
                    print(line)  # 输出到控制台
                    self.append_log(line)  # 保存到日志文件
                
                # 等待进程结束
                return_code = process.wait()
                
                if return_code == 0:
                    self.set_finished()
                    self.append_log(f"实验完成，退出码: {return_code}")
                else:
                    self.set_error(f"实验失败，退出码: {return_code}")
                
                return return_code
                
        except Exception as e:
            error_msg = f"启动实验失败: {str(e)}"
            self.set_error(error_msg)
            raise RuntimeError(error_msg)
    
    # ============ 日志管理 ============
    
    def get_log_file_path(self, run_id: str = None) -> Path:
        """获取日志文件路径
        
        Args:
            run_id: 运行ID，默认使用当前运行ID
            
        Returns:
            Path: 日志文件路径
        """
        run_id = run_id or self.current_run_id or "default"
        return self.work_dir / "terminal_logs" / f"{run_id}.log"
    
    def append_log(self, content: str, run_id: str = None):
        """追加日志内容
        
        Args:
            content: 日志内容
            run_id: 运行ID，默认使用当前运行ID
        """
        log_file = self.get_log_file_path(run_id)
        timestamp = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {content}\n")
            f.flush()  # 立即刷新到磁盘
    
    def read_log(self, run_id: str = None) -> str:
        """读取完整日志内容
        
        Args:
            run_id: 运行ID，默认使用当前运行ID
            
        Returns:
            str: 日志内容
        """
        log_file = self.get_log_file_path(run_id)
        if log_file.exists():
            with open(log_file, "r", encoding="utf-8") as f:
                return f.read()
        return ""
    
    def get_log_tail(self, lines: int = 50, run_id: str = None) -> str:
        """获取日志尾部内容
        
        Args:
            lines: 获取的行数
            run_id: 运行ID，默认使用当前运行ID
            
        Returns:
            str: 日志尾部内容
        """
        log_file = self.get_log_file_path(run_id)
        if not log_file.exists():
            return ""
        
        with open(log_file, "r", encoding="utf-8") as f:
            return "".join(f.readlines()[-lines:])
    
    # ============ 指标管理 ============
    
    def __init_metrics_row(self):
        """初始化当前指标行数据"""
        if not hasattr(self, '_current_metrics_row'):
            self._current_metrics_row = {}
    
    def upd_row(self, **kwargs):
        """
        更新当前行的指标字段
        Args:
            **kwargs: 要更新的指标字段，如 epoch=1, train_loss=0.5, val_acc=0.85
        Example:
            exp.upd_row(epoch=1, train_loss=0.5)
            exp.upd_row(val_loss=0.6, val_acc=0.85)
        """
        self.__init_metrics_row()
        # 更新字段值
        for key, value in kwargs.items():
            self._current_metrics_row[key] = value
    
    def get_metrics_file_path(self) -> Path:
        """
        获取指标文件路径
        """
        return self.work_dir / "metrics" / f"{self.current_run_id}.csv"

    def save_row(
        self,
        *,
        lark: bool = False,
        lark_config: Optional[Union[Dict[str, str], str]] = None,
    ):
        """
        保存当前行数据到 CSV 文件，并可选同步到飞书多维表格
        Args:
            lark: 是否同步到飞书多维表格
            lark_config: 本次同步使用的飞书配置或者 url，若提供将与实例默认配置合并
        """
        if not hasattr(self, '_current_metrics_row') or not self._current_metrics_row:
            self.append_log("警告: 没有待保存的指标数据")
            return
        
        metrics_file = self.get_metrics_file_path()
        metrics_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 获取当前行的所有字段名
        current_fields = set(self._current_metrics_row.keys())
        
        existing_data = []          # 已有的行数据
        existing_fields = set()     # 已有的字段名
        if metrics_file.exists():
            try:
                with open(metrics_file, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        existing_fields = set(reader.fieldnames)
                        existing_data = list(reader)
            except Exception as e:
                self.append_log(f"读取现有CSV数据失败: {e}")
        
        all_fields = existing_fields | current_fields   # 合并所有字段名
        fieldnames = sorted(list(all_fields))  # 排序保证一致性
        
        # 写入完整数据
        with open(metrics_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            # 写入现有数据
            for row in existing_data:
                # 填充缺失字段
                complete_row = {field: row.get(field, '') for field in fieldnames}
                writer.writerow(complete_row)
            
            # 写入新数据行
            complete_row = {field: self._current_metrics_row.get(field, '')
                           for field in fieldnames}
            writer.writerow(complete_row)

        row_snapshot = dict(complete_row)
        
        # 清空当前行数据，准备下一行
        self._current_metrics_row = {}

        if not lark:
            return

        # 拿到最终版的飞书配置
        resolved_config = resolve_lark_config(
            lark_config,    # 本次调用提供的飞书配置
            existing=self.lark_config,  # 实例的飞书配置
            logger=self.append_log, # 日志记录函数
        )
        if not resolved_config:
            return

        # 如果配置发生了变化，持久化保存
        if resolved_config != self.lark_config:
            try:
                self.lark_config = resolved_config
                self._save_metadata()
                self.append_log("飞书配置已更新并保存")
            except Exception as exc:
                self.append_log(f"警告: 无法持久化飞书配置: {exc}")

        # 进行飞书同步
        sync_row_to_lark(
            row_snapshot,   # 一条记录
            resolved_config,    # 飞书配置
            logger=self.append_log  # 日志记录函数
        )
    
    def load_metrics_df(self):
        """
        加载指标数据为DataFrame
        """
        metrics_file = self.get_metrics_file_path()
        if not metrics_file.exists():
            return []
        return pd.read_csv(metrics_file)

    # ============ 运行管理 ============
    
    def start_new_run(self) -> str:
        """开始新的运行（用于多次运行同一个实验）
        智能确定下一个运行编号，使用4位数字格式
        Returns:
            str: 新的运行ID
        """
        # 检查现有的日志文件，找到最大的运行编号
        log_dir = self.work_dir / "terminal_logs"
        max_run_num = 0
        
        if log_dir.exists():
            for log_file in log_dir.glob("run_*.log"):
                try:
                    # 从文件名提取运行编号：run_0001.log -> 1
                    run_num_str = log_file.stem.split('_')[1]  # run_0001 -> 0001
                    run_num = int(run_num_str)
                    max_run_num = max(max_run_num, run_num)
                except (ValueError, IndexError):
                    # 忽略无法解析的文件名
                    continue
        
        # 下一个运行编号
        next_run_num = max_run_num + 1
        self.current_run_id = f"run_{next_run_num:04d}"  # 格式化为4位数字
        self._save_metadata()
        
        # 记录开始新运行的日志
        self.append_log(f"开始新运行: {self.current_run_id}")
        self.append_log(f"启动命令: {self.command}")
        if self.gpu_ids:
            self.append_log(f"使用GPU: {self.gpu_ids}")
        
        return self.current_run_id
    
    # ============ 实用方法 ============
    
    def get_all_runs(self) -> List[str]:
        """获取所有运行ID列表
        Returns:
            List[str]: 运行ID列表
        """
        log_dir = self.work_dir / "terminal_logs"
        if not log_dir.exists():
            return []
        runs = []
        for log_file in log_dir.glob("*.log"):
            run_id = log_file.stem
            runs.append(run_id)
        return sorted(runs)

    def get_summary(self) -> Dict:
        """获取实验摘要信息"""
        return {
            "name": self.name,
            "status": self.status.value,
            "timestamp": self.timestamp.isoformat(),
            "tags": self.tags,
            "work_dir": str(self.work_dir),
            "total_runs": len(self.get_all_runs()),
            "current_run": self.current_run_id,
            "pid": self.pid,
            "gpu_ids": self.gpu_ids,
        }


def load_experiment() -> Experiment:
    """
    便捷函数：从环境变量加载当前实验实例
    用于在训练脚本中获取由 EXP 框架管理的实验实例。
    该函数会从环境变量中读取实验的工作目录，然后加载实验元数据。
    Returns:
        Experiment: 当前实验实例
    Raises:
        RuntimeError: 如果不在 EXP 管理的环境中运行
        FileNotFoundError: 如果实验元数据文件不存在
    """
    work_dir_env = os.environ.get("EXPERIMENT_WORK_DIR")
    if not work_dir_env:
        raise RuntimeError(
            "未找到 EXPERIMENT_WORK_DIR 环境变量。"
            "此函数只能在 EXP 框架管理的实验中使用。"
            "请确保通过 Experiment.run() 启动您的训练脚本。"
        )
    work_dir = Path(work_dir_env)
    if not work_dir.exists():
        raise FileNotFoundError(f"实验工作目录不存在: {work_dir}")
    return Experiment.load_from_dir(work_dir)