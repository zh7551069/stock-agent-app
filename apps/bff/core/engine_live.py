"""盘中策略的实时版本（数据来自 core/realtime.LiveMarket）。

与 engine.py 的差异：
  - 不读本地 5min 历史，读实时快照（现价/均价/量比/涨速/日内高低/昨收）
  - 「首触涨停」「炸板回封」用当日实时 5min K 线（只对涨停候选拉取）
  - 非交易时段自动退化为「当日收盘快照」（仍是最新，不是历史回放）
"""
import numpy as np
import pandas as pd

from core.engine import _mk


# ─────────────────────────── 盘中共用 ───────────────────────────
def _ew(live, lo=-2.0):
    """强势类策略的统一门槛：当日跌幅 > -2%"""
    return np.isfinite(live.pct) & (live.pct > lo)


def _sat(x, k):
    """平滑饱和：0→0，k 处约 0.76，之后缓慢趋近 1（避免硬 cap 造成的同分）"""
    return np.tanh(np.maximum(x, 0.0) / max(k, 1e-9))


def _tn(a, fill=0.0):
    """走势数组取值（无分时序列时按中性 0 处理，避免整条策略消失）"""
    return np.where(np.isfinite(a), a, fill)


def _tnote(live, i):
    """走势说明（仅当分时序列已就绪时出现，盘前不打印 0.00%）"""
    vs, m5 = live.t_vwap_slope[i], live.t_mom5[i]
    if not (np.isfinite(vs) or np.isfinite(m5)):
        return ""
    parts = []
    if np.isfinite(vs):
        parts.append("均价线" + ("↑" if vs > 0.05 else ("↓" if vs < -0.05 else "→"))
                     + f" {vs:+.2f}%")
    if np.isfinite(m5):
        parts.append(f"5min {m5:+.2f}%")
    return " · " + " · ".join(parts)


def _fmt(x, nd=2):
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return "—"


def _vwap_state(live, i):
    op, pc = live.open[i], live.prev_close[i]
    if np.isfinite(op) and np.isfinite(pc) and op < pc * 0.995:
        return "低开高走"
    return "下探回升"


def intra_vwap_support(live, p):
    """站上分时均价（低开/下探后收复）"""
    uni = live.universe()
    above = np.isfinite(live.vwap) & np.isfinite(live.price) & (live.price > live.vwap * 1.001)
    dipped = ((live.open < live.prev_close * 0.995)
              | (np.isfinite(live.vwap) & (live.low < live.vwap * 0.998)))
    sel = uni & above & dipped & _ew(live)
    idx = np.where(sel)[0]
    prem = live.price[idx] / live.vwap[idx] - 1
    # 分时形态（知识点-分时基础形态）：均价线上升 + 放量站上 = 突破看多；
    # 均价线走低时打折（破线看空的前兆），低开高走站上均价须 >0.5% 距离（噪声区规则）
    vs = _tn(live.t_vwap_slope[idx])
    pulse = _tn(live.t_vol_pulse[idx], 1.0)
    sc = (55 + 40 * _sat(prem, 0.05)
          + 6 * _sat(vs, 1.5)              # 均价线向上
          + 5 * _sat(pulse - 1.0, 1.0))    # 放量站上
    sc = np.where(vs < -0.3, sc * 0.9, sc)   # 均价线明显走低 → 形态降级
    sealed = live.at_limit[idx]
    sc = np.where(sealed, sc * 0.75, sc)
    rs = [f"现价 {_fmt(live.price[i])} 站上分时均价 {_fmt(live.vwap[i])}（{_vwap_state(live, i)}）"
          + _tnote(live, i) + ("·已封板" if sealed[k] else "")
          for k, i in enumerate(idx)]
    return _mk(live.ctx, idx, sc, rs, "回踩均价线不破可持；跌破均价线离场")


def intra_volume_breakout(live, p):
    """盘中放量突破：现价刷新日内高点 + 量能/涨速配合"""
    uni = live.universe()
    high = live.high
    near_high = np.isfinite(high) & (live.price >= high * 0.999)
    vr = np.where(np.isfinite(live.vol_ratio), live.vol_ratio, 0)
    speed = np.where(np.isfinite(live.speed), live.speed, 0)
    sel = uni & near_high & ((vr >= 2.0) | (speed >= 0.3)) & _ew(live)
    idx = np.where(sel)[0]
    vr_ex = np.maximum(vr[idx] - 2.0, 0.0)
    mom = _tn(live.t_mom5[idx])
    acc = _tn(live.t_accel[idx])
    sc = (60 + 30 * _sat(vr_ex, 6.0) + 10 * _sat(speed[idx], 3.0)
          + 6 * _sat(mom, 2.0) + 4 * _sat(acc, 1.0))   # 突破须有走势延续，不做"一根脉冲"
    sealed = live.at_limit[idx]
    sc = np.where(sealed, sc * 0.75, sc)
    rs = [(f"现价刷新日内新高 {_fmt(live.price[i])}（量比 {_fmt(vr[i])}"
           + (f"，5min 涨速 {mom[k]:+.1f}%" if mom[k] > 0 else "") + "）"
           + ("·已封板" if sealed[k] else "")) for k, i in enumerate(idx)]
    return _mk(live.ctx, idx, sc, rs, "突破后回踩日内高点不破即持有")


def intra_speed_surge(live, p):
    """涨速异动：近 5 分钟涨速 ≥1% 且站上均价"""
    uni = live.universe()
    speed = np.where(np.isfinite(live.speed), live.speed, -9)
    sel = (uni & (speed >= 1.0)
           & np.isfinite(live.vwap) & (live.price > live.vwap) & _ew(live))
    idx = np.where(sel)[0]
    sc = 60 + 39 * _sat(speed[idx], 4.0)
    rs = [f"近 5 分钟涨速 {speed[i]:+.1f}%（现价 {_fmt(live.price[i])}）" for i in idx]
    return _mk(live.ctx, idx, sc, rs, "涨速后不追，等回踩均价线")


def _limit_touch_info(live, pos):
    """返回 (首触涨停时间, 是否开过板) 或 (None, False)"""
    bars = live.bars.get(pos)
    if bars is None or not len(bars):
        return None, False
    lu = live.limit_up[pos]
    if not np.isfinite(lu):
        return None, False
    times = live.bar_times.get(pos) or []
    first = None
    opened = False
    for j in range(len(bars)):
        hi, cl = bars[j, 1], bars[j, 3]
        if first is None and hi >= lu - 0.005:
            first = j
        elif first is not None and cl < lu - 0.005:
            opened = True
    if first is None:
        return None, False
    t = times[first] if first < len(times) else ""
    return (t[-8:] if t else None), opened


def intra_first_touch(live, p):
    """10:00 前首触涨停（用实时 5min K 线定位首触时间）"""
    rec = []
    uni = live.universe()
    for pos in list(live.bars.keys()):
        if not uni[pos]:
            continue
        t, _ = _limit_touch_info(live, pos)
        if not t or len(t) < 5:
            continue
        try:
            h, m = int(t[:2]), int(t[3:5])
        except Exception:
            continue
        minutes = h * 60 + m
        if minutes > 600:
            continue
        if not live.at_limit[pos]:          # 当前必须仍封住
            continue
        rec.append((pos, min(99, 95 - (minutes - 570) * 0.15), f"{t} 首触涨停（当前封住）"))
    if not rec:
        return _mk(live.ctx, [], [], [])
    idx, sc, rs = zip(*rec)
    return _mk(live.ctx, list(idx), list(sc), list(rs), "封单减弱/开板即走")


def intra_sector_flow(live, p):
    """板块资金流领涨：实时行业涨幅前列板块中的强势股"""
    uni = live.universe()
    sec = pd.DataFrame({"sec": live.industry, "pct": live.pct})
    sec = sec[np.isfinite(sec["pct"])]
    g = sec.groupby("sec")["pct"].agg(["mean", "count"])
    g = g[g["count"] >= 3]["mean"].sort_values(ascending=False)
    if not len(g):
        return _mk(live.ctx, [], [], [])
    top = set(g.head(p.get("top_n", 5)).index)
    sel = uni & np.array([s in top for s in live.industry]) & np.isfinite(live.pct) & (live.pct >= 3)
    idx = np.where(sel)[0]
    sc = 50 + 45 * _sat(live.pct[idx], 8.0)
    rs = [f"板块「{live.industry[i]}」实时领涨 + 个股 {live.pct[i]:+.1f}%" for i in idx]
    return _mk(live.ctx, idx, sc, rs, "板块效应延续则持有；板块转弱即减")


def intra_tail_strong(live, p):
    """尾盘强势（14:30 后才有意义；收盘后按收盘快照判定）"""
    from core import realtime
    now = realtime.now_cn()
    phase = live.phase
    ready = (now.hour * 60 + now.minute >= 14 * 60 + 30) or phase in ("已收盘", "尾盘竞价")
    if not ready:
        return _mk(live.ctx, [], [], [])
    uni = live.universe()
    high = live.high
    near_high = np.isfinite(high) & (live.price >= high * 0.995)
    speed = np.where(np.isfinite(live.speed), live.speed, 0)
    sel = uni & near_high & (live.pct > 0) & (speed >= 0)
    idx = np.where(sel)[0]
    sc = 60 + 30 * _sat(live.pct[idx], 8.0) + 9 * _sat(speed[idx], 3.0)
    sealed = live.at_limit[idx]
    sc = np.where(sealed, sc * 0.75, sc)
    rs = [f"尾盘收在日内高点附近（现价 {_fmt(live.price[i])}，日内最高 {_fmt(high[i])}）"
          + ("·已封板" if sealed[k] else "") for k, i in enumerate(idx)]
    return _mk(live.ctx, idx, sc, rs, "次日竞价高开即溢价；低开走")


def intra_broken_reseal(live, p):
    """炸板回封：当日触板 → 开板 → 当前重新封住"""
    rec = []
    uni = live.universe()
    for pos in list(live.bars.keys()):
        if not uni[pos]:
            continue
        t, opened = _limit_touch_info(live, pos)
        if not opened or not live.at_limit[pos]:
            continue
        rec.append((pos, 85, f"{t or '盘中'} 触板后开板、当前回封（承接强）"))
    if not rec:
        return _mk(live.ctx, [], [], [])
    idx, sc, rs = zip(*rec)
    return _mk(live.ctx, list(idx), list(sc), list(rs), "回封后封单不减可持；二次开板走")


def intra_leader_pullback(live, p):
    """龙头回踩均价线不破（RS≥80）——多因子连续打分，避免 RS 截顶后全部同分。"""
    uni = live.universe()
    rs = live.rs(252)
    dev = np.abs(live.price / live.vwap - 1)              # 距均价线偏离
    near = np.isfinite(live.vwap) & (dev <= 0.005)
    above = np.isfinite(live.vwap) & (live.price > live.vwap)
    sel = uni & np.isfinite(rs) & (rs >= p.get("rs_min", 80)) & near & above & _ew(live, -3)
    idx = np.where(sel)[0]
    if not len(idx):
        return _mk(live.ctx, [], [], [])
    pull = (live.high[idx] - live.price[idx]) / np.maximum(live.high[idx], 1e-9) * 100
    vr = np.where(np.isfinite(live.vol_ratio[idx]), live.vol_ratio[idx], 1.0)
    pct = np.where(np.isfinite(live.pct[idx]), live.pct[idx], 0.0)
    # 40 底分 + RS(0~16) + 贴线(0~14) + 回踩幅度(0~12) + 缩量(0~10) + 涨幅温和(0~6)
    vs = _tn(live.t_vwap_slope[idx])
    mom = _tn(live.t_mom5[idx])
    sc = (40
          + np.clip((rs[idx] - 80) * 0.8, 0, 16)
          + np.clip(1 - dev[idx] / 0.005, 0, 1) * 14
          + np.clip(pull / 3.0, 0, 1) * 12
          + np.clip((1.1 - vr) / 1.1, 0, 1) * 10
          + np.clip((6 - pct) / 6, 0, 1) * 6
          + 5 * _sat(vs, 1.5)              # 均价线仍在向上 = 回踩不破的前提
          - 6 * _sat(-mom, 2.0))           # 5min 仍在加速下跌 → 不是回踩是转弱
    reasons = [f"RS {rs[i]:.0f} · 贴线 {dev[i]*100:.2f}% · 回撤 {pull[k]:.1f}% · 量比 {vr[k]:.2f} · {pct[k]:+.1f}%"
               + _tnote(live, i)
               for k, i in enumerate(idx)]
    return _mk(live.ctx, idx, sc, reasons, "回踩均价线即低吸点；跌破均价线止损")


IMPL_LIVE = {
    "intra_vwap_support": intra_vwap_support,
    "intra_volume_breakout": intra_volume_breakout,
    "intra_speed_surge": intra_speed_surge,
    "intra_first_touch": intra_first_touch,
    "intra_sector_flow": intra_sector_flow,
    "intra_tail_strong": intra_tail_strong,
    "intra_broken_reseal": intra_broken_reseal,
    "intra_leader_pullback": intra_leader_pullback,
}


def run_strategy(live, strat, topn=30):
    """签名与 engine.run_strategy 对齐，供 recommend.run_bucket 直接调用"""
    fn = IMPL_LIVE.get(strat["impl"])
    empty = pd.DataFrame(columns=["code6", "score", "reason", "trigger", "risk"])
    if fn is None:
        return empty
    try:
        df = fn(live, strat.get("params", {}))
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ live {strat['id']}: {type(e).__name__}: {e}")
        return empty
    df = df.copy()
    if len(df) == 0:
        return empty
    df["code6"] = df["code6"].astype(str)
    df = df.drop_duplicates("code6").head(topn)
    ctx = live.ctx
    df["name"] = df["code6"].map(lambda c: ctx.name.get(c, c))
    df["strategy"] = strat["id"]
    df["strategy_name"] = strat["name"]
    df["bucket"] = strat["bucket"]
    # 覆盖价格为实时价
    pos = live.pos
    df["close"] = df["code6"].map(lambda c: float(live.price[pos[c]]) if c in pos else np.nan)
    df["pct_chg"] = df["code6"].map(lambda c: float(live.pct[pos[c]]) if c in pos else np.nan)
    return df
