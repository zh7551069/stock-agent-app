#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连板候选·竞价先手【历史回放】（2026-10-13 建）

用途：以「真实盘中当时可得的数据」重放某交易日的竞价先手选股 → 复盘核验：
      "如果当天 09:26（或任意盘中时点）我在盘，这套系统会选出谁？"

与 `liupan_auction_rank.py`（实盘版）的关系：
  - 实盘版 `_snap` = 腾讯【实时】快照（只反映"当下"）
  - 回放版：monkeypatch `_snap` = 目标日【历史竞价快照】→ **复用 rank() 的全部逻辑
    （先手分/板块层/可买性闸门/🌅板块异动）**，只换数据源。
  ⚠️ 不修改实盘版（其 v2.1 已冻结）。

🔴 数据源（全部线上抓取 —— 模拟"盘中真实环境"，不把本地库当行情）：
  - 竞价价/量：**新浪 1min K线**（quotes.sina.cn）
       · asof ≤ 09:30 → 取首根 bar(09:31) 的 **open**（=集合竞价价）＋其 volume（≈竞价量）
       · asof > 09:30 → 取 ≤ asof 的最后一根 bar 的 **close**＋累计量
  - 昨收：目标日**前一交易日** `daily.close`（未复权·权威价）
  - 换手基准：目标日 `tech_factor_hfq.turnover`（全天换手%）×（截至 asof 量 / 全天量）
  - 板块层：申万行业（`stock_info`，静态映射）
  - 候选池：`liupan_scan --date <目标日前一交易日>`（判据同源·禁自选）
  ⚠️ 昨收/换手基准/板块映射属**历史与静态依赖**（回测性质），价量一律走线上。

用法：
  python3 scripts/liupan_auction_replay.py --date 2026-10-12 [--asof 09:26] [--tier 锐利] [--json] [--selftest]
  退出码：0=正常；3=无数据（外置盘未挂载 / 新浪取数为空）。
"""
import os, sys, json, re, argparse, urllib.request, concurrent.futures as cf, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import liupan_auction_rank as M  # 复用 rank() 全逻辑（不修改）

DB = M.DB
SINA = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
        "?symbol={sec}&scale=1&ma=no&datalen=320")
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
OPEN_HM = 9 * 60 + 30   # 连续竞价开始（09:30）


def _prev_trading_day(con, d):
    """目标日 d 的前一交易日（daily 里 < d 的最大日期）。"""
    r = con.execute(f"select max(date) from daily where date < DATE '{d}'").fetchone()[0]
    return r


def _fetch_bars(sec, date):
    """新浪 1min K线 → 当天 bars（失败返回 None）。sec 形如 'sz301052'。"""
    for _ in range(5):
        try:
            u = SINA.format(sec=sec)
            d = urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=8).read().decode("utf-8", "ignore")
            m = re.search(r"\((\[.*\])\)", d, re.S)
            if not m:
                continue
            arr = json.loads(m.group(1))
            today = [r for r in arr if r["day"].startswith(date)]
            return today or None
        except Exception:
            continue
    return None


def _truncated(bars, date):
    """🔴 数据完整性判定：首根必须是当日开盘附近（≤09:31）。

    新浪 `datalen=320` 只保留最近 ~320 根 1min → 回放 2 个交易日以上时
    当日数据会被从**早盘截掉**，首根变成午后 bar。
    若不检测，`_point` 会把午后价格当「竞价价」→ 高开% 全错、换手分位全错。
    🩸 血证 2026-11-01：10-29 回放全池 1944 只首根=13:37，
       龙江交通（昨收3.18/今开3.19）被读成 3.50(+10.06%) → 误判「已涨停买不进」。
    """
    if not bars:
        return True
    head = bars[0]["day"][11:16]
    tail = bars[-1]["day"][11:16]
    return head > "09:32" or tail < "14:55"


def _point(bars, asof_hm):
    """bars → (price, frac)。frac = 截至 asof 量 / 全天量。

    🔴 竞价档（asof≤09:30）取 bars[0] 的 open —— **前提 bars[0] 是开盘 bar**，
    调用方必须先过 `_truncated()`。本地库首根=09:30(开盘集合竞价)、
    新浪首根=09:31(标签为结束时刻)，两者 open 均=竞价价。
    """
    if not bars:
        return None
    tot = sum(float(b["volume"]) for b in bars)
    if asof_hm <= OPEN_HM:
        f = bars[0]                      # 集合竞价：首根 bar 的 open=竞价价
        return float(f["open"]), (float(f["volume"]) / tot if tot > 0 else 1.0)
    hhmm = f"{asof_hm // 60:02d}:{asof_hm % 60:02d}:00"
    upto = [b for b in bars if b["day"][11:19] <= hhmm]
    if not upto:
        f = bars[0]
        return float(f["open"]), (float(f["volume"]) / tot if tot > 0 else 1.0)
    cum = sum(float(b["volume"]) for b in upto)
    return float(upto[-1]["close"]), (cum / tot if tot > 0 else 1.0)


def _fetch_bars_local(con, date, codes):
    """本地 minute_1min → {sec: bars}（形态对齐新浪：day/open/close/volume）。
    用途：新浪仅存最近 ~320 根(≈1.3 天) → 回放更早的日子必须走本地分钟库。"""
    rows = con.execute("""
        SELECT code, dt, open, close, volume FROM minute_1min
        WHERE dt >= CAST(? AS TIMESTAMP) AND dt < CAST(? AS TIMESTAMP) + INTERVAL 1 DAY
          AND market IN ('hs','bj')
    """, [date, date]).fetchall()
    want = set(codes)
    out = {}
    for code, dtv, o, c, v in rows:
        if code not in want:
            continue
        out.setdefault(code, []).append(
            {"day": dtv.strftime("%Y-%m-%d %H:%M:%S"), "open": o, "close": c, "volume": v})
    for s in out:
        out[s].sort(key=lambda b: b["day"])
    return out


def replay(tier="锐利", date=None, asof="09:26", workers=8, source="auto"):
    if not os.path.exists(DB):
        return {"status": "no_disk", "reason": f"DB 未挂载：{DB}"}
    if not date:
        return {"status": "no_data", "reason": "必须指定 --date（回放目标日）"}
    D = dt.date.fromisoformat(date)
    ah, am = asof.split(":")
    asof_hm = int(ah) * 60 + int(am)
    asof_t = dt.time(int(ah), int(am))

    import duckdb
    con = duckdb.connect(DB, read_only=True)
    try:
        prev = _prev_trading_day(con, D)
        if not prev:
            return {"status": "no_data", "reason": f"找不到 {D} 的前一交易日"}
        pool_day = str(prev)

        # ── 候选池（判据同源）──
        j, err = M._pool(tier, date=pool_day)
        if err:
            return {"status": "no_data", "reason": err}
        if j.get("status") != "ok":
            return {"status": j.get("status", "no_data"), "reason": j.get("reason", "无数据")}
        pool = (j.get("pool") or {}).get(tier) or []
        if not pool:
            return {"status": "ok", "asof": pool_day, "n": 0, "text": f"（{pool_day} 无 {tier} 档候选）"}

        # ── 板块成分（申万）──
        ind_of = M._industry([c[:6] for c in pool])
        inds = sorted({v for v in ind_of.values() if v and v != "None"})
        mem = M._sect_members(inds)
        codes = sorted({s for s in (M._secid(c) for c in pool) if s} | {s for v in mem.values() for s in v})

        # ── 本地静态/历史基准：昨收、目标日换手、名称 ──
        pre_map = {r[0][2:]: r[1] for r in con.execute(
            f"select code, close from daily where date = DATE '{prev}'").fetchall()}
        turn_map = {str(r[0])[:6]: r[1] for r in con.execute(
            f"select code, turnover from tech_factor_hfq where date = DATE '{D}'").fetchall()}
        name_map = {str(r[0]): r[1] for r in con.execute("select code, name from stock_info").fetchall()}
    finally:
        con.close()

    # ── 取数：sina=线上 / local=本地分钟库 / auto=先线上，空则回退本地 ──
    raw = {}
    # 🔴 先探一根：新浪 datalen=320 只覆盖最近 ~1.33 日 → 更早的日期必然截断，
    #    先探一次省掉 1900+ 次无效请求（否则每天白跑 2-4 分钟）
    if source in ("sina", "auto") and codes:
        _probe = _fetch_bars(codes[0][1], date)
        if _probe and _truncated(_probe, date):
            print(f"⚠️ 新浪 {date} 已截断（首根 {_probe[0]['day'][11:16]}）→ 直接走本地分钟库",
                  file=sys.stderr, flush=True)
            source = "local" if source == "auto" else source
            if source == "sina":
                source = "local"
    if source in ("sina", "auto"):
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for sec, bars in ex.map(lambda s: (s, _fetch_bars(s, date)), codes):
                if bars:
                    raw[sec] = bars
    used_src = "sina"
    trunc = [sec for sec, b in raw.items() if _truncated(b, date)]
    if trunc:
        # 🔴 截断即污染：竞价价=午后价、换手分位=残缺量 → 整池作废，强制走本地库
        frac_bad = len(trunc) / max(len(raw), 1)
        print(f"⚠️ 新浪 1min 截断：{len(trunc)}/{len(raw)} 只（{frac_bad*100:.1f}%）"
              f"首根={raw[trunc[0]][0]['day'][11:16]} → 该日竞价快照不可用，强制回退本地分钟库",
              file=sys.stderr, flush=True)
        raw = {}
    if not raw and source in ("local", "auto"):
        con = duckdb.connect(DB, read_only=True)
        try:
            raw = _fetch_bars_local(con, date, codes)
        finally:
            con.close()
        used_src = "local"
    if raw:
        bad2 = [sec for sec, b in raw.items() if _truncated(b, date)]
        if bad2:
            return {"status": "no_data",
                    "reason": f"{date} 分钟数据残缺（{len(bad2)}/{len(raw)} 只非全日）→ 拒绝回放，避免拿午后价当竞价价"}

    snap = {}
    for sec, bars in raw.items():
        c6 = sec[2:]
        pt = _point(bars, asof_hm)
        pre = pre_map.get(c6)
        if not pt or not pre:
            continue
        px, frac = pt
        base = turn_map.get(c6) or 0.0
        snap[sec] = {"name": name_map.get(c6) or sec, "pre": pre, "price": px,
                     "turnover": base * frac, "pct": (px / pre - 1) * 100}

    M._snap = lambda secids: {s: snap[s] for s in secids if s in snap}
    res = M.rank(tier, date=pool_day, now=dt.datetime.combine(D, asof_t), asof=asof)
    res["replay"] = {"date": date, "asof": asof, "pool_day": pool_day, "source": used_src,
                     "fetched": len(raw), "universe": len(codes), "used": len(snap)}
    return res


def main():
    ap = argparse.ArgumentParser(description="连板候选·竞价先手【历史回放】")
    ap.add_argument("--date", help="回放目标日 YYYY-MM-DD（必填）")
    ap.add_argument("--asof", default="09:26", help="回放时点 HH:MM（默认 09:26）")
    ap.add_argument("--tier", default="锐利", choices=["锐利", "实用"])
    ap.add_argument("--source", default="auto", choices=["auto", "sina", "local"],
                    help="数据源：auto（先新浪后本地）/ sina（线上1min·仅近~1.3天）/ local（本地minute_1min）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    res = replay(a.tier, a.date, a.asof, source=a.source)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        rp = res.get("replay") or {}
        if rp:
            print(f"🕰️ 回放 {rp['date']} {rp['asof']}｜池 as-of {rp['pool_day']}｜源 {rp.get('source','?')}｜"
                  f"取数 {rp['fetched']}/{rp['universe']}｜入榜 {rp['used']}")
        print(res.get("text") or f"（无输出 / {res.get('status')}：{res.get('reason','')}）")
    sys.exit(3 if res.get("status") in ("no_disk", "no_data") else 0)


def selftest():
    """故障注入：不联网、不读库，验证 竞价/盘中两档取点 + 复用 rank 排序。"""
    ok = []

    # 1) _point：集合竞价档（asof≤09:30）取首根 open
    bars = [{"day": "2026-10-12 09:31:00", "open": "25.69", "close": "26.85", "volume": "100"},
            {"day": "2026-10-12 09:32:00", "open": "26.85", "close": "27.44", "volume": "300"}]
    px, frac = _point(bars, 9 * 60 + 26)
    ok.append(("集合竞价档取首根open", abs(px - 25.69) < 1e-6 and abs(frac - 0.25) < 1e-6))

    # 2) _point：盘中档（asof>09:30）取 ≤asof 最后一根 close
    px2, frac2 = _point(bars, 9 * 60 + 31)
    ok.append(("盘中档取≤asof收盘", abs(px2 - 26.85) < 1e-6 and abs(frac2 - 0.25) < 1e-6))

    # 3) _point：asof 早于首根且 >09:30 → 退化取首根 open
    px3, _ = _point(bars, 9 * 60 + 31)
    ok.append(("无≤asof bar 退化不崩", isinstance(px3, float)))

    # 4) _point：空 bars → None
    ok.append(("空bars返回None", _point([], 9 * 60 + 26) is None))

    # 5) replay 对非法入参
    r = replay("锐利", None, "09:26")
    ok.append(("缺date拒出", r.get("status") == "no_data"))

    # 6) 🔴 截断检测（血证 2026-11-01：10-29 全池首根=13:37 → 午后价当竞价价）
    trunc_bars = [{"day": "2026-10-29 13:37:00", "open": "3.500", "close": "3.500", "volume": "28500"},
                  {"day": "2026-10-29 15:00:00", "open": "3.500", "close": "3.500", "volume": "306000"}]
    ok.append(("截断识别·午后起始", _truncated(trunc_bars, "2026-10-29") is True))
    # 7) 完整日不误报（新浪首根=09:31 / 本地首根=09:30）
    full_sina = [{"day": "2026-10-30 09:31:00", "open": "3.470", "close": "3.470", "volume": "1"},
                 {"day": "2026-10-30 15:00:00", "open": "3.260", "close": "3.260", "volume": "1"}]
    full_local = [{"day": "2026-10-29 09:30:00", "open": "3.19", "close": "3.19", "volume": "9998"},
                  {"day": "2026-10-29 15:00:00", "open": "3.50", "close": "3.50", "volume": "1"}]
    ok.append(("完整日不误报", _truncated(full_sina, "2026-10-30") is False
               and _truncated(full_local, "2026-10-29") is False))
    # 8) 空 bars 视为不可用
    ok.append(("空bars视为截断", _truncated([], "2026-10-29") is True))
    # 9) 尾部残缺（早停）也要识别
    early = [{"day": "2026-10-29 09:31:00", "open": "3.19", "close": "3.19", "volume": "1"},
             {"day": "2026-10-29 11:30:00", "open": "3.3", "close": "3.3", "volume": "1"}]
    ok.append(("尾部残缺识别", _truncated(early, "2026-10-29") is True))

    allok = all(v for _, v in ok)
    for n, v in ok:
        print(("✅" if v else "❌"), n)
    print(f"selftest {sum(v for _, v in ok)}/{len(ok)}")
    return 0 if allok else 1


if __name__ == "__main__":
    main()
