"""实验调度器

读取 TOML 配置，一次性调度多组实验运行。
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from experiment_manager.core import Experiment, ExperimentStatus
from experiment_manager.integrations.lark.sync_utils import (
    coerce_lark_config_input,
    expand_lark_config,
)
from experiment_manager.utils.config import ConfigManager
from experiment_manager.scheduler.state_store import SchedulerStateStore, LOCAL_TZ


@dataclass(order=True)
class ScheduledExperiment:
    """内部调度用的实验定义"""

    # dataclass order 使用 sort_index 来实现按优先级排序（优先级越大越靠前）
    sort_index: int = field(init=False, repr=False)

    name: str
    command: str
    priority: int = 0
    tags: List[str] = field(default_factory=list)
    gpu_ids: List[int] = field(default_factory=list)
    cwd: Optional[str] = None
    base_dir: Optional[str] = None
    environment: Dict[str, Any] = field(default_factory=dict)
    resume: Optional[str] = None
    description: Optional[str] = None
    repeats: int = 1
    max_retries: int = 0
    delay_seconds: float = 0.0
    lark_config_raw: Optional[dict] = None  # 原始 lark 配置（来自配置文件中的 lark_config / lark_url 合并）

    def __post_init__(self) -> None:
        self.sort_index = -self.priority

    def to_payload(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "priority": self.priority,
            "tags": list(self.tags),
            "gpu_ids": list(self.gpu_ids),
            "cwd": self.cwd,
            "base_dir": self.base_dir,
            "environment": dict(self.environment),
            "resume": self.resume,
            "description": self.description,
            "repeats": self.repeats,
            "max_retries": self.max_retries,
            "delay_seconds": self.delay_seconds,
            "lark_config_raw": dict(self.lark_config_raw) if isinstance(self.lark_config_raw, dict) else None,
        }


class ExperimentScheduler:
    """实验调度器：按配置顺序执行多组实验"""

    def __init__(self, config_path: Path, dry_run: bool = False):
        self.config_path = Path(config_path)
        self.config_dir = self.config_path.parent.resolve() # 配置文件所在目录
        self.invocation_cwd = Path.cwd().resolve()  # 调度器启动时的工作目录
        self.config_manager = ConfigManager(self.config_path)   # 配置对象
        scheduler_cfg = self.config_manager.get_scheduler_config()  # 调度器配置
        self.max_concurrent = int(scheduler_cfg.get("max_concurrent_experiments", 1))   # 最大并发实验数
        self.check_interval = float(scheduler_cfg.get("check_interval", 1))    # 状态检查间隔 (降低到1秒提升响应性)
        base_dir_value = scheduler_cfg.get("base_experiment_dir")
        if not base_dir_value or not str(base_dir_value).strip():
            raise ValueError("配置项 scheduler.base_experiment_dir 为必填，请在配置文件中显式指定")
        base_dir_path = Path(base_dir_value).expanduser()
        if base_dir_path.is_absolute():
            self.base_experiment_dir = base_dir_path.resolve()
        else:
            self.base_experiment_dir = (self.invocation_cwd / base_dir_path).resolve()   # 实验输出根目录
        self.auto_restart = bool(scheduler_cfg.get("auto_restart_on_error", False)) # 是否自动重启错误的实验
        self.linger_when_idle = bool(scheduler_cfg.get("linger_when_idle", True))   # 实验全部完成后是否继续等待 UI 操作命令

        # 解析 scheduler 级别 lark 配置
        scheduler_lark_url = scheduler_cfg.get("lark_url")
        scheduler_lark_dict = scheduler_cfg.get("lark_config") or {}
        if scheduler_lark_url and isinstance(scheduler_lark_dict, dict):
            scheduler_lark_dict = {**scheduler_lark_dict, "url": scheduler_lark_url}
        elif scheduler_lark_url and not scheduler_lark_dict:
            scheduler_lark_dict = {"url": scheduler_lark_url}
        # 允许为空或非 dict 忽略
        if isinstance(scheduler_lark_dict, dict) and scheduler_lark_dict:
            self._scheduler_lark_config_raw = scheduler_lark_dict
        else:
            self._scheduler_lark_config_raw = None

        self.dry_run = dry_run
        self._status_indicator = "running"  # 调度器状态指示器：running / awaiting_shutdown / stopped
        self._shutdown_requested = False    # 是否收到关闭调度器的请求 (收到后会变为 True, 下一轮会退出)
        self._waiting_for_shutdown = False  # 是否正在等待关闭指令 (当全部结束空闲挂起状态时且 linger_when_idle=True 时变为 True)

        self._scheduled = self._load_experiments_from_config()   # 加载所有组实验的配置
        self._pending = []    # pending 列表
        self._active = []     # running 列表
        self._finished = []   # finished 列表

        self._task_counter = 0  # 内部递增的流水号, 保证即使有重试事件也能唯一标识条目
        self.state_store = SchedulerStateStore(self.base_experiment_dir)    # 拿到状态持久化管理器 (会把调度器的操作/状态读写到本地磁盘)

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------
    def _load_experiments_from_config(self) -> List[ScheduledExperiment]:
        experiments_cfg = self.config_manager.get_experiments()
        scheduled: List[ScheduledExperiment] = []

        # cfg 是 toml 里每一个 experiment 的配置
        for index, cfg in enumerate(experiments_cfg):
            try:
                scheduled.append(self._create_experiment_config(cfg))
            except Exception as exc:  # pragma: no cover - 配置错误时的防御
                raise ValueError(f"加载第 {index + 1} 个实验配置失败: {exc}")

        # 按优先级排序（大优先级在前）
        scheduled.sort()

        # 根据 repeats 扩展队列
        expanded: List[ScheduledExperiment] = []
        for exp_cfg in scheduled:
            repeat_count = max(1, int(exp_cfg.repeats))
            for _ in range(repeat_count):
                clone = ScheduledExperiment(
                    name=exp_cfg.name,
                    command=exp_cfg.command,
                    priority=exp_cfg.priority,
                    tags=list(exp_cfg.tags),
                    gpu_ids=list(exp_cfg.gpu_ids),
                    cwd=exp_cfg.cwd,
                    base_dir=exp_cfg.base_dir,
                    environment=dict(exp_cfg.environment),
                    resume=exp_cfg.resume,
                    description=exp_cfg.description,
                    repeats=1,
                    max_retries=exp_cfg.max_retries,
                    delay_seconds=exp_cfg.delay_seconds,
                    lark_config_raw=exp_cfg.lark_config_raw,
                )
                expanded.append(clone)

        return expanded

    def _create_experiment_config(self, cfg: Dict[str, Any]) -> ScheduledExperiment:
        try:
            name = cfg["name"]
            command = cfg["command"]
        except KeyError as missing:
            raise ValueError(f"实验配置缺少必需字段: {missing}") from None

        priority = int(cfg.get("priority", 0))
        tags = cfg.get("tags", []) or []
        if not isinstance(tags, list):
            raise ValueError("tags 必须是列表")

        gpu_ids_raw = cfg.get("gpu_ids")
        gpu_ids: List[int] = []
        if gpu_ids_raw is None:
            gpu_ids = []
        elif isinstance(gpu_ids_raw, (list, tuple)):
            try:
                gpu_ids = [int(item) for item in gpu_ids_raw]
            except (TypeError, ValueError) as exc:
                raise ValueError("gpu_ids 必须是整数列表") from exc
        elif isinstance(gpu_ids_raw, str):
            try:
                gpu_ids = [int(part.strip()) for part in gpu_ids_raw.split(",") if part.strip()]
            except ValueError as exc:
                raise ValueError("gpu_ids 字符串需由逗号分隔的整数构成") from exc
        else:
            raise ValueError("gpu_ids 必须是列表、元组或字符串")

        base_dir = cfg.get("base_dir")
        cwd_value = cfg.get("cwd")
        env_cfg = cfg.get("environment", {}) or {}
        if not isinstance(env_cfg, dict):
            raise ValueError("environment 必须是字典")

        resume = cfg.get("resume")
        description = cfg.get("description")
        repeats = int(cfg.get("repeats", 1))
        max_retries = int(cfg.get("max_retries", 0))
        delay_seconds = float(cfg.get("delay_seconds", 0))

        # 解析实验级 lark 配置
        exp_lark_url = cfg.get("lark_url")
        exp_lark_dict = cfg.get("lark_config") or {}
        if exp_lark_url and isinstance(exp_lark_dict, dict):
            exp_lark_dict = {**exp_lark_dict, "url": exp_lark_url}
        elif exp_lark_url and not exp_lark_dict:
            exp_lark_dict = {"url": exp_lark_url}

        return ScheduledExperiment(
            name=name,
            command=command,
            priority=priority,
            tags=tags,
            gpu_ids=gpu_ids,
            cwd=cwd_value,
            base_dir=base_dir,
            environment=env_cfg,
            resume=resume,
            description=description,
            repeats=repeats,
            max_retries=max_retries,
            delay_seconds=delay_seconds,
            lark_config_raw=exp_lark_dict if isinstance(exp_lark_dict, dict) and exp_lark_dict else None,
        )

    # ------------------------------------------------------------------
    # 调度生命周期
    # ------------------------------------------------------------------
    def run_all(self) -> None:
        """执行配置中的全部实验"""
        self._prepare_pending_queue()

        if self.dry_run:
            self._print_plan_only()
            return

        print(f"🔧 实验调度器启动，共 {len(self._pending)} 个任务，最大并发 {self.max_concurrent}。")

        summary_printed = False

        # 只要没收到 "停止调度器" 指令就一直循环
        while not self._shutdown_requested:
            self._consume_commands()    # 执行一条指令
            self._try_launch_new_tasks()    # 尝试启动新的实验

            if self._active:
                if self._waiting_for_shutdown:
                    self._waiting_for_shutdown = False
                    self._status_indicator = "running"
                    self._sync_state()
                time.sleep(self.check_interval)
                self._harvest_finished_tasks()  # 收割一波
                summary_printed = False
                continue

            if self._pending:
                if self._waiting_for_shutdown:
                    self._waiting_for_shutdown = False
                    self._status_indicator = "running"
                    self._sync_state()
                time.sleep(self.check_interval)
                summary_printed = False
                continue

            if not summary_printed:
                # 如果进到这就说明 _active 和 _pending 都空了, 打印总结
                self._print_summary()
                summary_printed = True

            # 进到这说明既空闲, 且用户自己设置了空闲时就退出, 那么就退出
            if not self.linger_when_idle:
                break

            # 进到这说明空闲, 但用户设置了空闲时继续等待指令, 那么就把 _waiting_for_shutdown 置为 True
            if not self._waiting_for_shutdown:
                self._waiting_for_shutdown = True
                self._status_indicator = "awaiting_shutdown"
                self._sync_state()

            sleep_interval = min(self.check_interval or 0.5, 0.5)
            time.sleep(sleep_interval)

        if not summary_printed:
            self._print_summary()

        self._status_indicator = "stopped"
        self._waiting_for_shutdown = False
        self._shutdown_requested = False
        self._sync_state()

    # ------------------------------------------------------------------
    # 队列与执行
    # ------------------------------------------------------------------
    def _prepare_pending_queue(self) -> None:
        self._pending = []
        for order, exp_cfg in enumerate(self._scheduled):
            self._pending.append(
                {
                    "config": exp_cfg,
                    "order": order,
                    "attempt": 0,
                    "id": self._new_task_id(),  # 唯一标识 id
                    "created_at": datetime.now(tz=LOCAL_TZ),
                }
            )
        self._sync_state()

    def _print_plan_only(self) -> None:
        print("📝 调度计划 (dry-run mode)")
        for idx, item in enumerate(self._pending, start=1):
            cfg = item["config"]
            print(
                f"[{idx:02d}] name={cfg.name}, priority={cfg.priority}, "
                f"command={cfg.command}, resume={cfg.resume or '-'}"
            )

    def _try_launch_new_tasks(self) -> None:
        launched = 0
        while self._pending and len(self._active) < self.max_concurrent:
            # 取出 _pending 队首
            task = self._pending.pop(0)
            cfg = task["config"]
            task["attempt"] += 1
            # 打包为一个 experiment 实例
            experiment = self._launch_experiment(cfg, attempt=task["attempt"])

            self._active.append(
                {
                    "config": cfg,
                    "experiment": experiment,
                    "started_at": datetime.now(tz=LOCAL_TZ),
                    "attempt": task["attempt"],
                    "id": task["id"],
                    "created_at": task.get("created_at"),
                    "work_dir": str(experiment["instance"].work_dir) if isinstance(experiment, dict) else None,
                    "run_id": experiment["instance"].current_run_id if isinstance(experiment, dict) else None,
                }
            )
            launched += 1

        if launched:
            print(f"🚀 本轮启动 {launched} 个实验，当前运行 {len(self._active)} 个。")
            self._sync_state()

    def _launch_experiment(self, cfg: ScheduledExperiment, attempt: int):
        config_dir = self.config_dir

        if cfg.base_dir:
            custom_base = Path(cfg.base_dir).expanduser()
            if custom_base.is_absolute():
                base_dir = custom_base.resolve()
            else:
                base_dir = (self.invocation_cwd / custom_base).resolve()
        else:
            base_dir = self.base_experiment_dir

        if cfg.cwd:
            custom_cwd = Path(cfg.cwd).expanduser()
            if custom_cwd.is_absolute():
                working_dir = custom_cwd.resolve()
            else:
                working_dir = (self.invocation_cwd / custom_cwd).resolve()
        else:
            working_dir = config_dir

        # 合并 lark 配置：scheduler 级别 < 实验级别 （后者优先覆盖）
        merged_lark: Optional[dict] = None
        if self._scheduler_lark_config_raw:
            merged_lark = dict(self._scheduler_lark_config_raw)
        if cfg.lark_config_raw:
            if merged_lark:
                merged_lark.update(cfg.lark_config_raw)
            else:
                merged_lark = dict(cfg.lark_config_raw)
        # 规范化并展开 URL（如果有）
        expanded_lark = None
        if merged_lark:
            coerced = coerce_lark_config_input(merged_lark)
            expanded_lark = expand_lark_config(coerced) or coerced

        exp = Experiment(
            base_dir=base_dir,
            name=cfg.name,
            command=cfg.command,
            gpu_ids=list(cfg.gpu_ids),
            cwd=working_dir,
            tags=cfg.tags,
            resume=cfg.resume,
            description=cfg.description,
            lark_config=expanded_lark,
        )

        if cfg.delay_seconds > 0:
            exp.append_log(f"任务配置了启动延迟 {cfg.delay_seconds}s (attempt={attempt})")
            time.sleep(cfg.delay_seconds)

        env_updates = self._prepare_environment(cfg)

        if self.dry_run:
            return exp

        process = exp.run(background=True, extra_env=env_updates)
        exp.append_log(f"调度 attempt={attempt}")

        return {
            "instance": exp,
            "process": process,
        }

    def _prepare_environment(self, cfg: ScheduledExperiment) -> Dict[str, str]:
        env = {}
        for key, value in cfg.environment.items():
            env[key] = str(value)
        return env

    def _harvest_finished_tasks(self) -> None:
        still_running: List[Dict[str, Any]] = []
        for slot in self._active:
            cfg = slot["config"]
            runtime = slot["experiment"]
            process = runtime["process"]
            if process.poll() is None:  # 该实验仍在运行中
                still_running.append(slot)
                continue

            return_code = process.returncode
            experiment_instance = runtime["instance"]
            success = return_code == 0 and experiment_instance.status == ExperimentStatus.FINISHED

            self._finished.append(
                {
                    "config": cfg,
                    "status": "success" if success else "failed",
                    "attempt": slot["attempt"],
                    "return_code": return_code,
                    "id": slot.get("id", self._new_task_id()),
                    "created_at": slot.get("created_at"),
                    "started_at": slot.get("started_at"),
                    "completed_at": datetime.now(tz=LOCAL_TZ),
                    "work_dir": str(runtime["instance"].work_dir),
                    "run_id": runtime["instance"].current_run_id,
                }
            )

            if not success:
                print(f"⚠️ 实验 {cfg.name} attempt {slot['attempt']} 失败 (code={return_code})")
                if self._should_retry(cfg, slot["attempt"]):
                    print(f"↺ 将实验 {cfg.name} 重新排队")
                    self._pending.insert(
                        0,
                        {
                            "config": cfg,
                            "order": slot.get("order", 0),
                            "attempt": slot["attempt"],
                            "id": self._new_task_id(),
                            "created_at": datetime.now(tz=LOCAL_TZ),
                        },
                    )

        self._active = still_running
        self._sync_state()

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    def _sync_state(self) -> None:

        def _build_queue(
            records: Iterable[Dict[str, Any]],  # 某个队列 (pending / running / finished / errors)
            status: str # 这个队列对应的状态字符串值 ("pending" / "running" / "finished" / "errors")
        ) -> List[Dict[str, Any]]:
            output: List[Dict[str, Any]] = []   # 要把队列里每一个条目都转换成字典形式, 然后存到 output 列表里
            for item in records:
                cfg = item["config"]
                payload = cfg.to_payload()  # 把 cfg 对象转为字典
                payload.update(
                    {
                        "id": self._serialize_scalar(item.get("id")),
                        "status": status,   # 该条目所在队列的状态
                        "raw_status": self._serialize_scalar(item.get("status", status)),   # 条目自己的状态
                        "attempt": int(item.get("attempt", 0)),
                        "created_at": self._format_dt(item.get("created_at")),
                        "started_at": self._format_dt(item.get("started_at")),
                        "completed_at": self._format_dt(item.get("completed_at")),
                        "return_code": self._serialize_scalar(item.get("return_code")),
                        "work_dir": self._serialize_scalar(item.get("work_dir")),   # 实际展开的绝对路径工作目录
                        "run_id": self._serialize_scalar(item.get("run_id")),
                    }
                )
                output.append(payload)
            return output

        finished_records = [item for item in self._finished if item.get("status") == "success"]
        error_records = [item for item in self._finished if item.get("status") != "success"]

        summary = {
            "total": len(self._scheduled),
            "pending": len(self._pending),
            "running": len(self._active),
            "finished": len(finished_records),
            "errors": len(error_records),
            "status_indicator": self._status_indicator,
            "waiting_for_shutdown": self._waiting_for_shutdown,
            "shutdown_requested": self._shutdown_requested,
        }

        # 写入到文件
        self.state_store.write_state(
            pending=_build_queue(self._pending, ExperimentStatus.PENDING.value),
            running=_build_queue(self._active, ExperimentStatus.RUNNING.value),
            finished=_build_queue(finished_records, ExperimentStatus.FINISHED.value),
            errors=_build_queue(error_records, ExperimentStatus.ERROR.value),
            summary=summary,
        )

    def _new_task_id(self) -> str:
        self._task_counter += 1
        return f"task-{int(time.time())}-{self._task_counter:05d}"

    @staticmethod
    def _format_dt(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=LOCAL_TZ)
            return value.astimezone(LOCAL_TZ).isoformat()
        return str(value)

    @staticmethod
    def _serialize_scalar(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=LOCAL_TZ)
            return value.astimezone(LOCAL_TZ).isoformat()
        return str(value)

    # ------------------------------------------------------------------
    # 命令处理
    # ------------------------------------------------------------------
    def _consume_commands(self) -> None:
        commands = self.state_store.consume_commands()
        if not commands:
            return

        for command in commands:
            action = command.get("action")
            payload = command.get("payload", {})
            if action == "remove_pending":
                self._handle_remove_pending(payload)
            elif action == "terminate_running":
                self._handle_terminate_running(payload)
            elif action == "retry_error":
                self._handle_retry_error(payload)
            elif action == "remove_finished":
                self._handle_remove_finished(payload)
            elif action == "remove_error":
                self._handle_remove_error(payload)
            elif action == "shutdown_scheduler":
                self._handle_shutdown_scheduler()

        self._sync_state()

    def _handle_remove_pending(self, payload: Dict[str, Any]) -> None:
        task_id = payload.get("id")
        if not task_id:
            return
        before = len(self._pending)
        self._pending = [item for item in self._pending if item.get("id") != task_id]
        if len(self._pending) != before:
            print(f"🗑️ 已移除 pending 任务 {task_id}")

    def _handle_terminate_running(self, payload: Dict[str, Any]) -> None:
        task_id = payload.get("id")
        if not task_id:
            return
        for slot in list(self._active):
            if slot.get("id") != task_id:
                continue
            runtime = slot["experiment"]
            process = runtime["process"]
            
            # 更强力的进程终止逻辑
            print(f"🛑 开始终止运行任务 {task_id} (PID: {process.pid})")

            if not self._terminate_process_tree(process, task_id):
                print(f"⚠️ 任务 {task_id} 终止可能不完整 (PID: {process.pid})")
            
            # 设置实验实例错误状态        
            runtime["instance"].set_error("terminated by user")
            
            # 将任务移到完成列表
            self._finished.append(
                {
                    "config": slot["config"],
                    "status": "terminated",
                    "attempt": slot["attempt"],
                    "return_code": process.returncode,
                    "id": slot.get("id", self._new_task_id()),
                    "created_at": slot.get("created_at"),
                    "started_at": slot.get("started_at"),
                    "completed_at": datetime.now(tz=LOCAL_TZ),
                    "work_dir": str(runtime["instance"].work_dir),
                    "run_id": runtime["instance"].current_run_id,
                }
            )
            self._active.remove(slot)
            print(f"🛑 用户终止运行任务 {task_id} 已完成")
            break

    def _handle_retry_error(self, payload: Dict[str, Any]) -> None:
        task_id = payload.get("id")
        if not task_id:
            return
        for record in list(self._finished):
            if record.get("id") != task_id:
                continue
            if record.get("status") not in {"failed", "terminated"}:
                return
            cfg = record["config"]
            self._pending.insert(
                0,
                {
                    "config": cfg,
                    "order": record.get("order", 0),
                    "attempt": record.get("attempt", 0),
                    "id": record.get("id", self._new_task_id()),
                    "created_at": datetime.now(tz=LOCAL_TZ),
                },
            )
            print(f"↻ 重新调度任务 {task_id}")
            self._finished.remove(record)
            break

    def _handle_remove_finished(self, payload: Dict[str, Any]) -> None:
        task_id = payload.get("id")
        if not task_id:
            return
        before = len(self._finished)
        self._finished = [item for item in self._finished if item.get("id") != task_id]
        if len(self._finished) != before:
            print(f"🧹 已移除完成记录 {task_id}")

    def _handle_remove_error(self, payload: Dict[str, Any]) -> None:
        task_id = payload.get("id")
        if not task_id:
            return
        removed = False
        for record in list(self._finished):
            if record.get("id") == task_id and record.get("status") in {"failed", "terminated"}:
                self._finished.remove(record)
                removed = True
        if removed:
            print(f"🧹 已移除错误记录 {task_id}")

    def _handle_shutdown_scheduler(self) -> None:
        if self._shutdown_requested:
            return
        print("🛎️ 收到手动关机指令，准备退出调度器")
        self._shutdown_requested = True
        self._status_indicator = "stopped"

    def _terminate_process_tree(self, process, task_id: str) -> bool:
        """向整个进程组发送终止信号，必要时升级为强制杀死。"""
        if process.poll() is not None:
            print(f"ℹ️ 任务 {task_id} 已结束 (code={process.returncode})，无需再次终止")
            return True

        gentle_sent = self._send_signal(process, force=False)
        if gentle_sent and self._wait_for_exit(process, timeout=5):
            print(f"🛑 任务 {task_id} 已友好终止")
            return True

        print(f"🛑 友好终止任务 {task_id} 失败，准备强制终止")
        force_sent = self._send_signal(process, force=True)
        if force_sent and self._wait_for_exit(process, timeout=3):
            print(f"🛑 任务 {task_id} 已强制终止")
            return True

        return process.poll() is not None

    def _send_signal(self, process, *, force: bool) -> bool:
        if process.poll() is not None:
            return False
        try:
            if os.name != "nt":
                pgid = os.getpgid(process.pid)
                sig = signal.SIGKILL if force else signal.SIGTERM
                os.killpg(pgid, sig)
            else:  # pragma: no cover - Windows 特殊逻辑
                if force:
                    process.kill()
                else:
                    ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
                    if ctrl_break is not None:
                        process.send_signal(ctrl_break)
                    else:
                        process.terminate()
            return True
        except ProcessLookupError:
            return False
        except PermissionError as exc:
            print(f"⚠️ 无法向进程 {process.pid} 发送信号: {exc}")
            return False

    def _wait_for_exit(self, process, *, timeout: float) -> bool:
        if process.poll() is not None:
            return True
        try:
            process.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return process.poll() is not None

    def _should_retry(self, cfg: ScheduledExperiment, attempt: int) -> bool:
        if not self.auto_restart:
            return False
        if cfg.max_retries <= 0:
            return False
        return attempt < cfg.max_retries + 1

    def _print_summary(self) -> None:
        grouped: Dict[int, Dict[str, Any]] = {}
        for record in self._finished:
            cfg = record["config"]
            key = id(cfg)
            entry = grouped.setdefault(key, {"config": cfg, "records": []})
            entry["records"].append(record)

        success_without_retry = 0
        success_with_retry = 0
        final_failure_ids: set[int] = set()

        for entry in grouped.values():
            records = entry["records"]
            success_records = [item for item in records if item.get("status") == "success"]
            if success_records:
                first_success = min(success_records, key=lambda item: item["attempt"])
                if first_success["attempt"] <= 1:
                    success_without_retry += 1
                else:
                    success_with_retry += 1
            else:
                final_failure_ids.add(id(entry["config"]))

        print(
            "📊 调度完成: 直接成功 {} 个, 重试后成功 {} 个, 失败 {} 个".format(
                success_without_retry, success_with_retry, len(final_failure_ids)
            )
        )

        failed_records = [item for item in self._finished if item.get("status") == "failed"]
        if failed_records:
            recovered_config_ids = {
                id(entry["config"])
                for entry in grouped.values()
                if any(record.get("status") == "success" for record in entry["records"])
            }
            for item in failed_records:
                cfg = item["config"]
                marker = "🟡" if id(cfg) in recovered_config_ids else "🔴"
                print(
                    f"   - {marker} {cfg.name} "
                    f"(attempt={item['attempt']}, return_code={item.get('return_code')})"
                )