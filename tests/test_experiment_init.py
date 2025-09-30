"""
Test cases for Experiment.__init__ method
"""
import json
import pytest
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from experiment_manager.core.experiment import Experiment
from experiment_manager.core.status import ExperimentStatus
from experiment_manager.integrations.lark.bitable import LarkBitableError
from experiment_manager.integrations.lark.sync_utils import (
    normalize_lark_fields,
    resolve_lark_config,
    sync_row_to_lark,
)


class TestExperimentInit:
    """Test cases for Experiment.__init__ method"""

    @pytest.fixture
    def temp_base_dir(self):
        """Create a temporary directory for experiments"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_init_basic_required_params(self, temp_base_dir):
        """Test initialization with only required parameters"""
        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir
        )
        
        assert exp.name == "test_exp"
        assert exp.command == "python train.py"
        assert exp.base_dir == temp_base_dir
        assert exp.tags == []
        assert exp.gpu_ids == []
        assert exp.cwd is None
        assert exp.description is None
        assert exp.status == ExperimentStatus.PENDING
        assert exp.pid is None
        assert exp.current_run_id == "run_0001"

    def test_init_with_optional_params(self, temp_base_dir):
        """Test initialization with all optional parameters"""
        cwd_path = Path("/custom/work/dir")
        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir,
            tags=["ml", "training"],
            gpu_ids=[0, 1],
            cwd=cwd_path,
            description="Test experiment description"
        )
        
        assert exp.tags == ["ml", "training"]
        assert exp.gpu_ids == [0, 1]
        assert exp.cwd == cwd_path
        assert exp.description == "Test experiment description"

    def test_init_none_base_dir_raises_error(self):
        """Test that None base_dir raises ValueError"""
        with pytest.raises(ValueError, match="base_dir 参数是必传的"):
            Experiment(
                name="test_exp",
                command="python train.py",
                base_dir=None
            )

    @patch('experiment_manager.core.experiment.datetime')
    def test_init_creates_directory_structure(self, mock_datetime, temp_base_dir):
        """Test that initialization creates proper directory structure"""
        fixed_time = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = fixed_time
        
        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir
        )
        
        # Check work directory exists
        expected_work_dir = temp_base_dir / "test_exp_2023-01-01__12-00-00"
        assert exp.work_dir == expected_work_dir
        assert exp.work_dir.exists()
        
        # Check subdirectories
        assert (exp.work_dir / "terminal_logs").exists()
        assert (exp.work_dir / "metrics").exists()

    @patch('experiment_manager.core.experiment.datetime')
    def test_init_saves_metadata(self, mock_datetime, temp_base_dir):
        """Test that initialization saves metadata.json"""
        fixed_time = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = fixed_time
        
        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir,
            tags=["test"],
            gpu_ids=[0],
            description="Test desc"
        )
        
        metadata_file = exp.work_dir / "metadata.json"
        assert metadata_file.exists()
        
        with open(metadata_file, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        assert metadata["name"] == "test_exp"
        assert metadata["command"] == "python train.py"
        assert metadata["tags"] == ["test"]
        assert metadata["gpu_ids"] == [0]
        assert metadata["status"] == ExperimentStatus.PENDING.value
        assert metadata["current_run_id"] == "run_0001"
        assert metadata["description"] == "Test desc"

    def test_init_with_resume_existing_experiment(self, temp_base_dir):
        """Test initialization with resume parameter for existing experiment"""
        # Create an existing experiment directory
        existing_timestamp = "2023-01-01__12-00-00"
        existing_dir = temp_base_dir / f"test_exp_{existing_timestamp}"
        existing_dir.mkdir(parents=True)
        (existing_dir / "terminal_logs").mkdir()
        (existing_dir / "metrics").mkdir()
        
        # Create metadata for existing experiment
        existing_metadata = {
            "name": "test_exp",
            "command": "python old_train.py",
            "tags": ["old_tag"],
            "timestamp": "2023-01-01T12:00:00",
            "status": ExperimentStatus.FINISHED.value,
            "pid": None,
            "gpu_ids": [1, 2],
            "current_run_id": "run_0003",
            "cwd": "/old/work/dir",
            "description": "Old description"
        }
        
        with open(existing_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(existing_metadata, f)
        
        # Initialize with resume
        exp = Experiment(
            name="test_exp",
            command="python new_train.py",
            base_dir=temp_base_dir,
            resume=existing_timestamp
        )
        
        # Check that it loaded the existing experiment's data
        assert exp.work_dir == existing_dir
        assert exp.timestamp == datetime.fromisoformat("2023-01-01T12:00:00")
        assert exp.status == ExperimentStatus.PENDING  # Status reset to PENDING
        assert exp.pid is None
        assert exp.gpu_ids == [1, 2]  # Inherited from existing
        assert exp.cwd == Path("/old/work/dir")  # Inherited from existing
        assert exp.description == "Old description"  # Inherited from existing
        assert exp.tags == ["old_tag"]  # Inherited from existing
        assert exp.command == "python new_train.py"  # New command

    def test_init_with_resume_nonexistent_experiment(self, temp_base_dir):
        """Test that resume with nonexistent directory raises ValueError"""
        with pytest.raises(ValueError, match="未找到可继续的实验目录"):
            Experiment(
                name="test_exp",
                command="python train.py",
                base_dir=temp_base_dir,
                resume="2023-01-01__12-00-00"
            )

    def test_init_with_resume_explicit_params_override(self, temp_base_dir):
        """Test that explicit parameters override inherited ones when resuming"""
        # Create existing experiment
        existing_timestamp = "2023-01-01__12-00-00"
        existing_dir = temp_base_dir / f"test_exp_{existing_timestamp}"
        existing_dir.mkdir(parents=True)
        (existing_dir / "terminal_logs").mkdir()
        (existing_dir / "metrics").mkdir()
        
        existing_metadata = {
            "name": "test_exp",
            "command": "python old_train.py",
            "tags": ["old_tag"],
            "timestamp": "2023-01-01T12:00:00",
            "status": ExperimentStatus.FINISHED.value,
            "pid": None,
            "gpu_ids": [1, 2],
            "current_run_id": "run_0003",
            "cwd": "/old/work/dir",
            "description": "Old description"
        }
        
        with open(existing_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(existing_metadata, f)
        
        # Initialize with resume and explicit overrides
        exp = Experiment(
            name="test_exp",
            command="python new_train.py",
            base_dir=temp_base_dir,
            resume=existing_timestamp,
            tags=["new_tag"],
            gpu_ids=[3, 4],
            cwd=Path("/new/work/dir"),
            description="New description"
        )
        
        # Check that explicit parameters override inherited ones
        assert exp.tags == ["new_tag"]
        assert exp.gpu_ids == [3, 4]
        assert exp.cwd == Path("/new/work/dir")
        assert exp.description == "New description"

    def test_smart_start_next_run_first_run(self, temp_base_dir):
        """Test _smart_start_next_run creates run_0001 for first run"""
        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir
        )
        
        assert exp.current_run_id == "run_0001"
        log_file = exp.work_dir / "terminal_logs" / "run_0001.log"
        assert log_file.exists()

    @patch('experiment_manager.core.experiment.datetime')
    def test_smart_start_next_run_with_existing_runs(self, mock_datetime, temp_base_dir):
        """Test _smart_start_next_run finds next available run number"""
        # Mock datetime for consistent directory naming
        fixed_time = datetime(2023, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = fixed_time
        
        # Create work directory with existing log files
        work_dir = temp_base_dir / "test_exp_2023-01-01__12-00-00"
        log_dir = work_dir / "terminal_logs"
        log_dir.mkdir(parents=True)
        
        # Create existing log files
        (log_dir / "run_0001.log").touch()
        (log_dir / "run_0003.log").touch()
        (log_dir / "run_0005.log").touch()
        (log_dir / "invalid_name.log").touch()  # Should be ignored
        
        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir
        )
        
        # Should find next available number after 0005
        assert exp.current_run_id == "run_0006"

    def test_init_creates_log_entry_for_new_run(self, temp_base_dir):
        """Test that initialization creates initial log entries"""
        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir,
            gpu_ids=[0, 1],
            description="Test experiment"
        )
        
        log_content = exp.read_log()
        assert "实验初始化完成" in log_content
        assert "运行编号: run_0001" in log_content
        assert "实验名称: test_exp" in log_content
        assert "启动命令: python train.py" in log_content
        assert "分配GPU: [0, 1]" in log_content
        assert "实验描述: Test experiment" in log_content
        assert str(exp.work_dir) in log_content

    def test_metadata_timestamp_uses_shanghai_timezone(self, temp_base_dir):
        exp = Experiment(
            name="tz_exp",
            command="python train.py",
            base_dir=temp_base_dir,
        )

        metadata_file = exp.work_dir / "metadata.json"
        with open(metadata_file, "r", encoding="utf-8") as fh:
            metadata = json.load(fh)

        ts = metadata["timestamp"]
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None
        offset = parsed.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 8 * 3600

    def test_init_parses_lark_url(self, temp_base_dir):
        url = (
            "https://example.feishu.cn/base/MockAppToken123456789abcdef"
            "?table=tblMockTable123&view=vewMockView123"
        )

        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir,
            lark_config={
                "url": url,
                "app_id": "APP_ID",
                "app_secret": "APP_SECRET",
            },
        )

        expected = {
            "app_id": "APP_ID",
            "app_secret": "APP_SECRET",
            "url": url,
            "app_token": "MockAppToken123456789abcdef",
            "table_id": "tblMockTable123",
            "view_id": "vewMockView123",
        }

        assert exp.lark_config == expected

        metadata_file = exp.work_dir / "metadata.json"
        with open(metadata_file, "r", encoding="utf-8") as fh:
            metadata = json.load(fh)

        assert metadata["lark_config"] == expected

    def test_resolve_lark_config_updates_tokens_from_url(self, temp_base_dir):
        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir,
            lark_config={
                "app_id": "ENV_APP",
                "app_secret": "ENV_SECRET",
                "app_token": "app_old",
                "table_id": "tbl_old",
                "view_id": "vew_old",
            },
        )

        new_url = (
            "https://example.feishu.cn/base/appNewToken"
            "?table=tblNewTable&view=vewNewView"
        )

        logs: List[str] = []
        resolved = resolve_lark_config(
            {"url": new_url},
            existing=exp.lark_config,
            logger=lambda message: logs.append(message),
        )

        assert resolved["app_token"] == "appNewToken"
        assert resolved["table_id"] == "tblNewTable"
        assert resolved["view_id"] == "vewNewView"
        assert resolved["url"] == new_url
        assert resolved["app_id"] == "ENV_APP"
        assert resolved["app_secret"] == "ENV_SECRET"

        # 原始实验配置应该保持不变（resolve_lark_config 不会自动更新实验实例）
        assert exp.lark_config["app_token"] == "app_old"
        assert exp.lark_config["table_id"] == "tbl_old"
        assert exp.lark_config["view_id"] == "vew_old"

        # metadata.json 文件也应该保持原始配置不变
        metadata_file = exp.work_dir / "metadata.json"
        with open(metadata_file, "r", encoding="utf-8") as fh:
            metadata = json.load(fh)

        assert metadata["lark_config"]["app_token"] == "app_old"
        assert metadata["lark_config"]["table_id"] == "tbl_old"

    def test_sync_row_to_lark_success_logs_record_ids(self, monkeypatch, temp_base_dir):
        logs = []
        monkeypatch.setattr(
            "experiment_manager.integrations.lark.sync_utils.list_field_names",
            lambda cfg, use_cache=True: {"metric": 1},
        )
        monkeypatch.setattr(
            "experiment_manager.integrations.lark.sync_utils.sync_record",
            lambda cfg, fields: ["rec123"],
        )

        result = sync_row_to_lark({"metric": 1}, {
            "app_id": "ENV_APP",
            "app_secret": "ENV_SECRET",
            "app_token": "app_token",
            "table_id": "table_id",
        }, logger=lambda message: logs.append(message))

        assert result is True
        assert logs[-1] == "飞书同步成功，记录ID: rec123"

    def test_sync_row_to_lark_handles_sdk_errors(self, monkeypatch, temp_base_dir):
        logs = []
        monkeypatch.setattr(
            "experiment_manager.integrations.lark.sync_utils.list_field_names",
            lambda cfg, use_cache=True: {"metric": 1},
        )

        def _raise_error(cfg, fields):
            raise LarkBitableError("错误信息")

        monkeypatch.setattr(
            "experiment_manager.integrations.lark.sync_utils.sync_record",
            _raise_error,
        )

        result = sync_row_to_lark({"metric": 1}, {
            "app_id": "ENV_APP",
            "app_secret": "ENV_SECRET",
            "app_token": "app_token",
            "table_id": "table_id",
        }, logger=lambda message: logs.append(message))

        assert result is False
        assert logs[-1].startswith("飞书同步失败: 错误信息")

    def test_sync_row_to_lark_skips_when_field_missing(self, monkeypatch, temp_base_dir):
        logs: List[str] = []
        monkeypatch.setattr(
            "experiment_manager.integrations.lark.sync_utils.list_field_names",
            lambda cfg, use_cache=True: {"现有字段": 1},
        )

        spy_called = {"value": False}

        def _spy_sync(cfg, fields):
            spy_called["value"] = True
            return []

        monkeypatch.setattr(
            "experiment_manager.integrations.lark.sync_utils.sync_record",
            _spy_sync,
        )

        result = sync_row_to_lark({"metric": 1}, {
            "app_id": "ENV_APP",
            "app_secret": "ENV_SECRET",
            "app_token": "app_token",
            "table_id": "table_id",
        }, logger=lambda message: logs.append(message))

        assert result is False
        assert any("未找到字段 metric" in message for message in logs)
        assert spy_called["value"] is False

    def test_sync_row_to_lark_converts_datetime_to_timestamp(self, monkeypatch, temp_base_dir):
        captured: Dict[str, Any] = {}
        monkeypatch.setattr(
            "experiment_manager.integrations.lark.sync_utils.list_field_names",
            lambda cfg, use_cache=True: {"recorded_at": 5},
        )

        def _capture(cfg, fields):
            captured.update(fields)
            return ["rec456"]

        monkeypatch.setattr(
            "experiment_manager.integrations.lark.sync_utils.sync_record",
            _capture,
        )

        sample_dt = datetime(2025, 9, 29, 9, 30, 0)

        result = sync_row_to_lark({"recorded_at": sample_dt}, {
            "app_id": "ENV_APP",
            "app_secret": "ENV_SECRET",
            "app_token": "app_token",
            "table_id": "table_id",
        }, logger=lambda message: None)

        assert result is True
        assert captured, "字段应被发送到飞书"
        assert isinstance(captured.get("recorded_at"), int)
        assert captured["recorded_at"] >= 1_000_000_000_000

    def test_normalize_lark_fields_converts_text_numbers(self):
        row = {"batch": 3, "train_loss": 0.5}
        field_types = {"batch": 1, "train_loss": 2}

        normalized = normalize_lark_fields(row, field_types)

        assert normalized["batch"] == "3"
        assert isinstance(normalized["batch"], str)
        assert normalized["train_loss"] == 0.5

    def test_normalize_lark_fields_skips_empty_date_field(self):
        row = {"recorded_at": ""}
        field_types = {"recorded_at": 5}

        normalized = normalize_lark_fields(row, field_types)

        assert "recorded_at" not in normalized

    def test_normalize_lark_fields_handles_number_fields(self):
        """测试数字字段的正确处理"""
        row = {"val_loss": 1.234, "val_acc": 56.78, "epoch_time": 486}
        field_types = {"val_loss": 2, "val_acc": 2, "epoch_time": 2}

        normalized = normalize_lark_fields(row, field_types)

        assert normalized["val_loss"] == 1.234
        assert normalized["val_acc"] == 56.78
        assert normalized["epoch_time"] == 486

    def test_normalize_lark_fields_handles_invalid_numbers(self):
        """测试无效数字值的处理"""
        row = {"bad_val": None, "nan_val": float('nan'), "inf_val": float('inf')}
        field_types = {"bad_val": 2, "nan_val": 2, "inf_val": 2}

        normalized = normalize_lark_fields(row, field_types)

        # None 转换为 0
        assert normalized["bad_val"] == 0
        # NaN 和 inf 应该保持原值
        assert str(normalized["nan_val"]) == "nan"
        assert normalized["inf_val"] == float('inf')

    def test_init_preserves_provided_lark_config(self, temp_base_dir):
        exp = Experiment(
            name="test_exp",
            command="python train.py",
            base_dir=temp_base_dir,
            lark_config={"view_id": "OVERRIDE_VIEW"},
        )

        expected = {"view_id": "OVERRIDE_VIEW"}
        assert exp.lark_config == expected

        metadata_file = exp.work_dir / "metadata.json"
        with open(metadata_file, "r", encoding="utf-8") as fh:
            metadata = json.load(fh)

        assert metadata["lark_config"] == expected

    def test_resume_merges_existing_and_provided_lark_config(self, temp_base_dir):
        existing_timestamp = "2023-01-01__12-00-00"
        existing_dir = temp_base_dir / f"test_exp_{existing_timestamp}"
        existing_dir.mkdir(parents=True)
        (existing_dir / "terminal_logs").mkdir()
        (existing_dir / "metrics").mkdir()

        existing_metadata = {
            "name": "test_exp",
            "command": "python old_train.py",
            "tags": ["old_tag"],
            "timestamp": "2023-01-01T12:00:00+08:00",
            "status": ExperimentStatus.FINISHED.value,
            "pid": None,
            "gpu_ids": [1, 2],
            "current_run_id": "run_0003",
            "cwd": "/old/work/dir",
            "description": "Old description",
            "lark_config": {"table_id": "EXISTING_TABLE"},
        }

        with open(existing_dir / "metadata.json", "w", encoding="utf-8") as fh:
            json.dump(existing_metadata, fh)

        exp = Experiment(
            name="test_exp",
            command="python new_train.py",
            base_dir=temp_base_dir,
            resume=existing_timestamp,
            lark_config={"view_id": "OVERRIDE_VIEW"},
        )

        expected = {
            "table_id": "EXISTING_TABLE",
            "view_id": "OVERRIDE_VIEW",
        }
        assert exp.lark_config == expected

        metadata_file = exp.work_dir / "metadata.json"
        with open(metadata_file, "r", encoding="utf-8") as fh:
            metadata = json.load(fh)

        assert metadata["lark_config"] == expected