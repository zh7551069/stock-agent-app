"""ETL：本地 DuckDB（只读）→ data/cache/*.parquet

设计要点：
- 外盘库可能被数据同步任务写锁 → 带重试；失败不阻塞（网站用旧缓存）
- 代码统一成 code6（6 位数字）+ ts_code（000001.SZ）双键
- 每张表独立 try/except，部分失败也保留其余缓存
"""
import json
import os
import sys
import time

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def log(*a):
    print(*a, flush=True)


def connect(retries=8, sleep=10):
    last = None
    for i in range(retries):
        try:
            return config.tune_duck(duckdb.connect(config.DUCKDB, read_only=True))
        except Exception as e:
            last = e
            log(f"  连接失败({i+1}/{retries})，{sleep}s 后重试…")
            time.sleep(sleep)
    raise RuntimeError(f"DuckDB 不可用: {last}")


def norm_code(df, col):
    """sh600000 / 600000.SH / 600000 / 000001.XSHE → code6 + ts_code"""
    s = df[col].astype(str).str.strip().str.upper()
    df["code6"] = s.str.extract(r"(\d{6})", expand=False)
    ts = s.str.extract(r"^(\d{6})\.(SH|SZ|BJ)$", expand=False)      # DataFrame: 0,1
    pre = s.str.extract(r"^(SH|SZ|BJ)(\d{6})$", expand=False)       # DataFrame: 0,1
    out = pd.Series(None, index=df.index, dtype=object)
    if ts.shape[1] == 2:
        m = ts[1].notna()
        out[m] = ts[0][m] + "." + ts[1][m]
    if pre.shape[1] == 2:
        m = pre[0].notna()
        out[m] = pre[1][m] + "." + pre[0][m]
    df["ts_code"] = out
    return df


def save(df, name, manifest):
    if df is None or len(df) == 0:
        log(f"  ⚠️ {name}: 空")
        return
    p = os.path.join(config.CACHE, f"{name}.parquet")
    df.to_parquet(p, index=False)
    manifest[name] = {"rows": int(len(df)), "cols": list(df.columns)[:40], "mb": round(os.path.getsize(p) / 1e6, 1)}
    log(f"  ✓ {name}: {len(df):,} 行 {os.path.getsize(p)/1e6:.1f}MB")


def main():
    if not os.path.exists(config.DUCKDB):
        sys.exit(f"❌ 外盘未挂载: {config.DUCKDB}")
    os.makedirs(config.CACHE, exist_ok=True)
    t0 = time.time()
    con = connect()
    manifest = {}

    # 截止日 = min(交易日历最大日, 实际行情最新日)：交易日历含未来日期，不能直接用
    cal = con.execute("select max(date) from trade_cal").fetchone()[0]
    data_max = con.execute("select max(date) from daily").fetchone()[0]
    cutoff = min(cal, data_max) if data_max else cal
    dates = [r[0] for r in con.execute(
        "select date from trade_cal where date <= ? order by date desc limit ?",
        [cutoff, config.DAILY_DAYS]).fetchall()][::-1]
    d0, d1 = dates[0], dates[-1]
    mdates = dates[-config.MINUTE_DAYS:]
    f0 = dates[-config.FLOW_DAYS]
    log(f"数据截止 {d1}；日线回溯 {len(dates)} 日；分钟 {len(mdates)} 日")

    # ── 1. 日线（前复权 + 37 指标）────────────────────────────────
    try:
        cols = ["code", "date", "open", "high", "low", "close", "vol_hand", "amount_k",
                "pct_chg", "turnover", "vol_ratio", "adj", "ma5", "ma10", "ma20", "ma60",
                "ema12", "ema26", "macd_dif", "macd_dea", "macd", "rsi6", "rsi12", "rsi24",
                "kdj_k", "kdj_d", "kdj_j", "boll_up", "boll_mid", "boll_low", "atr",
                "up_days", "down_days", "new_high_days", "new_low_days"]
        have = {r[0] for r in con.execute("describe tech_factor_qfq").fetchall()}
        cols = [c for c in cols if c in have]
        df = con.execute(
            f"select {','.join(cols)} from tech_factor_qfq where date between ? and ?",
            [d0, d1]).df()
        df = norm_code(df, "code")
        save(df, "daily", manifest)
    except Exception as e:
        log(f"  ✗ daily: {e}")

    # ── 2. 市值 ───────────────────────────────────────────────────
    try:
        df = con.execute("""
            select code, date, close, total_mv_yi, circ_mv_yi from daily_mv
            where date between ? and ?""", [d0, d1]).df()
        df = norm_code(df, "code")
        save(df, "mv", manifest)
    except Exception as e:
        log(f"  ✗ mv: {e}")

    # ── 3. 涨跌停价 ──────────────────────────────────────────────
    try:
        df = con.execute("""
            select code, date, prev_close, limit_up, limit_down, limit_pct, is_new
            from limit_price where date between ? and ?""", [d0, d1]).df()
        df = norm_code(df, "code")
        save(df, "limit_price", manifest)
    except Exception as e:
        log(f"  ✗ limit_price: {e}")

    # ── 4. 股票基础信息 ──────────────────────────────────────────
    try:
        df = con.execute("""
            select ts_code, code, name, area, industry, market, exchange, list_status,
                   list_date, delist_date, hs_connect
            from stock_info""").df()
        df["code6"] = df["code"].astype(str).str.zfill(6)
        save(df, "stock_info", manifest)
    except Exception as e:
        log(f"  ✗ stock_info: {e}")

    # ── 5. 指数 ──────────────────────────────────────────────────
    try:
        df = con.execute("""
            select code, name, date, open, high, low, close, volume, pct_chg
            from index_daily where date between ? and ?""", [d0, d1]).df()
        save(df, "index_daily", manifest)
    except Exception as e:
        log(f"  ✗ index_daily: {e}")

    # ── 6. 涨停 / 连板 / 炸板 / 竞价 ────────────────────────────
    for tbl, name in [("zt_ths_limit_up", "zt_limit_up"),
                      ("zt_ths_ladder_pool", "zt_ladder"),
                      ("zt_ths_broken", "zt_broken")]:
        try:
            df = con.execute(f"""
                select * from {tbl} where 交易日期 between ? and ?""", [f0, d1]).df()
            df = norm_code(df, "股票代码")
            save(df, name, manifest)
        except Exception as e:
            log(f"  ✗ {name}: {e}")
    try:
        df = con.execute("select * from zt_pool_auction where 交易时间 between ? and ?",
                         [f0, d1]).df()
        df = norm_code(df, "代码")
        save(df, "zt_auction", manifest)
    except Exception as e:
        log(f"  ✗ zt_auction: {e}")
    # 东财涨停池（含涨停时间 → 用于「10 点前首触」）
    try:
        sql = ('select 代码, 交易时间, 涨停时间, 最后涨停时间, 开板时间, '
               '"状态（N连板）" as 连板状态, 封单, 成交额, "换手率%" as 换手率, 实际流通 '
               'from zt_pool_limit_up where 交易时间 between ? and ?')
        df = con.execute(sql, [f0, d1]).df()
        df = norm_code(df, "代码")
        save(df, "zt_pool_up", manifest)
    except Exception as e:
        log(f"  ✗ zt_pool_up: {e}")

    # ── 7. 龙虎榜 ────────────────────────────────────────────────
    try:
        df = con.execute("select * from lhb_daily where 交易日期 between ? and ?", [f0, d1]).df()
        df = norm_code(df, "股票代码")
        save(df, "lhb_daily", manifest)
    except Exception as e:
        log(f"  ✗ lhb_daily: {e}")
    try:
        df = con.execute("select * from lhb_inst where 交易日期 between ? and ?", [f0, d1]).df()
        df = norm_code(df, "股票代码")
        save(df, "lhb_inst", manifest)
    except Exception as e:
        log(f"  ✗ lhb_inst: {e}")

    # ── 8. 资金流 ────────────────────────────────────────────────
    try:
        df = con.execute("select * from moneyflow_stock where date between ? and ?", [f0, d1]).df()
        df = norm_code(df, "code")
        save(df, "moneyflow", manifest)
    except Exception as e:
        log(f"  ✗ moneyflow: {e}")
    try:
        df = con.execute("select * from stock_flow_em where 交易日期 between ? and ?",
                         [int(f0.strftime("%Y%m%d")), int(d1.strftime("%Y%m%d"))]).df()
        df = norm_code(df, "股票代码")
        save(df, "stock_flow", manifest)
    except Exception as e:
        log(f"  ✗ stock_flow: {e}")

    # ── 9. 板块资金流 ───────────────────────────────────────────
    try:
        df = con.execute("""
            select "交易日期" as date, "板块名称" as sector, "板块涨跌幅(%)" as pct_chg,
                   "主力净流入净额(元)" as main_net
            from board_flow_em where "交易日期" between ? and ?""", [int(f0.strftime("%Y%m%d")), int(d1.strftime("%Y%m%d"))]).df()
        save(df, "board_flow", manifest)
    except Exception as e:
        log(f"  ✗ board_flow: {e}")
    try:
        df = con.execute("""
            select 交易日期 as date, 行业名称 as sector, "指数涨跌幅(%)" as pct_chg,
                   "净额(亿元)" as net
            from industry_flow_ths where 交易日期 between ? and ?""", [f0.strftime("%Y%m%d"), d1.strftime("%Y%m%d")]).df()
        save(df, "industry_flow", manifest)
    except Exception as e:
        log(f"  ✗ industry_flow: {e}")

    # ── 10. 停牌 / 交易日历 ─────────────────────────────────────
    try:
        df = con.execute("select * from suspension where 交易日期 between ? and ?",
                         [f0.strftime("%Y%m%d"), d1.strftime("%Y%m%d")]).df()
        df = norm_code(df, "股票代码")
        save(df, "suspension", manifest)
    except Exception as e:
        log(f"  ✗ suspension: {e}")
    try:
        save(pd.DataFrame({"date": dates}), "trade_cal", manifest)
    except Exception as e:
        log(f"  ✗ trade_cal: {e}")

    # ── 11. 5 分钟线 ────────────────────────────────────────────
    try:
        t = time.time()
        df = con.execute("""
            select code, dt, open, high, low, close, volume, amount
            from minute_5min
            where dt >= ? and dt < ?
              and (code like 'sh6%' or code like 'sz0%' or code like 'sz3%' or code like 'bj%')
        """, [pd.Timestamp(mdates[0]), pd.Timestamp(d1) + pd.Timedelta(days=1)]).df()
        df = norm_code(df, "code")
        save(df, "minute5", manifest)
        log(f"    5min 耗时 {time.time()-t:.0f}s")
    except Exception as e:
        log(f"  ✗ minute5: {e}")

    manifest["_meta"] = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "duckdb": config.DUCKDB,
        "last_trade_date": str(d1),
        "daily_days": len(dates),
        "minute_days": len(mdates),
        "elapsed_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(config.CACHE, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    log(f"\n完成 {time.time()-t0:.0f}s → {config.CACHE}")


if __name__ == "__main__":
    main()
