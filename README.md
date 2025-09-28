# 实验管理框架

一个用于管理科研实验的Python框架，提供智能运行ID分配和数据库风格的CSV指标记录系统。

## ✨ 核心特性

- 🔢 **智能运行ID**: 自动分配 `run_0001`, `run_0002`... 格式，无需手动管理
- 📊 **CSV指标记录**: 数据库风格的 `upd_row()` + `save_row()` 操作
- 🔄 **动态字段添加**: 运行时自由添加新指标字段，CSV自动扩展
- 📈 **pandas集成**: 一键转换为DataFrame进行数据分析
- 🚀 **全自动配置**: GPU分配、目录创建、日志初始化
- 📁 **智能组织**: 自动创建结构化的实验目录
- 🔍 **向后兼容**: 支持旧的API，平滑升级

## 🚀 快速开始

### 安装
```bash
pip install -e .
```

安装后会注册名为 `EXP` 的命令行工具，用于启动调度器 UI。后文有详细说明。

### 使用示例

#### CNN训练示例

项目包含一个完整的CNN训练示例 `cnn_train.py`，演示所有核心功能：

```bash
# 基础训练
python cnn_train.py --epochs 10 --lr 0.001

# 自定义配置
python cnn_train.py --epochs 20 --lr 0.01 --batch-size 64 --save-model
```

#### 批量调度器

使用 TOML 配置批量运行多组实验：

```bash
# 仅查看执行计划
python run_scheduler.py --config example_config.toml --dry-run

# 按配置依次执行实验（最多使用配置中的并发数）
python run_scheduler.py --config example_config.toml
```

每个实验可以设置 `priority`、`gpu_ids`、`description`、`resume` 等字段，调度器会自动在对应目录中新建 `run_xxxx` 运行并将指标写回。

#### ⏱️ 长时间调度演示（便于观察 UI 状态）

`toy_example/long_running_config.toml` 预设 3 个耗时约 150/210/270 秒的任务，默认并发度为 2，可在面板中看到 Pending → Running → Finished 的流转。

```bash
# 终端 ①：启动调度器，运行约 5 分钟
python toy_example/run_scheduler.py --config toy_example/long_running_config.toml

# 终端 ②：启动 UI，观察 experiments_long_demo 目录
EXP experiments_long_demo
```

观察提示：

- `slow_with_jitter` 会因为 `--jitter` 参数导致耗时略有波动。
- 日志面板每 15~30 秒追加一行进度，方便测试实时刷新。
- 指标抽屉中的 `progress.csv` 会持续增长，可用“指标预览”快速检查写入情况。

#### 🎛️ 调度器可视化面板（TensorBoard 风格）

运行调度器后，可通过自带的 UI 面板实时查看 Pending/Running/Finished/Error 队列、实验详情、日志与指标：

```bash
EXP experiments
```

- `experiments` 是调度器生成实验目录的根路径；可替换为你的实际输出目录。
- 默认监听 `http://127.0.0.1:6066`，若端口占用会自动选取空闲端口并打印最终 URL。
- 默认自动打开浏览器，可通过 `--no-browser` 关闭；也可以用 `--host 0.0.0.0 --port 7000` 指定监听地址/端口。

UI 页面提供以下能力：

- 四个状态面板：一目了然地查看实验队列。
- 点击实验卡片打开详情抽屉，展示 `metadata.json`、指标预览与所有终端日志。
- 「监控」按钮打开实时日志面板（多窗口支持，提供分页切换与多列布局按钮）。
- 在错误面板中可直接点击「重跑」触发 `retry_error` 命令；其他面板提供删除/终止操作。
- 页面右上角可手动刷新，也会每 3 秒自动轮询最新状态。

#### 核心API用法

```python
from pathlib import Path
from experiment_manager.core.experiment import Experiment

# 创建实验 - 智能运行ID分配
exp = Experiment(
    name="my_experiment",
    command="python train.py --epochs 100 --lr 0.001",
    base_dir=Path("./experiments"),
    tags=["deep-learning", "classification"]
)

print(f"智能分配的运行ID: {exp.current_run_id}")  # run_0001

# 新的CSV指标记录系统
exp.upd_row(
    model="ResNet-18",
    dataset="CIFAR-10",
    batch_size=128,
    learning_rate=0.001
)
exp.save_row()  # 保存配置行

# 训练循环中的指标记录
for epoch in range(1, 11):
    # ... 训练代码 ...
    
    # 记录每个epoch的指标
    exp.upd_row(
        epoch=epoch,
        train_loss=train_loss,
        train_acc=train_acc,
        val_loss=val_loss,
        val_acc=val_acc
    )
    exp.save_row()

# pandas数据分析
df = exp.load_metrics_df()
best_acc = df['val_acc'].max()
best_epoch = df.loc[df['val_acc'].idxmax()]
print(f"最佳性能: Epoch {best_epoch['epoch']}, 准确率 {best_acc:.4f}")
```

## 📊 CSV指标记录系统

### 核心优势

相比传统的JSON系统，新的CSV系统提供：

| 特性 | 旧系统(JSON) | 新系统(CSV) |
|------|-------------|-------------|
| 数据格式 | JSON字典 | CSV表格 |
| pandas支持 | 需要转换 | 原生支持 |
| 字段管理 | 手动 | 自动 |
| 数据分析 | 复杂 | 简单 |
| 运行ID | 手动设置 | 智能分配 |
| 扩展性 | 有限 | 无限 |

### 动态字段示例

```python
# 第一行：基础字段
exp.upd_row(model="CNN", accuracy=0.85)
exp.save_row()

# 第二行：添加新字段 - CSV自动扩展
exp.upd_row(
    model="CNN", 
    accuracy=0.88,
    f1_score=0.87,     # 新字段
    precision=0.89     # 新字段
)
exp.save_row()
```

生成的CSV：
```csv
timestamp,run_id,model,accuracy,f1_score,precision
2025-09-26T14:30:15,run_0001,CNN,0.85,,
2025-09-26T14:30:45,run_0001,CNN,0.88,0.87,0.89
```

## 🛠️ API参考

### 实验创建
```python
exp = Experiment(
    name="experiment_name",           # 实验名称
    command="python train.py",        # 执行命令
    base_dir=Path("./experiments"),   # 基础目录
    tags=["tag1", "tag2"],           # 标签
    gpu_ids=[0],                      # 指定使用的 GPU（可选）
    description="baseline sweep"      # 实验描述（可选）
)
```

### CSV指标记录
```python
# 更新当前行的字段
exp.upd_row(epoch=1, loss=0.5, accuracy=0.85)

# 保存当前行到CSV
exp.save_row()

# 加载数据进行分析
df = exp.load_metrics_df()           # pandas DataFrame
metrics = exp.load_metrics_dict()    # 字典列表
```

### 实验管理
```python
exp.set_running(pid=12345)           # 标记为运行状态
exp.set_finished()                   # 标记为完成状态
exp.append_log("训练开始")            # 添加日志
```

## 📁 项目结构

```
实验目录/
├── experiment_name_2025-09-26__14-30-15/  # 实验工作目录
│   ├── logs/
│   │   ├── run_0001.log                    # 运行日志
│   │   └── experiment.log                  # 实验日志
│   ├── metrics/
│   │   └── run_0001.csv                    # CSV指标文件
│   ├── checkpoints/                        # 模型检查点
│   ├── config.json                         # 实验配置
│   └── status.json                         # 运行状态
```

## 🚀 高级特性

### 智能运行ID管理
- 自动检测已有运行: `run_0001`, `run_0002`, `run_0003`...
- 支持最多9999次运行
- 零配置，完全自动化

### 完美pandas集成
```python
import pandas as pd

# 一键加载为DataFrame
df = exp.load_metrics_df()

# 数据分析
training_curve = df[df['phase'] == 'training']
best_performance = df.loc[df['val_acc'].idxmax()]

# 可视化
import matplotlib.pyplot as plt
plt.plot(df['epoch'], df['train_acc'], label='Train')
plt.plot(df['epoch'], df['val_acc'], label='Val')
plt.legend()
plt.show()
```

## 📋 更新日志

### v2.0.0 - CSV指标记录系统
- ✅ 新增智能运行ID分配系统
- ✅ 新增CSV指标记录系统
- ✅ 支持动态字段添加
- ✅ 完美pandas集成
- ✅ 向后兼容旧API

## 🤝 贡献

欢迎提交Issue和Pull Request！

## 📄 许可证

MIT License