#!/usr/bin/env python3
import time
import sys

print("Starting test experiment...")
for i in range(20):
    print(f"Step {i+1}: Processing item {i+1}/20")
    print(f"  Progress: {(i+1)/20*100:.1f}%")
    if i % 3 == 0:
        print(f"  Special operation at step {i+1}")
    time.sleep(0.5)

print("Test experiment completed successfully!")