# 开发者指南

本项目提供一个用于批量调度、运行与管理实验的轻量级框架。调度器根据 TOML 配置批量启动实验，`Experiment` 核心类负责追踪运行状态、日志与指标。本指南面向将来维护者，帮助你理解代码结构、常见开发流程，以及如何在 `Experiment` 类中新增字段。

## 项目结构概览

```
experiment_manager/
  core/
    experiment.py      # 实验生命周期管理，实现状态/日志/元信息持久化
    status.py          # 实验状态枚举
  scheduler/
    scheduler.py       # 调度器主循环，解析配置并顺序/并发执行实验
      state_store.py     # 调度器状态与命令持久化（UI 读取）
  utils/
    config.py          # 配置加载与校验
    gpu.py             # GPU 可用性检测与分配工具（若启用）
   ui/
      service.py         # SchedulerUISession 封装，面向 FastAPI/CLI
      server.py          # FastAPI 路由与 WebSocket 服务
      cli.py             # `EXP` 命令入口，TensorBoard 风格体验
      static/            # 前端仪表盘（HTML/CSS/JS）
experiments/           # 实际运行生成的长期实验目录
experiments_tmp/       # 临时或测试性的实验输出（可清理）
toy_example/           # 示例脚本与配置
pyproject.toml         # Python 项目依赖声明
README.md              # 面向用户的运行说明
DEVELOPER_README.md    # 本指南
```

核心代码分层：

- **配置层 (`utils/config.py`)**：读取 `toml` 文件，提供访问接口与基础校验。
- **调度层 (`scheduler/scheduler.py`)**：将配置翻译为 `ScheduledExperiment` 队列，负责并发控制、失败重试、终端摘要等。
- **状态层 (`scheduler/state_store.py`)**：为 UI/CLI 提供磁盘持久化（`scheduler_state.json` + `commands.json`）。
- **实验层 (`core/experiment.py`)**：表示单个实验实例，负责目录搭建、日志记录、状态更新以及子进程生命周期管理；内置 `description` 字段用于记录自然语言描述。
*- **UI 层 (`ui/`)**：`SchedulerUISession` 读取状态、下发命令；`server.py` 提供 FastAPI REST + WebSocket；`static/` 前端页面实现状态面板、详情抽屉、实时日志分页视图；`cli.py` 封装 `EXP` 指令，负责环境检查、端口分配与浏览器打开。*

## 运行流程速览

1. 调度器读取配置，解析 `scheduler` 设置与 `experiments` 列表。
2. 每个实验配置被封装为 `ScheduledExperiment` 实例（支持 `priority`、`gpu_ids`、`description`、`max_retries` 等字段）。
3. 调度器按并发限制轮询 `_pending` 队列，将实验实例化为 `Experiment` 对象并启动子进程。
4. `Experiment` 会：
   - 初始化输出目录结构；
   - 自动分配 run id 并记录日志；
   - 启动子进程并持续收集 stdout/stderr；
   - 更新状态并将元数据写入 `metadata.json`。
5. 调度器循环收割已完成任务，根据返回码决定是否重试，并在结尾打印摘要。
6. UI/CLI（如 `EXP ./experiments`）通过 `SchedulerStateStore` 读取 `.exp_state/` 目录，展示最新状态并对命令队列写入操作，由调度器在下一轮消费。

## 关键模块说明

### `ScheduledExperiment`（调度器内部）
- 位于 `scheduler/scheduler.py`，`dataclass` 模型定义了调度器处理的字段。
- `_create_experiment_config` 会从 TOML 抽取字段填充该类。
- `_launch_experiment` 将 `ScheduledExperiment` 转换为真正的 `Experiment` 实例。
- 当前字段包括：`name`、`command`、`priority`、`tags`、`gpu_ids`、`cwd`、`base_dir`、`environment`、`resume`、`description`、`repeats`、`max_retries` 与 `delay_seconds`。

### `Experiment` 核心能力
- 日志：`append_log` 写入带时间戳的日志到 `terminal_logs/run_xxxx.log`。
- 状态：通过 `set_running` / `set_finished` / `set_error` 更新状态并回写 `metadata.json`。
- 元数据：`_save_metadata` 保存运行时信息（包含 `description` 等可扩展字段）；`load_from_dir` 支持恢复 (`resume`) 并自动填充缺省值。
- 子进程：`run` 方法封装了环境变量准备、命令执行、输出流监听与错误捕获。

## 环境与依赖

项目使用 Python 3.12+。依赖记录在 `pyproject.toml` 中，推荐使用 `pip` 或 `poetry` 安装：

```bash
pip install -e .
```

示例脚本统一放在 `toy_example/` 目录，便于快速体验与回归测试。

安装后会注册 `EXP` 命令，可在项目任意位置运行：

```bash
EXP path/to/experiments --host 0.0.0.0 --port 6066
```

命令最终调用 `experiment_manager.ui.cli:main`，内部使用 FastAPI + Uvicorn 提供 Web 服务，并自动选择空闲端口与打开浏览器。

## 常见开发任务

### 调度器逻辑调整
- 查看 `ExperimentScheduler.run_all` 及其辅助方法（启动、收割、重试、摘要）。
- 修改 `ScheduledExperiment` 字段时务必同步更新 `_create_experiment_config`、`_launch_experiment` 和相关日志输出。

### Experiment 行为扩展
- **UI/CLI 扩展**：
   - 后端：在 `SchedulerUISession` 中新增方法时，记得同步 FastAPI 路由以及测试（`tests/test_ui_service.py`、`tests/test_ui_api.py`）。
   - 前端：`static/app.js` 负责状态轮询、详情抽屉与日志面板（含分页/布局按钮）；修改后请在浏览器中手动验证，并可考虑添加端到端测试脚本。
   - CLI：`ui/cli.py` 需保持参数与 README 示例一致，注意端口占用处理与 `--no-browser` 选项。
- 新增状态或日志逻辑：在 `Experiment` 内追加方法或调整现有方法，并确保 `_save_metadata` 与 `load_from_dir` 保持一致。
- 调整目录结构或文件命名时，注意与现有分析脚本或外部依赖的兼容性。

## 如何在 `Experiment.__init__` 中新增字段

当你需要让实验携带更多上下文（例如本文已实现的 `description` 字段，或未来的「超参数快照」）时，需要同时修改多个层级，确保该字段贯穿配置、调度与持久化。以下是通用步骤清单：

1. **更新数据模型**
   - 在 `scheduler/scheduler.py` 的 `ScheduledExperiment` dataclass 中新增字段（例如 `description: Optional[str] = None`）。
   - 在 `_create_experiment_config` 中读取 TOML 对应字段并传入 dataclass。
   - 如字段对运行目录或重试逻辑有影响，检查 `_prepare_pending_queue`、`_should_retry` 等是否需要改动。

2. **传递给 Experiment**
   - 在 `_launch_experiment` 构造 `Experiment` 时，将新字段作为关键字参数传入。
   - 如果字段应影响环境变量或日志，可在 `_prepare_environment` 或 `_launch_experiment` 中处理。

3. **修改 `Experiment` 类**
   - 为 `__init__` 签名新增参数，并存储为实例属性。
   - 在 `resume` 分支中，确保当从旧目录恢复时也能正确加载该字段（必要时提供默认值）。
   - 更新 `_save_metadata`，将字段写入 `metadata.json`；在 `load_from_dir` 中读取并赋值。
   - 如果字段应在日志中呈现，可在 `_smart_start_next_run` 或 `run` 开头写入说明。

4. **配置与示例**
   - 更新相关示例配置/文档，告知用户如何在 TOML 中填写新字段。
   - 若字段必填，请加入 `ConfigManager.validate_config` 中的校验逻辑。

5. **测试与回归**
   - 运行最小示例（如 `toy_example/run_scheduler.py`）验证字段能正确保存、加载与在摘要中体现。
   - 手动或单元测试检查 `resume`、重试等分支是否表现正常。

## 当前工作建议

- **代码阅读**：从 `Experiment` 的 `_save_metadata` 与 `load_from_dir` 开始理解状态持久化；再沿着调度器 `_launch_experiment` 理解实例化流程。
- **扩展字段实践**：按照上文步骤实施后，再执行一次回归调度，确认新字段在 `metadata.json` 中出现并在需要的地方可用。
- **UI 验证**：开发 UI 功能后，运行 `pytest` 以及 `EXP <实验目录>` 对照前端界面，确保 `scheduler_state.json`、`commands.json` 与 WebSocket 日志流协同工作。

祝开发顺利！如需更多文档或自动化测试支持，可在 `docs/` 目录新增主题化说明，并在此 README 中保持索引更新。
