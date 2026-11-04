#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""liupan_live_scan.py — 全市场【实时候选池】双通道（2026-10-16 建）

治「当天新热点（如军工）进不了候选池」的结构缺陷：
  旧链路 = 盘前候选池（T-1 本地因子）→ 竞价/盘中只在该池内**重排** → 池子锁死，当日启动的热点永远看不见。
  本脚本 = 在**竞价/盘中**，用「本地历史(T-1及之前) ＋ 当天实时」**重新做一次全市场候选池筛选**。

双通道（union·去重·标注来源）：
  🔵 通道A · 蓄势：原 7 特征用【实时价重算】→ 过原锐利/实用带（判据同源 liupan_scan.TIERS）
     ⚠️ 蓄势带天然排除「已超买/创新高」→ 捕捉不到当天涨停的热点（设计使然）。
  🟠 通道B · 动量：当天实时口径 —— 涨幅 + 换手 + 量比 + 站上均价 + 申万行业共振
     → 这是能捕捉「当天资金流动 / 板块启动」的一侧（血证 2026-10-16：军工 37 只在此入榜）。

数据口径：
  · 本地(T-1)：tech_factor_hfq（后复权因子·算历史）/ daily（未复权权威价·算换算比）/ stock_info（行业·上市日）
  · 线上(实时)：腾讯快照 qq_snap（现价/涨跌幅/换手率/量比/均价/成交额）——**全市场只拉一次**
  · 🔴 复权对齐：实时价 × (tech_factor_hfq.close ÷ daily.close)[T-1] = 后复权实时价 → 与因子表同口径
  · 🔴 板块共振只用【申万行业】(stock_info.industry)，禁用概念/主力净额当钥匙

铁律：输出=候选池（盯盘名单），**非买入信号**；买入仍须逐只过 advice_gate --pair。
用法：
  python3 scripts/liupan_live_scan.py [--tier 锐利] [--asof now|HH:MM] [--top 12] [--json] [--selftest]
退出码：0=正常（含静默）；3=无数据（外置盘未挂载 / 实时源不可用）。
"""
import os, sys, json, argparse, statistics, datetime as dt, subprocess
import duckdb
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.environ.setdefault("QQ_SNAP_ALLOW_INTRADAY", "1")   # 竞价/盘中要拉全市场(~5500只) → 必须豁免
DB = '/Volumes/My PSSD/本地数据库_v2版（淘宝购买）/duckdb/taobao_minute.duckdb'
MIN_AGE = 60

import liupan_scan as LS   # 复用 TIERS（判据同源·禁自拼阈值）

# ── 通道B 动量带（实时口径·2026-10-16 收紧 v2：涨幅/换手/量比 三档全抬）──
MOM = dict(pct=(3.0, 9.8), turn=(4.0, 60.0), vr=(2.0, 50.0))
# ── 板块脉冲门（判据同源 concept_radar.pulse_trigger 思路·申万行业口径）：
#    无「强板块」(板中位涨幅≥med ∧ 板内涨停≥zt) → 整体不推（治刷屏·专注板块效应）
SECTGATE = dict(med=1.2, zt=3)

# ── 推送冷却状态（高频轮询·同一强板块组合 N 分钟内不重复推）──
STATE = os.path.join(ROOT, "state", "liupan_live_state.json")


def _cooldown_ok(key, cooldown=0):
    """同一强板块组合在冷期内 → 不推。返回 (should_push, reason)。"""
    if cooldown <= 0:
        return True, "no_cooldown"
    now = dt.datetime.now()
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    if st.get("date") != now.strftime("%Y-%m-%d"):
        return True, "new_day"
    if st.get("key") != key:
        return True, "new_sectors"
    mins = (now.timestamp() - float(st.get("ts", 0))) / 60.0
    if mins < cooldown:
        return False, f"冷却中（同板块组合 {mins:.0f} 分钟前已推·阈值 {cooldown} 分钟）"
    return True, f"超时（{mins:.0f} 分钟）"


def _save_state(key):
    now = dt.datetime.now()
    d = {"date": now.strftime("%Y-%m-%d"), "ts": now.timestamp(), "key": key}
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def _secid(suf):
    if suf.endswith('.SH'): return 'sh' + suf[:6]
    if suf.endswith('.SZ'): return 'sz' + suf[:6]
    if suf.endswith('.BJ'): return 'bj' + suf[:6]
    return None


def _c6sid(c6):
    if c6.startswith('6'): return 'sh' + c6
    if c6.startswith(('0', '3')): return 'sz' + c6
    if c6.startswith(('4', '8', '9')): return 'bj' + c6
    return None


def _in_trade_window(hm=None):
    """盘中时段判据（纯函数）：09:30-11:30 / 13:00-15:00。hm=当日分钟数。"""
    if hm is None:
        n = dt.datetime.now()
        hm = n.hour * 60 + n.minute
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)


def _snap(secids):
    from qq_snap import snapshot
    return snapshot(list(secids))


def _rsi6(closes):
    """简化 RSI6（近 7 点 → 6 差分）。closes 顺序=由旧到新。"""
    if len(closes) < 7:
        return None
    d = [closes[i] - closes[i - 1] for i in range(len(closes) - 6, len(closes))]
    g = sum(x for x in d if x > 0) / 6.0
    l = -sum(x for x in d if x < 0) / 6.0
    if g + l == 0:
        return 50.0
    return 100.0 * g / (g + l)


def _lim(suf):
    c = suf[:6]
    if suf.endswith('.SH') and c.startswith('68'): return 20.0
    if suf.endswith('.SZ') and c.startswith('30'): return 20.0
    if suf.endswith('.BJ'): return 30.0
    return 10.0


def _zb(price, high, pre, lim, tol=0.003):
    """炸板判定（纯函数）：最高触及涨停价 ∧ 现价明显低于涨停价。返回 (炸板 bool, 距今高回落%)。"""
    if not price or not pre:
        return False, None
    ztp = pre * (1 + lim / 100.0)
    touched = high is not None and high >= ztp * (1 - 0.002)
    off = (price / high - 1) * 100 if high else None
    return (bool(touched and price < ztp * (1 - tol))), off


def live_pool(tier="锐利", asof=None, top=12, cooldown=0, with_a=True, trade_window=False):
    if trade_window and not _in_trade_window():   # 🔴 盘中时段硬 guard（盘前/盘后不执行）
        return {"status": "out_window", "n_pool": 0,
                "text": "[skip] 非盘中时段（09:30-11:30 / 13:00-15:00）", "reason": "非盘中时段"}
    if not os.path.exists(DB):
        return {"status": "no_disk", "reason": "外置盘未挂载"}
    con = duckdb.connect(DB, read_only=True)
    try:
        last = con.execute("select max(date) from tech_factor_hfq").fetchone()[0]
        if isinstance(last, str):
            last = dt.date.fromisoformat(last[:10])
        ser = con.execute(f"""
            select code, date, close, high, low, boll_up, boll_low, ma5, ma20
            from tech_factor_hfq
            where date BETWEEN DATE '{last}' - INTERVAL 150 DAY AND DATE '{last}'
              and (code like '%.SH' or code like '%.SZ' or code like '%.BJ')
            order by code, date
        """).df()
        raw = con.execute(f"""select code, close from daily where date = DATE '{last}'
            and (code like 'sh%' or code like 'sz%' or code like 'bj%')""").df()
        info = con.execute("""SELECT code,name,industry,list_date FROM stock_info
            WHERE market IN ('主板','创业板','科创板','北交所')""").df()
    finally:
        con.close()
    if ser.empty:
        return {"status": "no_data", "reason": f"{last} 无技术因子"}

    ser['date'] = pd.to_datetime(ser['date'])
    ser = ser.sort_values(['code', 'date'])
    g = ser.groupby('code')
    ser['hi60'] = g['high'].transform(lambda s: s.rolling(60).max())
    ser['lo60'] = g['low'].transform(lambda s: s.rolling(60).min())
    ser['c30'] = g['close'].shift(30)
    # 🔴 近期涨停历史（后复权 close 比值 = 真实涨幅）：治「连板后回落再冲/炸板」盲区
    ser['lim'] = ser['code'].map(_lim)
    ser['pc'] = g['close'].pct_change() * 100
    ser['iszt'] = ser['pc'] >= (ser['lim'] - 0.3)
    grp2 = ser.groupby('code')
    ser['zt5'] = grp2['iszt'].transform(lambda s: int(s.tail(5).sum()))
    ser['zt10'] = grp2['iszt'].transform(lambda s: int(s.tail(10).sum()))
    ser['zt20'] = grp2['iszt'].transform(lambda s: int(s.tail(20).sum()))
    ser['t1p'] = grp2['pc'].transform(lambda s: float(s.iloc[-1]) if pd.notna(s.iloc[-1]) else None)
    ser['t1zt'] = grp2['iszt'].transform(lambda s: bool(s.iloc[-1]))
    cur = ser[ser['date'] == pd.Timestamp(last)].copy()
    seqmap = g['close'].apply(lambda s: list(s)[-7:]).to_dict()   # 近 7 日后复权收盘
    # 历史涨停映射（全码·供 A/B 标注）
    histmap = {r2['code']: dict(zt5=int(r2['zt5']), zt10=int(r2['zt10']), zt20=int(r2['zt20']),
                                t1zt=bool(r2['t1zt']), t1p=r2['t1p'])
               for _, r2 in cur.iterrows()}

    # 换算比（后复权 ÷ 未复权）
    def suf_of(pfx):
        c = str(pfx)[2:]
        return c + ('.SH' if pfx.startswith('sh') else ('.SZ' if pfx.startswith('sz') else '.BJ'))
    raw['su'] = raw['code'].map(suf_of)
    ratiomap = dict(zip(raw['su'], raw['close']))
    cur['ratio'] = cur['close'] / cur['code'].map(ratiomap)

    imap = {}
    for _, r in info.iterrows():
        if r['code'] is None:
            continue
        imap[str(r['code']).zfill(6)[:6]] = (r['name'], str(r['industry']), r['list_date'])
    cur['code6'] = cur['code'].str[:6]
    cur['nm'] = cur['code6'].map(lambda c: imap.get(c, (None, None, None))[0])
    cur['ind'] = cur['code6'].map(lambda c: imap.get(c, (None, None, None))[1])
    cur['ld'] = cur['code6'].map(lambda c: imap.get(c, (None, None, None))[2])
    cur = cur[~cur['nm'].astype(str).str.contains('ST|退', na=False)]
    age = (pd.Timestamp(last) - pd.to_datetime(cur['ld'], format='%Y%m%d', errors='coerce')).dt.days
    cur = cur[(age >= MIN_AGE) & cur['ratio'].notna()].copy()

    # ── 实时快照（全市场·只拉一次）──
    sec2row = {}
    for _, r in cur.iterrows():
        sid = _secid(r['code'])
        if sid:
            sec2row[sid] = r
    snap = _snap(list(sec2row.keys()))
    if not snap:
        return {"status": "no_data", "reason": "实时快照为空（数据源不可用）"}

    # 行业聚合（板块共振·申万行业）
    sect = {}
    for sid, r in sec2row.items():
        rr = snap.get(sid) or {}
        p = rr.get('pct')
        ind = r['ind']
        if ind is None or not isinstance(p, (int, float)):
            continue
        d = sect.setdefault(ind, {"pts": [], "zt": 0})
        d["pts"].append(p)
        lim = _lim(r['code'])
        if p >= lim - 0.3:
            d["zt"] += 1
    for ind, d in sect.items():
        d["med"] = statistics.median(d["pts"]) if d["pts"] else None
        d["n"] = len(d["pts"])

    # 🔥 强板块判定（申万行业口径）：有强板块才推；无 → 直接静默
    strong = sorted([ind for ind, d in sect.items()
                     if d.get('med') is not None and d['med'] >= SECTGATE['med'] and d.get('zt', 0) >= SECTGATE['zt']],
                    key=lambda i: -(sect[i]['med']))
    if not strong:
        return {"status": "no_sector", "asof": str(last), "strong": [], "n_pool": 0,
                "text": f"[skip] 无强板块（板中位≥{SECTGATE['med']:.1f}% ∧ 板内涨停≥{SECTGATE['zt']}）",
                "reason": f"无强板块（板中位≥{SECTGATE['med']:.1f}% ∧ 板内涨停≥{SECTGATE['zt']}）"}
    # 冷却门（仅高频轮询时生效）：同一强板块组合 N 分钟内不重复推
    _key = "|".join(strong)
    _ok, _why = _cooldown_ok(_key, cooldown)
    if not _ok:
        return {"status": "cooldown", "asof": str(last), "strong": strong, "n_pool": 0,
                "text": f"[skip] {_why}", "reason": _why}

    base = LS.TIERS_DICT[tier] if hasattr(LS, "TIERS_DICT") else dict(LS.TIERS)[tier]
    bnd = base['band']
    B, rows = [], []
    for sid, r in sec2row.items():
        rr = snap.get(sid) or {}
        px, pre = rr.get('price'), rr.get('pre')
        if not px or not pre:
            continue
        ratio = float(r['ratio'])
        px_h = px * ratio
        pct = rr.get('pct'); turn = rr.get('turnover'); vr = rr.get('vol_ratio'); avg = rr.get('avg')
        lim = _lim(r['code'])
        zb, offhigh = _zb(px, rr.get('high'), pre, lim)
        st = sect.get(r['ind']) or {}
        rows.append(dict(code=r['code'], name=rr.get('name') or r['nm'], ind=r['ind'],
                         pct=pct, turn=turn, vr=vr, avg=avg, amt=rr.get('amount'), px=px,
                         zb=zb, offhigh=offhigh, zt5=int(r['zt5']), zt10=int(r['zt10']), zt20=int(r['zt20']),
                         t1zt=bool(r['t1zt']), t1p=r['t1p'], lim=lim,
                         sect_med=st.get('med'), sect_zt=st.get('zt')))
        # ── 通道B：动量（当日·2026-10-16 收紧：三档抬升 + 仅限强板块内）──
        if isinstance(pct, (int, float)) and turn is not None and vr is not None:
            if (MOM['pct'][0] <= pct <= MOM['pct'][1]
                    and MOM['turn'][0] <= turn <= MOM['turn'][1]
                    and MOM['vr'][0] <= vr <= MOM['vr'][1]
                    and r['ind'] in strong                       # 🔥 板块门
                    and (avg is None or px >= avg)               # 站上均价
                    and pct < _lim(r['code']) - 0.3):            # 非已涨停（炸板保留·标注+降权）
                B.append(dict(rows[-1], chan='B'))

    # ── 通道A：指纹池（🔴 判据同源 = 直接复用 liupan_scan · 禁自拼阈值/禁实时价重算）──
    #   血证 10/16：我一度用「实时价重算」搬指纹带 → 跑出「跌停但 BOLL 仍在带内」怪象
    #   （指纹 P25/P50/P75 画像 = 收盘截面统计 → 盘中价 = 分布外推）。
    A = []
    if with_a:
        try:
            _p = subprocess.run([sys.executable, os.path.join(HERE, "liupan_scan.py"),
                                 "--tier", tier, "--json"],
                                capture_output=True, text=True, timeout=180, cwd=ROOT)
            _s = _p.stdout or ""
            _j = json.loads(_s[_s.index("{"): _s.rindex("}") + 1])
            a_codes = (_j.get("pool") or {}).get(tier) or []
        except Exception:
            a_codes = []
        for c in a_codes:
            c6 = str(c)[:6]
            nm_i, ind_i, _ld = imap.get(c6, (None, None, None))
            sid = _secid(c)
            rr = snap.get(sid) or {}
            px2, pre2 = rr.get("price"), rr.get("pre")
            h = histmap.get(c) or {}
            st = sect.get(ind_i) or {}
            _z2, _o2 = (_zb(px2, rr.get("high"), pre2, _lim(c)) if (px2 and pre2) else (False, None))
            A.append(dict(code=c, name=rr.get("name") or nm_i, ind=ind_i,
                          pct=rr.get("pct"), turn=rr.get("turnover"), vr=rr.get("vol_ratio"),
                          avg=rr.get("avg"), amt=rr.get("amount"), px=px2,
                          zb=_z2, offhigh=_o2, zt5=h.get("zt5", 0), zt10=h.get("zt10", 0),
                          zt20=h.get("zt20", 0), t1zt=h.get("t1zt", False), t1p=h.get("t1p"),
                          lim=_lim(c), sect_med=st.get("med"), sect_zt=st.get("zt"), chan="A"))

    # 🔴 排序：炸板沉底 + 连板后回落再冲降权
    def _pen(x):
        return (x['pct'] * 0.5 + min(x['vr'], 6) * 6 + min(x['turn'], 15) * 1.5) \
            - (100.0 if x.get('zb') else 0.0) - max(0, x.get('zt10', 0) - 1) * 2.0
    B.sort(key=lambda x: -_pen(x))
    # 🟠 A 顺序 = liupan_scan 自带盘后蓄势分（🔴 判据同源·禁自拼排序）

    seen, pool = set(), []
    for r in B + A:
        if r['code'] in seen:
            continue
        seen.add(r['code']); pool.append(r)

    ind_dist = {}
    for r in pool:
        ind_dist[r['ind']] = ind_dist.get(r['ind'], 0) + 1
    top_ind = sorted(ind_dist.items(), key=lambda kv: -kv[1])[:10]

    L = [f"🎯 全市场实时候选池（本地T-1 ＋ 当天实时）· T-1={last} · asof={asof or 'now'}",
         f"   基础池 {len(rows)} 只（剔 ST/退/次新<{MIN_AGE}日）",
         "   🔥 强板块（板中位≥{:.1f}% ∧ 板内涨停≥{}）：".format(SECTGATE['med'], SECTGATE['zt'])
         + " ｜ ".join(f"{i}(中位{sect[i]['med']:+.2f}%,涨停{sect[i]['zt']})" for i in strong[:6]),
         f"   🔵 通道A 指纹池({'开' if with_a else '【已关·--no-a】'}·**同源复用 liupan_scan**)：{len(A)} 只 ｜ 🟠 通道B 动量(当日·限强板块)：{len(B)} 只 ｜ 合并候选池 {len(pool)} 只",
         "   🏷 行业分布 Top10：" + " ｜ ".join(f"{k} {v}" for k, v in top_ind),
         f"   {'#':>2} {'代码':<10}{'名称':<8}{'涨幅%':>7}{'换手%':>7}{'量比':>6}{'额(亿)':>8}{'行业':<9}{'板中位':>7}{'板涨停':>6}{'通道':>5}  标记"]
    for i, r in enumerate(pool[:top], 1):
        sm = f"{r['sect_med']:>7.2f}" if isinstance(r.get('sect_med'), (int, float)) else "      –"
        zt = f"{r['sect_zt']:>6}" if r.get('sect_zt') is not None else "     –"
        _a = r.get('amt')
        am = f"{_a / 1e4:>8.2f}" if isinstance(_a, (int, float)) else "       –"
        tg = []
        if r.get('zb'):
            tg.append(f"❗炸板(高{-(r.get('offhigh') or 0):.1f}%)")
        if r.get('zt20', 0) >= 2:
            tg.append(f"20日{r['zt20']}板")
        elif r.get('zt10', 0) >= 2:
            tg.append(f"10日{r['zt10']}板")
        elif r.get('zt5', 0) >= 1:
            tg.append(f"5日{r['zt5']}板")
        if r.get('t1zt'):
            tg.append("昨涨停")
        L.append(f"   {i:>2} {r['code']:<10}{str(r['name'])[:6]:<8}{r['pct']:>7.2f}{r['turn']:>7.2f}"
                 f"{r['vr']:>6.2f}{am}{str(r['ind'])[:7]:<9}{sm}{zt}{r['chan']:>5}  " + "/".join(tg))
    L.append("🔴 候选池=盯盘名单，**非买入信号**；🔵A=指纹池（T-1 收盘蓄势画像·今天怎么走是独立的事），🟠B=当日动量（可捕捉新热点）；买入仍过 advice_gate --pair。")
    _save_state(_key)
    return {"status": "ok", "asof": str(last), "n_base": len(rows), "n_A": len(A), "n_B": len(B),
            "n_pool": len(pool), "ind_dist": ind_dist, "strong": strong, "text": "\n".join(L),
            "pool": [{"code": r['code'], "name": r['name'], "pct": r['pct'], "turn": r['turn'],
                      "vr": r['vr'], "ind": r['ind'], "chan": r['chan'],
                      "zb": r.get('zb'), "offhigh": r.get('offhigh'), "zt5": r.get('zt5'),
                      "zt10": r.get('zt10'), "zt20": r.get('zt20'), "t1zt": r.get('t1zt'), "t1p": r.get('t1p')} for r in pool]}


def selftest():
    ok = []
    ok.append(("RSI6 全涨→100", _rsi6([10, 11, 12, 13, 14, 15, 16]) == 100.0))
    ok.append(("RSI6 全跌→0", _rsi6([16, 15, 14, 13, 12, 11, 10]) == 0.0))
    ok.append(("RSI6 平→50", _rsi6([10] * 7) == 50.0))
    ok.append(("RSI6 不足→None", _rsi6([1, 2, 3]) is None))
    ok.append(("secid sh", _secid("600316.SH") == "sh600316"))
    ok.append(("secid bj", _secid("920230.BJ") == "bj920230"))
    ok.append(("c6sid 沪", _c6sid("600316") == "sh600316"))
    ok.append(("c6sid 创业", _c6sid("300696") == "sz300696"))
    ok.append(("档位 主板", _lim("600316.SH") == 10.0))
    ok.append(("档位 科创", _lim("688499.SH") == 20.0))
    ok.append(("档位 北交所", _lim("920230.BJ") == 30.0))
    ok.append(("动量带合法", MOM['pct'][0] < MOM['pct'][1] and MOM['turn'][0] < MOM['turn'][1]))
    ok.append(("动量带已收紧(涨幅≥3/换手≥4/量比≥2)", MOM['pct'][0] >= 3 and MOM['turn'][0] >= 4 and MOM['vr'][0] >= 2))
    ok.append(("板块门合法", SECTGATE['med'] > 0 and SECTGATE['zt'] >= 1))
    ok.append(("冷却无状态→放行", _cooldown_ok("x", 30)[0] is True))
    ok.append(("通道A 默认开", live_pool.__defaults__[4] is True))
    ok.append(("盘中 09:45→True", _in_trade_window(9 * 60 + 45) is True))
    ok.append(("盘中 14:00→True", _in_trade_window(13 * 60 + 60) is True))
    ok.append(("盘前 09:20→False", _in_trade_window(9 * 60 + 20) is False))
    ok.append(("午休 12:00→False", _in_trade_window(12 * 60) is False))
    ok.append(("盘后 15:30→False", _in_trade_window(15 * 60 + 30) is False))
    # 炸板判定
    ok.append(("炸板：触板后回落", _zb(17.52, 17.72, 16.11, 10.0)[0] is True))
    ok.append(("非炸板：封板中", _zb(17.72, 17.72, 16.11, 10.0)[0] is False))
    ok.append(("非炸板：未触板", _zb(17.2, 17.4, 16.11, 10.0)[0] is False))
    ok.append(("炸板回落幅度", abs((_zb(17.52, 17.72, 16.11, 10.0)[1]) - (-1.129)) < 0.05))
    ok.append(("复用 liupan TIERS", set(dict(LS.TIERS).keys()) >= {"锐利", "实用"}))
    allok = all(v for _, v in ok)
    for n, v in ok:
        print(("✅" if v else "❌"), n)
    print(f"selftest {sum(v for _, v in ok)}/{len(ok)}")
    return 0 if allok else 1


def main():
    ap = argparse.ArgumentParser(description="全市场实时候选池（本地T-1 + 当天实时·双通道）")
    ap.add_argument("--tier", default="锐利", choices=["锐利", "实用"])
    ap.add_argument("--asof", default="now")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--cooldown", type=int, default=0, help="冷却分钟数（>0 时同板块组合 N 分钟内不重复推）")
    ap.add_argument("--with-a", dest="with_a", action="store_true", default=True, help="开启通道A（蓄势/指纹·默认开）")
    ap.add_argument("--no-a", dest="with_a", action="store_false", help="关闭通道A")
    ap.add_argument("--trade-window", action="store_true", help="仅盘中执行（盘前/盘后返回 [skip]）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    res = live_pool(a.tier, a.asof, a.top, cooldown=a.cooldown, with_a=a.with_a, trade_window=a.trade_window)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(res.get("text") or f"（无输出 / {res.get('status')}）")
    sys.exit(3 if res["status"] in ("no_disk", "no_data") else 0)


if __name__ == "__main__":
    main()
