#!/usr/bin/env python3
"""转到 apps/bff/scripts/liupan_5min_append.py。逻辑只保留一份。"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.abspath(os.path.join(
    HERE, "..", "..", "apps", "bff", "scripts", "liupan_5min_append.py"))
sys.argv[0] = TARGET
runpy.run_path(TARGET, run_name="__main__")
