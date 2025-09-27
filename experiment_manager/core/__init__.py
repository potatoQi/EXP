"""
实验管理框架的核心模块

提供 Experiment 类和相关的状态管理功能
"""

from .experiment import Experiment
from .status import ExperimentStatus

__all__ = ["Experiment", "ExperimentStatus"]