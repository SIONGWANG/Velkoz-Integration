# run.py — 独立图片查看器 启动脚本（供 subprocess 使用）
"""作为普通脚本运行（而非 -m），主动把项目根加入 sys.path，
避免打包版 Python 忽略 PYTHONPATH 导致找不到 image_viewer 包。
"""
import sys
import os

# 本文件位于 <project_root>/image_viewer/run.py
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from image_viewer.runner import main


if __name__ == "__main__":
    main()
