"""
简化的GPU工具 - 只提供基本GPU信息获取功能
"""
import subprocess
import psutil
from typing import List, Dict


class GPUManager:
    """简化的GPU管理器，只用于获取GPU信息"""
    
    def __init__(self):
        self.nvidia_smi_available = self._check_nvidia_smi()
    
    def _check_nvidia_smi(self) -> bool:
        """检查 nvidia-smi 是否可用"""
        try:
            result = subprocess.run(
                ["nvidia-smi", "-L"], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False
    
    def get_all_gpu_ids(self) -> List[int]:
        """获取系统中所有GPU的ID列表"""
        if not self.nvidia_smi_available:
            return []
        
        try:
            result = subprocess.run(
                ["nvidia-smi", "-L"], 
                capture_output=True, 
                text=True, 
                timeout=5
            )
            
            if result.returncode != 0:
                return []
            
            gpu_ids = []
            for line in result.stdout.strip().split('\n'):
                if line.startswith('GPU '):
                    gpu_id = int(line.split(':')[0].split()[1])
                    gpu_ids.append(gpu_id)
            
            return sorted(gpu_ids)
        
        except Exception as e:
            print(f"获取GPU列表时出错: {e}")
            return []
    
    def get_gpu_info(self) -> List[Dict]:
        """获取 GPU 信息列表"""
        if not self.nvidia_smi_available:
            return []
        
        try:
            cmd = [
                "nvidia-smi", 
                "--query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                return []
            
            gpu_info = []
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    parts = [part.strip() for part in line.split(',')]
                    if len(parts) >= 6:
                        gpu_info.append({
                            'id': int(parts[0]),
                            'name': parts[1],
                            'memory_total': int(parts[2]),
                            'memory_used': int(parts[3]),
                            'memory_free': int(parts[4]),
                            'utilization': int(parts[5])
                        })
            
            return gpu_info
        
        except Exception as e:
            print(f"获取 GPU 信息时出错: {e}")
            return []


def get_system_info() -> Dict:
    """获取系统信息"""
    return {
        'cpu_count': psutil.cpu_count(),
        'cpu_percent': psutil.cpu_percent(interval=1),
        'memory_total': psutil.virtual_memory().total,
        'memory_available': psutil.virtual_memory().available,
        'memory_percent': psutil.virtual_memory().percent,
        'disk_usage': psutil.disk_usage('/').percent
    }