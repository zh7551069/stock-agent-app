"""数据访问层：把 parquet 缓存加载成矩阵，供策略引擎使用。

单例加载一次（约 2~3 秒），后续请求复用。
"""
import json
import os
import sys
import threading

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def _p(name):
    return os.path.join(config.CACHE, f"{name}.parquet")


class Ctx:
    def __init__(self):
        man = json.load(open(os.path.join(config.CACHE, "manifest.json"), encoding="utf-8"))
        self.manifest = man
        self.daily = pd.read_parquet(_p("daily"))
        self.mv = pd.read_parquet(_p("mv"))
        self.limit = pd.read_parquet(_p("limit_price"))
        self.info = pd.read_parquet(_p("stock_info"))
        self.index = pd.read_parquet(_p("index_daily"))
        for opt in ["zt_limit_up", "zt_ladder", "zt_broken", "zt_auction", "zt_pool_up",
                    "lhb_daily", "lhb_inst", "moneyflow", "stock_flow",
                    "board_flow", "industry_flow", "suspension"]:
            try:
                setattr(self, opt, pd.read_parquet(_p(opt)))
            except Exception:
                setattr(self, opt, pd.DataFrame())
        self.minute5 = pd.read_parquet(_p("minute5"))
        self.trade_cal = pd.read_parquet(_p("trade_cal"))

        self.daily["date"] = pd.to_datetime(self.daily["date"])
        self.dates = np.array(sorted(self.daily["date"].unique()))
        self.last_date = self.dates[-1]
        self.last_date_str = pd.Timestamp(self.last_date).strftime("%Y-%m-%d")

        self._build_matrices()
        self._build_meta()
        self._build_minute()
        self._build_frames()

    # ---------------- 矩阵 ----------------
    def _build_matrices(self):
        d = self.daily
        codes = np.array(sorted(d["code6"].dropna().unique().astype(str)))
        self.codes = codes
        ci = {c: i for i, c in enumerate(codes)}
        di = {dt: i for i, dt in enumerate(self.dates)}
        T, N = len(self.dates), len(codes)
        self.T, self.N = T, N
        rows = d["date"].map(di).to_numpy()
        cols = d["code6"].astype(str).map(ci).to_numpy()
        keep = pd.notna(rows) & pd.notna(cols)
        rows, cols = rows[keep].astype(int), cols[keep].astype(int)
        self.M = {}
        for f in ["open", "high", "low", "close", "vol_hand", "amount_k", "pct_chg",
                  "turnover", "vol_ratio", "ma5", "ma10", "ma20", "ma60", "atr",
                  "rsi6", "rsi12", "kdj_k", "macd", "new_high_days", "new_low_days",
                  "boll_mid", "boll_up", "boll_low"]:
            if f not in d.columns:
                continue
            m = np.full((T, N), np.nan, np.float32)
            m[rows, cols] = d[f].to_numpy(np.float32)[keep]
            self.M[f] = m
        # 量能 5 日均值
        vol = self.M["vol_hand"]
        self.M["vol_ma5"] = pd.DataFrame(vol).rolling(5, min_periods=3).mean().to_numpy(np.float32)
        self.M["vol_ma20"] = pd.DataFrame(vol).rolling(20, min_periods=10).mean().to_numpy(np.float32)

    def _build_meta(self):
        info = self.info.drop_duplicates("code6").set_index("code6")
        self.name = info["name"].to_dict()
        self.industry = info["industry"].fillna("未知").to_dict()
        self.ts_code = info["ts_code"].to_dict()
        self.list_status = info["list_status"].to_dict()
        self.list_date = info.get("list_date", pd.Series(dtype=object)).to_dict()
        # 市值（最近一日）
        mv = self.mv[self.mv["date"] == pd.Timestamp(self.last_date)]
        self.circ_mv = dict(zip(mv["code6"], mv["circ_mv_yi"]))
        # 涨跌停（最近一日）
        lp = self.limit[self.limit["date"] == pd.Timestamp(self.last_date)]
        self.limit_up = dict(zip(lp["code6"], lp["limit_up"]))
        self.limit_down = dict(zip(lp["code6"], lp["limit_down"]))
        self.prev_close = dict(zip(lp["code6"], lp["prev_close"]))
        # 指数
        idx = self.index[self.index["code"] == "sh000001"].sort_values("date")
        self.index_series = idx.set_index("date")["close"]

    def _build_minute(self):
        m = self.minute5
        m["dt"] = pd.to_datetime(m["dt"])
        d = m[m["dt"].dt.normalize() == pd.Timestamp(self.last_date)].copy()
        if len(d) == 0:   # 当天无分钟数据（如节假日/分钟缓存未更新）→ 用最近有数据的一天
            last_m = pd.to_datetime(m["dt"]).max()
            last_m = pd.Timestamp(last_m).normalize()
            self.minute_date = last_m.strftime("%Y-%m-%d")
            d = m[m["dt"].dt.normalize() == last_m].copy()
        else:
            self.minute_date = self.last_date_str
        if len(d) == 0:   # 仍无数据：留空矩阵，页面走回放不足的兜底
            self.m_codes = np.array([], dtype=object)
            self.m_index = {}
            self.m = {f: np.full((0, 48), np.nan, np.float32)
                      for f in ["open", "high", "low", "close", "volume", "amount", "vwap"]}
            return
        d = d.sort_values(["code6", "dt"])
        d["bar"] = d.groupby("code6").cumcount()
        codes = np.array(sorted(d["code6"].unique()))
        self.m_codes = codes
        ci = {c: i for i, c in enumerate(codes)}
        S, B = len(codes), 48
        self.m = {}
        for f in ["open", "high", "low", "close", "volume", "amount"]:
            arr = np.full((S, B), np.nan, np.float32)
            ii = d["code6"].map(ci).to_numpy(dtype=np.int64)
            bb = d["bar"].to_numpy(dtype=np.int64)
            ok = bb < B
            arr[ii[ok], bb[ok]] = d[f].to_numpy(np.float32)[ok]
            self.m[f] = arr
        # 分时均价线 VWAP（volume 单位=手）
        vol = np.maximum(self.m["volume"], 1.0)
        amt = self.m["amount"]
        self.m["vwap"] = np.cumsum(amt, axis=1) / np.cumsum(vol, axis=1) / 100.0
        self.m_index = {c: i for i, c in enumerate(codes)}

    def _build_frames(self):
        def to_dt(df, col):
            if df is None or len(df) == 0 or col not in df.columns:
                return df
            df = df.copy()
            df[col] = pd.to_datetime(df[col].astype(str).str.slice(0, 10), errors="coerce")
            return df

        self.zt_limit_up = to_dt(self.zt_limit_up, "交易日期")
        self.zt_broken = to_dt(self.zt_broken, "交易日期")
        self.zt_ladder = to_dt(self.zt_ladder, "交易日期")
        self.zt_pool_up = to_dt(self.zt_pool_up, "交易时间")
        self.lhb_daily = to_dt(self.lhb_daily, "交易日期")
        self.lhb_inst = to_dt(self.lhb_inst, "交易日期")

        def fresh(df, col="date"):
            if df is None or len(df) == 0:
                return pd.DataFrame()
            return df[df[col] == pd.Timestamp(self.last_date)].copy()
        self.zt_limit_up_today = fresh(self.zt_limit_up, "交易日期")
        self.zt_up_today = self.zt_limit_up_today
        self.zt_broken_today = fresh(self.zt_broken, "交易日期")
        self.zt_ladder_today = fresh(self.zt_ladder, "交易日期")
        if len(self.zt_pool_up):
            last_pool = self.zt_pool_up["交易时间"].max()
            self.zt_pool_date = pd.Timestamp(last_pool).strftime("%Y-%m-%d")
            self.zt_pool_up_last = self.zt_pool_up[self.zt_pool_up["交易时间"] == last_pool].copy()
        else:
            self.zt_pool_date = None
            self.zt_pool_up_last = pd.DataFrame()
        self.lhb_today = fresh(self.lhb_daily, "交易日期")
        self.lhb_inst_today = fresh(self.lhb_inst, "交易日期")
        # 资金流：最近一日
        if len(self.moneyflow):
            self.moneyflow["date"] = pd.to_datetime(self.moneyflow["date"])
            self.flow_today = self.moneyflow[self.moneyflow["date"] == pd.Timestamp(self.last_date)]
        else:
            self.flow_today = pd.DataFrame()
        if len(self.stock_flow):
            self.stock_flow["交易日期"] = pd.to_datetime(self.stock_flow["交易日期"].astype(str),
                                                     format="%Y%m%d", errors="coerce")
            self.stock_flow_today = self.stock_flow[self.stock_flow["交易日期"] == pd.Timestamp(self.last_date)]
        else:
            self.stock_flow_today = pd.DataFrame()
        # 板块
        if len(self.board_flow):
            self.board_flow["date"] = pd.to_datetime(self.board_flow["date"].astype(str),
                                                   format="%Y%m%d", errors="coerce")
            self.board_today = self.board_flow[self.board_flow["date"] == pd.Timestamp(self.last_date)]
        else:
            self.board_today = pd.DataFrame()
        if len(self.industry_flow):
            self.industry_flow["date"] = pd.to_datetime(self.industry_flow["date"].astype(str),
                                                      format="%Y%m%d", errors="coerce")
            self.industry_today = self.industry_flow[self.industry_flow["date"] == pd.Timestamp(self.last_date)]
        else:
            self.industry_today = pd.DataFrame()
        # 停牌
        if len(self.suspension):
            sus = self.suspension.copy()
            sus["d"] = pd.to_datetime(sus["交易日期"].astype(str), format="%Y%m%d", errors="coerce")
            self.suspended = set(sus[sus["d"] == pd.Timestamp(self.last_date)]["code6"])
        else:
            self.suspended = set()

    # ---------------- 便捷 ----------------
    def col(self, field, t=None):
        t = self.T - 1 if t is None else t
        return self.M[field][t]

    def series(self, field, code_pos, n):
        return self.M[field][max(0, self.T - n):, code_pos]

    def universe(self, min_amount=30000.0, min_price=2.0, drop_st=True, drop_bj=True,
                 min_list_days=60):
        """可交易股票池 → 布尔掩码（长度 N），结果缓存"""
        key = (min_amount, min_price, drop_st, drop_bj, min_list_days)
        if getattr(self, "_uni_cache", None) and self._uni_cache[0] == key:
            return self._uni_cache[1]
        close = self.col("close")
        amt = self.col("amount_k")
        ok = np.isfinite(close) & (close >= min_price) & np.isfinite(amt) & (amt >= min_amount)
        for i, c in enumerate(self.codes):
            nm = str(self.name.get(c, ""))
            if drop_st and ("ST" in nm or "退" in nm):
                ok[i] = False
            if drop_bj and c.startswith(("8", "4", "92")):
                ok[i] = False
            if c in self.suspended:
                ok[i] = False
            if self.list_status.get(self.ts_code.get(c, ""), "上市") != "上市":
                ok[i] = False
        self._uni_cache = (key, ok)
        return ok


_CTX = None
_CTX_LOCK = threading.Lock()
_CTX_VERSION = 0


def get_ctx():
    """进程内单例；reset_ctx() 后下次访问会重新加载 parquet。"""
    global _CTX, _CTX_VERSION
    if _CTX is None:
        with _CTX_LOCK:
            if _CTX is None:
                _CTX_VERSION += 1
                c = Ctx()
                c.version = _CTX_VERSION
                _CTX = c
    return _CTX


def reset_ctx():
    """丢弃内存里的数据层（下次 get_ctx() 重新读 parquet）。"""
    global _CTX
    with _CTX_LOCK:
        _CTX = None


def ctx_version():
    return _CTX_VERSION
