"""持久化调度器状态以及命令队列的工具。

该模块负责在磁盘上维护以下两个文件：

- ``scheduler_state.json``：记录当前调度器的 Pending/Running/Finished/Error 实验列表。
- ``commands.json``：UI 写入的命令队列，调度器消费后会自动清空。

文件放置在 ``<base_experiment_dir>/.exp_state`` 目录下，写入采用原子替换以尽量避免并发竞争。
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional

ISO_TIMESTAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


@dataclass
class SchedulerCommand:
    """UI 推送给调度器的命令条目。"""

    action: str
    payload: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S%f"))
    created_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).strftime(ISO_TIMESTAMP))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class SchedulerStateStore:
    """针对调度器 UI 的状态/命令持久层。"""

    def __init__(self, base_experiment_dir: Path):
        self.base_dir = Path(base_experiment_dir)
        self.state_dir = self.base_dir / ".exp_state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "scheduler_state.json"
        self.command_path = self.state_dir / "commands.json"
        self._lock = threading.Lock()

        if not self.state_path.exists():
            self._write_json(self.state_path, self._initial_state())
        if not self.command_path.exists():
            self._write_json(self.command_path, [])

    # ------------------------------------------------------------------
    # 状态文件操作
    # ------------------------------------------------------------------
    def load_state(self) -> Dict[str, Any]:
        return self._read_json(self.state_path)

    def write_state(
        self,
        *,
        pending: Iterable[MutableMapping[str, Any]],
        running: Iterable[MutableMapping[str, Any]],
        finished: Iterable[MutableMapping[str, Any]],
        errors: Iterable[MutableMapping[str, Any]],
        summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        data = {
            "updated_at": datetime.now(tz=timezone.utc).strftime(ISO_TIMESTAMP),
            "pending": list(pending),
            "running": list(running),
            "finished": list(finished),
            "errors": list(errors),
            "summary": summary or {},
        }
        self._write_json(self.state_path, data)

    # ------------------------------------------------------------------
    # 命令队列操作
    # ------------------------------------------------------------------
    def enqueue_command(self, command: SchedulerCommand) -> None:
        with self._lock:
            commands = self._read_json(self.command_path)
            if not isinstance(commands, list):
                commands = []
            commands.append(command.to_dict())
            self._write_json(self.command_path, commands)

    def consume_commands(self) -> List[Dict[str, Any]]:
        with self._lock:
            commands = self._read_json(self.command_path)
            self._write_json(self.command_path, [])
        if isinstance(commands, list):
            return commands
        return []

    def has_pending_commands(self) -> bool:
        data = self._read_json(self.command_path)
        return bool(data)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _initial_state(self) -> Dict[str, Any]:
        return {
            "updated_at": datetime.now(tz=timezone.utc).strftime(ISO_TIMESTAMP),
            "pending": [],
            "running": [],
            "finished": [],
            "errors": [],
            "summary": {},
        }

    def _read_json(self, path: Path) -> Any:
        if not path.exists():
            return [] if path == self.command_path else self._initial_state()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except json.JSONDecodeError:
            # 文件损坏时兜底返回空，避免阻塞主流程
            return [] if path == self.command_path else self._initial_state()

    def _write_json(self, path: Path, data: Any) -> None:
        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


__all__ = ["SchedulerStateStore", "SchedulerCommand", "ISO_TIMESTAMP"]
