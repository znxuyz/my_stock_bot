"""
Pytest 共用設定：把專案根目錄加進 sys.path，讓 tests 可以直接 import 模組。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
