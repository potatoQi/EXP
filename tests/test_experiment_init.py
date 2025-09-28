"""
Test cases for Experiment.__init__ method
"""
import json
import pytest
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from experiment_manager.core.experiment import Experiment
from experiment_manager.core.status import ExperimentStatus


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