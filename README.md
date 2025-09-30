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
EXP set --preset lark         # 可选：配置飞书环境变量
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
| `load_experiment()` | 在训练脚本中获取当前实验实例，未在 EXP 环境下则提示没有环境变量。 |

## License

This repository is licensed under the [Apache-2.0 License](LICENSE).

## Star History

![Star History Chart](https://api.star-history.com/svg?repos=potatoQi/EXP&type=Date)