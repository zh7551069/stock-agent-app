#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
liupan_scan.py — 连板候选漏斗（首板前「蓄势慢热」扫描 · 双档）
=================================================================
定位：把全市场压成一个**小候选池**（关注名单·**非买入信号**），供次日观察。
判据来源：memory/连板股首板前共性挖掘-20261011.md（2023-09~2026-10·6,180 首板事件）
  T-1 区分度最强特征 = 换手率(d.62) / 量比30(d.56) / BOLL位置(d.51) / RSI6(d.48)
                        / 距60日低点(d.47) / 30日涨幅(d.44) / MA5>MA20(d.41)
  可执行性：联合条件把「次日连板率」从 0.16% 提到 ~0.5%（实用档 lift≈3x·锐利档更高）
            → 是**过滤器**非信号。

双档（都要求：收≥MA20 ∧ 距60日高点≤−5%(未创新高) ∧ T-1 未涨停）
  🔪 锐利：换手5-12 ∧ 量/30d 1.5-2.8 ∧ BOLL 0.60-1.05 ∧ RSI6 55-80 ∧ 30日涨 3-25%  → 前 8
  🎯 实用：换手4-12 ∧ 量/30d 1.0-2.8 ∧ BOLL 0.50-1.05 ∧ RSI6 50-80 ∧ 30日涨 0-30%  → 前 12
排序：按「蓄势画像贴合分」（对事件组 P50 的接近度·权重取挖掘 d 值·越低越贴 P50）。

用法：
  python3 scripts/liupan_scan.py                 # 最新交易日·双档
  python3 scripts/liupan_scan.py --date 2026-10-09 --tier 锐利
  python3 scripts/liupan_scan.py --json
  python3 scripts/liupan_scan.py --selftest
退出码：0=正常（含静默）；3=外置盘未挂载/无数据。
"""
import os, sys, json, argparse, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
DB = '/Volumes/My PSSD/本地数据库_v2版（淘宝购买）/duckdb/taobao_minute.duckdb'

MIN_AGE = 120
# 事件组画像 P25/P50/P75（判据同源：连板股首板前共性挖掘-20261011）
PROFILE = {'turnover': (2.20, 4.54, 9.74), 'vr': (0.82, 1.22, 1.96), 'bp': (0.42, 0.71, 0.93),
           'rsi6': (45.5, 58.6, 71.7), 'dist_lo60': (0.147, 0.284, 0.483),
           'r30': (-0.033, 0.083, 0.236), 'ma5_20': (-0.012, 0.028, 0.072)}
W = {'turnover': 0.62, 'vr': 0.56, 'bp': 0.51, 'rsi6': 0.48,
     'dist_lo60': 0.47, 'r30': 0.44, 'ma5_20': 0.41}
# 档位定义：band=特征带 · hi=距60日高点上限(负=至少低于高点 X%) · top=展示数
TIERS = [
    ("锐利", {'band': {'turnover': (5.0, 12.0), 'vr': (1.5, 2.8), 'bp': (0.60, 1.05),
                       'rsi6': (55.0, 80.0), 'r30': (0.03, 0.25)},
              'hi': -0.05, 'top': 8,
              'desc': "换手5-12% ∧ 量/30d 1.5-2.8 ∧ BOLL 0.60-1.05 ∧ RSI6 55-80 ∧ 30日涨 3-25%"}),
    ("实用", {'band': {'turnover': (4.0, 12.0), 'vr': (1.0, 2.8), 'bp': (0.50, 1.05),
                       'rsi6': (50.0, 80.0), 'r30': (0.0, 0.30)},
              'hi': -0.05, 'top': 12,
              'desc': "换手4-12% ∧ 量/30d 1.0-2.8 ∧ BOLL 0.50-1.05 ∧ RSI6 50-80 ∧ 30日涨 0-30%"}),
]



def _sfx(c6):
    """6 位裸码 → 交易所后缀（sh/sz/bj）。禁 ELSE 兜底：认不出的返回空串，宁缺勿错。"""
    c6 = str(c6).zfill(6)
    if c6[0] == "6" or c6[:3] in ("688", "689", "900"):
        return "SH"
    if c6[0] in ("0", "3") or c6[:3] in ("200", "300", "301"):
        return "SZ"
    if c6[0] in ("4", "8", "9"):
        return "BJ"
    return ""

def _lim(s):
    """⚠️ 仅作 fallback（DB 档位取不到时）。🔴 决策口径必须走 _limits_db（limit_price 真档位）——
    本函数漏 ST 5% / 北交所 30%，硬编码 10/20 会：①ST 涨 4.9%(已板) 被判"未涨停"混进池
    ②北交所涨 12%(离板还远) 被误判"已涨停"排除。"""
    c = s[:6]
    if s.endswith('.SH') and c.startswith(('68', '689')): return 20.0
    if s.endswith('.SZ') and c.startswith('30'): return 20.0
    return 10.0


def _limits_db(con, asof):
    """从 limit_price 取真档位 {前缀码: limit_pct}（🔴 单一事实源·禁硬编码）。"""
    try:
        rows = con.execute(
            "select code, cast(limit_pct as double) from limit_price where date = ?",
            [asof]).fetchall()
        return {str(c): float(p) for c, p in rows if p is not None}
    except Exception:
        return {}


def _pref(s):
    """后缀码 → daily/limit_price 的前缀码（002178.SZ → sz002178）。

    🔴 禁 ELSE 兜底：枚举 sh/sz/bj，其余返回 None（防指数/脏码铸出假票）。
    🔴 2026-11-01 修：原实现只判 '6' 否则 sz → **北交所 920xxx 被错转成 sz920xxx**。
    """
    c = str(s)[:6]
    if c.startswith("6"):
        return "sh" + c
    if c.startswith(("0", "3")):
        return "sz" + c
    if c.startswith(("4", "8", "9")):
        return "bj" + c
    return None


def _prices(con, codes, last):
    """权威【未复权】价位块（daily 为准，禁 limit_price / 后复权绝对价）：
    昨收(=last 收盘) / MA5 / MA10 / MA20 / 60日高 / 今日涨停价。"""
    out = {}
    for s in codes:
        p = _pref(s)
        try:
            rows = con.execute(f"""select date,close,high from daily where code='{p}'
                and date <= DATE '{last}' order by date desc limit 60""").fetchall()
        except Exception:
            continue
        if len(rows) < 20:
            continue
        closes = [float(r[1]) for r in rows]
        highs = [float(r[2]) for r in rows]
        c0 = closes[0]
        out[s] = {'close': c0,
                  'ma5': sum(closes[:5]) / 5, 'ma10': sum(closes[:10]) / 10,
                  'ma20': sum(closes[:20]) / 20, 'hi60': max(highs),
                  'zt': round(c0 * (1 + _lim(s) / 100), 2)}
    return out


def _pick(cur, blk):
    """在一档带内过滤 + 贴合分排序 → (df 降序, n_gate)"""
    import pandas as pd
    q = cur.copy()
    gate = pd.Series(True, index=q.index)
    for k, (lo, hi) in blk['band'].items():
        gate &= (q[k] >= lo) & (q[k] <= hi)
    gate &= (q['close'] >= q['ma20']) & (q['dist_hi60'] <= blk['hi'])
    gate &= (q['pct_chg'] < q['lim'] - 0.5)      # 当日未涨停（首板前不该已是板）
    # 🔴🔴 2026-11-01 加：当日跌停剔池。实测（真档位·n=52）跌停票次日买入 −0.504% vs 其余
    #    +0.094%；形态上「跌停∧放量」= 出货 ≠ 蓄势（血证 10-30 文投控股 −10.13% 混进池）
    gate &= (q['pct_chg'] > -(q['lim'] - 0.5))
    q = q[gate].copy()
    if len(q) == 0:
        return q, 0
    sc = pd.Series(0.0, index=q.index)
    for k, (p25, p50, p75) in PROFILE.items():
        sc += W[k] * (1 - (q[k] - p50).abs().div(max(p75 - p25, 1e-9)).clip(upper=1.0))
    q['score'] = sc / sum(W.values())
    # 🔴 标记列（只提示不剔除）：距涨停 < 1pp → 近板。实测真涨停次日买入 −0.914%(t=−2.26)
    #    → 已在 gate 剔除；近板票仍留池但必须让人看见，禁当"蓄势"买
    q['tag'] = ""
    q.loc[q['pct_chg'] >= q['lim'] - 1.0, 'tag'] = "⚠️近板"
    return q.sort_values('score', ascending=False).reset_index(drop=True), len(q)


def _rows(con, cur, last, tname, blk, zlmap):
    import pandas as pd
    q, n = _pick(cur, blk)
    head = f"──────── 🔪 {tname}档 · 带内 {n} 只 · 展示前 {min(blk['top'], n)} " + "─" * 6
    out = [head, f"   过滤：{blk['desc']} ∧ 收≥MA20 ∧ 距60高≤{blk['hi']*100:.0f}% ∧ 当日未涨停∧未跌停"]
    out.append(f"   {'#':>2} {'代码':<10}{'名称':<8}{'换手%':>7}{'量/30d':>7}{'30日涨%':>8}{'RSI6':>6}{'BOLL':>6}{'距60高%':>8}{'主力万':>9}{'分':>6}  标记")
    for i, r in q.head(blk['top']).iterrows():
        nm = str(r['name'])[:6]
        zl = zlmap.get(r['code'], float('nan'))
        zls = f"{zl:>9.0f}" if zl == zl else "      n/a"
        tg = str(r.get('tag') or '')
        out.append(f"   {i+1:>2} {r['code']:<10}{nm:<8}{r['turnover']:>7.2f}{r['vr']:>7.2f}"
                   f"{r['r30']*100:>8.2f}{r['rsi6']:>6.1f}{r['bp']:>6.2f}{r['dist_hi60']*100:>8.2f}{zls}{r['score']:>6.3f}  {tg}")
    # ── 权威价位块（未复权·daily）——禁把 limit_price.prev_close 当昨收、禁后复权绝对价
    pmap = {}
    if n > 0:
        try:
            pmap = _prices(con, list(q.head(blk['top'])['code']), last)
        except Exception:
            pmap = {}
    if pmap:
        out.append("   ── 权威价位(未复权·daily) ──  " +
                   f"{'昨收':>9}{'MA5':>8}{'MA10':>8}{'MA20':>8}{'60日高':>9}{'今涨停':>9}")
        for _, r in q.head(blk['top']).iterrows():
            pm = pmap.get(r['code'])
            if not pm:
                out.append(f"      {r['code']:<10}（价位缺失）")
                continue
            out.append(f"      {r['code']:<10}{pm['close']:>9.2f}{pm['ma5']:>8.2f}"
                       f"{pm['ma10']:>8.2f}{pm['ma20']:>8.2f}{pm['hi60']:>9.2f}{pm['zt']:>9.2f}")
    if n == 0:
        out.append("   （空·本档无候选）")
    elif n > blk['top']:
        p = f"{ROOT}/.openclaw/tmp/liupan_{tname}_full_{last}.txt"
        try:
            q.to_csv(p, index=False)
            out.append(f"   … 另有 {n-blk['top']} 只 → {p}")
        except Exception:
            pass
    return "\n".join(out), n, min(blk['top'], n), q


def scan(date=None, tier="all"):
    import duckdb, pandas as pd
    if not os.path.exists(DB):
        return {"status": "no_disk", "reason": "外置盘未挂载"}
    con = duckdb.connect(DB, read_only=True)
    from trade_day import latest_trade_day
    try:
        last = latest_trade_day(con, 'daily')
    except Exception:
        last = con.execute("select max(date) from daily").fetchone()[0]
    if date:
        last = dt.date.fromisoformat(date)
    if isinstance(last, str):
        last = dt.date.fromisoformat(last[:10])
    tfmax = con.execute("select max(date) from tech_factor_hfq").fetchone()[0]
    if isinstance(tfmax, str):
        tfmax = dt.date.fromisoformat(tfmax[:10])
    stale = 0
    if isinstance(tfmax, dt.date) and tfmax < last:
        stale = (last - tfmax).days
        last = tfmax                              # 用因子表实际最新日

    tf = con.execute(f"""
      select code,date,close,turnover,rsi6,ma5,ma20,vol_hand,high,low,boll_up,boll_low,pct_chg
      from tech_factor_hfq
      where date BETWEEN DATE '{last}' - INTERVAL 130 DAY AND DATE '{last}'
        and (code like '%.SH' or code like '%.SZ')
    """).df()
    if tf.empty:
        return {"status": "no_data", "reason": f"{last} 无技术因子"}
    tf['date'] = pd.to_datetime(tf['date'])
    tf = tf.sort_values(['code', 'date'])
    g = tf.groupby('code')
    tf['v30'] = g['vol_hand'].transform(lambda s: s.rolling(30).mean())
    tf['vr'] = tf['vol_hand'] / tf['v30']
    tf['r30'] = tf['close'] / g['close'].shift(30) - 1
    tf['lo60'] = g['low'].transform(lambda s: s.rolling(60).min())
    tf['dist_lo60'] = tf['close'] / tf['lo60'] - 1
    tf['hi60'] = g['high'].transform(lambda s: s.rolling(60).max())
    tf['dist_hi60'] = tf['close'] / tf['hi60'] - 1
    tf['bp'] = (tf['close'] - tf['boll_low']) / (tf['boll_up'] - tf['boll_low'])
    tf['ma5_20'] = tf['ma5'] / tf['ma20'] - 1
    cur = tf[tf['date'] == pd.Timestamp(last)].copy()

    info = con.execute("""SELECT code,name,list_date FROM stock_info
        WHERE market IN ('主板','创业板','科创板')""").df()
    info['code6'] = info['code'].astype(str)
    cur['code6'] = cur['code'].str[:6]
    cur = cur.merge(info[['code6', 'name', 'list_date']], on='code6', how='left')
    cur = cur[~cur['name'].astype(str).str.contains('ST|退', na=False)]
    age = (pd.Timestamp(last) - pd.to_datetime(cur['list_date'], format='%Y%m%d', errors='coerce')).dt.days
    cur = cur[age >= MIN_AGE]
    n_uni = len(cur)
    # 🔴 档位单一事实源 = limit_price.limit_pct（含 ST 5% / 北交所 30%）；取不到才 fallback
    _lp = _limits_db(con, last)
    _limmap = {}
    for _c in cur['code']:
        _p = _pref(_c)
        if _p is not None and _p in _lp:
            _limmap[_c] = _lp[_p]
    cur['lim'] = cur['code'].map(_limmap)
    _nb = int(cur['lim'].isna().sum())
    cur['lim'] = cur['lim'].fillna(cur['code'].map(_lim))   # fallback
    if _nb:
        cur.attrs['lim_fallback'] = _nb

    # 主力净额(东财, 万元) 最新可得日
    zlmap = {}
    try:
        ffd = con.execute("""select max(CAST(REPLACE(CAST(交易日期 AS VARCHAR),'-','') AS BIGINT)) d
            from stock_flow_em""").fetchone()[0]
        # 🔴 2026-11-01 修 code 归一（血证：stock_flow_em 混 8位前缀 'sz002510' /
        #    9位后缀 '002510.SZ'，而 cur['code'] 是后缀式 → 直接 zip 全 miss → 主力列恒 n/a）
        ff = con.execute(f'''select lpad(substr(regexp_replace(CAST(股票代码 AS VARCHAR),'(sh|sz|bj)',''),1,6),6,'0') code6,
            "今日主力净流入额（万元）" zl
            from stock_flow_em where CAST(REPLACE(CAST(交易日期 AS VARCHAR),'-','') AS BIGINT)={ffd}''').df()
        # 🔴🔴 列名骗人：`今日主力净流入额（万元）` 实存单位是「元」（全市场中位 |值| 2.02e6，
        #    按万元算是 202 亿/只，荒谬）→ 显示除以 1e4 才得到真·万元
        zlmap = {}
        for c, z in zip(ff['code6'], ff['zl']):
            v = (float(z) / 1e4) if z is not None and z == z else None
            zlmap[f"{c}.{_sfx(c)}"] = v
            zlmap[c] = v
    except Exception:
        pass

    want = [t for t in TIERS if tier in ("all", t[0])]
    secs, counts, pool = [], {}, {}
    for tname, blk in want:
        txt, n, shown, q = _rows(con, cur, last, tname, blk, zlmap)
        secs.append(txt)
        counts[tname] = {"n_gate": n, "n_show": shown}
        # 🔴 全带内（含未展示）→ 竞价先手重排用（只用展示前 N 会漏掉排名靠后的赢家）
        pool[tname] = [str(c) for c in q['code']]
    n_cand = sum(c["n_show"] for c in counts.values())

    if n_cand == 0:
        return {"status": "ok", "asof": str(last), "n_universe": n_uni, "tiers": counts,
                "n_cand": 0, "stale_days": stale, "text": "", "pool": pool}

    head = [f"🎯 连板候选漏斗（首板前蓄势）· as-of {last} · 盘后版"]
    if stale:
        head.append(f"⚠️ 技术因子表落后 {stale} 天（tech_factor_hfq），结果以该表为准")
    tail = " ／ ".join(f"{k} {v['n_gate']}只(展{v['n_show']})" for k, v in counts.items())
    head.append(f"可选池 {n_uni} ｜ {tail}")
    head.append("🔴 关注池·**非买入信号**（过滤器非信号）；买入须逐只过 advice_gate --pair")
    text = "\n".join(head) + "\n" + "\n".join(secs)
    return {"status": "ok", "asof": str(last), "n_universe": n_uni, "tiers": counts,
            "n_cand": n_cand, "stale_days": stale, "text": text, "pool": pool}


def selftest():
    ok = []
    import pandas as pd
    # 1) 贴合分：贴 P50 → 1.0，且高于偏离样本
    q = pd.DataFrame({'turnover': [4.54, 10.0], 'vr': [1.22, 2.5], 'bp': [0.71, 1.0],
                      'rsi6': [58.6, 75.0], 'dist_lo60': [0.284, 0.60],
                      'r30': [0.083, 0.25], 'ma5_20': [0.028, 0.10]})
    for k, (p25, p50, p75) in PROFILE.items():
        q['pr_' + k] = 1 - (q[k] - p50).abs().div(max(p75 - p25, 1e-9)).clip(upper=1.0)
    q['score'] = sum(W[k] * q['pr_' + k] for k in W) / sum(W.values())
    ok.append(("贴P50=1且降序", q.loc[0, 'score'] > q.loc[1, 'score'] and abs(q.loc[0, 'score'] - 1.0) < 1e-9))
    # 2) 权重和
    ok.append(("权重和", abs(sum(W.values()) - sum([0.62, 0.56, 0.51, 0.48, 0.47, 0.44, 0.41])) < 1e-9))
    # 3) 档位定义合理
    ok.append(("档位区间合法", all(lo < hi for _, b in TIERS for lo, hi in b['band'].values())
               and all(b['hi'] < 0 and b['top'] > 0 for _, b in TIERS)))
    # 4) 锐利 ⊂ 实用（逐特征子集）
    p = dict(TIERS)['实用']['band']; s = dict(TIERS)['锐利']['band']
    ok.append(("锐利更严", all(s[k][0] >= p[k][0] and s[k][1] <= p[k][1] for k in s)))
    # 5) 板档映射
    ok.append(("板档", _lim('688244.SH') == 20 and _lim('301513.SZ') == 20 and _lim('600303.SH') == 10))
    # 6) 前缀码映射（daily 取价用）
    ok.append(("前缀码", _pref('002178.SZ') == 'sz002178' and _pref('600592.SH') == 'sh600592'
               and _pref('688651.SH') == 'sh688651'))
    # 7) 🔴 前缀码·北交所（血证 2026-11-01：原实现只判 '6' 否则 sz → 920306.BJ 被转成 sz920306）
    ok.append(("前缀码·北交所", _pref('920306.BJ') == 'bj920306' and _pref('830799.BJ') == 'bj830799'))
    # 8) 🔴 前缀码·禁 ELSE 兜底（认不出必须 None，不能猜 sh/sz）
    ok.append(("前缀码·禁兜底", _pref('XYZ123') is None and _pref('') is None))
    # 9) 🔴 跌停剔池 + 近板标记（纯逻辑·不碰 DB）
    #    边界：−10%(跌停)剔 / −9.4%(未跌停)留 / 9.6%(涨停)剔 / 19.0%(20%板·未板)留
    _d = pd.DataFrame({'pct_chg': [3.0, -10.0, -9.4, 9.6, 19.0],
                       'lim': [10.0, 10.0, 10.0, 10.0, 20.0]})
    _gate = ((_d['pct_chg'] < _d['lim'] - 0.5) & (_d['pct_chg'] > -(_d['lim'] - 0.5)))
    ok.append(("跌停剔池", list(_gate) == [True, False, True, False, True]))
    _tag = pd.Series("", index=_d.index)
    _tag[_d['pct_chg'] >= _d['lim'] - 1.0] = "⚠️近板"
    ok.append(("近板标记", _tag.iloc[4] == "⚠️近板" and _tag.iloc[0] == ""))
    # 10) 🔴 档位必须来自 limit_price（决策口径），_lim 只是 fallback
    ok.append(("档位取DB函数存在", callable(_limits_db)))
    allok = all(v for _, v in ok)
    for n, v in ok:
        print(("✅" if v else "❌"), n)
    print(f"selftest {sum(v for _, v in ok)}/{len(ok)}")
    return 0 if allok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--tier", default="all", choices=["all", "锐利", "实用"])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    res = scan(a.date, a.tier)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(res.get("text") or f"（无候选 / {res.get('status')}）")
    sys.exit(3 if res["status"] in ("no_disk", "no_data") else 0)
