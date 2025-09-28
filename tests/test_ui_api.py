from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from experiment_manager.ui.server import create_app
from experiment_manager.ui.service import SchedulerUISession


def _build_workdir(base_dir: Path) -> tuple[Path, dict[str, object]]:
    work_dir = base_dir / "demo_2025-09-28__12-00-00"
    (work_dir / "terminal_logs").mkdir(parents=True, exist_ok=True)
    (work_dir / "metrics").mkdir(parents=True, exist_ok=True)

    metadata = {
        "name": "demo",
        "command": "python train.py",
        "timestamp": "2025-09-28T12:00:00",
        "status": "running",
    }
    (work_dir / "metadata.json").write_text("{\n  \"name\": \"demo\"\n}", encoding="utf-8")

    log_path = work_dir / "terminal_logs" / "run_0001.log"
    log_path.write_text("line1\nline2\n", encoding="utf-8")

    metric_path = work_dir / "metrics" / "result.csv"
    metric_path.write_text("step,loss\n1,0.5\n2,0.4\n", encoding="utf-8")

    record = {
        "id": "task-0001",
        "name": "demo",
        "command": "python train.py",
        "priority": 0,
        "tags": [],
        "gpu_ids": [],
        "cwd": None,
        "base_dir": None,
        "environment": {},
        "resume": None,
        "description": None,
        "repeats": 1,
        "max_retries": 0,
        "delay_seconds": 0.0,
        "status": "running",
        "raw_status": "running",
        "attempt": 1,
        "created_at": "2025-09-28T12:00:00.000Z",
        "started_at": "2025-09-28T12:00:05.000Z",
        "completed_at": None,
        "return_code": None,
        "work_dir": str(work_dir),
        "run_id": "run_0001",
    }
    return work_dir, record


def test_api_endpoints(tmp_path: Path) -> None:
    base_dir = tmp_path / "experiments"
    base_dir.mkdir(parents=True, exist_ok=True)

    session = SchedulerUISession(base_dir)
    _, record = _build_workdir(base_dir)
    session.state_store.write_state(
        pending=[],
        running=[record],
        finished=[],
        errors=[],
        summary={"total": 1, "running": 1},
    )

    app = create_app(session)
    client = TestClient(app)

    # state endpoint
    response = client.get("/api/state")
    assert response.status_code == 200
    state = response.json()
    assert state["running"][0]["id"] == "task-0001"

    # task details
    response = client.get("/api/tasks/task-0001")
    assert response.status_code == 200
    details = response.json()
    assert details["task"]["name"] == "demo"
    assert details["metadata"] is not None

    # log tail
    response = client.get("/api/tasks/task-0001/logs", params={"tail": 10})
    assert response.status_code == 200
    log_payload = response.json()
    assert log_payload["lines"][-1] == "line2"

    # metric preview download
    response = client.get("/api/tasks/task-0001/metrics/result.csv")
    assert response.status_code == 200
    metric_payload = response.json()
    assert metric_payload["type"] == "csv"
    assert metric_payload["columns"] == ["step", "loss"]

    # command enqueue
    response = client.post("/api/commands", json={"action": "remove_pending", "payload": {"id": "task-x"}})
    assert response.status_code == 200
    commands = session.state_store.consume_commands()
    assert commands and commands[0]["action"] == "remove_pending"