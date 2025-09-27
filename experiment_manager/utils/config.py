"""
配置文件处理工具
"""
import toml
from pathlib import Path
from typing import Dict, List, Any, Optional


class ConfigManager:
    """配置文件管理器"""
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path
        self.config = {}
        if config_path and config_path.exists():
            self.load_config()
    
    def load_config(self, config_path: Optional[Path] = None) -> Dict:
        """加载 TOML 配置文件"""
        if config_path:
            self.config_path = config_path
        
        if not self.config_path or not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = toml.load(f)
            return self.config
        except toml.TomlDecodeError as e:
            raise ValueError(f"配置文件格式错误: {e}")
    
    def save_config(self, config_path: Optional[Path] = None) -> None:
        """保存配置到文件
        
        Args:
            config_path: 保存路径，如果不提供则使用当前路径
        """
        if config_path:
            self.config_path = config_path
        
        if not self.config_path:
            raise ValueError("未指定配置文件路径")
        
        with open(self.config_path, 'w', encoding='utf-8') as f:
            toml.dump(self.config, f)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值，支持点号分隔的嵌套键
        
        Args:
            key: 配置键，支持 'section.subsection.key' 格式
            default: 默认值
            
        Returns:
            Any: 配置值
        """
        keys = key.split('.')
        value = self.config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value
    
    def set(self, key: str, value: Any) -> None:
        """设置配置值，支持点号分隔的嵌套键
        
        Args:
            key: 配置键，支持 'section.subsection.key' 格式
            value: 配置值
        """
        keys = key.split('.')
        config = self.config
        
        # 创建嵌套字典结构
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
    
    def get_experiments(self) -> List[Dict]:
        """获取实验配置列表"""
        return self.get('experiments', [])
    
    def get_scheduler_config(self) -> Dict:
        """获取调度器配置"""
        return self.get('scheduler', {})
    
    def get_gpu_config(self) -> Dict:
        """获取 GPU 配置"""
        return self.get('gpu', {})
    
    def validate_config(self) -> List[str]:
        """验证配置文件格式，返回错误信息列表"""
        errors = []
        
        # 检查必需的顶级键
        required_keys = ['experiments', 'scheduler']
        for key in required_keys:
            if key not in self.config:
                errors.append(f"缺少必需的配置项: {key}")
        
        # 验证实验配置
        experiments = self.get_experiments()
        if not isinstance(experiments, list):
            errors.append("experiments 必须是一个列表")
        else:
            for i, exp in enumerate(experiments):
                if not isinstance(exp, dict):
                    errors.append(f"实验配置 {i} 必须是一个字典")
                    continue
                
                # 检查实验必需字段
                exp_required = ['name', 'command']
                for field in exp_required:
                    if field not in exp:
                        errors.append(f"实验 {i} 缺少必需字段: {field}")
                
                # 检查数据类型
                if 'tags' in exp and not isinstance(exp['tags'], list):
                    errors.append(f"实验 {i} 的 tags 必须是列表")
                
                if 'gpu_count' in exp and not isinstance(exp['gpu_count'], int):
                    errors.append(f"实验 {i} 的 gpu_count 必须是整数")
        
        # 验证调度器配置
        scheduler_config = self.get_scheduler_config()
        if not isinstance(scheduler_config, dict):
            errors.append("scheduler 配置必须是一个字典")
        
        return errors


def create_default_config(config_path: Path) -> None:
    """创建默认配置文件
    
    Args:
        config_path: 配置文件保存路径
    """
    default_config = {
        'scheduler': {
            'max_concurrent_experiments': 4,
            'check_interval': 10,
            'base_experiment_dir': '~/experiments',
            'auto_restart_on_error': False
        },
        'gpu': {
            'enable_gpu_management': True,
            'max_memory_usage': 0.1,
            'max_utilization': 10,
            'allocation_strategy': 'first_fit'  # first_fit, round_robin, least_loaded
        },
        'experiments': [
            {
                'name': 'example_experiment',
                'command': 'python train.py --config config.yaml',
                'tags': ['example', 'training'],
                'gpu_count': 1,
                'cwd': '.',
                'environment': {
                    'CUDA_VISIBLE_DEVICES': 'auto'
                }
            }
        ]
    }
    
    with open(config_path, 'w', encoding='utf-8') as f:
        toml.dump(default_config, f)


def load_experiment_config(config_path: Path) -> ConfigManager:
    """加载实验配置文件的便捷函数
    
    Args:
        config_path: 配置文件路径
        
    Returns:
        ConfigManager: 配置管理器实例
    """
    if not config_path.exists():
        create_default_config(config_path)
        print(f"已创建默认配置文件: {config_path}")
    
    config_manager = ConfigManager(config_path)
    
    # 验证配置
    errors = config_manager.validate_config()
    if errors:
        raise ValueError(f"配置文件有错误:\n" + "\n".join(f"- {error}" for error in errors))
    
    return config_manager