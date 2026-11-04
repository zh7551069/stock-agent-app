#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连板候选 · 竞价先手重排（2026-10-12 建 · 2026-10-12 v2 加「板块层」）

用途：把「连板候选漏斗」(liupan_scan) 的**全带内候选池**，在 09:25 集合竞价结束后，
      用**先手指标**（竞价高开% ＋ 竞价换手%）＋**板块共振**（申万行业）重排
      → 输出「今日盯盘首选」。

为什么建它（教训）：
  先手谱系铁律 = 竞价 > 开盘急杀 > 分时钩头 > 价格动量2-3日 > 主力资金 > 均线。
  旧「盯盘排序」只用慢变量（板块共振/主力资金/画像分/未透支），把 2026-10-12 早盘
  竞价最强的那只（果麦文化 301052）压到后面；而当天唯一封板(20%)的正是它。

🔴 v2 为什么加「板块层」（2026-10-12 追加教训）：
  当天 果麦文化 所在**申万行业「出版」= 全行业涨跌幅第 1（+3.99%）**，且**板块内集体涨停**
  （新华文轩 +10% / 新经典 +9.98% / 果麦 +20%）→ 真·板块共振，是它涨停的**前置引力**。
  而 v1 排序器**完全没有板块维度** → 选不出它。
  ⚠️ 口径铁律：板块**只用「申万行业」**（与辉哥看的「新浪资金热力图·行业」同源），
     **禁**用证监会行业/概念/主力净额（净额口径曾把两者都带偏：当日净额第1是计算机应用）。

🔴 铁律：
  - 先用**竞价**（先手），不用慢变量决定「盯谁」；
  - 板块层只作**共振加成**，**不用「主力净额排名」当钥匙**；
  - 🔴 **可买性闸门**：涨幅 ≥ 涨停价−0.3% 的候选=**已涨停·买不进** → **移出可买榜**、单列「🚫 已涨停·仅观察」
    （血证：10/12 09:42 跑，金冠电气已封 +20%，若当「首选」推 = 推荐了买不到的票）；
  - 🔴 **时点守卫**：竞价窗=**09:15-09:32**；>09:32 → 告警且标签改为「涨幅」（防止把盘中涨幅当「高开」误读）；
  - 输出 = **盯盘优先级**，**非买入信号**（追涨停/买入须逐只过 advice_gate --pair）；
  - 判据同源：候选池取自 `liupan_scan.py --json` 的 `pool`（**禁自拼阈值/自选样本**）。

用法：
  python3 scripts/liupan_auction_rank.py [--tier 锐利|实用] [--date YYYY-MM-DD] [--asof HH:MM|now] [--json] [--selftest]

  🔴 语义开关 `--asof HH:MM|now`：
     · ≤09:32 → **竞价先手版**（字段=高开%/竞价换手%）
     · >09:32 → **盘中动量版**（字段=即时涨幅/当前换手%·显式声明则不告警）
     · `now` → 取当前时间并显式声明（**盘中说「盘中动量」直接跑这条·免算 HH:MM**）
     · 不传 → 按当前时间自动判（窗外仍打漂移告警，防误用）
退出码：0=正常；3=无数据（外置盘未挂载 / 非竞价时段无快照）。
"""
import os, sys, json, argparse, subprocess, datetime as dt, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# 🔴 09:26 盘中要拉 ~2100 只【板块成分】→ 远超 qq_snap 盘中闸门(>200)
#    （闸门 2026-10-10 建、本脚本板块层 10-12 建 → 未开豁免会在 09:26 直接 raise→崩）
#    → 显式声明确需盘中取数（qq_snap 文档指定的豁免入口）
os.environ.setdefault("QQ_SNAP_ALLOW_INTRADAY", "1")
DB = '/Volumes/My PSSD/本地数据库_v2版（淘宝购买）/duckdb/taobao_minute.duckdb'

# ── 先手分权重（v2：竞价两项 0.75 ／ 板块共振 0.25）──
W_OPEN = 0.45   # 高开% 权重（先手方向）
W_TURN = 0.30   # 竞价换手% 权重（先手活跃度）
W_SECT = 0.25   # 板块共振权重（申万行业）
# ── 板块强度内部权重 ──
SECT_MED_W = 0.6   # 板块涨跌幅中位
SECT_ZT_W = 0.4    # 板块内涨停家数密度

# ────── 🔴🔴 绝对刻度锚点（2026-10-31 改 · 池内 min-max → 固定锚点）──────
# 为什么改：池内 min-max 的分母每天不同 → 跨日不可比（血证 10-30：全池低开时
#   大千生态 高开 −0.04% 也拿 0.98，读出「极强」；10-29 龙江 0.703 其实更强）。
# 锚点来源：auction_rank_backtest 23,680 票次 · 2024-01~2026-10 · P5~P95
#   （复算脚本见 memory/竞价先手分回测否证-20261019.md 同源口径）
# 🔴 维护：每 6 个月用最近 3 年数据重算一次；改锚点须记档，否则历史不可比。
A_OPEN = (-2.39, 1.93)    # 高开% ／ 盘中涨幅%
A_TURN = (0.0, 0.25)      # 竞价换手% ／ 当前换手%
A_SECT = (0.143, 0.267)   # 板块原始强度 sect_raw

# 绝对水位标签（跨日可比）
LV_STRONG = 0.60   # ≥ → 强先手
LV_WEAK = 0.45     # < → 弱（今日无先手票）

# 整池水位门（治「相对最强 = 伪第一」）
GATE_MAX_OPEN = 0.0    # 全池最高高开 < 此值 → 整池低开·无先手票（硬门）
GATE_MED_OPEN = -1.0   # 全池高开中位 < 此值 → 整池弱势·只列不荐

# ── 惩罚扣分（🔴 2026-10-31 重标定）──
# 旧值 100 / 2.0 / 1.0 是按「池内归一 0~1 分」拍脑袋定的：一旦惩罚层真的取到数，
# 任何 2 板票直接 −3 分 → 全榜负数。改绝对刻度后按同一「沉底 / 降权」语义重标：
PEN_ZB = 0.30      # 昨炸板 → 沉底（高于单票正常分差）
PEN_ZT10 = 0.06    # 近10日每多 1 板
PEN_ZT20 = 0.03    # 近20日每多 1 板

# 盘中模式换手量纲不同（竞价 0~0.25% vs 盘中 0~15%）→ 另给一套锚点
A_TURN_ID = (0.0, 15.0)

WIN_LO, WIN_HI = 9 * 60 + 15, 9 * 60 + 32   # 竞价有效时段 09:15-09:32（09:25 竞价结束＋开盘前段）


def _pool(tier="锐利", date=None, live=False, asof=None, codes=None):
    """候选池来源（判据同源·禁自选）：
      · 默认 = `liupan_scan --json` 全带内候选（盘前 T-1 池）
      · `live=True` = `liupan_live_scan --json` 全市场实时池（本地T-1 ＋ 当天实时·双通道）
        —— 治「当天新热点（如军工）进不了盘前池」的缺陷（2026-10-16）。
      · `codes=[...]` = **直连指定票池，跳过任何子进程扫描**（2026-11-04 加）
        —— 前端已把候选池缓存在 localStorage，盘中动量复用它，只实时算先手分。
        省掉一次全市场扫描（≈1.5s 起，含 4995 只票的因子窗口），
        且保证「动量排序的池子」与「用户看到的候选池」**完全同一批票**，不会串。"""
    if codes:
        # 池名/日期由调用方给定（前端传缓存的 asof），不重扫、不自选
        cs = [str(c).strip() for c in codes if str(c).strip()]
        print(f"[_pool] 使用外部指定票池 {len(cs)} 只（跳过 liupan_scan）",
              file=sys.stderr)
        return {"status": "ok", "asof": (date or dt.date.today().isoformat()),
                "pool": {tier: cs}, "meta": {}, "_external": True, "n_pool": len(cs)}, ""
    if live:
        cmd = [sys.executable, os.path.join(HERE, "liupan_live_scan.py"),
               "--tier", tier, "--json", "--asof", str(asof or "now")]
        src = "liupan_live_scan"
    else:
        cmd = [sys.executable, os.path.join(HERE, "liupan_scan.py"), "--tier", tier, "--json"]
        if date:
            cmd += ["--date", date]
        src = "liupan_scan"
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=240, cwd=ROOT)
    s = p.stdout or ""
    try:
        j = json.loads(s[s.index("{"): s.rindex("}") + 1])
    except Exception:
        return None, f"{src} 未返回 JSON（rc={p.returncode}）"
    if live:
        pool = j.get("pool") or []
        codes = [d.get("code") for d in pool if d.get("code")]
        meta = {d["code"]: d for d in pool if d.get("code")}
        return {"status": j.get("status"), "asof": j.get("asof"), "_live": True,
                "n_pool": len(codes), "pool": {tier: codes}, "meta": meta}, ""
    return j, ""


def _secid(suffix):
    c = str(suffix)
    if c.endswith(".SH"):
        return "sh" + c[:6]
    if c.endswith(".SZ"):
        return "sz" + c[:6]
    if c.endswith(".BJ"):
        return "bj" + c[:6]
    return None


def _snap(secids):
    """竞价/实时快照（薄封装·selftest 可替换）。"""
    from qq_snap import snapshot
    return snapshot(list(secids))


def _nz(x, lo, hi):
    """线性归一到 [0,1]（**固定锚点**用）· 超界截断。"""
    if hi <= lo or x is None:
        return 0.5
    return max(0.0, min(1.0, (float(x) - lo) / (hi - lo)))


def _hist_flags(pool_asof, codes):
    """从本地库补算「昨日炸板 + 近5/10/20日涨停次数」。

    🔴 为什么需要：`liupan_scan --json` 只返回代码列表，**不返回 zb/zt5/zt10/zt20**
      → 09:26 盘前路径上 `_pen` 恒为 0，惩罚层形同虚设（血证 2026-10-30
      调试打印 zb=None zt5=None zt20=None 全空）。
      live 路径（liupan_live_scan）自带这些字段，故本函数只补**缺失项**。
    失败（外置盘未挂载/表缺失）→ 返回 {}，不影响主流程（fail-soft）。
    """
    out = {}
    if not pool_asof or not codes:
        return out
    try:
        import duckdb
        pref = {}
        for c in codes:
            s = str(c)
            if s.endswith(".SH"):
                pref[c] = "sh" + s[:6]
            elif s.endswith(".SZ"):
                pref[c] = "sz" + s[:6]
            elif s.endswith(".BJ"):
                pref[c] = "bj" + s[:6]
        if not pref:
            return out
        d0 = str(pool_asof).replace("-", "")
        d0 = f"{d0[:4]}-{d0[4:6]}-{d0[6:8]}"
        con = duckdb.connect(DB, read_only=True)
        try:
            ph = ",".join("?" * len(set(pref.values())))
            q = f"""SELECT d.code, d.date, d.high, d.close, l.limit_up
                    FROM daily d
                    LEFT JOIN limit_price l ON l.code = d.code AND l.date = d.date
                    WHERE d.code IN ({ph}) AND d.date <= ?
                    ORDER BY d.code, d.date"""
            rows = con.execute(q, list(set(pref.values())) + [d0]).fetchall()
        finally:
            con.close()
        agg = {}
        for code, _d, hi, cl, lu in rows:
            agg.setdefault(code, []).append((hi, cl, lu))
        for suf, pfx in pref.items():
            hist = agg.get(pfx, [])[-20:]
            if not hist:
                continue
            def _iszt(t):
                hi, cl, lu = t
                return lu is not None and cl is not None and cl >= float(lu) * 0.999
            flags = [1 if _iszt(t) else 0 for t in hist]
            last = hist[-1]
            zb = bool(last[2] is not None and last[0] is not None
                      and float(last[0]) >= float(last[2]) * 0.999
                      and float(last[1] or 0) < float(last[2]) * 0.999)
            out[suf] = {"zb": zb,
                        "zt5": sum(flags[-5:]), "zt10": sum(flags[-10:]),
                        "zt20": sum(flags[-20:])}
    except Exception:
        return out
    return out


# ── 板块一致性（方案B·2026-10-19·只做可见性，不改任何分数/排序）──
CONSIST_ZT_BREAK = 3   # 板块中位为负时，涨停家数≥此值 → 判「背离」
def _consist(med, zt):
    """板块一致性标签：同向 / 背离 / — 。
    🔴 纯标注，不参与打分。背离 = 板块整体在跌但个别票涨停（个股行情，非板块共振）。"""
    if not isinstance(med, (int, float)) or zt is None:
        return "–"
    if med > 0 and zt >= 1:
        return "同向"
    if med < 0 and zt >= CONSIST_ZT_BREAK:
        return "背离"
    return "—"


# ───────────────────────── 板块层（申万行业·判据同源） ─────────────────────────
def _industry(codes6):
    """code6 → 申万行业名（stock_info.industry）。DB 未挂载 → {}（板块层降级为中性）。"""
    if not os.path.exists(DB):
        return {}
    import duckdb
    con = duckdb.connect(DB, read_only=True)
    try:
        qs = "','".join(str(c)[:6] for c in codes6)
        rows = con.execute(
            f"select code, industry from stock_info where code in ('{qs}')").fetchall()
        return {str(r[0]): str(r[1]) for r in rows}
    finally:
        con.close()


def _sect_members(inds, per_max=400):
    """行业名集合 → {行业: [secid,...]}（只取候选池涉及行业·沪深主板/创业/科创）。"""
    out = {i: [] for i in inds}
    if not os.path.exists(DB) or not inds:
        return out
    import duckdb
    con = duckdb.connect(DB, read_only=True)
    try:
        qs = "','".join(str(i) for i in inds)
        rows = con.execute(
            f"""select ts_code, industry from stock_info
                where industry in ('{qs}') and market in ('主板','创业板','科创板')""").fetchall()
        for ts, ind in rows:
            s = _secid(ts)
            if s and len(out.get(str(ind), [])) < per_max:
                out[str(ind)].append(s)
    finally:
        con.close()
    return out


def _lim_pct(secid):
    """secid → 涨跌幅上限（%）：科创688/689·创业30=20，北交所=30，其余=10。"""
    s = str(secid)
    num = s[2:8]
    if s.startswith("sh") and num.startswith("68"):
        return 20.0
    if s.startswith("sz") and num.startswith("30"):
        return 20.0
    if s.startswith("bj"):
        return 30.0
    return 10.0


def _sect_stats(members, snap):
    """板块强度：{行业: {med, zt, n, zt_density, leader, lead_pct}}。"""
    res = {}
    for ind, secs in (members or {}).items():
        pts, zt, best, ztn = [], 0, (-999.0, ""), []
        for s in secs:
            r = snap.get(s) or {}
            p = r.get("pct")
            if not isinstance(p, (int, float)):
                continue
            pts.append(float(p))
            if p >= _lim_pct(s) - 0.3:
                zt += 1
                ztn.append(r.get("name") or s[2:8])
            if p > best[0]:
                best = (float(p), r.get("name") or "")
        if pts:
            res[ind] = {"med": statistics.median(pts), "zt": zt, "n": len(pts),
                        "zt_density": zt / len(pts), "leader": best[1], "lead_pct": best[0],
                        "zt_names": ztn}
    return res


def rank(tier="锐利", date=None, now=None, asof=None, live=False, codes=None):
    j, err = _pool(tier, date, live, asof, codes)
    if err:
        return {"status": "no_data", "reason": err}
    if j.get("status") != "ok":
        return {"status": j.get("status", "no_data"), "reason": j.get("reason", "无数据")}
    raw_pool = (j.get("pool") or {}).get(tier) or []
    meta = j.get("meta") or {}
    codes = [(c, _secid(c)) for c in raw_pool]
    codes = [(c, s) for c, s in codes if s]
    if not codes:
        return {"status": "ok", "asof": j.get("asof"), "n": 0, "text": ""}

    pool_asof = j.get("asof")
    snap = _snap([s for _, s in codes])
    # 🔴 惩罚层取数：盘前池无 meta → 用本地库补（T-1 炸板 / 近N日涨停次数）
    hist = _hist_flags(pool_asof, [c for c, _ in codes])
    rows = []
    for c, s in codes:
        r = snap.get(s) or {}
        pre, px = r.get("pre"), r.get("price")
        if not pre or not px:
            continue
        row = {"code": c, "name": r.get("name") or c,
               "open_pct": (px / pre - 1) * 100,
               "turn": r.get("turnover") or 0.0,
               "pct": r.get("pct"), "px": px, "pre": pre, "lim": _lim_pct(s)}
        row.update({k: v for k, v in (meta.get(c) or {}).items() if v is not None})
        # hist 只补**仍然缺失**的项（live 自带真实时值优先）
        row.update({k: v for k, v in (hist.get(c) or {}).items() if row.get(k) is None})
        rows.append(row)
    if not rows:
        return {"status": "no_data", "reason": "竞价快照为空（非竞价时段 / 数据源不可用）"}

    # ── 板块层：候选所属申万行业 → 该行业成分快照 → 板块强度 ──
    ind_of = _industry([r["code"] for r in rows])
    inds = sorted({v for v in ind_of.values() if v and v != "None"})
    sect = {}
    if inds:
        members = _sect_members(inds)
        secids = sorted({s for v in members.values() for s in v})
        if secids:
            ssnap = _snap(secids)
            sect = _sect_stats(members, ssnap)
    for r in rows:
        r["ind"] = ind_of.get(r["code"][:6]) or "—"
        st = sect.get(r["ind"]) or {}
        r["sect_med"] = st.get("med")
        r["sect_zt"] = st.get("zt")
        r["sect_n"] = st.get("n")
        r["sect_leader"] = st.get("leader")
        # 板块原始强度（无数据 → 中性 0.5）
        if st:
            r["sect_raw"] = SECT_MED_W * _nz(st["med"], -3, 6) + SECT_ZT_W * _nz(st["zt_density"], 0, 0.15)
        else:
            r["sect_raw"] = None
        r["consist"] = _consist(r.get("sect_med"), r.get("sect_zt"))

    # ── 时点语义先定：换手锚点随模式切换，必须在算分之前 ──
    if now is None:
        now = dt.datetime.now()
    if asof is not None:                       # 显式时点：语义由调用方声明
        if str(asof).lower() in ("now", "auto", "current"):
            _t = now.time()
        else:
            _t = asof if isinstance(asof, dt.time) else dt.datetime.strptime(str(asof), "%H:%M").time()
        now = now.replace(hour=_t.hour, minute=_t.minute, second=0, microsecond=0)
        explicit = True
    else:
        explicit = False
    hm = now.hour * 60 + now.minute
    if WIN_LO <= hm <= WIN_HI:                 # 竞价窗 → 竞价先手版
        mode, oh_lab, turn_col = "auction", "高开", "竞换"
        _pos_lab, _turn_lab = "先手最强", "竞价换手"
        warn = ""
    else:                                      # 窗外 → 盘中动量版（显式声明则不告警）
        mode, oh_lab, turn_col = "intraday", "涨幅", "换手"
        _pos_lab, _turn_lab = "当前最强", "当前换手"
        warn = "" if explicit else \
            "⚠️ 非竞价时段（09:15-09:32）运行：「高开%」实为实时涨幅，可能选出已涨停票 → 仅供盯盘参考"
    _turn_anchor = A_TURN if mode == "auction" else A_TURN_ID

    # ── 合成先手分（🔴 绝对刻度·固定锚点 → 池内可排、跨日可比）──
    # 旧口径 min-max 池内归一已废止（分母每日不同 → 0.98 与 0.70 不可比）
    for r in rows:
        sn = _nz(r["sect_raw"], *A_SECT) if r["sect_raw"] is not None else 0.5
        r["sect_score"] = sn
        r["score"] = (W_OPEN * _nz(r["open_pct"], *A_OPEN)
                      + W_TURN * _nz(r["turn"], *_turn_anchor)
                      + W_SECT * sn)

    # ── 整池水位（治「全池低开 → 相对最强拿满分 = 伪第一」）──
    _opens = [r["open_pct"] for r in rows]
    level = {"max_open": max(_opens), "med_open": statistics.median(_opens),
             "weak_pool": max(_opens) < GATE_MAX_OPEN,
             "soft_weak": statistics.median(_opens) < GATE_MED_OPEN}
    for r in rows:
        r["zt"] = r["open_pct"] >= r["lim"] - 0.3   # 已涨停 → 买不进
        r["room"] = r["lim"] - r["open_pct"]        # 距涨停空间%
    # 🔴 近期涨停/炸板惩罚（治「炸板了评分依然高」）：炸板沉底 + 连板后回落再冲降权
    for r in rows:
        _pen = (PEN_ZB if r.get("zb") else 0.0) + max(0, (r.get("zt10") or 0) - 1) * PEN_ZT10 \
            + max(0, (r.get("zt20") or 0) - 1) * PEN_ZT20
        r["pen"] = _pen
        r["score_adj"] = r["score"] - _pen
    buy = [r for r in rows if not r["zt"]]           # 可买榜（剔已涨停）
    lock = [r for r in rows if r["zt"]]              # 已涨停·仅观察
    buy.sort(key=lambda r: r["score_adj"], reverse=True)
    lock.sort(key=lambda r: r["score_adj"], reverse=True)

    pool_asof = j.get("asof") or pool_asof
    _src = "🟢实时池" if live else "盘前池"
    head = (f"🎯 竞价先手重排 v2 · {_src} 候选池 {len(rows)} 只 · 池 as-of {pool_asof}" if mode == "auction"
            else f"📊 盘中动量重排（非竞价先手）· {_src} 候选池 {len(rows)} 只 · 池 as-of {pool_asof}")
    L = [head]
    if warn:
        L.append(warn)
    if mode == "intraday" and explicit:
        L.append("ℹ️ 显式盘中时点：字段=即时涨幅/当前换手 → **非竞价先手**，勿与 09:26 竞价结果混看")
    _lo = "高开%" if mode == "auction" else "涨幅%"
    _lt = "竞价换手%" if mode == "auction" else "当前换手%"
    L.append(f"   先手分 = {W_OPEN:.2f}×{_lo} + {W_TURN:.2f}×{_lt} + {W_SECT:.2f}×板块共振"
             f"（🔴 绝对刻度·固定锚点 高开{A_OPEN[0]:g}~{A_OPEN[1]:g}% / 换手0~{A_TURN[1]:g}% → **跨日可比**）")
    L.append(f"   水位（按{_lo}）：≥{LV_STRONG:.2f} 强先手 ｜ {LV_WEAK:.2f}~{LV_STRONG:.2f} 中性 ｜ <{LV_WEAK:.2f} 弱（今日无先手票）")
    if level["weak_pool"]:
        L.append(f"   🔴🔴 整池低开：全池最高高开 {level['max_open']:+.2f}%（<{GATE_MAX_OPEN:g}%）"
                 f"｜池中位 {level['med_open']:+.2f}% → **今日无先手票**，下列排序只作「谁最不跌」参考，"
                 f"不得当首选/买入依据")
    elif level["soft_weak"]:
        L.append(f"   ⚠️ 整池弱势：高开中位 {level['med_open']:+.2f}%（<{GATE_MED_OPEN:g}%）→ 分数普遍偏低属正常，只列不荐")
    L.append(f"   🔴 已扣「近期涨停/炸板」惩罚：炸板 −{PEN_ZB:.2f}｜近10日每多1板 −{PEN_ZT10:.2f}｜"
             f"近20日每多1板 −{PEN_ZT20:.2f}（T-1 数据来自本地库；旧版盘前路径取不到数=惩罚失效，2026-10-31 修）")
    L.append("   板块一致：同向=板块中位>0且有涨停 ｜ 背离=板块中位<0但涨停≥3（个股行情·非板块共振）｜— 中性")
    # 🌅 板块竞价异动（竞价涨停/一字开盘家数>0）—— 一眼可见的「板块先手」信号
    hot = sorted([(i, st) for i, st in sect.items() if st.get("zt", 0) > 0],
                 key=lambda kv: (-kv[1]["zt"], -(kv[1]["med"] or 0)))
    if hot:
        seg = " ｜ ".join(f"{i} {st['zt']}/{st['n']}（{'/'.join(st.get('zt_names', [])[:4])}）"
                          for i, st in hot[:5])
        L.append(f"   🌅 板块竞价异动：{seg}")
    else:
        L.append("   🌅 板块竞价异动：无（竞价无板块级一字/涨停开盘）")
    # ── 可买榜（已剔除「已涨停·买不进」）──
    L.append(f"   ✅ 可买候选（已剔已涨停{len(lock)} 只→另列）")
    L.append(f"   {'#':>2} {'代码':<10}{'名称':<8}{oh_lab+'%':>7}{'距涨停':>7}{(turn_col+'%'):>7}{'板块':<8}{'板涨%':>7}{'板涨停':>6}{'板一致':>8}{'板':>3}{'先手分':>8}  标记")
    if buy:
        for i, r in enumerate(buy[:8], 1):
            ld = "🐉" if (r.get("sect_leader") and r.get("sect_leader") == r["name"]) else ""
            med = f"{r['sect_med']:>7.2f}" if isinstance(r.get("sect_med"), (int, float)) else "      –"
            zt = f"{r['sect_zt']:>6}" if r.get("sect_zt") is not None else "     –"
            _sc = r["score_adj"] if r.get("pen") else r["score"]
            tg = []
            if r.get("zb"):
                tg.append(f"❗炸板(回落{-(r['offhigh']):.1f}%)" if isinstance(r.get("offhigh"), (int, float))
                          else "❗昨炸板")
            if (r.get("zt20") or 0) >= 2:
                tg.append(f"20日{r['zt20']}板")
            elif (r.get("zt10") or 0) >= 2:
                tg.append(f"10日{r['zt10']}板")
            elif (r.get("zt5") or 0) >= 1:
                tg.append(f"5日{r['zt5']}板")
            if r.get("t1zt"):
                tg.append("昨涨停")
            L.append(f"   {i:>2} {r['code']:<10}{str(r['name'])[:6]:<8}{r['open_pct']:>7.2f}"
                     f"{r['room']:>7.2f}{r['turn']:>7.2f}{str(r['ind'])[:6]:<8}{med}{zt}"
                     f"{str(r.get('consist') or '–'):^8}{ld:>3}{_sc:>8.3f}  " + "/".join(tg))
    else:
        L.append("   （无可买候选）")
    if lock:
        L.append("   🚫 已涨停·买不进（仅观察）：" + " ｜ ".join(
            f"{r['name']} {r['code']}({r['open_pct']:+.2f}%)" for r in lock[:5]))
    top = buy[0] if (buy and not level["weak_pool"]) else None
    L.append("")
    if level["weak_pool"]:
        L.append(f"▶ 🔴 水位门未过（全池高开无一为正）→ **不出首选**。"
                 f"池内相对第一：{buy[0]['name']} {buy[0]['code']} 先手分 {buy[0]['score_adj']:.3f}"
                 f"（<{LV_WEAK:.2f} 属弱）—— 这是「跌得最少」，不是「有人接」" if buy
                 else "▶ 🔴 水位门未过（全池低开）→ 无候选")
    elif top:
        L.append(f"▶ 可买首选（{_pos_lab}·未涨停）：{top['name']} {top['code']}｜{oh_lab} {top['open_pct']:+.2f}%"
                 f"｜距涨停 {top['room']:+.2f}%｜{_turn_lab} {top['turn']:.2f}%｜板块「{top['ind']}」"
                 f"｜先手分 {top['score_adj']:.3f}"
                 + ("（弱）" if top["score_adj"] < LV_WEAK else
                    ("（中性）" if top["score_adj"] < LV_STRONG else "（强）"))
                 + (f"（板块涨跌中位 {top['sect_med']:+.2f}%，板内涨停 {top['sect_zt']} 家）"
                    if isinstance(top.get("sect_med"), (int, float)) else ""))
        if top.get("consist") == "背离" and isinstance(top.get("sect_med"), (int, float)):
            L.append(f"   ⚠️ 但该首选**板块背离**：板块中位 {top['sect_med']:+.2f}%，仅 {top['sect_zt']} 家涨停 "
                     f"→ 个股行情非板块共振，勿当板块合力看")
        if top.get("zb"):
            L.append("   ⚠️ 但该首选**上一交易日炸板**（触板后回落）→ 弱化信号，勿追"
                     + (f"（自当日高点回落 {-(top['offhigh']):.1f}%）" if isinstance(top.get("offhigh"), (int, float)) else ""))
        if (top.get("zt10") or 0) >= 2:
            L.append(f"   ⚠️ 该首选**近10日已 {top['zt10']} 次涨停**（高位博弈）→ 追高风险高")
    else:
        L.append("▶ 无「未涨停」候选 → 本次无可执行首选（勿追已涨停）")
    L.append("🔴 先手 = 盯盘优先级，**非买入信号**；且**已剔除已涨停票**（买不进）；追/买须逐只过 `advice_gate --pair`。")

    return {"status": "ok", "asof": pool_asof, "mode": mode, "n": len(rows), "text": "\n".join(L),
            "hot": [{"ind": i, "zt": st["zt"], "n": st["n"], "names": st.get("zt_names", [])}
                    for i, st in hot],
            "buy": [{k: r.get(k) for k in ("code", "name", "open_pct", "room", "turn", "ind",
                                           "sect_med", "sect_zt", "consist", "score", "score_adj", "zb",
                                           "offhigh", "zt5", "zt10", "zt20", "t1zt")} for r in buy],
            "locked": [{k: r.get(k) for k in ("code", "name", "open_pct", "turn", "ind", "score")}
                       for r in lock],
            "level": level}


def selftest():
    """故障注入：不联网、不读库，替换 _pool/_snap/_industry/_sect_members，验排序/板块/静默/告警。"""
    global _pool, _snap, _industry, _sect_members, _hist_flags
    real = (_pool, _snap, _industry, _sect_members, _hist_flags)
    _hist_flags = lambda pool_asof, codes: {}      # selftest 不读库（真实实现另有单测）
    ok = []
    try:
        # 1) 排序：高开+换手双高者夺冠（模拟 2026-10-12）
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (
            {"status": "ok", "asof": "2026-10-09",
             "pool": {"锐利": ["002178.SZ", "301052.SZ", "600592.SH"]}}, "")
        _industry = lambda codes6: {"002178": "软件服务", "301052": "出版业", "600592": "机械基件"}
        _sect_members = lambda inds, per_max=400: {"软件服务": ["sz002178"], "出版业": ["sz301052", "sh601811"],
                                                   "机械基件": ["sh600592"]}
        _snap = lambda secids: {
            "sz002178": {"name": "延华智能", "pre": 5.06, "price": 5.05, "turnover": 0.04, "pct": -0.2},
            "sz301052": {"name": "果麦文化", "pre": 25.39, "price": 25.69, "turnover": 0.12, "pct": 20.0},
            "sh600592": {"name": "龙溪股份", "pre": 15.32, "price": 15.24, "turnover": 0.04, "pct": -0.5},
            "sh601811": {"name": "新华文轩", "pre": 20.91, "price": 23.0, "turnover": 0.05, "pct": 10.0}}
        r = rank(now=dt.datetime(2026, 10, 12, 9, 26))
        ok.append(("首选=果麦", "果麦文化" in r["text"].split("首选")[1][:40]))
        ok.append(("池数=3", r["n"] == 3))
        ok.append(("板块列在输出", "出版业" in r["text"]))
        ok.append(("🌅板块竞价异动行", "🌅 板块竞价异动：" in r["text"]
                   and "出版业" in r["text"].split("板块竞价异动")[1][:60]))

        # 2) 板块共振能翻盘：先手并列时，板块更强者胜出
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (
            {"status": "ok", "asof": "2026-10-09", "pool": {"锐利": ["000001.SZ", "000002.SZ"]}}, "")
        _industry = lambda codes6: {"000001": "弱行业", "000002": "强行业"}
        _sect_members = lambda inds, per_max=400: {"弱行业": ["sz000001"], "强行业": ["sz000002", "sz000003"]}
        _snap = lambda secids: {
            "sz000001": {"name": "甲", "pre": 10.0, "price": 10.5, "turnover": 0.08, "pct": 0.0},
            "sz000002": {"name": "乙", "pre": 10.0, "price": 10.5, "turnover": 0.08, "pct": 5.0},
            "sz000003": {"name": "丙", "pre": 10.0, "price": 11.0, "turnover": 0.05, "pct": 10.0}}
        r = rank(now=dt.datetime(2026, 10, 12, 9, 26))
        ok.append(("共振强板块胜出", "首选" in r["text"] and "乙" in r["text"].split("首选")[1][:20]))

        # 3) 池空 → 静默
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: ({"status": "ok", "asof": "2026-10-09", "pool": {"锐利": []}}, "")
        ok.append(("空池→静默", rank()["text"] == ""))

        # 4) 快照全空 → no_data
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (
            {"status": "ok", "asof": "2026-10-09", "pool": {"锐利": ["002178.SZ"]}}, "")
        _snap = lambda secids: {}
        ok.append(("快照空→no_data", rank()["status"] == "no_data"))

        # 5) liupan_scan 非 JSON → 告警
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (None, "liupan_scan 未返回 JSON（rc=1）")
        ok.append(("非JSON→告警", rank()["status"] == "no_data"))

        # 6) DB 缺失 → 板块层降级为中性（不得崩）
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (
            {"status": "ok", "asof": "2026-10-09", "pool": {"锐利": ["002178.SZ", "301052.SZ"]}}, "")
        _industry = lambda codes6: {}
        _sect_members = lambda inds, per_max=400: {}
        _snap = lambda secids: {
            "sz002178": {"name": "延华智能", "pre": 5.06, "price": 5.05, "turnover": 0.04, "pct": -0.2},
            "sz301052": {"name": "果麦文化", "pre": 25.39, "price": 25.69, "turnover": 0.12, "pct": 20.0}}
        r = rank(now=dt.datetime(2026, 10, 12, 9, 26))
        ok.append(("无板块数据仍出结果", r["status"] == "ok" and "果麦文化" in r["text"]))

        # 7) 非竞价时段 → 告警行
        ok.append(("非竞价时段告警", "非竞价时段" in rank(now=dt.datetime(2026, 10, 12, 14, 0))["text"]))

        # 8) code→secid 映射
        ok.append(("secid", _secid("002178.SZ") == "sz002178" and _secid("600592.SH") == "sh600592"
                   and _secid("688651.SH") == "sh688651" and _secid("n/a") is None))

        # 9) 竞价无任何板块一字 → 异动行须显式写「无」
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (
            {"status": "ok", "asof": "2026-10-09", "pool": {"锐利": ["002178.SZ"]}}, "")
        _industry = lambda codes6: {"002178": "软件服务"}
        _sect_members = lambda inds, per_max=400: {"软件服务": ["sz002178"]}
        _snap = lambda secids: {"sz002178": {"name": "延华智能", "pre": 5.06, "price": 5.07,
                                              "turnover": 0.04, "pct": 0.2}}
        r = rank(now=dt.datetime(2026, 10, 12, 9, 26))
        ok.append(("无异动→写「无」", "🌅 板块竞价异动：无" in r["text"] and r["hot"] == []))

        # 10) 已涨停票（金冠 688 科创 20%）→ 必须移出可买榜、落入 locked
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (
            {"status": "ok", "asof": "2026-10-09", "pool": {"锐利": ["688517.SH", "301052.SZ"]}}, "")
        _industry = lambda codes6: {"688517": "电气设备", "301052": "出版业"}
        _sect_members = lambda inds, per_max=400: {"电气设备": ["sh688517"], "出版业": ["sz301052"]}
        _snap = lambda secids: {
            "sh688517": {"name": "金冠电气", "pre": 10.0, "price": 12.0, "turnover": 0.10, "pct": 20.0},
            "sz301052": {"name": "果麦文化", "pre": 25.39, "price": 26.61, "turnover": 0.077, "pct": 4.81}}
        r = rank(now=dt.datetime(2026, 10, 12, 9, 42))
        ok.append(("已涨停移出可买榜", r["buy"] and r["buy"][0]["code"] == "301052.SZ"
                   and r["locked"] and r["locked"][0]["code"] == "688517.SH"))
        ok.append(("🚫已涨停行在输出", "🚫 已涨停·买不进" in r["text"] and "金冠电气" in r["text"].split("买不进")[1][:30]))
        ok.append(("距涨停列在输出", "距涨停" in r["text"]))
        ok.append(("09:42→漂移告警", "非竞价时段" in r["text"] and "涨幅" in r["text"]))

        # 11) --asof 语义开关：≤09:32=竞价版；>09:32=盘中动量版（显式→不告警）
        ra = rank(asof="09:26")
        ok.append(("asof09:26→竞价版", ra["mode"] == "auction" and "竞价先手重排" in ra["text"]))
        rb = rank(asof="10:30")
        ok.append(("asof10:30→盘中动量版·不告警", rb["mode"] == "intraday"
                   and "盘中动量重排" in rb["text"] and "非竞价先手" in rb["text"]
                   and "非竞价时段" not in rb["text"]))
        rn = rank(asof="now")
        ok.append(("asof=now→不崩·模式合法", rn.get("status") == "ok" and rn.get("mode") in ("auction", "intraday")))

        # 12) 🔴 绝对刻度：同一指标跨池可比；池内最强者不再恒为 1.0
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (
            {"status": "ok", "asof": "2026-10-09", "pool": {"锐利": ["600001.SH", "600002.SH"]}}, "")
        _industry = lambda codes6: {"600001": "甲行业", "600002": "甲行业"}
        _sect_members = lambda inds, per_max=400: {"甲行业": ["sh600001", "sh600002"]}
        _snap = lambda secids: {
            "sh600001": {"name": "弱开", "pre": 10.0, "price": 10.1, "turnover": 0.10, "pct": 1.0},
            "sh600002": {"name": "强开", "pre": 10.0, "price": 10.5, "turnover": 0.10, "pct": 5.0}}
        r = rank(now=dt.datetime(2026, 10, 12, 9, 26))
        sc = {x["name"]: x["score"] for x in r["buy"]}
        ok.append(("绝对刻度：强开>弱开", sc["强开"] > sc["弱开"]))
        ok.append(("绝对刻度：非池内满分", sc["强开"] < 0.999))

        # 13) 🔴 水位门：全池低开 → 判「无先手票」且不出首选（血证 2026-10-30 大千生态 0.98）
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (
            {"status": "ok", "asof": "2026-10-30",
             "pool": {"锐利": ["603955.SH", "002307.SZ", "300115.SZ"]}}, "")
        _industry = lambda codes6: {"603955": "建筑工程", "002307": "建筑工程", "300115": "元器件"}
        _sect_members = lambda inds, per_max=400: {"建筑工程": ["sh603955", "sz002307"], "元器件": ["sz300115"]}
        _snap = lambda secids: {
            "sh603955": {"name": "大千生态", "pre": 26.53, "price": 26.52, "turnover": 0.13, "pct": -0.04},
            "sz002307": {"name": "北新路桥", "pre": 5.0, "price": 4.908, "turnover": 0.07, "pct": -1.82},
            "sz300115": {"name": "长盈精密", "pre": 20.0, "price": 19.307, "turnover": 0.05, "pct": -3.46}}
        r = rank(now=dt.datetime(2026, 10, 30, 9, 26))
        ok.append(("水位门：整池低开→无先手票", "整池低开" in r["text"] and "今日无先手票" in r["text"]))
        ok.append(("水位门：不出首选", "可买首选" not in r["text"]))
        ok.append(("水位门：JSON level 标记", r["level"]["weak_pool"] is True))
        ok.append(("水位门：分数不再被拉满", max(x["score"] for x in r["buy"]) < 0.6))

        # 14) 🔴 惩罚层：hist 补到「昨炸板」→ 分数沉底（旧版盘前路径此层恒 0）
        _pool = lambda tier="锐利", date=None, live=False, asof=None, codes=None: (
            {"status": "ok", "asof": "2026-10-09", "pool": {"锐利": ["600001.SH", "600002.SH"]}}, "")
        _snap = lambda secids: {
            "sh600001": {"name": "炸板票", "pre": 10.0, "price": 10.6, "turnover": 0.20, "pct": 6.0},
            "sh600002": {"name": "干净票", "pre": 10.0, "price": 10.3, "turnover": 0.10, "pct": 3.0}}
        _hist_flags = lambda pool_asof, codes: ({"600001.SH": {"zb": True, "zt5": 1, "zt10": 1, "zt20": 1}}
                                                if "600001.SH" in codes else {})
        r = rank(now=dt.datetime(2026, 10, 12, 9, 26))
        ok.append(("惩罚层：炸板沉底", r["buy"][0]["code"] == "600002.SH"
                   and r["buy"][-1]["code"] == "600001.SH"))
        ok.append(("惩罚层：标记可见", "炸板" in r["text"]))
        _hist_flags = lambda pool_asof, codes: {}

    finally:
        _pool, _snap, _industry, _sect_members, _hist_flags = real

    # 11) 🔴 盘中闸门回归：真实 _snap 传 >200 只、盘中时段 → 必须已开豁免、不 raise
    try:
        import qq_snap as _q
        _rg, _ri = _q.requests.get, _q.in_intraday

        class _R:
            text = ''
            encoding = 'gbk'
        try:
            _q.requests.get = lambda *a, **k: _R()
            _q.in_intraday = lambda now=None: True
            _snap(['sh600000'] * 201)          # 盘中 + >200 → 未豁免会 raise
            ok.append(("🔴盘中>200已豁免不崩", os.environ.get("QQ_SNAP_ALLOW_INTRADAY") == "1"))
        finally:
            _q.requests.get, _q.in_intraday = _rg, _ri
    except RuntimeError:
        ok.append(("🔴盘中>200已豁免不崩", False))

    allok = all(v for _, v in ok)
    for n, v in ok:
        print(("✅" if v else "❌"), n)
    print(f"selftest {sum(v for _, v in ok)}/{len(ok)}")
    return 0 if allok else 1


def main():
    ap = argparse.ArgumentParser(description="连板候选·竞价先手重排（v2 含板块层）")
    ap.add_argument("--tier", default="锐利", choices=["锐利", "实用"])
    ap.add_argument("--date")
    ap.add_argument("--live", action="store_true", help="候选池改走全市场实时池（本地T-1+当天实时·双通道）")
    ap.add_argument("--codes", help="直连指定票池（逗号分隔 000001.SZ,...）·跳过 liupan_scan 扫描；配 --date 指定池的 asof")
    ap.add_argument("--asof", help="显式时点 HH:MM 或 now（≤09:32=竞价先手版；>09:32=盘中动量版；now=当前时刻；不传=按当前时间自动判）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    _codes = [x.strip() for x in (a.codes or "").split(",") if x.strip()] or None
    res = rank(a.tier, a.date, asof=a.asof, live=a.live, codes=_codes)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(res.get("text") or f"（无输出 / {res.get('status')}）")
    sys.exit(3 if res["status"] in ("no_disk", "no_data") else 0)


if __name__ == "__main__":
    main()
