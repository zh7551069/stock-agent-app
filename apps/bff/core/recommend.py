"""推荐编排：把 27 个策略按三时段聚合成股票推荐。"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core import engine, engine_live, realtime


def load_strategies():
    p = os.path.join(config.DATA, "strategies.json")
    return json.load(open(p, encoding="utf-8"))


def load_cards():
    p = os.path.join(config.DATA, "cards.json")
    return json.load(open(p, encoding="utf-8"))


# 策略权重（简单的先验，多策略共振时加权）
WEIGHT = {
    "post_rs80": 1.2, "post_cup_handle": 1.3, "post_55_breakout": 1.1,
    "post_range_volume": 1.2, "post_shrink_pullback": 1.0, "post_capital_return": 1.0,
    "pre_limitup_ladder": 1.2, "pre_first_board_next": 1.1, "intra_first_touch": 1.3,
    "intra_volume_breakout": 1.1, "post_volume_divergence": 1.0,
}


def market_env(ctx):
    """大盘环境（诉求-07 择时）：主要趋势 + 情绪"""
    idx = ctx.index_series
    out = {"date": ctx.last_date_str, "ok": False}
    if len(idx) < 70:
        return out
    c = idx.to_numpy(float)
    ma20 = np.nanmean(c[-20:])
    ma60 = np.nanmean(c[-60:])
    ret20 = c[-1] / c[-21] - 1
    ret5 = c[-1] / c[-6] - 1
    # 今日涨跌：首页主位置要用它，而不是 20 日累计（曾经混用过一次，会误读成"今天跌了 3.86%"）
    prev_close = float(c[-2]) if len(c) >= 2 else float("nan")
    index_pct = (c[-1] / prev_close - 1) if (np.isfinite(prev_close) and prev_close) else float("nan")
    trend = "上涨" if c[-1] > ma20 > ma60 else ("下跌" if c[-1] < ma20 < ma60 else "震荡")
    # 结构描述 + MA20 斜率：让"趋势"这个词可被核对，而不是一个孤零零的判断
    structure = "多头排列" if (c[-1] > ma20 > ma60) else ("空头排列" if (c[-1] < ma20 < ma60) else "均线纠缠")
    ma20_5ago = np.nanmean(c[-25:-5]) if len(c) >= 25 else ma20
    ma20_slope = (ma20 / ma20_5ago - 1) * 100 if ma20_5ago else 0.0
    # ── 涨停 / 炸板 / 封板率 ──────────────────────────────────────────────
    # 口径统一：与「涨跌家数」同源（日线 + 涨跌停价表），剔除 ST 与北交所。
    # 不能拿东财涨停池（54）除以涨停池+炸板池（54+30）：
    #   ① 炸板池含「炸板后又回封」的个股，与涨停池语义重叠、口径不一致；
    #   ② 两个池对 ST / 北交所的取舍与日线不同，分子分母不同源。
    codes_all = np.asarray(ctx.codes)
    names_all = np.array([str(ctx.name.get(c, "")) for c in codes_all])
    is_st = np.array(["ST" in n for n in names_all])
    is_bj = np.array([str(c).startswith(("4", "8", "9")) for c in codes_all])
    lu = np.array([ctx.limit_up.get(c, np.nan) for c in codes_all], dtype=float)
    close_t, high_t = ctx.M["close"][-1], ctx.M["high"][-1]
    ok = np.isfinite(lu) & np.isfinite(close_t) & np.isfinite(high_t) & ~is_st & ~is_bj
    sealed = ok & (close_t >= lu - 0.005)
    touched = ok & (high_t >= lu - 0.005)
    zt = int(sealed.sum())
    zb = int((touched & ~sealed).sum())
    touched_n = int(touched.sum())
    limit_base = int(ok.sum())          # 统计基数（剔除 ST/北交所后的全市场只数）
    seal_rate = zt / max(touched_n, 1) * 100
    zt_share = zt / max(limit_base, 1) * 100        # 收盘涨停占全市场比例
    touched_share = touched_n / max(limit_base, 1) * 100  # 曾涨停占全市场比例
    # 东财池表口径（保留作对照，不参与展示口径）
    zt_pool = len(ctx.zt_limit_up_today)
    zb_pool = len(ctx.zt_broken_today)
    # 涨跌家数
    pct = ctx.col("pct_chg")
    up = int(np.nansum(pct > 0))
    down = int(np.nansum(pct < 0))
    sec = engine._sector_today_pct(ctx)
    top_sec = list(sec.head(3).items()) if sec is not None and len(sec) else []
    # 简易择时闸门
    if trend == "下跌" or ret20 < -0.06:
        gate = "防守"
    elif trend == "上涨" and seal_rate >= 65:
        gate = "进攻"
    else:
        gate = "中性"
    out.update({
        "ok": True, "index_close": round(float(c[-1]), 2),
        "ma20": round(float(ma20), 2), "ma60": round(float(ma60), 2),
        "index_pct": round(float(index_pct) * 100, 2),
        "prev_close": round(prev_close, 2) if np.isfinite(prev_close) else None,
        "ret5_pct": round(float(ret5) * 100, 2), "ret20_pct": round(float(ret20) * 100, 2),
        "trend": trend, "zt_count": zt, "broken_count": zb,
        "seal_rate": round(seal_rate, 1), "up": up, "down": down,
        "touched_count": touched_n, "zt_pool": zt_pool, "zb_pool": zb_pool,
        "limit_base": limit_base, "zt_share": round(float(zt_share), 2),
        "touched_share": round(float(touched_share), 2),
        "structure": structure, "ma20_slope_pct": round(float(ma20_slope), 2),
        "limit_scope": "剔除 ST / 北交所；封板率 = 收盘封住 / 曾涨停",
        "top_sectors": [{"name": k, "pct": round(float(v), 2)} for k, v in top_sec],
        "gate": gate,
        "note": {"进攻": "趋势向上 + 封板率健康：可按信号正常出手",
                 "中性": "趋势不明：半仓以内，只做最强主线",
                 "防守": "趋势向下：以观察为主，禁止追高"}[gate],
    })
    return out


# 每条策略最多产出多少只命中个股（engine.run_strategy 的 topn 上限）
PER_STRATEGY = 30

# 盘中：天然「已封板」的两条策略 → 作为事件提醒，不参与个股推荐排序
EVENT_STRATS = {"intra_first_touch", "intra_broken_reseal"}
BREAKOUT_STRATS = {"intra_volume_breakout", "intra_speed_surge", "intra_sector_flow", "intra_tail_strong"}
PULLBACK_STRATS = {"intra_leader_pullback", "intra_vwap_support"}


def live_env(ctx, live, ix):
    """实时大盘环境：指数实时价 + 实时涨跌停/涨跌家数/领涨板块（盘中页与首页共用）"""
    import numpy as np

    closes = np.asarray(list(ctx.index_series.values), float)
    price = float(ix.get("price") or np.nan)
    c = np.append(closes, price) if np.isfinite(price) else closes
    ma20 = float(np.nanmean(c[-20:]))
    ma60 = float(np.nanmean(c[-60:]))
    prev = float(ix.get("prev_close") or closes[-1])
    ret5 = price / closes[-5] - 1 if np.isfinite(price) and len(closes) >= 5 else np.nan
    ret20 = price / closes[-20] - 1 if np.isfinite(price) and len(closes) >= 20 else np.nan
    trend = "上涨" if price > ma20 > ma60 else ("下跌" if price < ma20 < ma60 else "震荡")
    structure = "多头排列" if price > ma20 > ma60 else ("空头排列" if price < ma20 < ma60 else "震荡")
    # 涨跌停口径与 market_env 对齐：剔除 ST / 北交所，封板率 = 收盘封住 ÷ 曾涨停
    codes_all = np.asarray(ctx.codes)
    names_all = np.array([str(ctx.name.get(c, "")) for c in codes_all])
    is_st = np.array(["ST" in n for n in names_all])
    is_bj = np.array([str(c).startswith(("4", "8", "9")) for c in codes_all])
    base_ok = np.isfinite(live.prev_close) & np.isfinite(live.price) & ~is_st & ~is_bj
    sealed = base_ok & live.at_limit
    touched_mask = base_ok & live.touched_limit
    zt = int(sealed.sum())
    zb = int((touched_mask & ~sealed).sum())
    touched_n = int(touched_mask.sum())
    limit_base = int(base_ok.sum())
    seal = zt / max(touched_n, 1) * 100
    zt_share = zt / max(limit_base, 1) * 100
    touched_share = touched_n / max(limit_base, 1) * 100
    up = int(np.nansum(live.pct > 0))
    down = int(np.nansum(live.pct < 0))
    sec = pd.DataFrame({"sec": live.industry, "pct": live.pct})
    sec = sec[np.isfinite(sec["pct"])]
    g = sec.groupby("sec")["pct"].agg(["mean", "count"])
    g = g[g["count"] >= 3]["mean"].sort_values(ascending=False)
    top = [{"name": k, "pct": round(float(v), 2)} for k, v in g.head(3).items()]
    if trend == "下跌" or ret20 < -0.06:
        gate = "防守"
    elif trend == "上涨" and seal >= 65:
        gate = "进攻"
    else:
        gate = "中性"
    return {
        "ok": True, "live": True, "quote_time": ix.get("quote_time"),
        "index_close": round(price, 2), "index_pct": round(float(ix.get("pct") or 0), 2),
        "prev_close": round(prev, 2),
        "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "ma20_slope_pct": round(float((ma20 / float(np.nanmean(c[-25:-5])) - 1) * 100), 2)
        if len(c) >= 25 else 0.0,
        "ret5_pct": round(float(ret5) * 100, 2), "ret20_pct": round(float(ret20) * 100, 2),
        "trend": trend, "structure": structure,
        "zt_count": zt, "broken_count": zb, "seal_rate": round(seal, 1),
        "up": up, "down": down, "top_sectors": top, "gate": gate,
        "touched_count": touched_n, "limit_base": limit_base,
        "zt_share": round(float(zt_share), 2), "touched_share": round(float(touched_share), 2),
        "limit_scope": "剔除 ST / 北交所；封板率 = 收盘封住 / 曾涨停（实时口径）",
        "zt_pool": zt, "zb_pool": zb,
        "data_date": ctx.last_date_str, "date": ctx.last_date_str,
        "note": {"进攻": "趋势向上 + 封板率健康：可按信号正常出手",
                 "中性": "趋势不明：半仓以内，只做最强主线",
                 "防守": "趋势向下：以观察为主，禁止追高"}[gate],
    }


def run_bucket(ctx, bucket, strategies, topn=20, per_strategy=PER_STRATEGY, live=None):
    rows = []
    # 盘中：实时源可用时走实时引擎；否则回退本地历史回放
    use_live = bool(live is not None and getattr(live, "available", False) and bucket == "盘中")
    for s in strategies:
        if s["bucket"] != bucket:
            continue
        if use_live:
            df = engine_live.run_strategy(live, s, topn=per_strategy)
        else:
            df = engine.run_strategy(ctx, s, topn=per_strategy)
        if len(df):
            rows.append(df)
    live_meta = live.meta if use_live else None
    if not rows:
        return {"bucket": bucket, "stocks": [], "strategies": [],
                "hits_by_strategy": {}, "live": live_meta}
    allhits = pd.concat(rows, ignore_index=True)

    # 盘中：首触涨停 / 炸板回封 天然是封板状态，改为「事件提醒」，不进推荐榜
    events = []
    if bucket == "盘中":
        evh = allhits[allhits["strategy"].isin(EVENT_STRATS)]
        seen = set()
        for r in evh.itertuples():
            if r.code6 in seen:
                continue
            seen.add(r.code6)
            events.append({"code": str(r.code6), "name": r.name,
                           "strategy": r.strategy, "strategy_name": r.strategy_name,
                           "reason": r.reason, "trigger": r.trigger or ""})
        mainhits = allhits[~allhits["strategy"].isin(EVENT_STRATS)]
    else:
        mainhits = allhits

    stocks = []
    for code, g in mainhits.groupby("code6"):
        pos = [h for h in g.itertuples() if not h.risk]
        neg = [h for h in g.itertuples() if h.risk]
        if not pos:
            continue
        wsum = sum(WEIGHT.get(h.strategy, 1.0) for h in pos)
        base = sum(h.score * WEIGHT.get(h.strategy, 1.0) for h in pos) / max(wsum, 1e-9)
        # 多策略共振加成：命中越多越靠前
        bonus = min(24, (len(pos) - 1) * 8)
        score = min(100.0, base * 0.9 + bonus)
        if neg:
            score *= 0.75
        # ── 盘中走势融合（2026-11-03）：同分区间内用分时走势排序 ──────────
        # 单策略分表达「符合知识点判据的程度」，其分子多来自当日快照；
        # 走势分表达「这 5/15 分钟是在走强还是走弱」，只做 ±6 分的排序权重，
        # 不改判据本身（避免把"涨得多"误当成"符合买点"）。
        trend = None
        if use_live and code in live.pos:
            pi = live.pos[code]
            tsc = float(live.trend[pi]) if np.isfinite(live.trend[pi]) else None
            if tsc is not None:
                score = min(100.0, max(0.0, score + (tsc - 50.0) / 50.0 * 6.0))
                trend = {"score": round(tsc, 1),
                         "label": str(live.trend_label[pi]),
                         "mom5": _f(live.t_mom5[pi]),
                         "mom1": _f(live.t_mom1[pi]),
                         "accel": _f(live.t_accel[pi], 2),
                         "vwap_slope": _f(live.t_vwap_slope[pi], 2),
                         "vol_pulse": _f(live.t_vol_pulse[pi], 2),
                         "pos": _f(live.t_pos[pi], 2)}
        if use_live and code in live.pos:
            p_ = live.pos[code]
            live_close = _f(live.price[p_])
            live_pct = _f(live.pct[p_])
            live_ind = live.industry[p_] if p_ < len(live.industry) else ctx.industry.get(code, "")
            live_mv = _f(live.circ_mv[p_], 1) if np.isfinite(live.circ_mv[p_]) else _f(ctx.circ_mv.get(code))
        else:
            live_close = _f(ctx.M["close"][-1, _pos(ctx, code)])
            live_pct = _f(ctx.M["pct_chg"][-1, _pos(ctx, code)])
            live_ind = ctx.industry.get(code, "")
            live_mv = _f(ctx.circ_mv.get(code))
        if use_live and code in live.pos:
            p_ = live.pos[code]
            t_state = str(live.tradable[p_])
            t_rank = int(live.tradable_rank[p_])
            t_gap = _f(live.limit_gap[p_])
            pct_v = float(live.pct[p_]) if np.isfinite(live.pct[p_]) else None
        else:
            t_state, t_rank, t_gap = "—", 0, None
            pct_v = None
        # 盘中分组：回踩低吸型（优先）/ 突破型；追高风险标签
        strats = {h.strategy for h in pos}
        chase = None
        if pct_v is not None:
            chase = "高危" if pct_v >= 9 else ("追高" if pct_v >= 7 else None)
        if strats & BREAKOUT_STRATS:
            kind = "突破"
        elif strats & PULLBACK_STRATS:
            # 已涨 ≥7% 的「回踩」名不副实，划回突破组（低吸组只留未追高的）
            kind = "回踩" if (pct_v is None or pct_v < 7) else "突破"
        else:
            kind = "其他"
        stocks.append({
            "code": code,
            "tradable": t_state,
            "tradable_rank": t_rank,
            "limit_gap": t_gap,
            "kind": kind,
            "chase": chase,
            "ts_code": ctx.ts_code.get(code, ""),
            "name": ctx.name.get(code, code),
            "industry": live_ind,
            "close": live_close,
            "pct_chg": live_pct,
            "circ_mv": live_mv,
            "score": round(float(score), 1),
            "hit_count": len(pos),
            "hits": [{"strategy": h.strategy, "strategy_name": h.strategy_name,
                      "score": round(float(h.score), 1), "reason": h.reason,
                      "trigger": h.trigger or "", "risk": False} for h in pos],
            "warnings": [{"strategy": h.strategy_name, "reason": h.reason} for h in neg],
            "trend": trend,
        })
    stocks.sort(key=lambda x: (-x["score"], -((x.get("trend") or {}).get("score") or 0),
                               -x["hit_count"]))
    buyable = sum(1 for x in stocks if x.get("tradable_rank", 0) <= 1)
    sealed_n = sum(1 for x in stocks if x.get("tradable_rank", 0) >= 2)
    # 策略统计
    stat = (allhits.groupby(["strategy", "strategy_name"]).size()
            .reset_index(name="count").sort_values("count", ascending=False))
    # 每条策略自己的完整命中集合（列表的「今日命中」与详情的命中列表都由它派生，
    # 不再从推荐榜 topn 股里反查 —— 那样会漏掉没进榜的命中）
    hits_by_strategy = {}
    for sid, g in allhits.groupby("strategy"):
        hits_by_strategy[sid] = [
            {"code": str(r.code6), "name": r.name,
             "score": round(float(r.score), 1), "reason": r.reason,
             "trigger": (r.trigger or ""), "risk": bool(r.risk)}
            for r in g.itertuples()
        ]
    return {"bucket": bucket, "stocks": stocks[:topn],
            "strategies": [{"id": r.strategy, "name": r.strategy_name, "count": int(r["count"]),
                            "capped": int(r["count"]) >= per_strategy}
                           for _, r in stat.iterrows()],
            "hits_by_strategy": hits_by_strategy, "live": live_meta,
            "buyable_count": buyable, "sealed_count": sealed_n,
            "events": events}


def _pos(ctx, code):
    try:
        return int(np.where(ctx.codes == code)[0][0])
    except Exception:
        return 0


def _f(x, nd=2):
    try:
        v = float(x)
        return round(v, nd) if np.isfinite(v) else None
    except Exception:
        return None


def run_all(ctx, buckets=("盘前", "盘中", "盘后"), live=None):
    data = load_strategies()
    strat = data["strategies"]
    env = market_env(ctx)
    out = {"data_date": ctx.last_date_str, "minute_date": ctx.minute_date,
           "env": env, "buckets": {}, "hits_by_strategy": {}, "live": None}
    for b in buckets:
        rb = run_bucket(ctx, b, strat, live=live)
        out["buckets"][b] = rb
        out["hits_by_strategy"].update(rb.get("hits_by_strategy", {}))
        if rb.get("live"):
            out["live"] = rb["live"]
    return out



# ─────────────────── 蓄势观察池（2026-11-03，P1）───────────────────
# 定位：**观察档，不是买点**。回测（src/presurge_test2.py，103 天 1.57 亿样本）结论：
#   - 缩量横盘蓄势不提高「未来 30 分钟拉升」的概率（同波动率配对 lift 0.70），
#     但把下行风险从 3.14% 砍到 0.75%，T+1 期望比基线好 8bp；
#   - 「启动确认」也没有增量（lift 0.96）。
# 所以它只做一件事：把"追已经涨上去的"换成"盯住还没涨、且抛压已经枯竭的"，等启动确认再决定。
WATCH_MIN_AMT = 3.0e7          # 成交额下限（元）
WATCH_COIL_PCT = (-1.0, 2.0)   # 蓄势：涨幅区间
WATCH_COIL_AMP = 1.8           # 蓄势：振幅上限（%）
WATCH_COIL_VR = 0.80           # 蓄势：量比上限（缩量）
WATCH_GO_PCT = (-1.0, 3.5)     # 启动：涨幅区间（还没大幅拉升）
WATCH_GO_AMP = 3.0             # 启动：振幅上限
WATCH_GO_VR = 1.20             # 启动：量比下限


def watch_pool(live, topn=12, topn_go=6):
    """盘中「蓄势观察池」：缩量横盘等待启动的票（主）+ 刚创日内新高但涨幅还不大的票（辅）。

    只用实时快照（零额外请求）。返回 {"coil": [...], "go": [...], "counts": {...}}
    """
    if live is None or not getattr(live, "available", False):
        return []
    try:
        import numpy as np
        uni = live.universe(min_amount=WATCH_MIN_AMT, min_price=2.0)
        pct = live.pct
        above = np.where(np.isfinite(live.vwap), live.price / live.vwap - 1, np.nan)
        amp = np.where(np.isfinite(live.prev_close) & (live.prev_close > 0),
                       (live.high - live.low) / live.prev_close * 100, np.nan)
        vr = live.vol_ratio
        rng = live.high - live.low
        pos = np.where(rng > 0, (live.price - live.low) / rng, np.nan)
        gap = live.limit_gap
        base = (uni & np.isfinite(pct) & np.isfinite(above) & np.isfinite(amp)
                & np.isfinite(vr) & np.isfinite(pos) & np.isfinite(gap)
                & (gap > 5.0) & (live.amount >= WATCH_MIN_AMT))
        coil = (base & (pct >= WATCH_COIL_PCT[0]) & (pct <= WATCH_COIL_PCT[1])
                & (amp <= WATCH_COIL_AMP) & (vr <= WATCH_COIL_VR)
                & (above >= -0.003) & (pos >= 0.25) & (pos <= 0.85))
        go = (base & (pct >= WATCH_GO_PCT[0]) & (pct <= WATCH_GO_PCT[1])
              & (amp <= WATCH_GO_AMP) & (vr >= WATCH_GO_VR)
              & (above >= 0.0) & (pos >= 0.90)
              & (live.price >= live.high * 0.995))
        # 板块不弱：同一行业平均涨幅（≥0 记满分 5）
        sec = {}
        for j, s in enumerate(live.industry):
            if s and np.isfinite(pct[j]):
                sec.setdefault(s, []).append(pct[j])
        sec_mean = {k: float(np.mean(v)) for k, v in sec.items() if len(v) >= 3}

        rows = []
        for mask, state in ((coil, "蓄势"), (go, "启动")):
            idx = np.where(mask)[0]
            if not len(idx):
                continue
            for i in idx:
                sm = sec_mean.get(live.industry[i], 0.0)
                if state == "蓄势":
                    score = (40
                             + min(max((0.80 - vr[i]) / 0.50, 0), 1) * 20
                             + min(max(1 - abs(above[i]) / 0.008, 0), 1) * 15
                             + min(max(1 - abs(pos[i] - 0.55) / 0.45, 0), 1) * 10
                             + min(max((WATCH_COIL_AMP - amp[i]) / 1.5, 0), 1) * 10
                             + (5 if sm >= 0 else 0))
                    reason = (f"缩量 {vr[i]:.2f}× · 贴均价 {above[i]*100:+.2f}%"
                              f" · 日内位置 {pos[i]*100:.0f}% · 振幅 {amp[i]:.1f}%")
                else:
                    score = (55
                             + min(max((vr[i] - 1.2) / 1.5, 0), 1) * 15
                             + min(max((3.0 - pct[i]) / 3.0, 0), 1) * 10
                             + (5 if sm >= 0 else 0))
                    reason = (f"刚创日内新高 · 量比 {vr[i]:.2f} · 涨幅 {pct[i]:+.2f}%"
                              f" · 振幅 {amp[i]:.1f}%")
                rows.append({
                    "code": str(live.codes[i]), "state": state,
                    "name": live.ctx.name.get(str(live.codes[i]), str(live.codes[i])),
                    "industry": live.industry[i],
                    "close": _f(live.price[i]), "pct_chg": _f(pct[i]),
                    "vol_ratio": _f(vr[i], 2), "above_vwap": _f(above[i] * 100, 2),
                    "amp": _f(amp[i], 2), "pos": _f(pos[i] * 100, 0),
                    "limit_gap": _f(gap[i], 1),
                    "score": round(float(score), 1),
                    "reason": reason,
                })
        coil_rows = sorted([r for r in rows if r["state"] == "蓄势"],
                           key=lambda x: -x["score"])[:topn]
        go_rows = sorted([r for r in rows if r["state"] == "启动"],
                         key=lambda x: -x["score"])[:topn_go]
        return {"coil": coil_rows, "go": go_rows,
                "counts": {"coil": int(coil.sum()), "go": int(go.sum())}}
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ watch_pool: {type(e).__name__}: {e}")
        return {"coil": [], "go": [], "counts": {"coil": 0, "go": 0}}


def run_intraday(ctx, force=False):
    """盘中专用入口：优先实时，失败回退本地回放。force=True 时绕过实时快照 TTL（手动刷新）。"""
    data = load_strategies()
    strat = data["strategies"]
    live = realtime.get_live(ctx, force=force)
    if live is not None:
        rb = run_bucket(ctx, "盘中", strat, topn=120, live=live)
        return {**rb, "watch": watch_pool(live),
                "source": ("cache" if getattr(live, "stale", False) else "realtime"),
                "live": live.meta, "live_status": realtime.status()}
    rb = run_bucket(ctx, "盘中", strat)
    st = realtime.status()
    return {**rb, "watch": {"coil": [], "go": [], "counts": {"coil": 0, "go": 0}},
            "source": "replay",
            "live": {"source": "replay", "quote_time": None, "is_trading": False,
                     "phase": st.get("phase"), "reason": st.get("error") or "实时源不可用"},
            "live_status": st}


def sparkline(ctx, code, n=40):
    """返回最近 n 日收盘价（给前端画走势）"""
    p = _pos(ctx, code)
    cl = ctx.M["close"][-n:, p]
    return [None if not np.isfinite(x) else round(float(x), 2) for x in cl]
