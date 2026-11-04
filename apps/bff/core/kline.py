"""分钟 K 线数据层：东财实时 → 本地 DuckDB → 本地 parquet 缓存 三层回退。

返回统一结构：
  {bars: [{t,o,h,l,c,v,a}], source, tf, tf_actual, note}
  - t: "2026-10-21 09:35"
  - v: 成交量（手）、a: 成交额（元）
"""
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"
EM_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TF_KLT = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}
TF_GROUP = {"1m": 1, "5m": 1, "15m": 3, "30m": 6, "60m": 12}   # 以 5min 为基
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60}

_LOCK = threading.Lock()
_EM_CACHE = {}      # (code, tf) -> (ts, payload)
_DB = {"con": None, "ts": 0.0, "err": None}
_CACHE_TTL = 25.0
_LIMIT = {"1m": 240, "5m": 240, "15m": 192, "30m": 192, "60m": 160}


def log(*a):
    print("[kline]", *a, flush=True)


def db_code(code6):
    c = str(code6).zfill(6)
    if c[0] in "69":
        return "sh" + c
    if c.startswith(("4", "8", "92")):
        return "bj" + c
    return "sz" + c


def secid(code6):
    c = str(code6).zfill(6)
    return ("1." if c[0] in "69" else "0.") + c


# ─────────────────────────── ① 东财实时 ───────────────────────────
def _em_kline(code6, tf, limit):
    params = {"secid": secid(code6), "klt": TF_KLT[tf], "fqt": 1,
              "beg": "0", "end": "20500101", "lmt": limit,
              "fields1": "f1,f2,f3,f4,f5,f6",
              "fields2": "f51,f52,f53,f54,f55,f56,f57"}
    url = EM_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": "https://quote.eastmoney.com/"})
    with urllib.request.urlopen(req, timeout=6) as r:
        js = json.loads(r.read().decode("utf-8", "ignore"))
    data = js.get("data") or {}
    kl = data.get("klines") or []
    if not kl:
        raise RuntimeError("东财返回空 K 线")
    bars = []
    for line in kl[-limit:]:
        p = line.split(",")
        if len(p) < 7:
            continue
        bars.append({"t": p[0], "o": float(p[1]), "c": float(p[2]), "h": float(p[3]),
                     "l": float(p[4]), "v": float(p[5]), "a": float(p[6])})
    return {"bars": bars, "source": "eastmoney", "tf": tf,
            "note": f"东财实时 · {data.get('name') or ''}"}


# ─────────────────────────── ② 本地 DuckDB ───────────────────────────
_DB_LOCK = threading.Lock()


def _db_kline(code6, tf, limit):
    """短连接：打开 → 查询 → 关闭。避免长期占用只读锁影响你的数据同步任务。"""
    if not os.path.exists(config.DUCKDB):
        raise RuntimeError("本地行情库未挂载")
    if not getattr(config, "KLINE_USE_DB", True):
        raise RuntimeError("KLINE_USE_DB=False")
    import duckdb
    dc = db_code(code6)
    n = limit if tf in ("1m", "5m") else limit * TF_GROUP[tf]
    table = "minute_1min" if tf == "1m" else "minute_5min"
    sql = (f"select dt as t, open as o, high as h, low as l, close as c, "
           f"volume as v, amount as a from {table} where code = ? order by dt desc limit ?")
    with _DB_LOCK:
        con = config.tune_duck(duckdb.connect(config.DUCKDB, read_only=True))
        try:
            df = con.execute(sql, [dc, int(n)]).df()
        finally:
            con.close()
    if len(df) == 0:
        raise RuntimeError(f"本地库无 {dc} 的 {table} 数据")
    df = df.sort_values("t").reset_index(drop=True)
    if tf in ("1m", "5m"):
        out = df
    else:
        g = TF_GROUP[tf]
        df["d"] = df["t"].dt.normalize()
        df["i"] = df.groupby("d", sort=False).cumcount() // g
        out = (df.groupby(["d", "i"], sort=False)
                 .agg(t=("t", "last"), o=("o", "first"), h=("h", "max"),
                      l=("l", "min"), c=("c", "last"), v=("v", "sum"), a=("a", "sum"))
                 .reset_index(drop=True)).tail(limit)
    bars = [{"t": pd.Timestamp(r.t).strftime("%Y-%m-%d %H:%M"),
             "o": float(r.o), "h": float(r.h), "l": float(r.l),
             "c": float(r.c), "v": float(r.v), "a": float(r.a)}
            for r in out.itertuples(index=False)]
    return {"bars": bars, "source": "local_db", "tf": tf,
            "note": f"本地 DuckDB · {table}（前复权未套用，原始价）"}


# ─────────────────────────── ③ 本地 parquet（最近 12 日 5min）────────
def _parquet_kline(code6, tf, limit):
    p = os.path.join(config.CACHE, "minute5.parquet")
    if not os.path.exists(p):
        raise RuntimeError("无本地分钟缓存")
    df = pd.read_parquet(p, columns=["code6", "dt", "open", "high", "low", "close", "volume", "amount"],
                         filters=[("code6", "==", str(code6))])
    if len(df) == 0:
        raise RuntimeError("本地缓存无该股票")
    df = df.sort_values("dt").rename(columns={"dt": "t", "open": "o", "high": "h",
                                              "low": "l", "close": "c", "volume": "v", "amount": "a"})
    df = df.reset_index(drop=True)
    actual = tf
    if tf == "1m":
        actual = "5m"          # 本地缓存只有 5min
    elif tf in ("15m", "30m", "60m"):
        g = TF_GROUP[tf]
        df["d"] = df["t"].dt.normalize()
        df["i"] = df.groupby("d", sort=False).cumcount() // g
        df = (df.groupby(["d", "i"], sort=False)
                .agg(t=("t", "last"), o=("o", "first"), h=("h", "max"),
                     l=("l", "min"), c=("c", "last"), v=("v", "sum"), a=("a", "sum"))
                .reset_index(drop=True))
    df = df.tail(limit)
    bars = [{"t": pd.Timestamp(r.t).strftime("%Y-%m-%d %H:%M"),
             "o": float(r.o), "h": float(r.h), "l": float(r.l),
             "c": float(r.c), "v": float(r.v), "a": float(r.a)}
            for r in df.itertuples(index=False)]
    return {"bars": bars, "source": "local_parquet", "tf": tf, "tf_actual": actual,
            "note": "本地缓存（最近 12 个交易日 5min）" + ("· 1分钟不可用，已降级为5分钟" if actual != tf else "")}


# ─────────────────────────── 对外 ───────────────────────────
def get_kline(code6, tf="5m", limit=None, force=False):
    tf = tf if tf in TF_KLT else "5m"
    limit = limit or _LIMIT[tf]
    key = (str(code6), tf, limit)
    now = time.time()
    if not force and key in _EM_CACHE and now - _EM_CACHE[key][0] < _CACHE_TTL:
        return _EM_CACHE[key][1]

    errors = []
    for fn in (_em_kline, _db_kline, _parquet_kline):
        try:
            data = fn(code6, tf, limit)
            if data.get("bars"):
                data.setdefault("tf_actual", tf)
                _EM_CACHE[key] = (now, data)
                if fn is not _em_kline:
                    log(f"{code6} {tf}: {data['source']} ({len(data['bars'])} bars)")
                return data
        except Exception as e:  # noqa: BLE001
            errors.append(f"{fn.__name__}: {type(e).__name__}: {e}")
    return {"bars": [], "source": "none", "tf": tf, "tf_actual": tf,
            "note": "K 线不可用：" + " | ".join(errors[-2:])}


def reset_db():
    """兼容旧调用；短连接模式下无需处理。"""
    return None
