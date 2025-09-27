"""
实验状态枚举定义
"""
from enum import Enum


class ExperimentStatus(Enum):
    """实验状态枚举"""
    PENDING = "pending"     # 等待执行
    RUNNING = "running"     # 正在运行
    FINISHED = "finished"   # 已完成
    ERROR = "error"         # 执行错误

    def __str__(self):
        return self.value

    @classmethod
    def from_string(cls, status_str: str) -> "ExperimentStatus":
        """从字符串创建状态枚举"""
        for status in cls:
            if status.value == status_str:
                return status
        raise ValueError(f"Invalid status: {status_str}")