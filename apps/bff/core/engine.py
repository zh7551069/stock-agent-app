"""策略引擎：27 个可执行策略（实现自知识点卡）。

每个策略函数签名：fn(ctx, params) -> pd.DataFrame[code6, score, reason, trigger?]
- score: 0~100（越大越符合该知识点）
- reason: 命中依据（给人看的一句话）
- risk=True 的策略表示「预警/排除项」，聚合时做负向处理
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)
np.seterr(all="ignore")

EPS = 1e-9


# ─────────────────────────── 工具 ───────────────────────────
def _pct_rank(x):
    """NaN 安全的百分位（0~100）"""
    s = pd.Series(x)
    return s.rank(pct=True, na_option="keep").to_numpy() * 100


def _pick(df, *cands):
    """在中文列名里模糊找列"""
    if df is None or len(df) == 0:
        return None
    for c in cands:
        if c in df.columns:
            return c
    for c in df.columns:
        for cand in cands:
            if cand in str(c):
                return c
    return None


def _mk(ctx, idx, score, reason, trigger=None, risk=False):
    idx = np.asarray(idx, dtype=int)
    if len(idx) == 0:
        return pd.DataFrame(columns=["code6", "score", "reason", "trigger", "risk"])
    out = pd.DataFrame({
        "code6": ctx.codes[idx],
        "score": np.clip(np.asarray(score, dtype=float), 0, 100),
        "reason": list(reason) if not isinstance(reason, str) else [reason] * len(idx),
    })
    if isinstance(trigger, (list, tuple)):
        trig = list(trigger)
        if len(trig) != len(idx):
            trig = [trig[0] if trig else ""] * len(idx)
    else:
        trig = [trigger or ""] * len(idx)
    out["trigger"] = trig
    out["risk"] = risk
    return out.sort_values("score", ascending=False)


def _nan(x, fill=0.0):
    return np.where(np.isfinite(x), x, fill)


# ─────────────────────────── 盘前 ───────────────────────────
def pre_limitup_ladder(ctx, p):
    """昨日涨停梯队：连板高度排序（分歧转一致候选）"""
    df = ctx.zt_ladder_today if len(ctx.zt_ladder_today) else ctx.zt_limit_up_today
    if len(df) == 0:
        return _mk(ctx, [], [], [])
    tag = _pick(df, "涨停标签", "连板状态", "涨停状态")
    pos = {c: i for i, c in enumerate(ctx.codes)}
    rec = []
    for _, r in df.iterrows():
        c = r.get("code6")
        if c not in pos:
            continue
        label = str(r[tag]) if tag else ""
        # 解析连板高度：'3天3板'->3, '21天12板'->12, '首板'->1
        nums = [int(x) for x in __import__("re").findall(r"(\d+)\s*天", label)]
        h = nums[0] if nums else (1 if "首板" in label else 2)
        rec.append((pos[c], min(99, 50 + h * 8), f"{label or '涨停'}（连板高度 {h}）"))
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*rec)
    return _mk(ctx, idx, sc, rr, "次日竞价观察封单强度；低开或炸板即放弃")


def pre_first_board_next(ctx, p):
    """首板次日高开 4~6% 触发（一进二硬阈值）"""
    df = ctx.zt_limit_up_today
    if len(df) == 0:
        return _mk(ctx, [], [], [])
    st = _pick(df, "涨停标签", "涨停状态", "榜单类别")
    sub = df.copy()
    if st:
        sub = sub[sub[st].astype(str).str.contains("首板", na=False)]
    if len(sub) == 0:
        return _mk(ctx, [], [], [])
    pos = {c: i for i, c in enumerate(ctx.codes)}
    idx = [pos[c] for c in sub["code6"] if c in pos]
    g0, g1 = p.get("gap_min", 4.0), p.get("gap_max", 6.0)
    return _mk(ctx, idx, np.linspace(90, 65, len(idx)) if idx else [],
               [f"首板封住 → 次日高开 {g0:.0f}~{g1:.0f}% 触发" ] * len(idx),
               f"次日 09:25 竞价开盘价涨幅 {g0:.0f}%~{g1:.0f}% 时买入（低于 {g0:.0f}% 放弃）")


def pre_gap_volume(ctx, p):
    """竞价抢筹：竞价高开 + 竞价量能"""
    df = ctx.zt_auction
    if len(df) == 0:
        return _mk(ctx, [], [], [])
    dv = ctx.last_date_str.replace("-", "")
    c_date = _pick(df, "交易时间")
    if c_date:
        sub = df[df[c_date].astype(str).str.replace("-", "") == dv]
        if len(sub) == 0:
            sub = df
    else:
        sub = df
    cg = _pick(sub, "竞价涨幅")
    ch = _pick(sub, "竞价换手")
    if cg is None:
        return _mk(ctx, [], [], [])
    g = pd.to_numeric(sub[cg], errors="coerce")
    vr = pd.to_numeric(sub[ch], errors="coerce") if ch else pd.Series(0, index=sub.index)
    sub = sub[(g >= 1.0) & (g <= 8.0)]
    if len(sub) == 0:
        return _mk(ctx, [], [], [])
    pos = {c: i for i, c in enumerate(ctx.codes)}
    rec = []
    for _, r in sub.iterrows():
        c = r.get("code6")
        if c not in pos:
            continue
        gg = float(pd.to_numeric(r[cg], errors="coerce") or 0)
        hh = float(pd.to_numeric(r[ch], errors="coerce") or 0) if ch else 0.0
        rec.append((pos[c], min(99, 55 + gg * 5 + hh * 4), f"竞价高开 {gg:+.1f}%，竞价换手 {hh:.2f}%"))
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rs = zip(*rec)
    return _mk(ctx, idx, sc, rs, "开盘不破竞价平台则持有；跌破竞价均价止损")


def pre_auction_pool(ctx, p):
    """竞价强弱：高开 1%~7% 且非一字（用当日首根 5min 近似开盘）"""
    g0, g1 = p.get("gap_min", 1.0), p.get("gap_max", 7.0)
    prev = np.array([ctx.prev_close.get(c, np.nan) for c in ctx.codes], dtype=float)
    op = ctx.col("open")
    gap = (op / prev - 1) * 100
    uni = ctx.universe()
    lu = np.array([ctx.limit_up.get(c, np.nan) for c in ctx.codes], dtype=float)
    one_word = op >= lu * 0.995
    sel = uni & np.isfinite(gap) & (gap >= g0) & (gap <= g1) & ~one_word
    idx = np.where(sel)[0]
    return _mk(ctx, idx, 55 + gap[idx] * 5,
               [f"昨日高开 {gap[i]:+.1f}%（非一字）" for i in idx],
               "次日竞价复制同结构：高开 1~7% 且量能配合")


def pre_mainline_leader(ctx, p):
    """主线板块龙头：板块涨幅前列 + 个股 RS≥80"""
    rs = _rs(ctx)
    uni = ctx.universe()
    sec_pct = _sector_today_pct(ctx)
    top_sec = set()
    if sec_pct is not None and len(sec_pct):
        k = max(1, int(len(sec_pct) * 0.2))
        top_sec = set(sec_pct.head(k).index)
    pct = ctx.col("pct_chg")
    rec = []
    for i, c in enumerate(ctx.codes):
        if not uni[i] or not np.isfinite(rs[i]) or rs[i] < p.get("rs_min", 80):
            continue
        sec = ctx.industry.get(c, "")
        if top_sec and sec not in top_sec:
            continue
        rec.append((i, rs[i], f"板块「{sec}」今日居前 + RS {rs[i]:.0f}"))
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*rec)
    return _mk(ctx, idx, sc, rr, "次日不追高：回踩板块指数不破再介入")


def pre_strong_close(ctx, p):
    """昨日尾盘拉升：最后 30 分钟涨幅 ≥0.8%"""
    need = p.get("tail_min", 0.8)
    if not len(ctx.m_codes):
        return _mk(ctx, [], [], [])
    c = ctx.m["close"]
    tail = (c[:, -1] / c[:, -7] - 1) * 100
    day = (c[:, -1] / c[:, 0] - 1) * 100
    sel = np.isfinite(tail) & (tail >= need) & (day > 0)
    idx_m = np.where(sel)[0]
    pos = {cc: i for i, cc in enumerate(ctx.codes)}
    rec = [(pos[ctx.m_codes[i]], min(99, 60 + tail[i] * 8), f"尾盘 30 分钟 {tail[i]:+.1f}%")
           for i in idx_m if ctx.m_codes[i] in pos]
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*rec)
    return _mk(ctx, idx, sc, rr, "次日竞价高开确认后跟随；低开则放弃")


# ─────────────────────────── 盘中 ───────────────────────────
def _minute_join(ctx, fn):
    """在分钟矩阵上计算，返回 (code6, score, reason)"""
    if not len(ctx.m_codes):
        return []
    pos = {c: i for i, c in enumerate(ctx.codes)}
    out = []
    for i, c in enumerate(ctx.m_codes):
        if c not in pos:
            continue
        r = fn(i, c)
        if r:
            out.append((pos[c],) + r)
    return out


def intra_vwap_support(ctx, p):
    """低开高走 + 站上分时均价线"""
    tol = p.get("vwap_tol", 0.2)
    def fn(i, c):
        cl, vw = ctx.m["close"][i], ctx.m["vwap"][i]
        op = ctx.m["open"][i][0]
        if not (np.isfinite(vw[-1]) and np.isfinite(op)):
            return None
        above = cl[-1] > vw[-1] * (1 + tol / 100)
        low_open = op < cl[0] if np.isfinite(cl[0]) else False
        # 早盘曾跌破均价线、尾盘站上 = 转强
        dip = np.nanmin(cl[:24] / vw[:24]) < 1.0
        if above and (low_open or dip):
            pass
        if above and (low_open or dip):
            return (min(99, 55 + (cl[-1] / vw[-1] - 1) * 400), f"站上分时均价 {cl[-1]/vw[-1]:.3f}")
        return None
    rec = _minute_join(ctx, fn)
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*[(a, b, c) for a, b, c in rec])
    return _mk(ctx, idx, sc, rr, "回踩均价线不破可持；跌破均价线离场")


def intra_volume_breakout(ctx, p):
    # 强势类信号：当日跌幅超过 -2% 不纳入（避免接飞刀）
    """盘中放量突破：突破当日横盘区间 + 该 5min 量 ≥ 前 12 根均量 2 倍"""
    mult = p.get("vol_ratio_min", 2.0)
    def fn(i, c):
        cl, hi, vo = ctx.m["close"][i], ctx.m["high"][i], ctx.m["volume"][i]
        if not np.isfinite(cl).any():
            return None
        # 从第 12 根开始扫，找到「突破前高 + 放量」的第一根
        for b in range(12, 48):
            prev_hi = np.nanmax(hi[b - 12:b])
            if not np.isfinite(prev_hi) or not np.isfinite(cl[b]):
                continue
            base = np.nanmean(vo[b - 12:b])
            if cl[b] > prev_hi and base > 0 and vo[b] >= mult * base:
                return (min(99, 65 + (vo[b] / base - mult) * 10),
                        f"第 {b+1} 根 5min 放量 {vo[b]/base:.1f}× 突破前高")
        return None
    rec = _minute_join(ctx, fn)
    if not rec:
        return _mk(ctx, [], [], [])
    pct = ctx.col("pct_chg")
    rec = [r for r in rec if np.isfinite(pct[r[0]]) and pct[r[0]] > -2.0]
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*[(a, b, c) for a, b, c in rec])
    return _mk(ctx, idx, sc, rr, "突破后回踩不破突破位即持有")


def intra_speed_surge(ctx, p):
    # 强势类信号：当日跌幅超过 -2% 不纳入（避免接飞刀）
    """涨速异动：近 3 根 5min ≥1.2% 且站上均价"""
    need = p.get("speed_min", 1.2)
    def fn(i, c):
        cl, vw = ctx.m["close"][i], ctx.m["vwap"][i]
        if not np.isfinite(cl).all() or not np.isfinite(vw[-1]):
            return None
        sp = (cl[-1] / cl[-4] - 1) * 100
        if sp >= need and cl[-1] > vw[-1]:
            return (min(99, 60 + sp * 8), f"尾盘 15 分钟涨速 {sp:+.1f}%")
        return None
    rec = _minute_join(ctx, fn)
    if not rec:
        return _mk(ctx, [], [], [])
    pct = ctx.col("pct_chg")
    rec = [r for r in rec if np.isfinite(pct[r[0]]) and pct[r[0]] > -2.0]
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*[(a, b, c) for a, b, c in rec])
    return _mk(ctx, idx, sc, rr, "涨速后不追，等回踩均价线")


def intra_first_touch(ctx, p):
    """10:00 前首触涨停（龙头首触）—— 东财涨停池含涨停时间"""
    df = ctx.zt_pool_up_last
    if df is None or len(df) == 0:
        return _mk(ctx, [], [], [])
    tc = _pick(df, "涨停时间")
    if tc is None:
        return _mk(ctx, [], [], [])
    rec = []
    pos = {c: i for i, c in enumerate(ctx.codes)}
    for _, r in df.iterrows():
        c = r.get("code6")
        if c not in pos:
            continue
        tt = str(r[tc])[:8]
        try:
            h, m = int(tt[:2]), int(tt[3:5])
        except Exception:
            continue
        if h * 60 + m <= 600:
            rec.append((pos[c], min(99, 95 - (h * 60 + m - 570) * 0.15),
                        f"{tt[:5]} 首触涨停（10:00 前）"))
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*rec)
    return _mk(ctx, idx, sc, rr, "板上确认封单强度；开板即走")


def intra_sector_flow(ctx, p):
    """板块资金流领涨：当日资金流入前列板块中的强势股"""
    sec = _sector_today_pct(ctx)
    if sec is None or len(sec) == 0:
        return _mk(ctx, [], [], [])
    top = set(sec.head(p.get("top_n", 5)).index)
    pct = ctx.col("pct_chg")
    uni = ctx.universe()
    rec = []
    for i, c in enumerate(ctx.codes):
        if not uni[i] or ctx.industry.get(c, "") not in top:
            continue
        if not np.isfinite(pct[i]) or pct[i] < 3:
            continue
        rec.append((i, min(99, 50 + pct[i] * 3), f"板块「{ctx.industry[c]}」资金流入前 {p.get('top_n',5)} + 个股 {pct[i]:+.1f}%"))
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*rec)
    return _mk(ctx, idx, sc, rr, "板块效应延续则持有；板块转弱即减")


def intra_tail_strong(ctx, p):
    # 强势类信号：当日跌幅超过 -2% 不纳入（避免接飞刀）
    """尾盘强势：最后 30 分钟创新高且量能不衰"""
    need = p.get("tail_min", 1.0)
    def fn(i, c):
        cl, hi, vo = ctx.m["close"][i], ctx.m["high"][i], ctx.m["volume"][i]
        if not np.isfinite(cl).all():
            return None
        tail = (cl[-1] / cl[-7] - 1) * 100
        near_high = cl[-1] >= np.nanmax(hi) * 0.995
        vol_ok = np.nanmean(vo[-6:]) >= np.nanmean(vo[-24:]) * 0.8
        if tail >= need and near_high and vol_ok:
            return (min(99, 60 + tail * 6), f"尾盘 30 分钟 {tail:+.1f}% 且收在日内高点")
        return None
    rec = _minute_join(ctx, fn)
    if not rec:
        return _mk(ctx, [], [], [])
    pct = ctx.col("pct_chg")
    rec = [r for r in rec if np.isfinite(pct[r[0]]) and pct[r[0]] > -2.0]
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*[(a, b, c) for a, b, c in rec])
    return _mk(ctx, idx, sc, rr, "次日竞价高开即溢价；低开走")


def intra_broken_reseal(ctx, p):
    """炸板回封：曾开板（打开次数>0）但收盘仍封住"""
    u = ctx.zt_limit_up_today
    if len(u) == 0:
        return _mk(ctx, [], [], [])
    op = _pick(u, "打开次数")
    if op is None:
        return _mk(ctx, [], [], [])
    o = pd.to_numeric(u[op], errors="coerce").fillna(0)
    sub = u[o > 0]
    pos = {c: i for i, c in enumerate(ctx.codes)}
    rec = [(pos[c], min(99, 60 + n * 3), f"开板 {int(n)} 次后回封（承接强）")
           for c, n in zip(sub["code6"], o[sub.index]) if c in pos]
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*rec)
    return _mk(ctx, idx, sc, rr, "回封后封单不减可持；二次开板走")


def intra_leader_pullback(ctx, p):
    """龙头回踩均价线不破（RS≥80）"""
    rs = _rs(ctx)
    pos = {c: i for i, c in enumerate(ctx.codes)}
    rec = []
    for i, c in enumerate(ctx.m_codes):
        if c not in pos:
            continue
        p_ = pos[c]
        if not np.isfinite(rs[p_]) or rs[p_] < p.get("rs_min", 80):
            continue
        cl, vw = ctx.m["close"][i], ctx.m["vwap"][i]
        if not (np.isfinite(vw[-1]) and np.isfinite(cl).all()):
            continue
        touch = np.nanmin(np.abs(cl / vw - 1)) < p.get("vwap_tol", 0.5) / 100
        above = cl[-1] > vw[-1]
        if touch and above:
            rec.append((p_, min(99, rs[p_]), f"RS {rs[p_]:.0f} 龙头回踩均价线不破"))
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*rec)
    return _mk(ctx, idx, sc, rr, "回踩均价线即低吸点；跌破均价线止损")


# ─────────────────────────── 盘后 ───────────────────────────
def _ret(ctx, n):
    cl = ctx.M["close"]
    out = np.full(ctx.N, np.nan, np.float32)
    if ctx.T > n:
        out = cl[-1] / cl[-1 - n] - 1
    return out


def _rs(ctx):
    """52 周相对强度百分位（1~99）"""
    r = _ret(ctx, 252)
    return _pct_rank(r)


def _sector_today_pct(ctx):
    """当日板块涨幅：用个股 pct_chg 按 stock_info.industry 聚合（口径自洽）"""
    pct = ctx.M["pct_chg"][-1]
    df = pd.DataFrame({
        "sec": [ctx.industry.get(c, "未知") for c in ctx.codes],
        "p": pct,
    })
    df = df[np.isfinite(df["p"]) & (df["sec"] != "未知")]
    if len(df) == 0:
        return None
    g = df.groupby("sec")["p"].agg(["mean", "count"])
    g = g[g["count"] >= 3]["mean"].sort_values(ascending=False)
    return g


def post_rs80(ctx, p):
    rs = _rs(ctx)
    uni = ctx.universe()
    sel = uni & np.isfinite(rs) & (rs >= p.get("rs_min", 80))
    idx = np.where(sel)[0]
    r = _ret(ctx, 252)
    return _mk(ctx, idx, rs[idx], [f"RS {rs[i]:.0f}（52周 {r[i]*100:+.1f}%）" for i in idx],
               "RS<70 或连续 7 个月走弱即卖出")


def post_cup_handle(ctx, p):
    hi = np.nanmax(ctx.M["high"][-60:], axis=0)
    lo = np.nanmin(ctx.M["low"][-60:], axis=0)
    cl = ctx.col("close")
    depth = (hi - lo) / np.maximum(hi, EPS) * 100
    near = cl >= hi * 0.97
    h10 = np.nanmax(ctx.M["close"][-10:], axis=0)
    pull = (h10 - cl) / np.maximum(h10, EPS) * 100
    vr = ctx.col("vol_ratio")
    vr = np.where(np.isfinite(vr), vr, ctx.M["vol_hand"][-1] / np.maximum(ctx.M["vol_ma5"][-1], EPS))
    sel = (ctx.universe() & np.isfinite(depth)
           & (depth >= p.get("cup_depth_min", 12)) & (depth <= p.get("cup_depth_max", 33))
           & near & (pull <= p.get("handle_pullback_max", 12)) & (vr >= p.get("vol_ratio_min", 1.4)))
    idx = np.where(sel)[0]
    sc = 60 + np.minimum(30, (vr[idx] - 1.4) * 20) + np.minimum(10, (cl[idx] / hi[idx] - 0.97) * 300)
    return _mk(ctx, idx, sc, [f"杯深 {depth[i]:.0f}%、柄回撤 {pull[i]:.0f}%、量比 {vr[i]:.1f}" for i in idx],
               "突破柄最高点买入；跌破柄低点止损")


def post_55_breakout(ctx, p):
    n = p.get("lookback", 55)
    cl = ctx.M["close"]
    if ctx.T < n + 2:
        return _mk(ctx, [], [], [])
    prev_hi = np.nanmax(cl[-n - 1:-1], axis=0)
    cl1 = cl[-1]
    sel = ctx.universe() & np.isfinite(prev_hi) & (cl1 > prev_hi)
    idx = np.where(sel)[0]
    margin = (cl1[idx] / prev_hi[idx] - 1) * 100
    return _mk(ctx, idx, 65 + np.minimum(30, margin * 15),
               [f"突破 {n} 日新高（+{margin[k]:.1f}%）" for k in range(len(idx))],
               "收盘跌破突破位止损；55 日通道系统1")


def post_range_volume(ctx, p):
    n = p.get("range_days", 20)
    hi = np.nanmax(ctx.M["high"][-n - 1:-1], axis=0)
    lo = np.nanmin(ctx.M["low"][-n - 1:-1], axis=0)
    rng = (hi - lo) / np.maximum(lo, EPS) * 100
    cl = ctx.col("close")
    vr = ctx.M["vol_hand"][-1] / np.maximum(ctx.M["vol_ma5"][-1], EPS)
    sel = (ctx.universe() & np.isfinite(rng) & (rng <= p.get("range_max", 3.0))
           & (cl > hi) & (vr >= p.get("vol_mult", 2.0)))
    idx = np.where(sel)[0]
    return _mk(ctx, idx, 60 + np.minimum(35, (vr[idx] - 2) * 15),
               [f"{n} 日振幅仅 {rng[i]:.1f}%，放量 {vr[i]:.1f}× 突破" for i in idx],
               "突破后回踩箱体上沿不破即加仓")


def post_shrink_pullback(ctx, p):
    cl = ctx.col("close")
    ma20, ma60 = ctx.col("ma20"), ctx.col("ma60")
    vol, vma5 = ctx.M["vol_hand"][-1], ctx.M["vol_ma5"][-1]
    shrink = vol <= p.get("vol_shrink", 0.75) * np.maximum(vma5, EPS)
    near = np.abs(cl / np.maximum(ma20, EPS) - 1) * 100 <= p.get("ma_tol", 3.0)
    up = (ma20 > ma60) & (cl > ma60)
    sel = ctx.universe() & np.isfinite(ma20) & shrink & near & up
    idx = np.where(sel)[0]
    return _mk(ctx, idx, 55 + np.minimum(35, (1 - vol[idx] / np.maximum(vma5[idx], EPS)) * 60),
               [f"缩量至 {vol[i]/max(vma5[i],EPS):.2f}× / 回踩 MA20（{ (cl[i]/ma20[i]-1)*100:+.1f}%）" for i in idx],
               "回踩不破 MA20 且次日放量即买；跌破 MA20 3% 止损")


def post_volume_divergence(ctx, p):
    cl = ctx.M["close"]
    hi60 = np.nanmax(cl[-61:], axis=0)
    new_high = cl[-1] >= hi60
    vol, vma5 = ctx.M["vol_hand"][-1], ctx.M["vol_ma5"][-1]
    low_vol = vol < p.get("vol_low", 0.8) * np.maximum(vma5, EPS)
    stall = (vol > p.get("stall_vol", 1.5) * np.maximum(vma5, EPS)) & (ctx.col("pct_chg") < 0.5)
    sel = ctx.universe() & ((new_high & low_vol) | stall)
    idx = np.where(sel)[0]
    rs = []
    for i in idx:
        if new_high[i] and low_vol[i]:
            rs.append(f"新高缩量（量仅 {vol[i]/max(vma5[i],EPS):.2f}×）→ 上涨乏力")
        else:
            rs.append(f"放量滞涨（量 {vol[i]/max(vma5[i],EPS):.2f}×，涨幅 {ctx.col('pct_chg')[i]:+.1f}%）→ 派发嫌疑")
    return _mk(ctx, idx, 80, rs, "预警项：持有者应减仓，不新增买入", risk=True)


def post_capital_return(ctx, p):
    to = ctx.col("turnover")
    ma5, ma10, ma20 = ctx.col("ma5"), ctx.col("ma10"), ctx.col("ma20")
    squeeze = (np.nanmax(np.vstack([ma5, ma10, ma20]), axis=0) /
               np.maximum(np.nanmin(np.vstack([ma5, ma10, ma20]), axis=0), EPS) - 1) * 100
    rsi6, rsi12 = ctx.col("rsi6"), ctx.col("rsi12")
    cl = ctx.col("close")
    div = (cl < ma20) & (rsi6 > rsi12)          # 底背离
    vol, vma5 = ctx.M["vol_hand"][-1], ctx.M["vol_ma5"][-1]
    shrink = vol < np.maximum(vma5, EPS)
    score = ((np.clip(p.get("turnover_max", 6.0) - to, 0, 6) / 6) * 30
             + (np.clip(3 - squeeze, 0, 3) / 3) * 30
             + div * 20 + shrink * 20)
    score = np.where(np.isfinite(score), score, 0)
    sel = ctx.universe() & np.isfinite(to) & (score >= p.get("score_min", 65))
    idx = np.where(sel)[0]
    return _mk(ctx, idx, score[idx],
               [f"换手 {to[i]:.1f}% + 均线粘合 {squeeze[i]:.1f}% + {'底背离 ' if div[i] else ''}评分 {score[i]:.0f}"
                for i in idx],
               "小仓埋伏；放量突破再加（卡内：≥65 分 ≤2 成）")


def post_position(ctx, p):
    hi = np.nanmax(ctx.M["high"][-60:], axis=0)
    lo = np.nanmin(ctx.M["low"][-60:], axis=0)
    cl = ctx.col("close")
    pos = (cl - lo) / np.maximum(hi - lo, EPS) * 100
    ma20 = ctx.col("ma20")
    sel = ctx.universe() & np.isfinite(pos) & (pos <= p.get("pos_max", 35)) & (cl > ma20)
    idx = np.where(sel)[0]
    return _mk(ctx, idx, 100 - pos[idx],
               [f"60 日位置 {pos[i]:.0f}%（低位）且站上 MA20" for i in idx],
               "位置>形态：低位先于形态；跌破 MA20 离场")


def post_sector_rs(ctx, p):
    r = _ret(ctx, 120)
    df = pd.DataFrame({"c": ctx.codes,
                       "sec": [ctx.industry.get(c, "未知") for c in ctx.codes],
                       "r": r})
    g = df.groupby("sec")["r"].mean().dropna().sort_values(ascending=False)
    k = max(1, int(len(g) * p.get("top_pct", 20) / 100))
    top = set(g.head(k).index)
    rs = _rs(ctx)
    uni = ctx.universe()
    rec = []
    for i, c in enumerate(ctx.codes):
        if uni[i] and ctx.industry.get(c, "") in top and np.isfinite(r[i]) and r[i] > 0:
            rec.append((i, min(99, 50 + rs[i] * 0.5), f"行业「{ctx.industry[c]}」近 120 日 {g[ctx.industry[c]]*100:+.1f}% + 个股 {r[i]*100:+.1f}%"))
    if not rec:
        return _mk(ctx, [], [], [])
    idx, sc, rr = zip(*rec)
    return _mk(ctx, idx, sc, rr, "只做前 20% 行业，回避后 20%")


def post_reversal(ctx, p):
    n = p.get("lookback", 20)
    r = _ret(ctx, n)
    pr = _pct_rank(r)
    ma5 = ctx.col("ma5")
    cl = ctx.col("close")
    vol, vma5 = ctx.M["vol_hand"][-1], ctx.M["vol_ma5"][-1]
    stabilize = (cl > ma5) & (vol < np.maximum(vma5, EPS))
    sel = ctx.universe() & np.isfinite(pr) & (pr <= 10) & stabilize
    idx = np.where(sel)[0]
    return _mk(ctx, idx, 100 - pr[idx],
               [f"{n} 日跌幅居前（{r[i]*100:+.1f}%）+ 缩量站回 MA5" for i in idx],
               "短期反转：A 股 1 月反转最强（本地实测 IC +0.04）；反抽 5~8% 即走")


def post_box_breakout(ctx, p):
    n = p.get("box_days", 40)
    hi = np.nanmax(ctx.M["high"][-n - 1:-2], axis=0)
    lo = np.nanmin(ctx.M["low"][-n - 1:-2], axis=0)
    rng = (hi - lo) / np.maximum(lo, EPS) * 100
    cl = ctx.col("close")
    vr = ctx.M["vol_hand"][-1] / np.maximum(ctx.M["vol_ma5"][-1], EPS)
    sel = (ctx.universe() & np.isfinite(rng) & (rng <= p.get("box_max", 15))
           & (cl > hi) & (vr >= 1.5))
    idx = np.where(sel)[0]
    return _mk(ctx, idx, 60 + np.minimum(35, (vr[idx] - 1.5) * 20),
               [f"{n} 日箱体（{rng[i]:.0f}%）放量 {vr[i]:.1f}× 突破上沿" for i in idx],
               "箱体突破后反抽上沿不破可加；跌回箱体止损")


def post_gap_chain(ctx, p):
    hi_prev = ctx.M["high"][-2]
    lo = ctx.M["low"][-1]
    gap = (lo / np.maximum(hi_prev, EPS) - 1) * 100
    vr = ctx.M["vol_hand"][-1] / np.maximum(ctx.M["vol_ma5"][-1], EPS)
    sel = ctx.universe() & np.isfinite(gap) & (gap >= p.get("gap_min", 2.0)) & (vr >= 1.2)
    idx = np.where(sel)[0]
    return _mk(ctx, idx, 60 + np.minimum(35, gap[idx] * 6),
               [f"向上跳空 {gap[i]:+.1f}%（突破缺口）+ 量 {vr[i]:.1f}×" for i in idx],
               "突破缺口 3 日不补即有效；回补缺口止损")


def post_high_vol_leader(ctx, p):
    hi = np.nanmax(ctx.M["high"][-20:], axis=0)
    lo = np.nanmin(ctx.M["low"][-20:], axis=0)
    amp = (hi - lo) / np.maximum(lo, EPS) * 100
    rs = _rs(ctx)
    sel = ctx.universe() & np.isfinite(amp) & (amp >= p.get("amp_min", 15)) & (rs >= p.get("rs_min", 80))
    idx = np.where(sel)[0]
    return _mk(ctx, idx, np.minimum(99, rs[idx]),
               [f"20 日波幅 {amp[i]:.0f}%（≥15 才有钱赚）+ RS {rs[i]:.0f}" for i in idx],
               "高波动品种：仓位减半，止损放宽到 2N")


# ─────────────────────────── 注册表 ───────────────────────────
IMPL = {
    "pre_limitup_ladder": pre_limitup_ladder,
    "pre_first_board_next": pre_first_board_next,
    "pre_gap_volume": pre_gap_volume,
    "pre_auction_pool": pre_auction_pool,
    "pre_mainline_leader": pre_mainline_leader,
    "pre_strong_close": pre_strong_close,
    "intra_vwap_support": intra_vwap_support,
    "intra_volume_breakout": intra_volume_breakout,
    "intra_speed_surge": intra_speed_surge,
    "intra_first_touch": intra_first_touch,
    "intra_sector_flow": intra_sector_flow,
    "intra_tail_strong": intra_tail_strong,
    "intra_broken_reseal": intra_broken_reseal,
    "intra_leader_pullback": intra_leader_pullback,
    "post_rs80": post_rs80,
    "post_cup_handle": post_cup_handle,
    "post_55_breakout": post_55_breakout,
    "post_range_volume": post_range_volume,
    "post_shrink_pullback": post_shrink_pullback,
    "post_volume_divergence": post_volume_divergence,
    "post_capital_return": post_capital_return,
    "post_position": post_position,
    "post_sector_rs": post_sector_rs,
    "post_reversal": post_reversal,
    "post_box_breakout": post_box_breakout,
    "post_gap_chain": post_gap_chain,
    "post_high_vol_leader": post_high_vol_leader,
}


def run_strategy(ctx, strat, topn=30):
    fn = IMPL.get(strat["impl"])
    if fn is None:
        return pd.DataFrame(columns=["code6", "score", "reason", "trigger", "risk"])
    try:
        df = fn(ctx, strat.get("params", {}))
    except Exception as e:
        print(f"  ✗ {strat['id']}: {type(e).__name__}: {e}")
        return pd.DataFrame(columns=["code6", "score", "reason", "trigger", "risk"])
    df = df.copy()
    df["code6"] = df["code6"].astype(str)
    df = df.drop_duplicates("code6").head(topn)
    df["name"] = df["code6"].map(lambda c: ctx.name.get(c, c))
    df["strategy"] = strat["id"]
    df["strategy_name"] = strat["name"]
    df["bucket"] = strat["bucket"]
    return df
