"""
实验管理框架

一个用于管理科研实验的Python框架，提供实验状态监控、日志管理、指标记录等功能。
"""

__version__ = "0.1.0"
__author__ = "Research Team"

from experiment_manager.core import Experiment, ExperimentStatus
from experiment_manager.scheduler import ExperimentScheduler

__all__ = [
    "Experiment", 
    "ExperimentStatus", 
    "ExperimentScheduler"
]