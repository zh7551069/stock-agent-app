#!/usr/bin/env python3
"""回填连板股决策轨迹日志（候选池·历史日）。

为什么只回填候选池：
  * 候选池走 liupan_scan.py --date D，纯 T-1 日线截面，历史可精确复现（~3s/天）
  * 盘中动量依赖当日实时快照，历史无法可靠复现 → 只记真实查询，不回填

用法：
  python3 scripts/backfill_liupan_trace.py --days 10
  python3 scripts/backfill_liupan_trace.py --start 2026-10-13 --end 2026-10-31
"""
import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config
from core import liupan

TRACE = liupan.TRACE_FILE


def existing_backfill():
    done = set()
    if not os.path.exists(TRACE):
        return done
    with open(TRACE, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("backfill"):
                done.add(str(r.get("asof")))
    return done


def trade_days(n):
    """从缓存 daily 取最近的 n 个交易日（升序）。"""
    import pandas as pd
    df = pd.read_parquet(os.path.join(config.CACHE, "daily.parquet"))
    d = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").unique()
    return sorted(d)[-n:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--force", action="store_true", help="重新回填已存在的日子")
    a = ap.parse_args()

    if a.start and a.end:
        import pandas as pd
        all_days = trade_days(4000)
        days = [x for x in all_days if a.start <= x <= a.end]
    else:
        days = trade_days(a.days)

    done = set() if a.force else existing_backfill()
    todo = [d for d in days if d not in done]
    print(f"待回填 {len(todo)} 天（已存在 {len(done)} 天跳过）：{todo[0] if todo else '-'} → {todo[-1] if todo else '-'}")

    ok = fail = 0
    for d in todo:
        try:
            raw = liupan._run("liupan_scan.py", ["--tier", liupan.TIER,
                                                 "--date", d, "--json"], 180)
            nm = liupan._name_map()
            codes = raw.get("pool", {}).get(liupan.TIER) or []
            rec = {
                "ts": f"{d} 09:30:00",
                "kind": "pool",
                "tier": liupan.TIER,
                "asof": raw.get("asof") or d,
                "n": raw.get("n_cand", len(codes)),
                "items": [{"code": c, "code6": liupan.code6(c),
                           "name": nm.get(liupan.code6(c), liupan.code6(c))} for c in codes][:10],
                "backfill": True,
                "note": "历史回填（候选池·T-1 截面），非当日真实查询",
            }
            with open(TRACE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            ok += 1
            print(f"  ✅ {d}  {len(codes)} 只")
        except Exception as e:
            fail += 1
            print(f"  ❌ {d}  {e}")
    print(f"\n回填完成：成功 {ok} 天 · 失败 {fail} 天 · 轨迹文件 {TRACE}")


if __name__ == "__main__":
    main()
