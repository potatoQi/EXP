from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from experiment_manager.ui.service import SchedulerUISession


@pytest.fixture
def session(tmp_path: Path) -> SchedulerUISession:
    base_dir = tmp_path / "experiments"
    base_dir.mkdir(parents=True, exist_ok=True)
    return SchedulerUISession(base_dir)


def _build_state_record(work_dir: Path) -> dict:
    return {
        "id": "task-0001",
        "name": "demo",
        "command": "python train.py",
        "attempt": 1,
        "created_at": "2025-09-27T12:00:00.000Z",
        "started_at": "2025-09-27T12:00:10.000Z",
        "completed_at": "2025-09-27T12:10:00.000Z",
        "work_dir": str(work_dir),
        "run_id": "run_0001",
    }


def test_get_state_returns_structure(session: SchedulerUISession) -> None:
    state = session.get_state()
    assert set(state.keys()) >= {"pending", "running", "finished", "errors", "summary", "updated_at"}


def test_get_task_details_reads_metadata(session: SchedulerUISession, tmp_path: Path) -> None:
    work_dir = session.base_dir / "demo_2025-09-27__12-00-00"
    (work_dir / "terminal_logs").mkdir(parents=True, exist_ok=True)
    (work_dir / "metrics").mkdir(parents=True, exist_ok=True)

    metadata = {
        "name": "demo",
        "command": "python train.py",
        "timestamp": "2025-09-27T12:00:00",
        "status": "finished",
    }
    with open(work_dir / "metadata.json", "w", encoding="utf-8") as fh:
        json.dump(metadata, fh)

    log_path = work_dir / "terminal_logs" / "run_0001.log"
    log_path.write_text("line1\nline2\n", encoding="utf-8")

    metrics_path = work_dir / "metrics" / "result.csv"
    metrics_path.write_text("step,loss\n1,0.5\n2,0.4\n", encoding="utf-8")

    record = _build_state_record(work_dir)
    session.state_store.write_state(pending=[], running=[], finished=[record], errors=[], summary={})

    details = session.get_task_details("task-0001")
    assert details["metadata"]["name"] == "demo"
    assert details["terminal_logs"] and details["terminal_logs"][0]["name"] == "run_0001.log"
    assert details["metrics"][0]["name"] == "result.csv"


def test_read_log_returns_tail(session: SchedulerUISession, tmp_path: Path) -> None:
    work_dir = session.base_dir / "demo_2025-09-27__12-00-00"
    (work_dir / "terminal_logs").mkdir(parents=True, exist_ok=True)
    (work_dir / "metrics").mkdir(parents=True, exist_ok=True)
    log_path = work_dir / "terminal_logs" / "run_0001.log"
    log_lines = "\n".join(f"line {idx}" for idx in range(50))
    log_path.write_text(log_lines, encoding="utf-8")

    record = _build_state_record(work_dir)
    session.state_store.write_state(pending=[], running=[record], finished=[], errors=[], summary={})

    result = session.read_log("task-0001", tail=5)
    assert len(result["lines"]) == 5
    assert result["lines"][-1] == "line 49"


@pytest.mark.asyncio
async def test_stream_log_sends_updates(session: SchedulerUISession, tmp_path: Path) -> None:
    work_dir = session.base_dir / "demo_2025-09-27__12-00-00"
    (work_dir / "terminal_logs").mkdir(parents=True, exist_ok=True)
    log_path = work_dir / "terminal_logs" / "run_0001.log"
    log_path.write_text("line1\n", encoding="utf-8")

    record = _build_state_record(work_dir)
    session.state_store.write_state(pending=[], running=[record], finished=[], errors=[], summary={})

    received: list[dict] = []

    async def collector(message: dict) -> None:
        received.append(message)
        if message.get("event") == "append" and "line2" in "\n".join(message.get("lines", [])):
            raise asyncio.CancelledError

    async def writer():
        await asyncio.sleep(0.5)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("line2\n")

    producer = asyncio.create_task(session.stream_log("task-0001", None, collector))
    modifier = asyncio.create_task(writer())

    with pytest.raises(asyncio.CancelledError):
        await producer
    await modifier
    assert any(msg.get("event") == "append" for msg in received)


def test_send_command_enqueues(session: SchedulerUISession) -> None:
    result = session.send_command("remove_pending", {"id": "task-0001"})
    assert result["action"] == "remove_pending"
    commands = session.state_store.consume_commands()
    assert commands and commands[0]["action"] == "remove_pending"
