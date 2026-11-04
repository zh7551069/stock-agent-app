#!/usr/bin/env python3
"""盘中约每 5 分钟：池级 live 扫描，成功才 append liupan_trace。

不伪造节点：
  * 非交易时段 / 周末 / 拿不到互斥锁 → 跳过，不写
  * 扫描 status 不是 ok（no_disk / no_data / …）→ 不写
  * buy 为空 → 仍记录这次成功扫描，但 top1=null，不入观察列表

与手机点触动量分离：本进程直接跑 liupan_auction_rank.py --live，
kind=scan_5min，不请求 GET /api/liupan/momentum。

launchd 示例见 docs/delivery/launchd-liupan-5min.md。本脚本不会安装 launchd。

  python3 scripts/liupan_5min_append.py
  python3 scripts/liupan_5min_append.py --ignore-window
  python3 scripts/liupan_5min_append.py --input scan.json
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

import fcntl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from core import liupan, liupan_scan_append as scan  # noqa: E402

class LockBusy(Exception):
    pass


def _lock(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise LockBusy()
    return fd


def _parse_stdout(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _run_scanner(tier):
    script = os.path.join(liupan.SCRIPTS, "liupan_auction_rank.py")
    if not os.path.isfile(script):
        print(f"[no-append] 找不到扫描脚本：{script}", file=sys.stderr)
        return None, 2
    cmd = scan.scanner_command(sys.executable, liupan.SCRIPTS, tier)
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=liupan.WS, capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("[no-append] 扫描超时（>300s）", file=sys.stderr)
        return None, 1
    cost_ms = int((time.monotonic() - started) * 1000)
    data = _parse_stdout(proc.stdout)
    if data is None:
        tail = (proc.stderr or "")[-400:]
        print(f"[no-append] 扫描输出不是 JSON（rc={proc.returncode}）{tail}", file=sys.stderr)
        return None, 2
    data["_cost_ms"] = cost_ms
    return data, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="5min 池级扫描 append（成功才落库）")
    parser.add_argument("--tier", default=liupan.TIER, choices=["锐利", "实用"])
    parser.add_argument("--ignore-window", action="store_true",
                        help="忽略交易时段（仍只在扫描 status=ok 时落库）")
    parser.add_argument("--input", help="回放一份扫描 JSON，不调用扫描脚本；落库时 replay=true")
    parser.add_argument("--now", help="仅能配合 --input，YYYY-MM-DD HH:MM:SS（Asia/Shanghai）")
    args = parser.parse_args(argv)

    if args.now and not args.input:
        print("--now 只能配合 --input，避免给真实扫描编造时间", file=sys.stderr)
        return 2

    if not args.ignore_window and not args.input:
        reason = scan.skip_reason()
        if reason:
            print(f"[skip] {reason}")
            return 0

    lock_path = os.path.join(config.DATA, "liupan_5min.lock")
    try:
        fd = _lock(lock_path)
    except LockBusy:
        print("[skip] overlap")
        return 0

    try:
        replay = False
        now = None
        if args.input:
            replay = True
            try:
                with open(args.input, encoding="utf-8") as handle:
                    raw = json.load(handle)
            except Exception as exc:
                print(f"[no-append] 读不到 --input：{exc}", file=sys.stderr)
                return 2
            if args.now:
                try:
                    now = datetime.strptime(args.now, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    print("--now 格式须为 YYYY-MM-DD HH:MM:SS", file=sys.stderr)
                    return 2
            cost_ms = None
        else:
            raw, code = _run_scanner(args.tier)
            if raw is None:
                return code
            cost_ms = raw.get("_cost_ms")

        result = scan.append_successful_scan(
            raw, tier=args.tier, replay=replay, now=now, cost_ms=cost_ms,
        )
        if not result.get("appended"):
            print(f"[no-append] {result.get('reason')}")
            return 3
        top = result.get("top1")
        if top:
            print(f"[append] ts={result['ts']} top1={top.get('code')} "
                  f"score_adj={top.get('score_adj')} replay={result['replay']}")
        else:
            print(f"[append] ts={result['ts']} top1=null replay={result['replay']}")
        return 0
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
