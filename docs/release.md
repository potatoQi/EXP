# xpriment 发布指南

本文档介绍如何使用 GitHub Actions 自动化发布新版本的 xpriment。

## 🚀 快速发布

### 使用 GitHub Actions

1. **推送标签触发**：
   ```bash
   git tag v0.0.3
   git push origin v0.0.3
   ```

2. **手动触发**：
   - 访问 GitHub Actions 页面
   - 选择 "🚀 Release to PyPI" 工作流
   - 点击 "Run workflow"
   - 输入版本号

## 📋 发布流程详解

### GitHub Actions 工作流

当推送标签或手动触发时，GitHub Actions 会自动执行以下步骤：

1. **版本管理**
   - 自动从标签获取版本号
   - 或使用手动输入的版本号更新 `pyproject.toml`

2. **构建与验证**
   - 清理旧的构建产物
   - 重新构建源码包和 wheel
   - 验证包的元数据和长描述

3. **质量检查**
   - 在临时环境中测试安装
   - 验证核心模块导入

4. **发布**
   - 上传到 PyPI 或 TestPyPI
   - 创建 Git 提交和标签
   - 推送到远程仓库

## 🔧 配置要求

### GitHub Actions

需要在 GitHub 仓库设置中配置以下 Secrets：

- `PYPI_API_TOKEN`: PyPI API 令牌
- `GITHUB_TOKEN`: 自动提供，用于创建 Release

### 设置 PyPI API Token

1. 登录 [PyPI](https://pypi.org/)
2. 访问 Account Settings > API tokens
3. 创建新的 API token，scope 选择 "Entire account" 或特定项目
4. 复制生成的 token
5. 在 GitHub 仓库中：Settings > Secrets and variables > Actions > New repository secret
6. 名称设为 `PYPI_API_TOKEN`，值为复制的 token

## 📝 版本策略

遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范：

- **MAJOR** (1.0.0): 不兼容的 API 修改
- **MINOR** (0.1.0): 向下兼容的功能性新增
- **PATCH** (0.0.1): 向下兼容的问题修正

## 🛠 故障排除

### 常见问题

1. **包名冲突**：
   - 修改 `pyproject.toml` 中的 `name` 字段

2. **版本已存在**：
   - PyPI 不允许重复上传相同版本
   - 使用新的版本号

3. **认证失败**：
   - 检查 PyPI API 令牌是否正确
   - 确认令牌有上传权限

4. **构建失败**：
   - 检查 `pyproject.toml` 语法
   - 确认所有依赖都已正确声明

5. **GitHub Actions 失败**：
   - 检查 Secrets 配置是否正确
   - 查看 Actions 日志确定具体错误

### 手动版本更新

如果需要手动更新版本号：

```bash
# 1. 更新版本号
vim pyproject.toml

# 2. 提交并创建标签
git add pyproject.toml
git commit -m "🔖 发布版本 x.x.x"
git tag vx.x.x
git push origin main --tags
```

## 🔄 发布后检查

1. **验证 PyPI 页面**：https://pypi.org/project/xpriment/
2. **测试安装**：
   ```bash
   pip install xpriment
   python -c "from experiment_manager.core import Experiment"
   ```
3. **检查 GitHub Release**：确认自动创建的 Release 页面

## 📚 相关链接

- [PyPI Project](https://pypi.org/project/xpriment/)
- [GitHub Repository](https://github.com/potatoQi/EXP)
- [Python Packaging Guide](https://packaging.python.org/)
- [Semantic Versioning](https://semver.org/)