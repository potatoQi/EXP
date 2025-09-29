"""
Scheduler-focused tests.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional
from unittest.mock import Mock, patch

import pytest
import toml

from experiment_manager.core.status import ExperimentStatus
from experiment_manager.scheduler.scheduler import ExperimentScheduler, ScheduledExperiment


@pytest.fixture
def scheduler_config_builder(tmp_path: Path) -> Callable[[Iterable[Dict[str, object]], Optional[Dict[str, object]]], Path]:
    """Create a temporary scheduler config file and return its path."""

    def _builder(
        experiments: Iterable[Dict[str, object]],
        scheduler_overrides: Optional[Dict[str, object]] = None,
    ) -> Path:
        base_output = tmp_path / "outputs"
        base_output.mkdir(parents=True, exist_ok=True)

        scheduler_cfg: Dict[str, object] = {
            "max_concurrent_experiments": 2,
            "check_interval": 0.0,
            "base_experiment_dir": str(base_output),
            "auto_restart_on_error": False,
        }
        if scheduler_overrides:
            scheduler_cfg.update(scheduler_overrides)

        config = {
            "scheduler": scheduler_cfg,
            "experiments": list(experiments),
        }

        config_path = tmp_path / "scheduler_config.toml"
        with open(config_path, "w", encoding="utf-8") as fh:
            toml.dump(config, fh)
        return config_path

    return _builder


def test_prepare_pending_queue_orders_by_priority_and_repeats(
    scheduler_config_builder: Callable[[Iterable[Dict[str, object]], Optional[Dict[str, object]]], Path]
) -> None:
    config_path = scheduler_config_builder(
        [
            {"name": "low", "command": "echo low", "priority": 1, "repeats": 1},
            {"name": "medium", "command": "echo medium", "priority": 3, "repeats": 1},
            {"name": "high", "command": "echo high", "priority": 5, "repeats": 2},
        ]
    )

    scheduler = ExperimentScheduler(config_path)
    scheduler._prepare_pending_queue()

    names = [item["config"].name for item in scheduler._pending]
    assert names == ["high", "high", "medium", "low"]
    assert all(task["attempt"] == 0 for task in scheduler._pending)


def test_try_launch_new_tasks_respects_concurrency(
    scheduler_config_builder: Callable[[Iterable[Dict[str, object]], Optional[Dict[str, object]]], Path]
) -> None:
    config_path = scheduler_config_builder(
        [
            {"name": "exp1", "command": "echo one"},
            {"name": "exp2", "command": "echo two"},
            {"name": "exp3", "command": "echo three"},
        ],
        scheduler_overrides={"max_concurrent_experiments": 2},
    )

    scheduler = ExperimentScheduler(config_path)
    scheduler._prepare_pending_queue()

    def fake_launch(cfg: ScheduledExperiment, attempt: int) -> Dict[str, object]:
        process = Mock()
        return {"instance": Mock(), "process": process}

    with patch.object(scheduler, "_launch_experiment", side_effect=fake_launch) as mock_launch:
        scheduler._try_launch_new_tasks()

    assert mock_launch.call_count == 2
    assert len(scheduler._active) == 2
    assert len(scheduler._pending) == 1
    assert all(slot["attempt"] == 1 for slot in scheduler._active)


def test_harvest_finished_tasks_moves_and_requeues(
    scheduler_config_builder: Callable[[Iterable[Dict[str, object]], Optional[Dict[str, object]]], Path]
) -> None:
    config_path = scheduler_config_builder(
        [],
        scheduler_overrides={"auto_restart_on_error": True},
    )
    scheduler = ExperimentScheduler(config_path)

    success_cfg = ScheduledExperiment(name="success", command="echo success")
    failure_cfg = ScheduledExperiment(name="failure", command="echo failure", max_retries=1)

    success_process = Mock()
    success_process.poll.return_value = 0
    success_process.returncode = 0
    success_instance = Mock()
    success_instance.status = ExperimentStatus.FINISHED

    failure_process = Mock()
    failure_process.poll.return_value = 0
    failure_process.returncode = 1
    failure_instance = Mock()
    failure_instance.status = ExperimentStatus.ERROR

    scheduler._pending = []
    scheduler._active = [
        {
            "config": success_cfg,
            "experiment": {"instance": success_instance, "process": success_process},
            "attempt": 1,
            "started_at": datetime.now(),
        },
        {
            "config": failure_cfg,
            "experiment": {"instance": failure_instance, "process": failure_process},
            "attempt": 1,
            "started_at": datetime.now(),
        },
    ]

    scheduler._harvest_finished_tasks()

    assert scheduler._active == []
    status_by_name = {record["config"].name: record["status"] for record in scheduler._finished}
    assert status_by_name["success"] == "success"
    assert status_by_name["failure"] == "failed"
    assert scheduler._pending
    assert scheduler._pending[0]["config"].name == "failure"
    assert scheduler._pending[0]["attempt"] == 1


def test_run_all_executes_until_complete(
    scheduler_config_builder: Callable[[Iterable[Dict[str, object]], Optional[Dict[str, object]]], Path]
) -> None:
    config_path = scheduler_config_builder(
        [
            {"name": "exp", "command": "echo run"},
        ],
        scheduler_overrides={
            "max_concurrent_experiments": 1,
            "linger_when_idle": False,
        },
    )
    scheduler = ExperimentScheduler(config_path)

    process = Mock()
    process.poll.side_effect = [None, 0]
    process.returncode = 0
    experiment_instance = Mock()
    experiment_instance.status = ExperimentStatus.FINISHED

    runtime = {"instance": experiment_instance, "process": process}

    with patch("experiment_manager.scheduler.scheduler.time.sleep", return_value=None), patch.object(
        scheduler, "_launch_experiment", return_value=runtime
    ) as mock_launch, patch("builtins.print") as mock_print:
        scheduler.run_all()

    assert mock_launch.call_count == 1
    assert scheduler._pending == []
    assert scheduler._active == []
    assert scheduler._finished and scheduler._finished[0]["status"] == "success"
    assert any("调度完成" in record.args[0] for record in mock_print.call_args_list if record.args)


def test_relative_base_dir_uses_invocation_cwd(
    scheduler_config_builder: Callable[[Iterable[Dict[str, object]], Optional[Dict[str, object]]], Path]
) -> None:
    config_path = scheduler_config_builder(
        [],
        scheduler_overrides={"base_experiment_dir": "./rel_outputs"},
    )

    run_cwd = config_path.parent / "invocation_cwd"
    run_cwd.mkdir()

    with patch("experiment_manager.scheduler.scheduler.Path.cwd", return_value=run_cwd):
        scheduler = ExperimentScheduler(config_path)

    assert scheduler.base_experiment_dir == (run_cwd / "rel_outputs").resolve()


def test_relative_experiment_cwd_uses_invocation_cwd(
    scheduler_config_builder: Callable[[Iterable[Dict[str, object]], Optional[Dict[str, object]]], Path]
) -> None:
    config_path = scheduler_config_builder(
        [
            {"name": "demo", "command": "echo demo", "cwd": "./nested"},
        ],
        scheduler_overrides={"base_experiment_dir": "./outputs"},
    )

    run_cwd = config_path.parent / "invocation_cwd"
    (run_cwd / "nested").mkdir(parents=True)

    with patch("experiment_manager.scheduler.scheduler.Path.cwd", return_value=run_cwd):
        scheduler = ExperimentScheduler(config_path, dry_run=True)

    with patch("experiment_manager.scheduler.scheduler.Experiment") as mock_experiment:
        instance = Mock()
        instance.work_dir = run_cwd / "dummy"
        instance.current_run_id = "run_0001"
        mock_experiment.return_value = instance

        scheduler._launch_experiment(scheduler._scheduled[0], attempt=1)

    assert mock_experiment.call_args is not None
    kwargs = mock_experiment.call_args.kwargs
    assert kwargs["cwd"] == (run_cwd / "nested").resolve()