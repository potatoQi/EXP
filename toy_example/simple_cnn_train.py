#!/usr/bin/env python3
"""
CNN训练脚本 - 作为外部命令被Experiment.run()调用
简化版本，专注于模型训练，不包含实验管理逻辑
"""
import os
from pathlib import Path

import torch
import torch.nn as nn
import torch.optim as optim
import torchvision
import torchvision.transforms as transforms
import argparse
import sys
import time
from experiment_manager.core.experiment import Experiment

# 强制实时输出，避免缓冲
# sys.stdout.reconfigure(line_buffering=True)
# sys.stderr.reconfigure(line_buffering=True)

class SimpleCNN(nn.Module):
    """简单的CNN模型"""
    def __init__(self, num_classes=10):
        super(SimpleCNN, self).__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2),
            nn.Conv2d(32, 64, 3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2, 2)
        )
        self.classifier = nn.Sequential(
            nn.Linear(64 * 8 * 8, 128),
            nn.ReLU(),
            nn.Dropout(0.5),
            nn.Linear(128, num_classes)
        )
        
    def forward(self, x):
        x = self.features(x)
        x = x.view(-1, 64 * 8 * 8)
        x = self.classifier(x)
        return x

def load_active_experiment():
    """如果存在实验上下文，则加载 Experiment 实例"""
    work_dir = os.environ.get("EXPERIMENT_WORK_DIR")
    if not work_dir:
        return None
    try:
        return Experiment.load_from_dir(Path(work_dir))
    except Exception as exc:
        print(f"⚠️ 无法加载实验上下文: {exc}", flush=True)
        return None


def main():
    parser = argparse.ArgumentParser(description='CNN训练')
    parser.add_argument('--epochs', type=int, default=5, help='训练轮数')
    parser.add_argument('--lr', type=float, default=0.001, help='学习率')
    parser.add_argument('--batch-size', type=int, default=128, help='批次大小')
    args = parser.parse_args()
    
    print("🚀 CNN训练开始")
    print(f"配置: epochs={args.epochs}, lr={args.lr}, batch_size={args.batch_size}")
    
    # 设备检测
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"🖥️  使用设备: {device}")
    
    # 数据加载 - 使用固定的共享数据目录
    print("📦 加载CIFAR-10数据集...")

    # 使用HOME目录下的共享数据目录，避免每次实验重复下载
    data_dir = os.path.expanduser("~/datasets/cifar10")
    
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
    ])
    
    trainset = torchvision.datasets.CIFAR10(root=data_dir, train=True, download=True, transform=transform)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=args.batch_size, shuffle=True, num_workers=2)
    
    testset = torchvision.datasets.CIFAR10(root=data_dir, train=False, download=True, transform=transform)
    testloader = torch.utils.data.DataLoader(testset, batch_size=args.batch_size, shuffle=False, num_workers=2)
    
    print(f"✅ 数据加载完成: 训练集{len(trainset)}张, 测试集{len(testset)}张")
    
    # 模型初始化
    model = SimpleCNN().to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=args.lr)
    
    # 计算参数量
    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"🏗️  模型参数: 总计{total_params:,}, 可训练{trainable_params:,}")
    
    print("🏃 开始训练...")

    experiment = load_active_experiment()
    if experiment:
        print(f"📝 监控指标将写入: {experiment.get_metrics_file_path()}")
    
    # 训练循环
    for epoch in range(args.epochs):
        start_time = time.time()
        
        # 训练
        model.train()
        running_loss = 0.0
        correct = 0
        total = 0
        
        print(f"\n--- Epoch {epoch+1}/{args.epochs} ---", flush=True)
        
        for batch_idx, (inputs, targets) in enumerate(trainloader):
            inputs, targets = inputs.to(device), targets.to(device)
            
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, targets)
            loss.backward()
            optimizer.step()
            
            running_loss += loss.item()
            _, predicted = outputs.max(1)
            total += targets.size(0)
            correct += predicted.eq(targets).sum().item()
            
            # 每100个batch打印一次
            if (batch_idx + 1) % 100 == 0:
                print(f"   批次 {batch_idx+1}: 损失={loss.item():.3f}, 准确率={100.*correct/total:.2f}%", flush=True)
                time.sleep(0.1)  # 模拟实际训练时的间隔

            if experiment:
                experiment.upd_row(
                    epoch=epoch + 1,
                    batch=batch_idx + 1,
                    train_loss=running_loss / (batch_idx + 1),
                    train_acc=100. * correct / total
                )
                experiment.save_row()
        
        train_loss = running_loss / len(trainloader)
        train_acc = 100. * correct / total
        
        # 验证
        model.eval()
        test_loss = 0
        test_correct = 0
        test_total = 0
        
        with torch.no_grad():
            for inputs, targets in testloader:
                inputs, targets = inputs.to(device), targets.to(device)
                outputs = model(inputs)
                loss = criterion(outputs, targets)
                
                test_loss += loss.item()
                _, predicted = outputs.max(1)
                test_total += targets.size(0)
                test_correct += predicted.eq(targets).sum().item()
        
        test_loss = test_loss / len(testloader)
        test_acc = 100. * test_correct / test_total
        
        epoch_time = time.time() - start_time
        
        print(f"✅ Epoch {epoch+1} 完成:")
        print(f"   训练: 损失={train_loss:.4f}, 准确率={train_acc:.2f}%")
        print(f"   验证: 损失={test_loss:.4f}, 准确率={test_acc:.2f}%")
        print(f"   用时: {epoch_time:.1f}秒")

        if experiment:
            experiment.upd_row(
                epoch=epoch + 1,
                train_loss=train_loss,
                train_acc=train_acc,
                val_loss=test_loss,
                val_acc=test_acc,
                epoch_time=epoch_time
            )
            experiment.save_row()
    
    print("\n🎉 训练完成!")
    print(f"📊 最终验证准确率: {test_acc:.2f}%")

if __name__ == '__main__':
    main()