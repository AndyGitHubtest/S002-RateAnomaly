#!/usr/bin/env python3
"""等待Binance IP解封后自动下载1H数据"""
import time
import subprocess
import sys

BAN_UNTIL = 1776198271026 / 1000  # 转秒
now = time.time()
wait_sec = max(0, BAN_UNTIL - now + 60)  # 多等60秒确保解封

if wait_sec > 0:
    print(f"Waiting {wait_sec:.0f}s ({wait_sec/60:.1f}min) for Binance IP ban to expire...")
    time.sleep(wait_sec)

print("Ban should be expired, starting download...")
result = subprocess.run(
    [sys.executable, "scripts/download_1h.py", "--workers", "2", "--years", "2"],
    cwd="/home/ubuntu/S002-RateAnomaly",
    capture_output=True,
    text=True,
    timeout=600,
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[-500:])
print(f"Exit code: {result.returncode}")
