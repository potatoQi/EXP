"""
实验管理框架的核心模块

提供 Experiment 类和相关的状态管理功能
"""

from experiment_manager.core.experiment import Experiment, load_experiment
from experiment_manager.core.status import ExperimentStatus

__all__ = ["Experiment", "ExperimentStatus", "load_experiment"]