"""提供 UI 层访问调度器状态和实验详情的服务对象。"""
from __future__ import annotations

import asyncio
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..scheduler.state_store import ISO_TIMESTAMP, SchedulerCommand, SchedulerStateStore


@dataclass
class MetricPreview:
    """指标文件的概要信息。"""

    name: str
    rows: int
    columns: List[str]
    sample: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "rows": self.rows,
            "columns": self.columns,
            "sample": self.sample,
        }


class SchedulerUISession:
    """封装调度器 UI 访问操作。"""

    def __init__(self, base_experiment_dir: Path):
        self.base_dir = Path(base_experiment_dir).expanduser().resolve()
        self.state_store = SchedulerStateStore(self.base_dir)

    # ------------------------------------------------------------------
    # 状态访问
    # ------------------------------------------------------------------
    def get_state(self) -> Dict[str, Any]:
        return self.state_store.load_state()

    def find_task(self, task_id: str) -> Tuple[str, Dict[str, Any]]:
        state = self.get_state()
        for section in ("pending", "running", "finished", "errors"):
            for item in state.get(section, []):
                if item.get("id") == task_id:
                    return section, item
        raise KeyError(f"task {task_id} not found")

    # ------------------------------------------------------------------
    # 任务详情
    # ------------------------------------------------------------------
    def get_task_details(self, task_id: str) -> Dict[str, Any]:
        section, record = self.find_task(task_id)
        details: Dict[str, Any] = {
            "section": section,
            "task": record,
        }

        work_dir = record.get("work_dir")
        if work_dir:
            work_path = Path(work_dir)
            metadata = self._load_json(work_path / "metadata.json")
            details["metadata"] = metadata
            details["work_dir_exists"] = work_path.exists()
            if metadata and metadata.get("timestamp"):
                details["experiment_timestamp"] = metadata["timestamp"]

            details["terminal_logs"] = self._list_terminal_logs(work_path)
            details["metrics"] = [preview.to_dict() for preview in self._list_metric_previews(work_path)]
        else:
            details["work_dir_exists"] = False
            details["terminal_logs"] = []
            details["metrics"] = []

        return details

    def read_log(self, task_id: str, run_id: Optional[str] = None, tail: int = 200) -> Dict[str, Any]:
        _, record = self.find_task(task_id)
        work_dir = record.get("work_dir")
        if not work_dir:
            raise FileNotFoundError("日志目录尚未生成")
        log_path = self._resolve_log_path(Path(work_dir), run_id or record.get("run_id"))
        if not log_path.exists():
            raise FileNotFoundError(f"未找到日志文件: {log_path}")

        lines = self._tail_file(log_path, tail)
        return {
            "task_id": task_id,
            "run_id": run_id or record.get("run_id"),
            "path": str(log_path),
            "lines": lines,
        }

    def read_metric(self, task_id: str, filename: str, limit: int = 200) -> Dict[str, Any]:
        _, record = self.find_task(task_id)
        work_dir = record.get("work_dir")
        if not work_dir:
            raise FileNotFoundError("指标目录尚未生成")
        metrics_dir = Path(work_dir) / "metrics"
        target = metrics_dir / filename
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"未找到指标文件: {target}")

        if target.suffix.lower() == ".csv":
            with open(target, "r", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                columns = reader.fieldnames or []
                rows = []
                for idx, row in enumerate(reader):
                    if idx >= limit:
                        break
                    rows.append(row)
            return {
                "type": "csv",
                "columns": columns,
                "rows": rows,
            }
        else:
            data = self._load_json(target)
            if isinstance(data, list):
                data = data[:limit]
            return {
                "type": "json",
                "data": data,
            }

    # ------------------------------------------------------------------
    # 命令下发
    # ------------------------------------------------------------------
    def send_command(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        command = SchedulerCommand(action=action, payload=payload)
        self.state_store.enqueue_command(command)
        return command.to_dict()

    # ------------------------------------------------------------------
    # 日志流工具
    # ------------------------------------------------------------------
    async def stream_log(self, task_id: str, run_id: Optional[str], send_callable) -> None:
        _, record = self.find_task(task_id)
        work_dir = record.get("work_dir")
        if not work_dir:
            await send_callable({"event": "error", "message": "日志目录尚未生成"})
            return

        log_path = self._resolve_log_path(Path(work_dir), run_id or record.get("run_id"))
        if log_path is None:
            await send_callable({"event": "error", "message": "找不到日志文件"})
            return

        await send_callable({"event": "info", "message": f"监听日志: {log_path}"})

        position = 0
        try:
            while True:
                if not log_path.exists():
                    await asyncio.sleep(1)
                    continue

                new_lines: List[str] = []
                with open(log_path, "r", encoding="utf-8", errors="ignore") as fh:
                    fh.seek(position)
                    chunk = fh.read()
                    position = fh.tell()
                if chunk:
                    new_lines = [line for line in chunk.splitlines()]

                if new_lines:
                    await send_callable({
                        "event": "append",
                        "lines": new_lines,
                    })

                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _list_terminal_logs(self, work_dir: Path) -> List[Dict[str, Any]]:
        terminal_dir = work_dir / "terminal_logs"
        if not terminal_dir.exists():
            return []
        result: List[Dict[str, Any]] = []
        for path in sorted(terminal_dir.glob("*.log")):
            stat = path.stat()
            result.append(
                {
                    "name": path.name,
                    "run_id": path.stem,
                    "size": stat.st_size,
                    "updated_at": self._format_timestamp(stat.st_mtime),
                }
            )
        return result

    def _list_metric_previews(self, work_dir: Path) -> List[MetricPreview]:
        metrics_dir = work_dir / "metrics"
        previews: List[MetricPreview] = []
        if not metrics_dir.exists():
            return previews

        for path in sorted(metrics_dir.iterdir()):
            if path.suffix.lower() != ".csv":
                continue
            with open(path, "r", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                rows = []
                for idx, row in enumerate(reader):
                    if idx >= 5:
                        break
                    rows.append(row)
                columns = reader.fieldnames or []
            previews.append(
                MetricPreview(
                    name=path.name,
                    rows=max(self._count_file_rows(path) - 1, 0),
                    columns=columns,
                    sample=rows,
                )
            )
        return previews

    def _resolve_log_path(self, work_dir: Path, run_id: Optional[str]) -> Optional[Path]:
        logs = self._list_terminal_logs(work_dir)
        if not logs:
            return None
        if run_id:
            candidate = work_dir / "terminal_logs" / f"{run_id}.log"
            if candidate.exists():
                return candidate
        # fallback to last log
        latest = max(logs, key=lambda item: item.get("updated_at") or "")
        return work_dir / "terminal_logs" / latest["name"]

    @staticmethod
    def _tail_file(path: Path, limit: int) -> List[str]:
        if limit <= 0:
            limit = 200
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
        return [line.rstrip("\n") for line in lines[-limit:]]

    @staticmethod
    def _load_json(path: Path) -> Any:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def _count_file_rows(path: Path) -> int:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return sum(1 for _ in fh)

    @staticmethod
    def _format_timestamp(value: float) -> str:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(value, tz=timezone.utc).strftime(ISO_TIMESTAMP)


__all__ = ["SchedulerUISession"]
