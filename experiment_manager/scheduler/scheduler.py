"""实验调度器

读取 TOML 配置，一次性调度多组实验运行。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core import Experiment, ExperimentStatus
from ..utils.config import ConfigManager


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

    def __post_init__(self) -> None:
        self.sort_index = -self.priority


class ExperimentScheduler:
    """实验调度器：按配置顺序执行多组实验"""

    def __init__(self, config_path: Path, dry_run: bool = False):
        self.config_path = Path(config_path)
        self.config_dir = self.config_path.parent.resolve()
        self.config_manager = ConfigManager(self.config_path)   # 配置对象
        scheduler_cfg = self.config_manager.get_scheduler_config()  # 调度器配置
        self.max_concurrent = int(scheduler_cfg.get("max_concurrent_experiments", 1))   # 最大并发实验数
        self.check_interval = float(scheduler_cfg.get("check_interval", 10))    # 状态检查间隔
        base_dir_value = scheduler_cfg.get("base_experiment_dir")
        if not base_dir_value or not str(base_dir_value).strip():
            raise ValueError("配置项 scheduler.base_experiment_dir 为必填，请在配置文件中显式指定")
        base_dir_path = Path(base_dir_value).expanduser()
        if base_dir_path.is_absolute():
            self.base_experiment_dir = base_dir_path.resolve()
        else:
            self.base_experiment_dir = (self.config_dir / base_dir_path).resolve()   # 实验输出根目录
        self.auto_restart = bool(scheduler_cfg.get("auto_restart_on_error", False)) # 是否自动重启错误的实验

        self.dry_run = dry_run

        self._scheduled: List[ScheduledExperiment] = self._load_experiments_from_config()   # 加载所有组实验的配置
        self._pending: List[Dict[str, Any]] = []    # pending 列表
        self._active: List[Dict[str, Any]] = []     # running 列表
        self._finished: List[Dict[str, Any]] = []   # finished 列表

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

        while self._pending or self._active:
            self._try_launch_new_tasks()    # 尝试启动新任务
            if not self._active:
                # 没有 running task 但仍有 pending task 时，等待资源
                time.sleep(self.check_interval)
                continue

            time.sleep(self.check_interval)
            self._harvest_finished_tasks()  # 收割已完成的任务

        self._print_summary()

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
                }
            )

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
            task = self._pending[0]
            cfg = task["config"]

            self._pending.pop(0)
            task["attempt"] += 1
            # 打包为一个 experiment 实例
            experiment = self._launch_experiment(cfg, attempt=task["attempt"])

            self._active.append(
                {
                    "config": cfg,
                    "experiment": experiment,
                    "started_at": datetime.now(),
                    "attempt": task["attempt"],
                }
            )
            launched += 1

        if launched:
            print(f"🚀 本轮启动 {launched} 个实验，当前运行 {len(self._active)} 个。")

    def _launch_experiment(self, cfg: ScheduledExperiment, attempt: int):
        config_dir = self.config_dir

        if cfg.base_dir:
            custom_base = Path(cfg.base_dir).expanduser()
            if custom_base.is_absolute():
                base_dir = custom_base.resolve()
            else:
                base_dir = (config_dir / custom_base).resolve()
        else:
            base_dir = self.base_experiment_dir

        if cfg.cwd:
            custom_cwd = Path(cfg.cwd).expanduser()
            if custom_cwd.is_absolute():
                working_dir = custom_cwd.resolve()
            else:
                working_dir = (config_dir / custom_cwd).resolve()
        else:
            working_dir = config_dir

        exp = Experiment(
            base_dir=base_dir,
            name=cfg.name,
            command=cfg.command,
            gpu_ids=list(cfg.gpu_ids),
            cwd=working_dir,
            tags=cfg.tags,
            resume=cfg.resume,
            description=cfg.description,
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
                }
            )

            if not success:
                print(f"⚠️ 实验 {cfg.name} attempt {slot['attempt']} 失败 (code={return_code})")
                if self._should_retry(cfg, slot["attempt"]):
                    print(f"↺ 将实验 {cfg.name} 重新排队")
                    self._pending.insert(0, {"config": cfg, "order": slot.get("order", 0), "attempt": slot["attempt"]})

        self._active = still_running

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