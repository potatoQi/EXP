# 实验管理框架

一个轻量级的Python实验管理框架，提供：

🚀 **批量调度** - TOML 配置文件一键启动多组实验，支持优先级和并发控制  
📊 **指标记录** - 动态 CSV 管理，`upd_row()` + `save_row()` 数据库风格操作  
🌐 **可视化监控** - 配备 Web UI，实时查看实验状态和日志  
📱 **飞书同步** - 训练指标实时同步到多维表格，团队协作更便捷

<div align="center">
  <img src="docs/images/1.png" alt="实验管理界面" width="80%">
  <p><em>实验管理界面 - 批量调度与实时监控</em></p>
</div>

<div align="center">
  <img src="docs/images/2.png" alt="实验详情页面" width="80%">
  <p><em>实验查询页面 - 实验查询与内容预览</em></p>
</div>


## 如何嵌入到你的工程

只需完成两件事：

1. **写包装脚本**——用 `Experiment` 包装你的训练命令，EXP 会负责目录、日志和状态。
2. **写指标**——在训练脚本中调用 `load_experiment()`，然后即可使用 EXP 提供的 api。

搞定这两步，就可以单点运行，也能批量调度。

### 使用前准备

```bash
pip install -e .              # 安装本项目
```

### 🎯 快速体验

### 方式一：单点运行

**1. 创建一个 toy example**

```python
# 创建一个 toy 训练脚本 train.py
import time
from experiment_manager.core import load_experiment

exp = load_experiment()

for i in range(3):
    exp.upd_row(step=i, loss=1.0/(i+1))
    exp.save_row()
    print(f"Step {i}, Loss: {1.0/(i+1):.3f}")
    time.sleep(1)

# 创建包装脚本 run_exp.py
from pathlib import Path
from experiment_manager.core import Experiment

exp = Experiment(
  name="test",
  command="python train.py",
  base_dir=Path("./results"),
  cwd=Path(".")
)
exp.run(background=False) # True 时后台运行
```

**2. 运行并查看结果**

  ```bash
  python run_exp.py
  ```

  输出会在 `<base_dir>/<name>_<timestamp>/`

### 方式二：配置驱动批量调度

1. **写一个最小配置**

  ```toml
  # config.toml
  [scheduler]
  base_experiment_dir = "./results"
  max_concurrent_experiments = 2

  [[experiments]]
  name = "exp1"
  command = "python train.py"

  [[experiments]]
  name = "exp2"
  command = "python train.py"
  ```

2. **启动调度器并打开 UI**

  ```bash
  EXP run config.toml               # 执行配置中所有实验
  EXP see ./results                 # 可视化监控界面
  ```

## 🧰 Experiment API 速览

| API | 说明 |
| --- | --- |
| `Experiment(...)` | 创建实验实例，常用参数：`name`、`command`、`base_dir`、`gpu_ids`、`tags`、`description`。 |
| `exp.run(background=False, extra_env=None)` | 启动训练命令，可选择后台运行并注入额外环境变量。 |
| `exp.upd_row(**metrics)` | 更新当前指标行（如 `epoch`、`train_loss` 等）。 |
| `exp.save_row(lark=False, lark_config=None)` | 将指标写入 CSV，并可选同步飞书多维表。 |
| `load_experiment()` | 在训练脚本中获取当前实验实例，若未通过 EXP 启动则会提示未找到运行上下文。 |

## 📈 进阶：飞书配置最佳实践

### 单点实验
- 在创建 `Experiment(...)` 时直接通过 `lark_config` 提供飞书凭据，可传字典或 URL 字符串。
- 建议在字典中显式包含 `app_id`、`app_secret`、`app_token`、`table_id`（视图可选 `view_id`）。若传入 URL，框架会自动解析 `app_token`/`table_id`/`view_id`。
- 实例在首次同步成功后会将最终配置写入 `metadata.json`，`resume` 或后续 `save_row(lark=True)` 会复用这份配置。

### 调度器
- 在 `[scheduler]` 段落设置共享凭据，例如 `lark_config = { app_id = "cli_xxx", app_secret = "xxx" }`，避免每个实验重复填写。
- 每个 `[[experiments]]` 可通过 `lark_url` 或 `lark_config` 覆盖/补充表格信息，字段会覆盖调度器级别的同名项。
- 若某实验需要独立账号，只需在该实验的 `lark_config` 中补齐完整凭据即可。

### 合并逻辑速览
- 调度模式下：`[scheduler].lark_config` < `[[experiments]].lark_config`/`lark_url`。
- 单点实验：构造函数的 `lark_config` 与实例已有配置（如 `resume` 读取的 `metadata.json`）合并，新传入值优先。
- `exp.save_row(lark=True, lark_config=...)` 会在实例默认配置之上再次叠加本次调用的覆盖值。

## License

This repository is licensed under the [Apache-2.0 License](LICENSE).

## Star History

![Star History Chart](https://api.star-history.com/svg?repos=potatoQi/EXP&type=Date)