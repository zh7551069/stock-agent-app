"""实时行情层（东财 push2）——供「盘中」策略使用。

能力：
  - 全市场快照（一次请求 ~5900 只）：现价/涨跌幅/开高低/昨收/量比/涨速/成交额/流通市值/行业
  - 涨停/炸板候选股的当日 5min K 线（用于「10 点前首触」「炸板回封」）
  - 交易时段判定（盘前/竞价/早盘/午休/午后/尾盘/已收盘/非交易日）

设计：
  - 快照 TTL 默认 12s；5min K 线 TTL 60s（只对涨停候选拉，约 50~100 只）
  - 全部对齐到 base_ctx.codes（N 长数组），策略无需改索引
  - 任何网络异常 → available=False，调用方回退到本地历史回放
  - 环境变量 STOCK_ADVICE_REALTIME=0 可强制关闭（用于回退验证）
"""
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

try:
    from zoneinfo import ZoneInfo
    CN_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    CN_TZ = None

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
REFERER = "https://quote.eastmoney.com/"
SNAP_URL = "https://push2.eastmoney.com/api/qt/clist/get"
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

SNAP_TTL = float(os.environ.get("STOCK_ADVICE_REALTIME_TTL", str(getattr(config, "REALTIME_TTL", 12))))
BARS_TTL = 60.0
ENABLED = os.environ.get("STOCK_ADVICE_REALTIME", "1" if getattr(config, "REALTIME", True) else "0") != "0"

FIELDS = ",".join(["f2", "f3", "f4", "f5", "f6", "f7", "f8", "f10", "f12", "f14",
                   "f15", "f16", "f17", "f18", "f20", "f21", "f22", "f62", "f100", "f124"])
FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

_LOCK = threading.Lock()
_INDEX = {"ts": 0, "data": None}
# 失败退避：被限流时别再撞接口（30s → 60s → … → 10min 上限）
_BACKOFF = {"until": 0.0, "fails": 0, "last_fail": 0.0}

# ── 全局限速与请求计数（防封）──────────────────────────────────
# 腾讯/东财都没有公开配额，但「每秒几次」和「每分钟几百次」是两个量级：
# 这里按主机做令牌桶排队（默认 8 req/s），并把计数暴露到 /api/overview 的 live_status。
# 想更保守：STOCK_ADVICE_PUSH_QPS=4 环境变量调低即可，不用改代码。
NET_LOCK = threading.Lock()
_NET = {"count": {}, "rate": {}, "since": time.time()}


def _qps():
    try:
        return max(1.0, float(os.environ.get("STOCK_ADVICE_PUSH_QPS", "8")))
    except Exception:
        return 8.0


_BURST = 10.0          # 令牌桶容量：允许「一次刷新」的并发批直接放行


def _throttle(url):
    """按主机令牌桶限速（平滑排队，不丢请求），并累加请求计数。

    效果：一次全市场刷新（15 个 400 只的批）里前 10 个立刻发出、其余按 8 req/s
    排队 ≈ 0.6s 内发完；持续速率被压在 8 req/s，远低于「被封」的量级。
    """
    host = urllib.parse.urlparse(url).netloc or "-"
    qps = _qps()
    with NET_LOCK:
        st = _NET["rate"].setdefault(host, {"tokens": _BURST, "ts": time.time()})
        now = time.time()
        st["tokens"] = min(_BURST, st["tokens"] + (now - st["ts"]) * qps)
        st["ts"] = now
        if st["tokens"] >= 1.0:
            st["tokens"] -= 1.0
            wait = 0.0
        else:
            wait = (1.0 - st["tokens"]) / qps
            st["tokens"] = 0.0
        _NET["count"][host] = _NET["count"].get(host, 0) + 1
    if wait > 0:
        time.sleep(min(wait, 1.0))


def net_stats():
    with NET_LOCK:
        return {"since": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_NET["since"])),
                "elapsed_s": round(time.time() - _NET["since"], 1),
                "count": dict(_NET["count"]),
                "qps_cap": _qps()}
SNAP_CACHE = os.path.join(config.CACHE, "realtime_snapshot.parquet")
SNAP_META = os.path.join(config.CACHE, "realtime_snapshot.json")


def _backoff_wait():
    left = _BACKOFF["until"] - time.time()
    if left > 0:
        raise RuntimeError(f"实时源冷却中（{int(left)}s 后重试）")


def _mark_fail(e):
    _BACKOFF["fails"] = min(_BACKOFF["fails"] + 1, 6)
    _BACKOFF["until"] = time.time() + min(600, 30 * (2 ** (_BACKOFF["fails"] - 1)))
    _BACKOFF["last_fail"] = time.time()
    with _LOCK:
        _SNAP["err"] = f"{type(e).__name__}: {e}"


def _mark_ok():
    # 10 分钟内失败过就保留现有退避等级：否则「失败→成功→失败」每次都被清零，
    # 退避永远停在 30s，限流时照样一直撞接口。
    if time.time() - _BACKOFF.get("last_fail", 0.0) > 600:
        _BACKOFF["fails"] = 0
        _BACKOFF["until"] = 0.0


def _save_snapshot(df):
    try:
        out = df.reset_index(drop=True)
        out.to_parquet(SNAP_CACHE, index=False)
        q = df.attrs.get("quote_time")
        json.dump({"quote_time": q.strftime("%Y-%m-%d %H:%M:%S") if q else None,
                   "saved_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                   "rows": int(len(df))},
                  open(SNAP_META, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def _load_snapshot():
    """读取最近一次成功快照（离线/限流兜底）"""
    try:
        if not (os.path.exists(SNAP_CACHE) and os.path.exists(SNAP_META)):
            return None, None
        meta = json.load(open(SNAP_META, encoding="utf-8"))
        df = pd.read_parquet(SNAP_CACHE)
        df["code6"] = df["code6"].astype(str).str.zfill(6)
        df = df.drop_duplicates("code6").set_index("code6", drop=False)
        q = None
        if meta.get("quote_time"):
            q = datetime.strptime(meta["quote_time"], "%Y-%m-%d %H:%M:%S")
            q = q.replace(tzinfo=CN_TZ) if CN_TZ else q
        df.attrs["quote_time"] = q
        df.attrs["saved_at"] = meta.get("saved_at")
        return df, meta
    except Exception:
        return None, None
_SNAP = {"ts": 0, "df": None, "err": None, "key": None, "source": None}
_BARS = {"ts": 0, "key": None, "data": {}, "err": None}


# ─────────────────────────── 基础工具 ───────────────────────────
def now_cn():
    return datetime.now(CN_TZ) if CN_TZ else datetime.now()


def clock_phase(now=None):
    now = now or now_cn()
    if now.weekday() >= 5:
        return "非交易日"
    t = now.time()
    if t < dtime(9, 15):
        return "盘前"
    if t < dtime(9, 25):
        return "集合竞价"
    if t < dtime(9, 30):
        return "竞价撮合"
    if t < dtime(11, 30):
        return "早盘"
    if t < dtime(13, 0):
        return "午休"
    if t < dtime(14, 57):
        return "午后"
    if t < dtime(15, 0):
        return "尾盘竞价"
    return "已收盘"


def is_trading_phase(phase):
    return phase in ("集合竞价", "竞价撮合", "早盘", "午后", "尾盘竞价")


def _get(url, timeout=6):
    _throttle(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER,
                                               "Accept": "application/json,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def _num(v):
    try:
        if v in ("-", "", None):
            return np.nan
        return float(v)
    except Exception:
        return np.nan


def secid(code6):
    c = str(code6).zfill(6)
    return ("1." if c.startswith(("6", "9")) else "0.") + c


def board_pct(code6, name=""):
    """涨跌停幅度（与本地 limit_price 口径一致；ST 5%，双创 20%，北交所 30%）"""
    c = str(code6).zfill(6)
    if "ST" in str(name) or str(name).startswith("*"):
        return 0.05
    if c.startswith(("300", "301", "688", "689")):
        return 0.20
    if c.startswith(("4", "8", "92")):
        return 0.30
    return 0.10


# ─────────────────────── 腾讯实时源（主源，2026-11-03 接入）───────────────────────
# 为什么加腾讯：东财 push2 的 clist / stock 接口在部分网络环境会被直接 reset
# （实测 RemoteDisconnected → 「盘中」长期静默回退到历史回放，排行榜看起来"全是历史数据"）。
# 腾讯 qt.gtimg.cn 批量报价稳定（400 只/请求、约 0.3s，全市场 15 个请求内），
# 且单行自带「均价线 / 涨停价 / 量比 / 流通市值」，正好覆盖盘中策略所需字段。
TENCENT_URL = "https://qt.gtimg.cn/q="
TENCENT_REFERER = "https://gu.qq.com/"


def tq_code(code6):
    """6 位代码 → 腾讯前缀代码（sh / sz / bj）"""
    c = str(code6).zfill(6)
    if c.startswith(("6", "9")):
        return "sh" + c
    if c.startswith(("4", "8")):
        return "bj" + c
    return "sz" + c


def _get_tencent(url, timeout=8):
    _throttle(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": TENCENT_REFERER})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("gbk", "ignore")


def _tf(v):
    try:
        return float(v)
    except Exception:
        return np.nan


def _parse_tencent_line(line):
    """腾讯单行行情 → dict（字段位按 2026-11-03 实测校准，见 realtime.py 文档）

    v_sh600000="1~浦发银行~600000~10.06~10.02~9.98~1164223~...";
      3 现价 4 昨收 5 今开 6 成交量(手) 30 时间 31 涨跌 32 涨跌%
      33 最高 34 最低 35 价格/量/额 37 成交额(万) 38 换手 43 振幅
      44 流通市值(亿) 45 总市值(亿) 47 涨停价 48 跌停价 49 量比 51 均价
    """
    if "~" not in line or "=" not in line:
        return None
    f = line.split("=", 1)[1].strip().strip(";").strip('"').split("~")
    if len(f) < 52:
        return None
    px, prev = _tf(f[3]), _tf(f[4])
    if not (np.isfinite(px) and px > 0 and np.isfinite(prev) and prev > 0):
        return None
    amt = np.nan
    try:
        amt = float(str(f[35]).split("/")[2])
    except Exception:
        amt = _tf(f[37]) * 1e4
    return {
        "code6": str(f[2]).zfill(6), "name": f[1],
        "price": px, "prev_close": prev, "open": _tf(f[5]),
        "high": _tf(f[33]), "low": _tf(f[34]),
        "volume": _tf(f[6]), "amount": amt,
        "pct": _tf(f[32]), "chg": _tf(f[31]),
        "turnover": _tf(f[38]), "vol_ratio": _tf(f[49]), "amplitude": _tf(f[43]),
        "circ_mv": _tf(f[44]) * 1e8, "total_mv": _tf(f[45]) * 1e8,
        "limit_up": _tf(f[47]), "limit_down": _tf(f[48]),
        "vwap": _tf(f[51]), "speed": np.nan, "main_net": np.nan, "industry": None,
        "_ts": str(f[30]),
    }


def _parse_tencent_quote_time(rows):
    best, ts = None, np.nan
    for r in rows:
        s = str(r.get("_ts") or "")
        if len(s) < 14:
            continue
        try:
            d = datetime.strptime(s[:14], "%Y%m%d%H%M%S")
        except Exception:
            continue
        if best is None or d > best:
            best = d
    if best is not None:
        if CN_TZ:
            best = best.replace(tzinfo=CN_TZ)
        ts = best.timestamp()
    return best, ts


def _fetch_tencent(codes6):
    """腾讯批量报价 → DataFrame（code6 索引；字段与东财口径对齐）"""
    codes6 = [str(c).zfill(6) for c in dict.fromkeys(codes6)]
    CHUNK = 400
    chunks = [codes6[i:i + CHUNK] for i in range(0, len(codes6), CHUNK)]

    def one(chunk):
        txt = _get_tencent(TENCENT_URL + ",".join(tq_code(c) for c in chunk), timeout=10)
        out = []
        for line in txt.split(";"):
            r = _parse_tencent_line(line)
            if r:
                out.append(r)
        return out

    rows = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for part in ex.map(one, chunks):
            rows.extend(part or [])
    if len(rows) < max(20, int(len(codes6) * 0.5)):
        raise RuntimeError(f"腾讯快照覆盖不足：{len(rows)}/{len(codes6)}")
    qt, ts = _parse_tencent_quote_time(rows)
    df = pd.DataFrame(rows).drop(columns=["_ts"], errors="ignore")
    df["ts"] = ts
    # 退市/停牌股腾讯会返回 -1 涨停价、0 均价 → 统一置空，避免被当成有效值参与可买性判断
    for c in ("limit_up", "limit_down", "vwap", "circ_mv", "vol_ratio", "amount"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
            df.loc[df[c] <= 0, c] = np.nan
    df["code6"] = df["code6"].astype(str).str.zfill(6)
    df = df.drop_duplicates("code6").set_index("code6", drop=False)
    df.attrs["quote_time"] = qt
    return df


def _fetch_eastmoney():
    """东财 push2 clist 全市场快照（备用源；部分网络会被 reset）"""
    PAGE = 100          # 东财 clist 目前强制每页 100 条
    last = None

    def page_url(pn):
        params = {"pn": pn, "pz": PAGE, "po": 1, "np": 1, "fltt": 2, "invt": 2,
                  "fid": "f3", "fs": FS, "fields": FIELDS,
                  "ut": "bd1d9ddb04089700cf9c27f6f7426281"}
        return SNAP_URL + "?" + urllib.parse.urlencode(params)

    def fetch_page(pn):
        js = json.loads(_get(page_url(pn), timeout=8))
        d = js.get("data") or {}
        return int(d.get("total") or 0), (d.get("diff") or [])

    for _ in range(2):
        try:
            total, first = fetch_page(1)
            if not first:
                raise RuntimeError("快照为空")
            pages = max(1, (total + PAGE - 1) // PAGE)
            rest = []
            if pages > 1:
                with ThreadPoolExecutor(max_workers=6) as ex:
                    for _, rows in ex.map(fetch_page, range(2, pages + 1)):
                        rest.extend(rows or [])
            diff = first + rest
            if not diff:
                raise RuntimeError("快照为空")
            df = pd.DataFrame(diff)
            df = df.rename(columns={
                "f2": "price", "f3": "pct", "f4": "chg", "f5": "volume", "f6": "amount",
                "f7": "amplitude", "f8": "turnover", "f10": "vol_ratio", "f12": "code6",
                "f14": "name", "f15": "high", "f16": "low", "f17": "open",
                "f18": "prev_close", "f20": "total_mv", "f21": "circ_mv",
                "f22": "speed", "f62": "main_net", "f100": "industry", "f124": "ts"})
            num_cols = ["price", "pct", "chg", "volume", "amount", "amplitude", "turnover",
                        "vol_ratio", "high", "low", "open", "prev_close", "total_mv",
                        "circ_mv", "speed", "main_net"]
            for c in num_cols:
                if c in df.columns:
                    df[c] = df[c].map(_num)
            df["code6"] = df["code6"].astype(str).str.zfill(6)
            df = df.drop_duplicates("code6").set_index("code6", drop=False)
            ts = df["ts"].map(_num)
            qt = None
            if ts.notna().any():
                qt = datetime.fromtimestamp(float(ts.max()), CN_TZ) if CN_TZ else \
                    datetime.fromtimestamp(float(ts.max()))
            df.attrs["quote_time"] = qt
            # 东财快照没有均价线 → 用 成交额÷(成交量×100) 现算（与 LiveMarket 口径一致）
            with np.errstate(invalid="ignore", divide="ignore"):
                vw = pd.to_numeric(df["amount"], errors="coerce") / (
                    pd.to_numeric(df["volume"], errors="coerce") * 100.0)
            df["vwap"] = vw
            return df
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4)
    raise last if last else RuntimeError("东财快照失败")


# ─────────────────────────── 快照 ───────────────────────────
def fetch_snapshot(force=False, codes=None):
    """全市场快照 → DataFrame（code6 索引）。腾讯优先，东财兜底。

    codes: base_ctx.codes（腾讯源需要显式代码表；不传则只能走东财）
    """
    now = time.time()
    key = len(codes) if codes is not None else None
    if (not force and _SNAP["df"] is not None and now - _SNAP["ts"] < SNAP_TTL
            and _SNAP.get("key") == key):
        return _SNAP["df"]
    if not ENABLED:
        raise RuntimeError("实时行情已禁用（STOCK_ADVICE_REALTIME=0）")
    _backoff_wait()
    errs, df, src = [], None, None
    if codes is not None:
        try:
            df, src = _fetch_tencent(codes), "tencent.qt"
        except Exception as e:  # noqa: BLE001
            errs.append(f"tencent: {type(e).__name__}: {e}")
    if df is None:
        try:
            df, src = _fetch_eastmoney(), "eastmoney.push2"
        except Exception as e:  # noqa: BLE001
            errs.append(f"eastmoney: {type(e).__name__}: {e}")
    if df is None or not len(df):
        _mark_fail(RuntimeError("；".join(errs) or "快照为空"))
        raise RuntimeError("实时快照获取失败：" + ("；".join(errs) or "未知原因"))
    df.attrs["fetched_at"] = now
    df.attrs["source"] = src
    # 集合竞价（09:15~09:25、09:25~09:30 撮合前）全市场成交额为 0：
    # 这种快照没有成交价/均价线，既不能算盘中策略，也不该覆盖上一次有效快照。
    try:
        amt_sum = float(pd.to_numeric(df.get("amount"), errors="coerce").fillna(0).sum()) \
            if "amount" in df.columns else 0.0
    except Exception:
        amt_sum = 0.0
    df.attrs["traded"] = amt_sum > 0
    with _LOCK:
        _SNAP.update({"ts": now, "df": df, "err": None, "key": key, "source": src})
    _mark_ok()
    if df.attrs["traded"] or clock_phase() not in ("集合竞价", "竞价撮合"):
        _save_snapshot(df)
    return df


IDX_URL = "https://push2.eastmoney.com/api/qt/stock/get"


def _fetch_index_tencent():
    txt = _get_tencent(TENCENT_URL + "sh000001", timeout=8)
    row = None
    for line in txt.split(";"):
        row = _parse_tencent_line(line)
        if row:
            break
    if not row:
        raise RuntimeError("指数报价为空")
    qt, _ = _parse_tencent_quote_time([row])
    return {
        "name": row.get("name") or "上证指数",
        "price": row["price"], "high": row["high"], "low": row["low"],
        "open": row["open"], "prev_close": row["prev_close"], "pct": row["pct"],
        "quote_time": qt.strftime("%Y-%m-%d %H:%M:%S") if qt else
                      now_cn().strftime("%Y-%m-%d %H:%M:%S"),
    }


def fetch_index(force=False):
    """上证指数实时报价（用于大盘环境实时化）"""
    now = time.time()
    if not force and _INDEX["data"] is not None and now - _INDEX["ts"] < SNAP_TTL:
        return _INDEX["data"]
    if not ENABLED:
        raise RuntimeError("实时行情已禁用")
    out, err = None, None
    try:
        out = _fetch_index_tencent()
    except Exception as e:  # noqa: BLE001
        err = e
    if out is None:
        params = {"secid": "1.000001", "fltt": 2, "invt": 2,
                  "fields": "f43,f44,f45,f46,f57,f58,f60,f169,f170,f124",
                  "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
        js = json.loads(_get(IDX_URL + "?" + urllib.parse.urlencode(params), timeout=6))
        d = js.get("data") or {}
        out = {
            "name": d.get("f58") or "上证指数",
            "price": _num(d.get("f43")), "high": _num(d.get("f44")),
            "low": _num(d.get("f45")), "open": _num(d.get("f46")),
            "prev_close": _num(d.get("f60")), "pct": _num(d.get("f170")),
            "quote_time": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        }
    with _LOCK:
        _INDEX.update({"ts": now, "data": out})
    return out


# ─────────────────────────── 5min K 线 ───────────────────────────
TENCENT_BARS = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"


def _bars_tencent(c, today):
    """腾讯 5min K 线（返回 [(时间, o,h,l,c,v)]，只保留当日）"""
    url = f"{TENCENT_BARS}?param={tq_code(c)},m5,,60"
    js = json.loads(_get_tencent(url, timeout=8))
    d = (js.get("data") or {}).get(tq_code(c)) or {}
    m5 = d.get("m5") or []
    rows, tms = [], []
    for r in m5:
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            continue
        stamp = str(r[0])
        if not stamp.startswith(today):
            continue
        tms.append(stamp[8:10] + ":" + stamp[10:12])
        # [open, high, low, close, vol, amt]（与东财口径一致）
        rows.append([float(r[1]), float(r[3]), float(r[4]), float(r[2]),
                     float(r[5]), np.nan])
    return np.asarray(rows, np.float32), tms


def _bars_eastmoney(c, today):
    params = {"secid": secid(c), "klt": 5, "fqt": 1, "beg": today, "end": "20500101",
              "lmt": 60, "fields1": "f1,f2,f3,f4,f5,f6",
              "fields2": "f51,f52,f53,f54,f55,f56,f57",
              "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
    url = KLINE_URL + "?" + urllib.parse.urlencode(params)
    js = json.loads(_get(url, timeout=8))
    kl = ((js.get("data") or {}).get("klines")) or []
    rows, tms = [], []
    for line in kl:
        p = line.split(",")
        if len(p) < 7:
            continue
        tms.append(p[0].split(" ")[-1][:5])   # "2026-10-21 09:35" -> "09:35"
        rows.append([float(p[1]), float(p[3]), float(p[4]), float(p[2]),
                     float(p[5]), float(p[6])])
    return np.asarray(rows, np.float32), tms


BAR_CODE_TTL = 120.0      # 单只 5min K 的缓存秒数（K 线按 5 分钟更新，120s 足够）
BAR_MAX_FETCH = 60        # 单次刷新最多新拉多少只（封板数量爆炸时也不会打出一堆请求）
_BAR_CACHE = {}           # code6 -> (ts, rows|None, times|None)
_EMK = {"fails": 0, "until": 0.0}      # 东财 5min K 熔断状态


def fetch_bars(codes6, force=False):
    """批量取当日 5min K 线 → {code6: np.array(B,6) [open,high,low,close,vol,amt]}, {code6: [时间]}

    防封设计：按「单只」缓存 120s，只补新出现的封板股（增量为 0 时不发请求）；
    单次最多新拉 60 只。首触/回封只关心此刻仍封住的票，因此增量天然很小。
    """
    codes6 = [str(c).zfill(6) for c in dict.fromkeys(codes6)]
    if not codes6:
        return {}, {}
    if not ENABLED:
        raise RuntimeError("实时行情已禁用")
    now = time.time()
    today = now_cn().strftime("%Y%m%d")
    if _BARS.get("date") != today:          # 跨日清空
        _BAR_CACHE.clear()
        _BARS["date"] = today
    need = [c for c in codes6
            if force or c not in _BAR_CACHE or now - _BAR_CACHE[c][0] > BAR_CODE_TTL]
    need = need[:BAR_MAX_FETCH]

    def one(c):
        try:
            rows, tms = _bars_tencent(c, today)
            if len(rows):
                return c, (rows, tms)
        except Exception:
            pass
        # 东财 5min K 是备源：连续失败（典型是本机被 push2his 拒绝）就熔断 5 分钟，
        # 否则每只票都要多打一次注定失败的请求。
        if time.time() < _EMK["until"]:
            return c, None
        try:
            rows, tms = _bars_eastmoney(c, today)
            if len(rows):
                _EMK.update({"fails": 0, "until": 0.0})
                return c, (rows, tms)
        except Exception:
            _EMK["fails"] = min(_EMK.get("fails", 0) + 1, 6)
            if _EMK["fails"] >= 3:
                _EMK["until"] = time.time() + 300
            return c, None
        return c, None

    if need:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for c, r in ex.map(one, need):
                _BAR_CACHE[c] = (time.time(), r[0], r[1]) if r else (time.time(), None, None)

    out = {c: _BAR_CACHE[c][1] for c in codes6
           if c in _BAR_CACHE and _BAR_CACHE[c][1] is not None}
    times = {c: _BAR_CACHE[c][2] for c in out}
    with _LOCK:
        _BARS.update({"ts": now, "data": out, "times": times, "err": None})
    return out, times


# ─────────────────── 分时序列缓冲（盘中走势的唯一来源）───────────────────
# 设计：每次全市场快照成功，按「交易分钟」落一帧（close 价 / 成交额 / 均价线）。
# 由此得到任何窗口的涨速、加速度、均价线方向、量能脉冲——这些是历史日线里没有的，
# 也是「结合盘中实时走势」的落点。帧只存当日（换日自动清空），并每 3 分钟落盘兜底重启。
SER_PATH = os.path.join(config.CACHE, "realtime_series.npz")
SER_MAX = 260          # 最多保留 260 个交易分钟
SER_SAVE_EVERY = 180   # 至少隔 180s 才落盘一次
_SER = {"date": None, "codes": None, "frames": {}, "last_save": 0.0}


def trading_minute(qt):
    """行情时间 → 当日交易分钟序号（09:30=0，11:30=120，13:00=121，15:00=241）"""
    m = qt.hour * 60 + qt.minute
    if m <= 9 * 60 + 30:
        return 0
    if m <= 11 * 60 + 30:
        return m - (9 * 60 + 30)
    if m < 13 * 60:
        return 120
    if m <= 15 * 60:
        return 121 + (m - 13 * 60)
    return 241


def _ser_reset(date_str):
    _SER.update({"date": date_str, "frames": {}, "last_save": 0.0})


def _ser_load(date_str, ctx):
    """启动兜底：读回当日已落盘的分时序列（服务器重启后不丢早盘走势）"""
    try:
        if not os.path.exists(SER_PATH):
            return
        z = np.load(SER_PATH, allow_pickle=False)
        if str(z["date"]) != date_str:
            return
        codes = [str(c) for c in z["codes"]]
        if len(codes) != len(ctx.codes) or codes != [str(c) for c in ctx.codes]:
            return
        mins = z["min"].tolist()
        for i, m in enumerate(mins):
            _SER["frames"][int(m)] = (z["price"][i], z["amount"][i], z["vwap"][i])
        _SER["codes"] = tuple(codes)
    except Exception:
        pass


_SER_SAVING = {"busy": False}


def _ser_write(snap):
    try:
        frames = snap["frames"]
        mins = sorted(frames)
        price = np.stack([frames[m][0] for m in mins])
        amount = np.stack([frames[m][1] for m in mins])
        vwap = np.stack([frames[m][2] for m in mins])
        np.savez_compressed(SER_PATH, date=np.array(snap["date"]),
                            codes=np.array([str(c) for c in snap["codes"]]),
                            min=np.array(mins), price=price, amount=amount, vwap=vwap)
    except Exception:
        pass


def _ser_save(force=False):
    """落盘（后台线程，避免 16MB 压缩阻塞当次刷新请求）"""
    now = time.time()
    if not force and now - _SER["last_save"] < SER_SAVE_EVERY:
        return
    if not _SER["frames"] or _SER_SAVING["busy"]:
        return
    _SER["last_save"] = now
    snap = {"date": _SER["date"], "codes": tuple(_SER["codes"]),
            "frames": dict(_SER["frames"])}
    _SER_SAVING["busy"] = True

    def work():
        try:
            _ser_write(snap)
        finally:
            _SER_SAVING["busy"] = False

    threading.Thread(target=work, daemon=True).start()


def _aligned(df, ctx, col, dtype=np.float32):
    v = np.full(ctx.N, np.nan, np.float64)
    if df is not None and col in df.columns:
        try:
            v[:] = pd.to_numeric(df[col].reindex(ctx.codes), errors="coerce").to_numpy(np.float64)
        except Exception:
            pass
    return v.astype(dtype)


def _series_update(ctx, df, qt):
    """把本帧并入分时缓冲；返回 (features, meta)"""
    nan = lambda: np.full(ctx.N, np.nan, np.float64)  # noqa: E731
    feats = {k: nan() for k in ("mom1", "mom5", "mom15", "accel", "vwap_slope",
                                "vol_pulse", "pos", "dd", "rs")}
    meta = {"samples": 0, "minute": None, "date": None, "ready": False}
    if qt is None or df is None:
        return feats, meta
    today = now_cn().strftime("%Y%m%d")
    date_str = qt.strftime("%Y%m%d")
    meta["date"] = date_str
    # 只有「当日」行情才入缓冲；盘前/收盘后拿到的是上一交易日快照，不能污染走势
    if date_str != today:
        return feats, meta
    if _SER["date"] != date_str or _SER["codes"] is None or len(_SER["codes"]) != ctx.N:
        _ser_reset(date_str)
        _SER["codes"] = tuple(str(c) for c in ctx.codes)
        _ser_load(date_str, ctx)
    k = trading_minute(qt)
    meta["minute"] = k
    # 集合竞价（09:15~09:30）不落帧：否则竞价价会占掉 09:30 那一帧，5 分钟后算出的
    # 涨速会把「竞价→开盘」的跳空当成盘中动量。
    if qt.hour * 60 + qt.minute < 9 * 60 + 30:
        return feats, meta
    if k not in _SER["frames"]:
        _SER["frames"][k] = (_aligned(df, ctx, "price"),
                             _aligned(df, ctx, "amount"),
                             _aligned(df, ctx, "vwap"))
        for old in [m for m in _SER["frames"] if m < k - SER_MAX]:
            _SER["frames"].pop(old, None)
        _ser_save()
    frames = _SER["frames"]
    mins = sorted(frames)
    meta["samples"] = len(mins)
    if len(mins) < 2:
        return feats, meta
    cur = frames[mins[-1]]
    cur_k = mins[-1]
    meta["minute"] = cur_k
    prev_mins = [m for m in mins if m < cur_k]
    if not prev_mins:
        return feats, meta

    def pick(back):
        """挑一帧「距今最接近 back 分钟」的样本。

        手工刷新时帧是稀疏的（可能 2 分钟一帧，也可能 10 分钟才一帧），
        所以不要求正好 back 分钟：取最接近的一帧，再用真实间隔 gap 做折算。
        """
        cand = [m for m in prev_mins if 1 <= cur_k - m <= 20]
        if not cand:
            return None
        m = min(cand, key=lambda x: abs((cur_k - x) - back))
        return frames[m], max(1, cur_k - m)

    def rate(p, target):
        """按分钟折算的涨幅：gap 分钟涨了 raw%，折到 target 分钟，保证不同采样间隔下可比"""
        if p is None:
            return nan()
        f, gap = p
        with np.errstate(invalid="ignore", divide="ignore"):
            out = (cur[0].astype(np.float64) / f[0].astype(np.float64) - 1) * 100 * (target / gap)
        out[~np.isfinite(out)] = np.nan
        return out

    p1, p5, p15 = pick(1), pick(5), pick(15)
    feats["mom1"] = rate(p1, 1)
    feats["mom5"] = rate(p5, 5)
    feats["mom15"] = rate(p15, 15)
    meta["gap_min"] = p5[1] if p5 is not None else None
    # 加速度 = 近 5 分钟速度 − 近 15 分钟平均速度（每分钟百分点）
    feats["accel"] = np.nan_to_num(feats["mom5"]) / 5.0 - np.nan_to_num(feats["mom15"]) / 15.0
    feats["accel"][~np.isfinite(feats["mom5"])] = np.nan
    # 均价线方向：知识点-分时基础形态「均价线上升 + 放量突破 = 突破看多」（同样按分钟折算）
    if p5 is not None:
        f5, gap5 = p5
        with np.errstate(invalid="ignore", divide="ignore"):
            vs = (cur[2].astype(np.float64) / f5[2].astype(np.float64) - 1) * 100 * (5.0 / gap5)
        vs[~np.isfinite(vs)] = np.nan
        feats["vwap_slope"] = vs
        # 量能脉冲 = 这段窗口的成交额速率 ÷ 全天平均速率（>1 = 放量；无量纲，与采样间隔无关）
        with np.errstate(invalid="ignore", divide="ignore"):
            pulse = (cur[1].astype(np.float64) - f5[1].astype(np.float64)) / \
                    (cur[1].astype(np.float64) / max(cur_k, 1) * gap5)
        pulse[~np.isfinite(pulse)] = np.nan
        feats["vol_pulse"] = pulse
    # 日内位置 / 距日内高点回撤（来自快照的 high/low/price）
    price = _aligned(df, ctx, "price", np.float64)
    high = _aligned(df, ctx, "high", np.float64)
    low = _aligned(df, ctx, "low", np.float64)
    pct = _aligned(df, ctx, "pct", np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        rng = high - low
        pos = np.where(rng > 0, (price - low) / rng, np.nan)
        dd = np.where(high > 0, (high - price) / high * 100, np.nan)
    feats["pos"] = pos
    feats["dd"] = dd
    feats["rs"] = pct - np.nanmean(pct)          # 相对全市场（只做领涨股）
    meta["ready"] = bool(p5 is not None and np.isfinite(feats["mom5"]).any())
    return feats, meta


def trend_score(feats):
    """盘中走势分 0~100：均值 50，只用当日分时序列，不含未来函数。

    依据：知识点-分时基础形态与看盘口诀（均价线方向 + 量能）、
         知识点-分时做T正T六战法（白线上穿黄线放量）、
         知识点-只做领涨股（相对强度）、Tubbs 盘中六形态（时段强弱）。
    """
    n = np.nan_to_num
    sc = (50.0
          + 11 * np.tanh(n(feats["mom5"]) / 3.0)          # 5 分钟动量
          + 8 * np.tanh(n(feats["accel"]) / 1.2)          # 加速度
          + 10 * np.tanh(n(feats["vwap_slope"]) / 1.5)    # 均价线方向
          + 6 * np.tanh(n(feats["vol_pulse"], 0) - 1.0)   # 量能脉冲（放量）
          + 8 * (n(feats["pos"], 0.5) - 0.5)             # 日内位置
          + 6 * np.tanh(n(feats["rs"]) / 4.0))           # 相对大盘
    # 冲高回落 + 放量 = 诱多/出货（分时口诀 3/6：「流出量是否异常放大」是裁判）
    sc = np.where((n(feats["dd"]) > 2.0) & (n(feats["mom5"]) < 0) & (n(feats["vol_pulse"]) > 1.2),
                  sc - 7, sc)
    sc[~np.isfinite(feats["mom5"])] = np.nan
    return np.clip(sc, 0, 100)


# ─────────────────────────── 实时市场对象 ───────────────────────────

class LiveMarket:
    """把实时快照对齐到 base_ctx.codes 的 N 长数组，供盘中策略直接使用。"""

    def __init__(self, ctx, df, quote_time, phase, bars=None, bar_times=None, trend=None):
        self.ctx = ctx
        self.available = True
        self.source = (df.attrs.get("source") if df is not None else None) or "unknown"
        self.stale = False
        self.stale_since = None
        self.quote_time = quote_time
        self.fetch_time = now_cn()
        self.phase = phase
        self.is_trading = is_trading_phase(phase) and (
            quote_time is not None and quote_time.date() == now_cn().date())

        N = ctx.N
        self.N = N
        self.codes = ctx.codes
        self.pos = {c: i for i, c in enumerate(ctx.codes)}

        def arr(col, fill=np.nan):
            a = np.full(N, fill, np.float64)
            if col not in df.columns:
                return a
            v = df[col].reindex(ctx.codes)
            a[:] = pd.to_numeric(v, errors="coerce").to_numpy(np.float64)
            return a

        self.price = arr("price")
        self.pct = arr("pct")
        self.high = arr("high")
        self.low = arr("low")
        self.open = arr("open")
        self.prev_close = arr("prev_close")
        self.volume = arr("volume")       # 手
        self.amount = arr("amount")       # 元
        self.turnover = arr("turnover")
        self.vol_ratio = arr("vol_ratio")
        self.speed = arr("speed")
        self.main_net = arr("main_net")
        self.circ_mv = arr("circ_mv") / 1e8   # 亿
        self.amplitude = arr("amplitude")
        # 均价线：腾讯直接给「均价」（f51），比 成交额÷(成交量×100) 更准；缺失才现算
        if "vwap" in df.columns:
            self.vwap = arr("vwap")
            self.vwap[~np.isfinite(self.vwap) | (self.vwap <= 0)] = np.nan
        else:
            with np.errstate(invalid="ignore", divide="ignore"):
                self.vwap = self.amount / (self.volume * 100.0)
            self.vwap[~np.isfinite(self.vwap) | (self.vwap <= 0)] = np.nan
        # 行业用实时快照的 f100（比本地 industry 更新），缺失回退本地
        ind_col = df["industry"] if "industry" in df.columns else pd.Series(dtype=object)
        self.industry = [ind_col.get(c) if c in df.index else None for c in ctx.codes]
        self.industry = [i if isinstance(i, str) and i and i != "-" else ctx.industry.get(c, "未知")
                         for i, c in zip(self.industry, ctx.codes)]
        # 涨停/跌停价：腾讯快照自带交易所口径的涨停价，缺失才按板块幅度推算
        if "limit_up" in df.columns:
            self.limit_up = arr("limit_up")
            self.limit_down = arr("limit_down")
        else:
            pct_lim = np.array([board_pct(c, df["name"].get(c, "") if c in df.index else ctx.name.get(c, ""))
                                for c in ctx.codes])
            with np.errstate(invalid="ignore"):
                self.limit_up = np.round(self.prev_close * (1 + pct_lim), 2)
                self.limit_down = np.round(self.prev_close * (1 - pct_lim), 2)
        self.at_limit = np.isfinite(self.limit_up) & (self.price >= self.limit_up - 0.005)
        self.touched_limit = np.isfinite(self.limit_up) & (
            (self.high >= self.limit_up - 0.005) | self.at_limit)
        # ── 可买性（涨停买不到，是盘中推荐的核心可用性问题）──────────
        with np.errstate(invalid="ignore", divide="ignore"):
            self.limit_gap = (self.limit_up / self.price - 1) * 100   # 距涨停还有多少 %
        self.sealed = self.at_limit
        self.one_word = self.sealed & (np.abs(self.high - self.low) < 1e-6)
        rank = np.full(N, 0, np.int8)
        rank[self.sealed] = 2
        rank[np.isfinite(self.limit_gap) & (self.limit_gap <= 2.0) & ~self.sealed] = 1
        rank[self.one_word] = 3
        self.tradable_rank = rank          # 0=可买 1=临界(距涨停≤2%) 2=已封板 3=一字板
        label = {0: "可买", 1: "临界", 2: "已封板", 3: "一字板"}
        names = np.array([label[int(i)] for i in rank])
        self.tradable = names
        # 5min K 线（只对涨停候选拉）
        self.bars = {}          # pos -> np.array
        self.bar_times = {}     # pos -> [时间]
        if bars:
            for c, b in bars.items():
                p = self.pos.get(c)
                if p is not None:
                    self.bars[p] = b
                    self.bar_times[p] = (bar_times or {}).get(c, [])
        # ── 盘中走势（分时序列派生；无序列时全 NaN，策略自动退化）──────────
        trend = trend or {}
        feats = trend.get("features") or {}
        self.trend_meta = {"samples": trend.get("samples", 0),
                           "ready": bool(trend.get("ready")),
                           "minute": trend.get("minute"),
                           "gap_min": trend.get("gap_min")}
        for k in ("mom1", "mom5", "mom15", "accel", "vwap_slope",
                  "vol_pulse", "pos", "dd", "rs"):
            setattr(self, f"t_{k}", feats.get(k, np.full(N, np.nan)))
        self.trend = trend_score(feats) if trend.get("ready") else np.full(N, np.nan)
        # 涨速：统一用「近 5 分钟涨幅」——东财 f22 本身就是 5 分钟涨速，
        # 腾讯不提供该字段，因此由分时缓冲计算，口径一致。
        if trend.get("ready"):
            m5 = feats.get("mom5")
            if m5 is not None:
                self.speed = np.where(np.isfinite(m5), m5, self.speed)
        # 走势标签（给前端一行话说清"强在哪/弱在哪"）
        self.trend_label = self._trend_labels()

    def _trend_labels(self):
        N = self.N
        lbl = np.array([""] * N, dtype=object)
        m5, ac, vs = self.t_mom5, self.t_accel, self.t_vwap_slope
        dd, pos = self.t_dd, self.t_pos
        for i in range(N):
            if not np.isfinite(m5[i]):
                continue
            if dd[i] > 2.0 and m5[i] < 0:
                lbl[i] = "冲高回落"
            elif m5[i] >= 1.0 and ac[i] > 0:
                lbl[i] = "加速上攻"
            elif m5[i] > 0.3 and vs[i] > 0:
                lbl[i] = "均价线走强"
            elif m5[i] <= -0.5:
                lbl[i] = "盘中转弱"
            elif pos[i] >= 0.8 and vs[i] >= 0:
                lbl[i] = "高位横盘"
            else:
                lbl[i] = "走平"
        return lbl

    @property
    def meta(self):
        src = self.source
        return {
            "source": "cache" if self.stale else "realtime",
            "quote_time": self.quote_time.strftime("%Y-%m-%d %H:%M:%S") if self.quote_time else None,
            "fetch_time": self.fetch_time.strftime("%H:%M:%S"),
            "phase": self.phase,
            "is_trading": bool(self.is_trading),
            "bars": len(self.bars),
            "code": ("cache" if self.stale else src),
            "stale": bool(self.stale),
            "stale_since": self.stale_since,
            "trend": {"samples": self.trend_meta.get("samples", 0),
                      "ready": bool(self.trend_meta.get("ready")),
                      "minute": self.trend_meta.get("minute"),
                      "gap_min": self.trend_meta.get("gap_min")},
        }

    def universe(self, min_amount=3e7, min_price=2.0, drop_bj=True, drop_st=True):
        ok = (np.isfinite(self.price) & (self.price >= min_price)
              & np.isfinite(self.amount) & (self.amount >= min_amount)
              & np.isfinite(self.prev_close) & (self.prev_close > 0))
        for i, c in enumerate(self.codes):
            nm = str(self.ctx.name.get(c, ""))
            if "退" in nm:
                ok[i] = False
            if drop_st and ("ST" in nm):
                ok[i] = False
            if drop_bj and c.startswith(("8", "4", "92")):
                ok[i] = False
        return ok

    def rs(self, lookback=252):
        """52 周相对强度：本地历史收盘 + 实时现价"""
        base = self.ctx
        if base.T <= lookback:
            return np.full(self.N, np.nan)
        hist = base.M["close"][-1 - lookback]
        live = np.where(np.isfinite(self.price), self.price, base.M["close"][-1])
        with np.errstate(invalid="ignore", divide="ignore"):
            r = live / hist - 1
        s = pd.Series(r)
        return (s.rank(pct=True, na_option="keep").to_numpy() * 100)


# ─────────────────────────── 对外入口 ───────────────────────────
_LIVE = {"ts": 0, "obj": None, "err": None}


def get_live(ctx, force=False):
    """带 TTL 的 LiveMarket；失败返回 None（调用方回退本地回放）"""
    now = time.time()
    if not ENABLED:
        return None
    if not force and _LIVE["obj"] is not None and now - _LIVE["ts"] < SNAP_TTL:
        return _LIVE["obj"]
    phase = clock_phase()
    try:
        df = fetch_snapshot(force=force, codes=list(ctx.codes))
        # 只对涨停/触板候选拉 5min（用于首触时间 / 炸板回封）
        if "limit_up" in df.columns and df["limit_up"].notna().any():
            lu = pd.to_numeric(df["limit_up"], errors="coerce").to_numpy()
        else:
            pct_lim = np.array([board_pct(c, df["name"].get(c, "") if c in df.index else "")
                                for c in df.index])
            lu = np.round(pd.to_numeric(df["prev_close"], errors="coerce").to_numpy()
                          * (1 + pct_lim), 2)
        price = pd.to_numeric(df["price"], errors="coerce").to_numpy()
        high = pd.to_numeric(df["high"], errors="coerce").to_numpy()
        touched = np.isfinite(lu) & ((high >= lu - 0.005) | (price >= lu - 0.005))
        # 5min K 只给「当前封住」的票拉：首触/回封两条事件策略都要求此刻仍在板上，
        # 这样能把 160 只的请求量压到几十只（每只一次请求，10 并发）。
        sealed_now = np.isfinite(lu) & (price >= lu - 0.005)
        codes_bars = list(df.index[sealed_now])[:BAR_MAX_FETCH]
        if len(codes_bars) < 5:
            codes_bars = list(df.index[touched])[:40]
        # 盘前/收盘后拿到的是上一交易日快照：5min K 拉回来也是空的，直接跳过省请求
        qt_now = df.attrs.get("quote_time")
        if not df.attrs.get("traded", True) and phase in ("集合竞价", "竞价撮合"):
            with _LOCK:
                _LIVE["err"] = ("集合竞价未撮合（全市场暂无成交），先展示上一交易日快照"
                                "；09:25 撮合后自动切回实时")
            return None
        same_day = qt_now is not None and qt_now.date() == now_cn().date()
        bars, times = ({}, {})
        # 集合竞价（09:15~09:30）当日 K 线还是空的，拉一次要 2 倍请求（腾讯空 → 东财兜底）
        if codes_bars and same_day and phase not in ("集合竞价", "竞价撮合"):
            try:
                bars, times = fetch_bars(codes_bars)
            except Exception:
                bars, times = {}, {}
        # 分时走势：本帧入缓冲 → 计算动量/均价线方向/量能脉冲等
        feats, tmeta = _series_update(ctx, df, df.attrs.get("quote_time"))
        trend = {"features": feats, **tmeta}
        obj = LiveMarket(ctx, df, df.attrs.get("quote_time"), phase, bars, times, trend)
        with _LOCK:
            _LIVE.update({"ts": now, "obj": obj, "err": None})
        return obj
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            _LIVE["err"] = f"{type(e).__name__}: {e}"
        # 兜底：用最近一次成功落盘的快照（标注 stale）
        df, meta = _load_snapshot()
        if df is None or len(df) == 0:
            return None
        try:
            obj = LiveMarket(ctx, df, df.attrs.get("quote_time"), "快照缓存", {}, {}, None)
            obj.source = (df.attrs.get("source") or "eastmoney") + ".cache"
            obj.stale = True
            obj.stale_since = meta.get("saved_at")
            with _LOCK:
                _LIVE.update({"ts": now, "obj": obj, "err": _LIVE.get("err")})
            return obj
        except Exception:
            return None


def status():
    obj = _LIVE.get("obj")
    return {"enabled": ENABLED, "snapshot_age": round(time.time() - _SNAP["ts"], 1)
            if _SNAP["ts"] else None,
            "error": _SNAP["err"] or _LIVE["err"],
            "source": _SNAP.get("source"),
            "trend_samples": (getattr(obj, "trend_meta", {}) or {}).get("samples") if obj else 0,
            "trend_gap_min": (getattr(obj, "trend_meta", {}) or {}).get("gap_min") if obj else None,
            "backoff_fails": _BACKOFF["fails"],
            "net": net_stats(),
            "phase": clock_phase(), "now": now_cn().strftime("%Y-%m-%d %H:%M:%S")}
