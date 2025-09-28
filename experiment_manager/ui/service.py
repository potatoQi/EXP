"""提供 UI 层访问调度器状态和实验详情的服务对象。"""
from __future__ import annotations

import asyncio
import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from experiment_manager.scheduler.state_store import (
    SchedulerCommand,
    SchedulerStateStore,
)


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
    # 实验查询
    # ------------------------------------------------------------------
    def search_experiments(
        self,
        name_pattern: Optional[str] = None,
        tags: Optional[List[str]] = None,
        description: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """搜索实验
        
        Args:
            name_pattern: 实验名正则匹配模式
            tags: 标签列表，需要包含所有指定标签
            description: 描述关键词搜索
            start_time: 开始时间过滤 (ISO format)
            end_time: 结束时间过滤 (ISO format)
            
        Returns:
            List of experiment records with metadata
        """
        experiments = []
        
        # Debug: Log search parameters and base directory
        print(f"[DEBUG] Searching in base_dir: {self.base_dir}")
        print(f"[DEBUG] Search params - name: {name_pattern}, tags: {tags}, desc: {description}")
        
        if not self.base_dir.exists():
            print(f"[DEBUG] Base directory does not exist: {self.base_dir}")
            return experiments
        
        # 收集所有实验目录
        directories_found = 0
        metadata_files_found = 0
        
        for exp_dir in self.base_dir.glob("*"):
            if not exp_dir.is_dir() or exp_dir.name.startswith('.'):
                continue
                
            directories_found += 1
            print(f"[DEBUG] Checking directory: {exp_dir}")
                
            metadata_file = exp_dir / "metadata.json"
            if not metadata_file.exists():
                print(f"[DEBUG] No metadata.json in {exp_dir}")
                continue
                
            metadata_files_found += 1
            print(f"[DEBUG] Found metadata.json in {exp_dir}")
                
            try:
                metadata = self._load_json(metadata_file)
                if not metadata:
                    print(f"[DEBUG] Empty or invalid metadata in {metadata_file}")
                    continue
                    
                # 构建实验记录
                experiment = {
                    "name": metadata.get("name", ""),
                    "path": str(exp_dir),
                    "timestamp": metadata.get("timestamp", ""),
                    "tags": metadata.get("tags", []),
                    "description": metadata.get("description", ""),
                    "status": metadata.get("status", ""),
                    "command": metadata.get("command", ""),
                }
                
                print(f"[DEBUG] Parsed experiment: {experiment['name']} at {experiment['path']}")
                
                # 应用过滤条件
                if not self._matches_filters(experiment, name_pattern, tags, description, start_time, end_time):
                    print(f"[DEBUG] Experiment {experiment['name']} filtered out")
                    continue
                    
                experiments.append(experiment)
                print(f"[DEBUG] Added experiment: {experiment['name']}")
                
            except Exception as e:
                # 跳过无法解析的实验
                print(f"[DEBUG] Error parsing {metadata_file}: {e}")
                continue
        
        print(f"[DEBUG] Search results: {directories_found} dirs, {metadata_files_found} metadata files, {len(experiments)} experiments")
        
        # 按时间戳排序
        experiments.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return experiments
    
    def get_experiment_files(self, experiment_path: str) -> List[Dict[str, Any]]:
        """获取实验目录下的文件列表
        
        Args:
            experiment_path: 实验目录绝对路径
            
        Returns:
            List of file information
        """
        exp_dir = Path(experiment_path)
        if not exp_dir.exists() or not exp_dir.is_dir():
            return []
            
        files = []
        
        # 递归获取文件
        def collect_files(directory: Path, prefix: str = ""):
            try:
                for item in sorted(directory.iterdir()):
                    rel_path = prefix + item.name
                    if item.is_file():
                        stat = item.stat()
                        files.append({
                            "name": item.name,
                            "path": rel_path,
                            "absolute_path": str(item),
                            "size": stat.st_size,
                            "modified": self._format_timestamp(stat.st_mtime),
                            "type": "file"
                        })
                    elif item.is_dir() and not item.name.startswith('.'):
                        files.append({
                            "name": item.name,
                            "path": rel_path,
                            "absolute_path": str(item),
                            "type": "directory"
                        })
                        collect_files(item, rel_path + "/")
            except PermissionError:
                pass
                
        collect_files(exp_dir)
        return files
    
    def read_experiment_file(self, file_path: str, max_size: int = 1024 * 1024) -> Dict[str, Any]:
        """读取实验文件内容
        
        Args:
            file_path: 文件绝对路径
            max_size: 最大文件大小限制
            
        Returns:
            File content and metadata
        """
        file_path_obj = Path(file_path)
        if not file_path_obj.exists() or not file_path_obj.is_file():
            raise FileNotFoundError(f"File not found: {file_path}")
            
        stat = file_path_obj.stat()
        if stat.st_size > max_size:
            raise ValueError(f"File too large: {stat.st_size} bytes (max: {max_size})")
            
        # 尝试读取文本文件
        try:
            with open(file_path_obj, 'r', encoding='utf-8') as f:
                content = f.read()
            return {
                "content": content,
                "size": stat.st_size,
                "encoding": "utf-8",
                "type": "text"
            }
        except UnicodeDecodeError:
            # 如果不是文本文件，返回基本信息
            return {
                "content": None,
                "size": stat.st_size,
                "encoding": "binary",
                "type": "binary",
                "message": "Binary file cannot be previewed"
            }
    
    def _matches_filters(
        self,
        experiment: Dict[str, Any],
        name_pattern: Optional[str],
        tags: Optional[List[str]],
        description: Optional[str],
        start_time: Optional[str],
        end_time: Optional[str],
    ) -> bool:
        """检查实验是否匹配过滤条件"""
        
        # 名称正则匹配
        if name_pattern:
            try:
                if not re.search(name_pattern, experiment.get("name", ""), re.IGNORECASE):
                    return False
            except re.error:
                # 正则表达式错误，回退到简单匹配
                if name_pattern.lower() not in experiment.get("name", "").lower():
                    return False
        
        # 标签匹配 (需要包含所有指定标签)
        if tags:
            exp_tags = experiment.get("tags", [])
            if not all(tag in exp_tags for tag in tags):
                return False
        
        # 描述关键词搜索
        if description:
            exp_desc = experiment.get("description", "")
            if description.lower() not in exp_desc.lower():
                return False
        
        # 时间范围过滤
        exp_time = experiment.get("timestamp", "")
        if exp_time:
            try:
                if start_time and exp_time < start_time:
                    return False
                if end_time and exp_time > end_time:
                    return False
            except (ValueError, TypeError):
                # 时间格式错误，跳过时间过滤
                pass
        
        return True

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
        from datetime import datetime
        from zoneinfo import ZoneInfo

        shanghai_tz = ZoneInfo("Asia/Shanghai")
        return datetime.fromtimestamp(value, tz=shanghai_tz).isoformat()


__all__ = ["SchedulerUISession"]
