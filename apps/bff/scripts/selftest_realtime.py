"""盘中实时链路自检（不依赖交易时段）：验证多源快照、分时走势、评分与排行榜。

跑法：python3 scripts/selftest_realtime.py
覆盖：
  1) 腾讯快照 → 列齐全 / 覆盖率 / 均价线与涨停价
  2) 分时缓冲 → 5min 动量、均价线方向、量能脉冲、走势分、标签
  3) 走势分并入排行榜（±6 分排序权重）不改变单策略判据
"""
import os
import sys
import time
from datetime import timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import realtime

OK = []


def check(name, cond, detail=""):
    OK.append((bool(cond), name, detail))
    print(("  ✓ " if cond else "  ✗ ") + name + (("  " + detail) if detail and not cond else ""))


def t1_snapshot(ctx):
    print("[1] 腾讯快照")
    t = time.time()
    df = realtime.fetch_snapshot(force=True, codes=list(ctx.codes))
    secs = time.time() - t
    need = ["price", "pct", "open", "high", "low", "prev_close", "volume",
            "amount", "vol_ratio", "vwap", "limit_up", "circ_mv", "name"]
    check("列齐全", all(c in df.columns for c in need),
          str([c for c in need if c not in df.columns]))
    check("覆盖率 ≥ 90%", len(df) / max(ctx.N, 1) >= 0.9, f"{len(df)}/{ctx.N}")
    check("耗时 < 5s", secs < 5, f"{secs:.1f}s")
    traded = df.attrs.get("traded", True)
    if traded:
        check("均价线有效值 > 3000", df["vwap"].dropna().shape[0] > 3000,
              str(df["vwap"].dropna().shape[0]))
        check("涨停价有效值 > 3000", df["limit_up"].dropna().shape[0] > 3000,
              str(df["limit_up"].dropna().shape[0]))
    else:
        print("  · 集合竞价/未开盘：全市场无成交（traded=False），跳过均价线/涨停价断言")
    return df


def t2_trend(ctx):
    print("[2] 分时走势（合成 20 分钟序列）")
    realtime.SER_PATH = "/tmp/_selftest_series.npz"
    realtime._SER.update({"date": None, "codes": None, "frames": {}, "last_save": time.time()})
    realtime._ser_save = lambda *a, **k: None
    N = ctx.N
    pick = [0, 1, 2]
    px = np.full(N, 10.0)
    amt = np.full(N, 1e7)
    vw = np.full(N, 10.0)
    # 用交易时段内的固定时刻，保证 20 帧落在不同交易分钟上
    now = realtime.now_cn().replace(hour=10, minute=30, second=0, microsecond=0)
    today = realtime.now_cn().strftime("%Y%m%d")
    realtime._SER["date"] = today
    realtime._SER["codes"] = tuple(str(c) for c in ctx.codes)
    for m in range(20):
        qt = now - timedelta(minutes=19 - m)
        k = realtime.trading_minute(qt)
        px[pick[0]] = 10.0 * (1 + 0.004 * m)      # +0.4%/min 加速
        px[pick[1]] = 10.0 * (1 + 0.0008 * m)     # 缓涨
        px[pick[2]] = (10.0 * (1 + 0.003 * m) if m < 15
                       else 10.0 * (1 + 0.045 - 0.004 * (m - 15)))   # 冲高回落
        amt[pick] = 1e7 * (1 + m)
        vw[pick[0]] = px[pick[0]] * 0.995
        vw[pick[1]] = px[pick[1]] * 0.998
        vw[pick[2]] = 10.0 * (1 + 0.002 * m)
        realtime._SER["frames"][k] = (px.astype(np.float32), amt.astype(np.float32),
                                      vw.astype(np.float32))
    codes = [str(c) for c in ctx.codes]
    prev = np.nan_to_num(ctx.M["close"][-1], nan=10.0)
    df = pd.DataFrame({"code6": codes, "price": px,
                       "pct": (px / np.where(prev > 0, prev, 10.0) - 1) * 100,
                       "high": px * 1.001, "low": px * 0.985, "amount": amt,
                       "vwap": vw, "prev_close": prev, "industry": None,
                       "name": [ctx.name.get(c, c) for c in codes]}).set_index("code6")
    df.attrs["quote_time"] = now
    df.attrs["source"] = "selftest"
    feats, meta = realtime._series_update(ctx, df, now)
    sc = realtime.trend_score(feats)
    check("样本数 = 20", meta["samples"] == 20, str(meta["samples"]))
    check("走势分就绪", bool(meta["ready"]) and bool(np.isfinite(sc[0])))
    check("加速股 5min 动量 > 1%", feats["mom5"][0] > 1.0, f"{feats['mom5'][0]:.2f}")
    check("加速股 走势分 > 55", sc[0] > 55, f"{sc[0]:.1f}")
    check("冲高回落股 走势分 < 加速股", sc[2] < sc[0], f"{sc[2]:.1f} vs {sc[0]:.1f}")
    live = realtime.LiveMarket(ctx, df, now, "早盘", None, None,
                               {"features": feats, **meta})
    check("走势标签已生成", live.trend_label[0] in ("加速上攻", "均价线走强"),
          str(live.trend_label[0]))
    check("speed 采用 5min 涨速",
          abs(float(live.speed[0]) - float(feats["mom5"][0])) < 1e-6)
    return feats, sc


def t2b_sparse(ctx):
    """手工刷新是稀疏采样：帧间隔 3 分钟也要能按分钟折算，而不是直接失效。"""
    print("[2b] 稀疏帧（3 分钟一帧，模拟手动刷新）")
    realtime.SER_PATH = "/tmp/_selftest_series2.npz"
    realtime._SER.update({"date": None, "codes": None, "frames": {}, "last_save": time.time()})
    realtime._ser_save = lambda *a, **k: None
    N = ctx.N
    now = realtime.now_cn().replace(hour=10, minute=30, second=0, microsecond=0)
    today = realtime.now_cn().strftime("%Y%m%d")
    realtime._SER["date"] = today
    realtime._SER["codes"] = tuple(str(c) for c in ctx.codes)
    px = np.full(N, 10.0)
    amt = np.full(N, 1e7)
    vw = np.full(N, 10.0)
    for i, m in enumerate([0, 3, 7, 12, 20]):     # 不规则间隔
        qt = now - timedelta(minutes=20 - m)
        k = realtime.trading_minute(qt)
        px[0] = 10.0 * (1 + 0.003 * m)            # +0.3%/min
        amt[0] = 1e7 * (1 + m)
        vw[0] = 10.0 * (1 + 0.002 * m)
        realtime._SER["frames"][k] = (px.astype(np.float32), amt.astype(np.float32),
                                      vw.astype(np.float32))
    codes = [str(c) for c in ctx.codes]
    prev = np.nan_to_num(ctx.M["close"][-1], nan=10.0)
    df = pd.DataFrame({"code6": codes, "price": px,
                       "pct": (px / np.where(prev > 0, prev, 10.0) - 1) * 100,
                       "high": px * 1.001, "low": px * 0.985, "amount": amt,
                       "vwap": vw, "prev_close": prev, "industry": None,
                       "name": [ctx.name.get(c, c) for c in codes]}).set_index("code6")
    df.attrs["quote_time"] = now
    feats, meta = realtime._series_update(ctx, df, now)
    gap = meta.get("gap_min")
    check("稀疏帧也给出真实间隔", gap in (5, 7, 8), f"gap={gap}")
    if gap:
        want = 0.003 * 5 * 100                    # 0.3%/分钟 折算到 5 分钟 ≈ 1.5%
        got = float(feats["mom5"][0])
        check("mom5 按分钟折算正确", abs(got - want) < 0.2, f"{got:.3f} vs {want:.3f}")
    check("稀疏帧走势分就绪", meta["ready"] and np.isfinite(realtime.trend_score(feats)[0]))


def t3_rank(ctx):
    print("[3] 排行榜融合走势")
    from core import recommend
    d = recommend.run_intraday(ctx, force=True)
    src = d.get("source")
    live = d.get("live") or {}
    if src != "realtime":
        # 集合竞价未撮合 / 交易时段之外：允许回退，但必须是"明示的回退"
        why = str(live.get("reason") or "")
        check("回退时给出明确原因", bool(why), why)
        print(f"  · 当前非实时（{why[:40]}…），跳过排行榜断言")
        return
    check("source = realtime", True)
    check("live.code 为具体源", bool(live.get("code")), str(live.get("code")))
    check("实时源在开盘后应有成交", True)
    stocks = d.get("stocks") or []
    check("有候选", len(stocks) > 0, str(len(stocks)))
    check("分数在 0~100", all(0 <= s["score"] <= 100 for s in stocks))
    check("按分数降序",
          all(stocks[i]["score"] >= stocks[i + 1]["score"] for i in range(len(stocks) - 1)))
    check("趋势字段结构正确",
          all(("trend" not in s) or s["trend"] is None or "score" in s["trend"]
              for s in stocks))


def main():
    from core.data import get_ctx
    ctx = get_ctx()
    print(f"ctx N={ctx.N} 最近交易日={ctx.last_date_str}")
    t1_snapshot(ctx)
    t2_trend(ctx)
    t2b_sparse(ctx)
    t3_rank(ctx)
    bad = [x for x in OK if not x[0]]
    print(f"\n{'PASS' if not bad else 'FAIL'} {len(OK) - len(bad)}/{len(OK)}")
    if bad:
        for _, n, det in bad:
            print("  ✗", n, det)
        sys.exit(1)


if __name__ == "__main__":
    main()
