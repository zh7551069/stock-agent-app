"""本地增量包 → DuckDB：扫描 / 分类 / 映射 / 导入。

增量包目录（默认 /Volumes/My PSSD/downlodData/taobao）里每天会下载一批文件：
  YYYYMMDD.csv                 个股日线（26 列：OHLC/量额/换手/PE/PB/股本/市值）
  YYYYMMDD(1..6).csv           6 个不同数据集（带编号 ≠ 重复下载！）
  YYYYMMDD_{1,5,15,30,60}min.zip + (1)~(4) 分片   分钟线，5 个分片对应 5 类标的
  涨停_/炸板_/跌停_/涨停池_/连扳池_/炸板池_/跌停池_/冲刺涨停_/竞价_/自然涨停_  涨停榜单元数据
  连板天梯_/龙虎榜明细_/龙虎榜机构明细_/A股市场_/热股_/热基_/ETF_/美股_/港股市场_/行业板块_/涨停最强板块_
  题材成分_KPL_*.zip / 限售股解禁.zip / 股票列表.csv / 退市股票列表.csv
  财务类 zip（利润表/资产负债表/现金流量表/业绩快报/…）—— 仅登记，暂不导入
"""
import csv
import datetime
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile
from collections import defaultdict

import duckdb

import config

# ────────────────────────── 分类注册表 ──────────────────────────

CATEGORY_LABEL = {
    "minute": "分钟数据",
    "daily": "日线行情",
    "flow": "资金流向",
    "limit": "涨停/跌停/榜单",
    "lhb": "龙虎榜",
    "theme": "题材/热度",
    "base": "基础信息",
    "finance": "财务数据",
    "adj": "复权因子",
    "fund": "基金",
}

# 分钟 zip 分片 → (内容, minute.market)
MINUTE_PARTS = {
    None: ("行业概念板块", "board"),
    "1": ("市场统计指数", "stat"),
    "2": ("指数", "idx"),
    "3": ("京市（北交所）", "bj"),
    "4": ("沪深 A 股", "hs"),
    "5": ("LOF 基金", "lof"),
    "6": ("ETF 基金", "etf"),
}

# 🆕 2026-11-03：分钟分片 → **按 zip 内 CSV 文件名指纹识别市场**，禁信编号。
#   血证：2026-11-02 的 1min 有 8 个分片，其中 (3) 与 (2) md5 完全相同（重复下载）
#   → 编号 3 之后整体位移一位 → bj/hs/lof/etf 全部错标：
#     标 bj 的 529 只其实是 idx 副本、标 hs 的 354 只其实是 bj、
#     标 lof 的 5,223 只其实是 hs、标 etf 的 389 只其实是 lof、真 etf(1726) 漏导。
MARKET_LABEL = {
    "board": "行业概念板块", "stat": "市场统计指数", "idx": "指数",
    "bj": "京市（北交所）", "hs": "沪深 A 股", "lof": "LOF 基金", "etf": "ETF 基金",
}


def _minute_fingerprint(path):
    """读 zip 内 CSV 文件名，判定属于哪个市场。返回 (market|None, csv 数)。"""
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
    except Exception:
        return None, 0
    if not names:
        return None, 0
    base = [n.rsplit("/", 1)[-1][:-4] for n in names]
    n = len(base)
    if n == 0:
        return None, 0
    c_bj = sum(1 for b in base if b[:2].lower() == "bj")
    c_shsz = sum(1 for b in base if b[:2].lower() in ("sh", "sz"))
    c_dot = sum(1 for b in base if re.match(r"^\d{6}\.[A-Za-z]{2}$", b))
    c_bare = sum(1 for b in base if re.match(r"^\d{6}$", b))
    half = n * 0.5
    if c_bj > half:
        return "bj", n
    if c_shsz > half:
        return "hs", n
    if c_dot > half:
        # 后缀式：15/51/52/53/55/56/58 → ETF；16/50 → LOF
        c_etf = sum(1 for b in base if re.match(r"^(15|51|52|53|55|56|58)", b))
        c_lof = sum(1 for b in base if re.match(r"^(16|50)", b))
        return ("etf" if c_etf >= c_lof else "lof"), n
    if c_bare > half:
        if any(b.startswith("881") for b in base):
            return "board", n            # 含 881xxx → 行业概念板块
        c88 = sum(1 for b in base if b.startswith("88"))
        if c88 > n * 0.9:
            return ("stat" if n < 100 else "board"), n
        return "idx", n
    return None, n


POOL_PREFIX = {
    "涨停": ("开盘啦·涨停", "zt_pool_limit_up"),
    "炸板": ("开盘啦·炸板", "zt_pool_broken"),
    "跌停": ("开盘啦·跌停", "zt_pool_limit_down"),
}
THS_POOL = {
    "涨停池": ("同花顺·涨停池", "zt_ths_limit_up"),
    "炸板池": ("同花顺·炸板池", "zt_ths_broken"),
    "跌停池": ("同花顺·跌停池", "zt_ths_limit_down"),
    "连扳池": ("同花顺·连板池", "zt_ths_ladder_pool"),
    "冲刺涨停": ("同花顺·冲刺涨停", "zt_ths_rush"),
}

# ⚠️ 已由 SNAP_ZIP_CFG 取代（classify 不再读本表）；保留仅作历史参考/兼容。
FIN_ZIP_TABLES = {
    "利润表数据": ["fin_income"],
    "资产负债表数据": ["fin_balance"],
    "现金流量表数据": ["fin_cashflow"],
    "业绩快报数据": ["fin_express"],
    "业绩预告数据": ["fin_forecast"],
    "财务指标数据": ["fin_indicator"],
    "财务审计意见数据": ["fin_audit"],
    "财报披露计划": ["fin_schedule"],
    "分红送股数据": ["fin_dividend"],
    "主营业务构成数据": ["fin_main_biz"],
    "股东人数": ["fin_holder_num"],
    "股权质押明细": ["fin_pledge_detail"],
    "股权质押统计": ["fin_pledge_stat"],
    "个股资金流向": ["stock_flow_ths"],
    "个股资金流向_东财": ["stock_flow_em"],
    "个股资金流向_同花顺": ["stock_flow_ths"],
    "行业资金流向_同花顺": ["industry_flow_ths"],
}

ADJ_ZIP_TABLES = {
    "复权因子": ["adj_factor"],
    "复权因子_前复权": ["adj_factor"],
    "复权因子_后复权": ["adj_factor"],
    "ETF_复权因子_前复权": ["etf_adj_factor"],
    "ETF_复权因子_后复权": ["etf_adj_factor"],
    "LOF_复权因子_前复权": ["lof_adj_factor"],
    "LOF_复权因子_后复权": ["lof_adj_factor"],
}


def _kind(key, cat, label, tables, importer="generic", note="", extra=None):
    d = {"key": key, "cat": cat, "label": label, "tables": tables,
         "importer": importer, "note": note}
    if extra:
        d.update(extra)
    return d


# ══════════════ CSV 表头指纹（文件名只有日期时靠列名认表）══════════════
# 🔴🔴 血证 2026-10-28：`YYYYMMDD(N).csv` 的编号 (N) 与数据集**每次下载都可能不同**
#   本次 (1)~(5) 相对旧约定整体错位一位 →
#     板块资金流被当个股日线 / moneyflow 去读「股票代码」→ BinderException；
#     更糟的是 _imp_generic 只认「交易日期」一个同名列就整批插入 →
#     (1) 4,656 行灌进 industry_flow_ths、(2) 5,567 行灌进 stock_flow_ths、
#     (3) 5,223 行灌进 stock_flow_em、(5) 1,043 行灌进 board_flow_ths（资金列全 NULL）。
#   现在：**先读表头精确识别**；认不出就拒收（绝不靠编号盲猜去按日 DELETE）。

def _norm_col(c):
    """列名归一：去空白 + 全角括号→半角（仅用于指纹比对，不改实际列名）。"""
    s = re.sub(r"\s+", "", str(c or ""))
    return s.replace("（", "(").replace("）", ")")


def _csv_header(path, limit=8192):
    """读 CSV 第一行表头并归一化。失败返回 None。"""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            first = f.readline(limit)
        if not first:
            return None
        return [_norm_col(c) for c in next(csv.reader([first]))]
    except Exception:  # noqa: BLE001
        return None


# (必须全部出现的归一化列名, label, tables, importer, key, scale)
#   ⚠️ scale 的 key 也用归一化列名（_imp_generic 内按归一化匹配后相乘）
_FLOW_SF_SCALE = {"资金净流入(万元)": 1e4, "5日主力净额(万元)": 1e4,
                  "今日大单净流入额(万元)": 1e4, "今日中单净流入额(万元)": 1e4,
                  "今日小单净流入额(万元)": 1e4}
_FLOW_EM_SCALE = {"今日主力净流入额(万元)": 1e4, "今日超大单净流入额(万元)": 1e4,
                  "今日大单净流入额(万元)": 1e4, "今日中单净流入额(万元)": 1e4,
                  "今日小单净流入额(万元)": 1e4}

_HEADER_SIG = (
    (("股票代码", "小单买入量(手)", "净流入量(手)"),
     "资金流明细（小/中/大/特大单）", ["moneyflow_stock"], "moneyflow", "mf_detail", None),
    (("数据类型", "板块代码", "主力净流入净额(元)"),
     "板块资金流（东财）", ["board_flow_em"], "generic", "board_flow_em", None),
    (("板块代码", "板块名称", "流入资金(亿元)", "行业指数"),
     "板块资金流（同花顺）", ["board_flow_ths"], "generic", "board_flow_ths", None),
    (("行业代码", "行业名称", "收盘指数"),
     "行业资金流（同花顺）", ["industry_flow_ths"], "generic", "industry_flow_ths", None),
    (("股票代码", "5日主力净额(万元)"),
     "个股资金流（同花顺）", ["stock_flow_ths"], "generic", "stock_flow_ths", _FLOW_SF_SCALE),
    (("股票代码", "今日主力净流入额(万元)", "今日超大单净流入额(万元)"),
     "个股资金流（东财）", ["stock_flow_em"], "generic", "stock_flow_em", _FLOW_EM_SCALE),
    (("股票代码", "开盘价", "收盘价", "成交量(手)"),
     "个股日线（26 列）", ["daily", "daily_basic", "daily_mv"], "daily26", "daily_csv", None),
)


def _match_header(cols):
    """按表头列名匹配数据集。cols 应已 _norm_col 归一。"""
    if not cols:
        return None
    have = set(cols)
    for need, label, tables, importer, key, scale in _HEADER_SIG:
        if set(need) <= have:
            cat = "daily" if importer == "daily26" else ("limit" if importer == "moneyflow" else "flow")
            extra = {"scale": scale} if scale else None
            return _kind(key, cat, label, tables, importer=importer,
                         note=f"表头指纹识别（{len(cols)} 列）", extra=extra)
    return None


def classify(name, date, path=None):
    """根据文件名判断包类型。返回 dict 或 None。

    🔴 2026-10-28 起：「文件名只有日期」的包（YYYYMMDD.csv / YYYYMMDD(N).csv）
       一律**读表头指纹**识别；认不出 → unsupported 拒收，绝不靠 (N) 编号盲猜。
    """
    base_info = re.sub(r"\.csv$", "", (name or "").strip())
    # ── 文件名只有日期 → 表头指纹 ──
    # 🔴 2026-10-31 修：支持多层括号 20261030(1)(1).csv
    #    淘宝重复下载会叠括号；旧正则只吃一层 → 5 个资金流包全被拒收
    if re.match(r"^\d{8}(\(\d+\))*\.csv$", name or ""):
        hdr = _csv_header(path)
        sig = _match_header(hdr)
        if sig:
            return sig
        if path:
            head = ", ".join((hdr or [])[:6])
            return _kind("csv_unknown_header", "other", "未识别表头（文件名只有日期）", [],
                         importer="unsupported",
                         note=f"表头认不出是哪类数据（前 6 列：{head}）→ 请告知我加指纹，不会盲导")
        # 无 path（仅 UI 预览用）→ 标为不可导入，避免误导
        m0 = re.match(r"^(\d{8})((?:\(\d+\))+)\.csv$", name or "")
        if m0:
            tag = m0.group(2).replace("(", "").replace(")", "-").strip("-")
            return _kind(f"dated_{tag}", "flow",
                         f"日期编号数据集 ({tag})", [], importer="unsupported",
                         note="编号每次下载可能不同，仅凭文件名无法定表；导入时会按表头重新识别")
        return _kind("daily_csv", "daily", "个股日线（26 列）",
                     ["daily", "daily_basic", "daily_mv"], importer="daily26",
                     note="拆成日线 / 估值 / 市值三张表")
    # 分钟 zip 分片：含 (N) 有意义，先匹配
    mm = re.match(r"^(\d{8})_(\d{1,2})min(?:\((\d)\))?\.zip$", name)
    if mm:
        part = mm.group(3)
        freq = mm.group(2)
        # 🆕 2026-11-03：优先按 zip 内容指纹识别（禁信编号，血证 11-02 整体位移）
        fp, nn = _minute_fingerprint(path) if path else (None, 0)
        if fp:
            market = fp
            content = MARKET_LABEL.get(fp, fp)
            return _kind(f"minute_{freq}min", "minute", f"{freq} 分钟线 · {content}",
                         [f"minute_{freq}min"], importer="minute",
                         note=f"zip 内 {nn} 个 csv，指纹识别 market={market}（编号 {part or '主包'} 未采信）",
                         extra={"freq": freq, "part": part, "market": market, "content": content,
                                "fp_csv": nn})
        if part is not None and part not in MINUTE_PARTS:
            return _kind(f"minute_{freq}min_unknown", "minute",
                         f"{freq} 分钟线 · 未知分片 ({part})", [],
                         importer="unsupported",
                         note=f"分片编号 {part} 超出 1~6，且内容指纹认不出（{nn} 个 csv）→ 拒收")
        content, market = MINUTE_PARTS.get(part, ("行业概念板块", "board"))
        return _kind(f"minute_{freq}min", "minute", f"{freq} 分钟线 · {content}",
                     [f"minute_{freq}min"], importer="minute",
                     note=f"zip 内按代码分文件；market={market}",
                     extra={"freq": freq, "part": part, "market": market, "content": content})
    # 其余情况忽略 (N) 副本后缀
    low = re.sub(r"\(\d+\)(?=\.[A-Za-z0-9]+$)", "", name)
    # ── 日线 26 列：已在函数开头按表头指纹处理 ──
    # ── 涨停/跌停/炸板（24 列，开盘啦口径）──
    m = re.match(r"^(涨停|炸板|跌停)_(\d{8})\.csv$", low)
    if m:
        label, table = POOL_PREFIX[m.group(1)]
        return _kind(f"pool_{m.group(1)}", "limit", label, [table],
                     note="24 列，导入时补 src_type")
    # ── 同花顺池类（18 列）──
    m = re.match(r"^(涨停池|炸板池|跌停池|连扳池|冲刺涨停)_(\d{8})\.csv$", low)
    if m:
        label, table = THS_POOL[m.group(1)]
        return _kind(f"ths_{m.group(1)}", "limit", label, [table],
                     note="18 列，导入时补 src_type")
    # ── 竞价 / 自然涨停 ──
    if re.match(r"^竞价_\d{8}\.csv$", low):
        return _kind("auction", "limit", "竞价数据", ["zt_pool_auction"], note="24 列竞价明细")
    if re.match(r"^自然涨停_\d{8}\.csv$", low):
        return _kind("natural_up", "limit", "自然涨停", ["zt_pool_natural_up"])
    # ── 其他榜单 ──
    if re.match(r"^连板天梯_\d{8}\.csv$", low):
        return _kind("ladder", "limit", "连板天梯", ["ladder"])
    if re.match(r"^涨停最强板块_\d{8}\.csv$", low):
        return _kind("strong_board", "limit", "涨停最强板块", ["zt_strong_board"])
    if re.match(r"^龙虎榜明细_\d{8}\.csv$", low):
        return _kind("lhb_daily", "lhb", "龙虎榜明细", ["lhb_daily"])
    if re.match(r"^龙虎榜机构明细_\d{8}\.csv$", low):
        return _kind("lhb_inst", "lhb", "龙虎榜机构明细", ["lhb_inst"])
    # ── 热榜：按数据源分表（🩸 2026-10-27 曾把同花顺「行业板块」写进 hot_rank_em）──
    m = re.match(r"^(A股市场|ETF基金|港股市场|美股市场)_\d{8}\.csv$", low)
    if m:
        return _kind("hot_rank", "theme", f"热度榜(东财) · {m.group(1)}", ["hot_rank_em"],
                     note="10 列，「当前价」列名与 hot_rank_em 一致")
    m = re.match(r"^(热股|热基|ETF|行业板块|概念板块|可转债|港股|美股|期货)_\d{8}\.csv$", low)
    if m:
        return _kind("hot_rank_ths", "theme", f"热度榜(同花顺) · {m.group(1)}", ["hot_rank_ths"],
                     note="11 列，含「上榜解读」")
    # ── 题材 / 解禁 / 列表 ──
    if re.match(r"^题材成分_KPL_\d{8}\.zip$", low):
        return _kind("theme_kpl", "theme", "题材成分（开盘啦）", ["theme_stock"],
                     importer="theme_zip", note="zip 内每题材一个 csv")
    if low == "限售股解禁.zip":
        return _kind("restricted", "base", "限售股解禁", ["fin_restricted"],
                     importer="restricted_zip", note="zip 内每股票一个 csv")
    # ── 每日指标（daily_ind 的源包，此前完全未识别）──
    if low == "每日指标.zip":
        return _kind("daily_ind_zip", "daily", "每日指标（26 列·每票一 csv）",
                     ["daily_ind", "daily_mv"], importer="daily_ind_zip",
                     note="按文件名内无日期 → 用批次日期过滤；含全历史，只抽目标日")
    # ── 板块资金流（每票一 csv 结构）──
    if low in ("板块资金流向_东财.zip", "板块资金流向_同花顺.zip"):
        tbl = "board_flow_em" if "东财" in low else "board_flow_ths"
        return _kind(f"bflowzip_{low}", "flow", low.replace('.zip', ''), [tbl],
                     importer="board_flow_zip",
                     note="每板块一 csv 的权威全历史快照；整包覆盖（可纠正历史口径），比日常 (5)/(6) 更完整")
    # ── 基础三表：ST 名单 / 停复牌 / 涨跌停价格（2026-10-28 接入）──
    if re.match(r"^ST股票列表_\d{8}\.csv$", low):
        return _kind("st_stock", "base", "ST 股票列表", ["st_stock"], importer="st_susp",
                     note="5 列中文列名；按整行精确匹配补缺口，不覆盖")
    if re.match(r"^每日停复牌_\d{8}\.csv$", low):
        return _kind("suspension", "base", "每日停复牌", ["suspension"], importer="st_susp",
                     note="4 列中文列名；同一天停牌/复牌各一行")
    if re.match(r"^每日涨跌停价格_\d{8}\.csv$", low):
        return _kind("limit_price", "base", "每日涨跌停价格", ["limit_price"], importer="limit_price",
                     note="官方权威值；涨停价>=999999 视为无涨跌幅限制（哨兵 NULL）")
    # ── 2026-10-29：三个「未识别不支持」包（全历史 zip + 曾用名）──
    if re.match(r"^每日停复牌\.zip$", low):
        return _kind("suspension_zip", "base", "每日停复牌（全历史）", ["suspension"],
                     importer="susp_zip",
                     note="每股票一 csv 的全历史包；按整行精确匹配补缺口，不覆盖")
    if re.match(r"^每日涨跌停价格\.zip$", low):
        return _kind("limit_price_zip", "base", "每日涨跌停价格（全历史）", ["limit_price"],
                     importer="limit_price_zip",
                     note="每股票一 csv 的全历史包；按 code+date 补缺口，绝不按日删（库内含 2000-2006 派生数据）")
    if low == "股票曾用名汇总.csv":
        return _kind("stock_rename", "base", "股票曾用名汇总", ["stock_rename"],
                     importer="stock_rename",
                     note="7 列；按整行精确匹配补缺口，不覆盖")
    if low in ("股票列表.csv", "退市股票列表.csv"):
        return _kind("stock_list", "base", "股票列表", ["stock_info"],
                     importer="stock_list", note="17 列，含退市状态")
    # ── 分钟包附属基础信息（2026-10-28 接入）──
    if low in ("股票列表_沪深.csv", "股票列表_京市.csv"):
        return _kind("stock_list", "base", "股票列表", ["stock_info"],
                     importer="stock_list",
                     note="13 列（无上市状态/货币/退市日）→ MERGE 只更新非空列，不冲掉已有值")
    if base_info in INFO_TABLE_CFG:
        cfg = INFO_TABLE_CFG[base_info]
        return _kind(f"info_{cfg['table']}", cfg.get("cat", "base"), base_info,
                     [cfg["table"]], importer="info_table", note=cfg["note"])
    # ── 资金流杂项 ──
    if low == "大盘资金流向.csv":
        return _kind("market_flow", "flow", "大盘资金流向", ["market_flow_daily"],
                     importer="market_flow", note="15 列全历史快照，按交易日期整段覆盖")
    if low == "沪深港通资金流向.csv":
        return _kind("hk_flow", "flow", "沪深港通资金流向", ["hk_connect_flow"],
                     importer="hk_flow", note="中文括号列名自动映射，按交易日期整段覆盖")
    if low == "港股通每日成交统计.csv":
        return _kind("hk_daily", "flow", "港股通每日成交", ["hk_connect_daily"],
                     importer="hk_daily", note="5 列同名，按交易日期整段覆盖")
    if low in ("技术因子_前复权.zip", "技术因子_后复权.zip"):
        tbl = TECH_ZIP_MAP[re.sub(r"\.zip$", "", low)][0]
        which = "前复权" if "前复权" in low else "后复权"
        return _kind(f"techzip_{which}", "daily", f"技术因子·{which}（38 列·每股票一 csv 全历史）",
                     [tbl], importer="tech_zip",
                     note="只补缺口；全表口径修复另用 scripts/recalc_tech_factor_*.py")
    if re.match(r"^技术因子_\d{8}\.csv$", low):
        return _kind("tech_factor", "daily", "技术因子（38 列·后复权）", ["tech_factor_hfq"],
                     importer="tech_factor_day",
                     note="该单日文件是后复权口径 → 只写 tech_factor_hfq；前复权表另由 daily 重算")
    if re.match(r"^上市公司财务信息_\d{8}\.csv$", low):
        return _kind("fin_snapshot", "finance", "上市公司财务信息", ["fin_company_snapshot"],
                     importer="fin_snapshot", note="35 列快照，整表刷新")
    # 🆕 2026-10-31：全部上市公司财务信息 有两种包，靠列数区分（名称多「全部」二字）
    #   35 列 → fin_company_snapshot（公司快照，报告期=更新日期）
    #   397 列 → fin_report_full（季度财务宽表：资产+利润+现金流+指标）
    if re.match(r"^全部上市公司财务信息_\d{8}\.csv$", low):
        _h = _csv_header(path)
        if _h and len(_h) > 100:
            return _kind("fin_report_full", "finance", f"全部上市公司财务信息（{len(_h)} 列宽表）",
                         ["fin_report_full"], importer="fin_report_full",
                         note="季度财务宽表，按报告期整段替换")
        return _kind("fin_snapshot", "finance", "上市公司财务信息", ["fin_company_snapshot"],
                     importer="fin_snapshot", note="35 列快照，整表刷新")
    # ── 财务 zip（仅登记）──
    base = re.sub(r"\.zip$", "", low)
    if base in SNAP_ZIP_CFG:
        cf = SNAP_ZIP_CFG[base]
        cat = "flow" if "资金流向" in base else ("base" if "股东" in base else "finance")
        imp = "stock_flow_zip" if "资金流向" in base else "fin_zip"
        return _kind(f"snapzip_{base}", cat, base, [cf["table"]], importer=imp,
                     note=f"每股票一 csv 的全历史快照（{cf.get('note','补缺口，不覆盖库内已有行')}）")
    if base in ADJ_ZIP_TABLES:
        tbls = ADJ_ZIP_TABLES[base]
        if base in ("复权因子_前复权", "复权因子_后复权"):
            return _kind(f"adjzip_{base}", "adj", base, tbls, importer="adj_zip",
                         note="前复权包写 qfq_factor / 后复权包写 hfq_factor，两包都要传才完整")
        if base.startswith(("ETF_复权因子_", "LOF_复权因子_")):
            return _kind(f"adjzip_{base}", "adj", base, tbls, importer="fund_adj_zip",
                         note="全历史快照（每基金一 csv）；前复权写 qfq_factor / 后复权写 hfq_factor，两包都传才完整")
        if base == "复权因子":
            return _kind(f"adjzip_{base}", "adj", "累计后复权因子", tbls, importer="adj_cum_zip",
                         note="每股票一 csv 全历史；只写 hfq_factor（前复权必须整包重刷，另用 resync_adj_full.py）")
        return _kind(f"adjzip_{base}", "adj", base, tbls, importer="unsupported",
                     note="未登记的复权因子包")
    if low.startswith(("ETF基础信息", "ETF基准指数", "LOF基金列表")):
        if low.startswith("ETF基准指数"):
            return _kind("fund_bench", "fund", "ETF基准指数", ["etf_benchmark_index"],
                         importer="fund_list",
                         note="指数全历史快照 → etf_benchmark_index（按 index_code 删后插）")
        return _kind("fund_list", "fund", "基金基础信息", ["fund_info"],
                     importer="fund_list",
                     note="ETF/LOF 基础信息 → fund_info（无日期列，按 code_norm 删后插）")
    return None


# ────────────────────────── 扫描 ──────────────────────────

def _zip_entries(path):
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        return len(names), (os.path.basename(names[0]) if names else "")
    except Exception:
        return None, ""


def _file_date(name):
    m = re.search(r"(\d{8})", name)
    return m.group(1) if m else ""


def scan(pack_dir=None):
    """扫描增量包目录，按日期分组并分类。"""
    pack_dir = pack_dir or getattr(config, "PACK_DIR", "")
    if not pack_dir or not os.path.isdir(pack_dir):
        return {"dir": pack_dir, "exists": False, "dates": []}

    files = []
    for name in sorted(os.listdir(pack_dir)):
        if name.startswith(".") or name.startswith("_"):
            continue
        path = os.path.join(pack_dir, name)
        if not os.path.isfile(path) or name == ".DS_Store":
            continue
        date = _file_date(name)
        info = classify(name, date, path)
        st = os.stat(path)
        item = {
            "name": name, "date": date, "size": st.st_size,
            "mtime": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime)),
            "kind": info["key"] if info else "other",
            "cat": info["cat"] if info else "other",
            "label": info["label"] if info else "未识别",
            "tables": info["tables"] if info else [],
            "importer": info["importer"] if info else "unsupported",
            "note": info["note"] if info else "未识别文件类型",
        }
        if info and info.get("freq"):
            item["freq"] = info["freq"]
            item["part"] = info["part"]
            item["market"] = info["market"]
            item["content"] = info["content"]
        if name.lower().endswith(".zip"):
            n, sample = _zip_entries(path)
            item["entries"] = n
            item["sample"] = sample
        files.append(item)

    # 同日同类型同分片的重复文件（大小一致 → 标记为副本）
    seen = {}
    for f in files:
        sig = (f["date"], f["kind"], f["size"])
        if f["kind"] != "other" and sig in seen:
            f["dup"] = seen[sig]
        else:
            seen.setdefault(sig, f["name"])

    by_date = defaultdict(list)
    for f in files:
        by_date[f["date"] or "无日期"].append(f)

    dates = []
    for d in sorted(by_date, reverse=True):
        items = by_date[d]
        cat_order = ["minute", "daily", "flow", "limit", "lhb", "theme", "base", "finance", "adj", "fund", "other"]

        def _cat_key(c):
            return (cat_order.index(c) if c in cat_order else 99, c)

        cats = defaultdict(list)
        for f in items:
            cats[f["cat"]].append(f)
        out_cats = []
        for c in sorted(cats, key=_cat_key):
            v = cats[c]
            if c == "minute":
                v.sort(key=lambda x: (int(x.get("freq", 99)), str(x.get("part") or ""), x["name"]))
            else:
                v.sort(key=lambda x: x["name"])
            out_cats.append({"cat": c, "label": CATEGORY_LABEL.get(c, c), "items": v})
        dates.append({
            "date": d,
            "count": len(items),
            "size": sum(f["size"] for f in items),
            "cats": out_cats,
        })
    return {"dir": pack_dir, "exists": True, "dates": dates,
            "file_count": len(files)}


# ────────────────────────── DuckDB 入库状态 ──────────────────────────

_META = {"ts": 0, "data": None}
META_TTL = 60


def invalidate_meta():
    _META["ts"] = 0


def db_status(force=False):
    """每张表最新日期（读 table_meta），用于判断某天是否已入库。"""
    now = time.time()
    if not force and _META["data"] is not None and now - _META["ts"] < META_TTL:
        return _META["data"]
    data = {}
    try:
        con = config.tune_duck(duckdb.connect(config.DUCKDB, read_only=True))
        try:
            rows = con.execute("select table_name, date_col, max_val from table_meta").fetchall()
            for t, c, v in rows:
                data[t] = {"date_col": c, "max": str(v)}

            # 注意：不要在 149GB 库上对大表做 max() 全表扫描（外盘上可能几十秒）。
            # 缺失的表就返回 None，导入成功后由 _update_meta 补上。
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        data = {"_error": f"{type(e).__name__}: {e}"}
    _META.update({"ts": now, "data": data})
    return data


def _norm_date(v):
    """把 2026-10-26 / 2026-10-26 15:00:00 / 20261026 统一成 YYYYMMDD。"""
    if not v:
        return ""
    m = re.search(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", str(v))
    return f"{m.group(1)}{m.group(2)}{m.group(3)}" if m else ""


# 🆕 2026-11-03：整表刷新的快照表 —— 没有时间序列语义，
#   「max(日期) >= 文件日期」恒不成立（如 fin_company_snapshot 的「更新日期」是
#   财务数据更新日 20261031，永远小于文件日期 20261103）→ 只会误报「未导入」。
#   判据改为：**表非空即已导**。
SNAPSHOT_TABLES = {
    "fin_company_snapshot", "fin_report_full", "stock_info", "fund_info",
    "cb_info", "index_info", "board_info", "stock_rename",
}


def imported_state(date, status):
    """返回该日期各表是否已入库。"""
    out = {}
    for t, meta in (status or {}).items():
        if t.startswith("_"):
            continue
        if t in SNAPSHOT_TABLES:            # 快照表：非空即已导（无时间序列语义）
            out[t] = {"max": meta.get("max"), "ok": bool(meta.get("max"))}
        else:
            out[t] = {"max": meta.get("max"), "ok": _norm_date(meta.get("max")) >= date}
    return out


# ────────────────────────── 导入 ──────────────────────────

_PROGRESS = {"running": False, "date": "", "total": 0, "done": 0,
             "current": "", "log": [], "error": None, "results": [],
             "skipped": [], "deleted": [], "delete_failed": []}
_LOCK = threading.Lock()


def progress():
    with _LOCK:
        return dict(_PROGRESS)


def _log(msg):
    with _LOCK:
        _PROGRESS["log"].append(msg)
        _PROGRESS["log"] = _PROGRESS["log"][-80:]
    print("[packs]", msg, flush=True)


def _with_con(db_path, fn):
    con = config.tune_duck(duckdb.connect(db_path))
    try:
        return fn(con)
    finally:
        con.close()


def _insert_csv_by_name(con, path, table, date, const=None, date_col=None, dry=False):
    """表头与表列名完全一致时：DELETE 当日 + INSERT BY NAME。"""
    cols = [c[0] for c in con.execute(f"describe {table}").fetchall()]
    src = (f"read_csv_auto('{path}', header=true, all_varchar=true)")
    sel = f"select * from {src}"
    if const:
        sel = f"select *, {', '.join(repr(v)+' as '+k for k,v in const.items())} from {src}"
    if dry:
        n = con.execute(f"select count(*) from ({sel})").fetchone()[0]
        return {"table": table, "rows": int(n), "dry": True}
    if date_col:
        con.execute(f"delete from {table} where cast({date_col} as varchar) like ?", [f"{date[:4]}-{date[4:6]}-{date[6:8]}%"])
    con.execute(f"insert into {table} by name {sel}")
    return {"table": table, "rows": 0, "dry": False}


def _imp_minute(con, path, table, date, market, dry=False):
    tmp = tempfile.mkdtemp(prefix="packs_min_")
    try:
        with zipfile.ZipFile(path) as zf:
            # 跳过 macOS 压缩产生的 __MACOSX / ._xxx（否则会被 *.csv 通配吃进来）
            for nm in zf.namelist():
                if nm.startswith("._") or "__MACOSX" in nm:
                    continue
                zf.extract(nm, tmp)
        pattern = os.path.join(tmp, "*.csv")
        # 指数分片没有「成交量」列（DB 里 idx 的 volume 就是 NULL）
        # 取排序后第一个真 CSV（排除 ._ 垃圾）判断表头
        csvs = sorted(f for f in os.listdir(tmp)
                      if f.lower().endswith(".csv") and not f.startswith("._"))
        has_vol = False
        if csvs:
            try:
                with open(os.path.join(tmp, csvs[0]), encoding="utf-8-sig", errors="replace") as fh:
                    has_vol = "成交量" in fh.readline()
            except Exception:
                pass
        vol_expr = "cast(成交量 as double)" if has_vol else "null::double"
        base = (f"select '{market}' as market, 代码 as code, "
                f"coalesce(try_strptime(时间, '%Y/%m/%d %H:%M'), "
                f"try_strptime(时间, '%Y-%m-%d %H:%M:%S'), "
                f"try_strptime(时间, '%Y-%m-%d %H:%M')) as dt, "
                f"cast(开盘价 as double) as open, cast(最高价 as double) as high, "
                f"cast(最低价 as double) as low, cast(收盘价 as double) as close, "
                f"{vol_expr} as volume, cast(成交额 as double) as amount "
                f"from read_csv_auto('{pattern}', header=true, all_varchar=true, union_by_name=true)")
        if dry:
            n = con.execute(f"select count(*) from ({base})").fetchone()[0]
            return {"table": table, "rows": int(n), "dry": True}
        d0 = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        n = con.execute(f"select count(*) from ({base})").fetchone()[0]
        win = [market, f"{d0} 00:00:00", d0]
        # 🔴 DELETE + INSERT 必须同事务：否则 INSERT 失败时当天该 market 数据已被删光且不可恢复
        con.execute("begin transaction")
        try:
            con.execute(
                f"delete from {table} where market = ? and dt >= ?::timestamp "
                f"and dt < ?::timestamp + interval 1 day", win)
            con.execute(f"insert into {table} (market, code, dt, open, high, low, close, volume, amount) {base}")
            got = con.execute(
                f"select count(*) from {table} where market = ? and dt >= ?::timestamp "
                f"and dt < ?::timestamp + interval 1 day", win).fetchone()[0]
            if got != n:
                raise RuntimeError(f"{table}[{market}] 行数校验失败：入库 {got} ≠ 源 {n}")
            con.execute("commit")
        except Exception:
            con.execute("rollback")
            raise
        return {"table": table, "rows": int(n), "dry": False}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def _imp_daily26(con, path, date, dry=False):
    """26 列日线包 → daily / daily_basic / daily_mv"""
    d0 = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    src = (f"read_csv_auto('{path}', header=true, all_varchar=true)")
    market_expr = ("case when 股票代码 like '6%' or 股票代码 like '68%' then 'hs' "
                   "when 股票代码 like '%.BJ' then 'bj' when 股票代码 like '%.SH' then 'hs' else 'hs' end")
    code_expr = "lower(replace(股票代码, '.SZ', 'sz'))"
    # 统一成 sh/sz/bj 前缀代码
    code_norm = ("case when 股票代码 like '6%' then 'sh'||split_part(股票代码,'.',1) "
                 "when 股票代码 like '%.SH' then 'sh'||split_part(股票代码,'.',1) "
                 "else 'sz'||split_part(股票代码,'.',1) end")
    code_norm = ("case when 股票代码 like '%.BJ' then 'bj'||split_part(股票代码,'.',1) "
                 "when 股票代码 like '%.SH' then 'sh'||split_part(股票代码,'.',1) "
                 "else 'sz'||split_part(股票代码,'.',1) end")
    ops = {
        "daily": f"""select {market_expr} as market, {code_norm} as code,
            strptime(交易日期,'%Y-%m-%d')::date as date, cast(开盘价 as double) as open, cast(最高价 as double) as high,
            cast(最低价 as double) as low, cast(收盘价 as double) as close, cast("成交量(手)" as double) as volume,
            cast("成交额(千元)" as double)*1000 as amount, cast(涨跌幅 as double) as pct_chg from {src}""",
        "daily_basic": f"""select split_part(股票代码,'.',1) as ts_code,
            strptime(交易日期,'%Y-%m-%d')::date as trade_date,
            cast("总市值(万元)" as double)/10000 as total_mv,
            cast("市盈率TTM" as double) as pe_ttm, cast(市净率 as double) as pb from {src}""",
        "daily_mv": f"""select {market_expr} as market, {code_norm} as code,
            strptime(交易日期,'%Y-%m-%d')::date as date, cast(收盘价 as double) as close,
            cast("总市值(万元)" as double)/10000 as total_mv_yi,
            cast("流通市值(万元)" as double)/10000 as circ_mv_yi from {src}""",
    }
    # 三张表的日期列名不同：daily/daily_mv 是 date，daily_basic 是 trade_date
    date_cols = {"daily": "date", "daily_basic": "trade_date", "daily_mv": "date"}
    res = []
    for table, sel in ops.items():
        try:
            if dry:
                n = con.execute(f"select count(*) from ({sel})").fetchone()[0]
                res.append({"table": table, "rows": int(n), "dry": True})
                continue
            dc = date_cols[table]
            # 🔴 安全删除（2026-10-28 修）：DELETE 必须与 INSERT 的日期一致。
            #   原实现用传参 d0 删除、却按文件内容日期插入 → 当 date_override 与
            #   文件真实日期不同时，会删掉一个无关交易日的完好数据（血证：10-27 全表被删）。
            src_dates = [r[0] for r in con.execute(
                f"select distinct {dc} from ({sel}) where {dc} is not null").fetchall()]
            for _d in src_dates:
                con.execute(f"delete from {table} where {dc} = cast(? as date)", [_d])
            if d0 and d0 not in [str(x) for x in src_dates]:
                # 兼容：文件内容没日期时，退回用传参日期删一次
                con.execute(f"delete from {table} where {dc} = date '{d0}'")
            n = con.execute(f"select count(*) from ({sel})").fetchone()[0]
            con.execute(f"insert into {table} {sel}")
            res.append({"table": table, "rows": int(n), "dry": False})
            if table == "daily":
                # limit_price 无源包 → 由 daily 自动派生（今日涨停价/跌停价/昨收）
                try:
                    n_lp = derive_limit_price(con, date)
                    res.append({"table": "limit_price", "rows": int(n_lp), "dry": False,
                                "note": "由 daily 派生"})
                except Exception as e:  # noqa: BLE001
                    res.append({"table": "limit_price", "error": f"{type(e).__name__}: {e}"})
        except Exception as e:  # noqa: BLE001
            res.append({"table": table, "error": f"{type(e).__name__}: {e}"})
    return res


def _imp_moneyflow(con, path, date, dry=False):
    d0 = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    cols = {
        "s_buy_vol": "小单买入量(手)", "s_buy_amt": "小单买入金额(万元)",
        "s_sell_vol": "小单卖出量(手)", "s_sell_amt": "小单卖出金额(万元)",
        "m_buy_vol": "中单买入量(手)", "m_buy_amt": "中单买入金额(万元)",
        "m_sell_vol": "中单卖出量(手)", "m_sell_amt": "中单卖出金额(万元)",
        "l_buy_vol": "大单买入量(手)", "l_buy_amt": "大单买入金额(万元)",
        "l_sell_vol": "大单卖出量(手)", "l_sell_amt": "大单卖出金额(万元)",
        "xl_buy_vol": "特大单买入量(手)", "xl_buy_amt": "特大单买入金额(万元)",
        "xl_sell_vol": "特大单卖出量(手)", "xl_sell_amt": "特大单卖出金额(万元)",
        "net_vol": "净流入量(手)", "net_amt": "净流入金额(万元)",
    }
    # 🔴 2026-11-01：原来输出前缀式(sz000001)，但本表规范是**后缀式**(000001.SZ)，
    #   zip 通道写的是后缀式 → 两条路互不相认，全历史叠出 1,405 万行双份。
    sel = ("select " + _code_norm_expr("股票代码", "dot") + " as code, "
           "coalesce(try_strptime(cast(交易日期 as varchar),'%Y%m%d'), "
           "try_strptime(cast(交易日期 as varchar),'%Y-%m-%d'))::date as date, "
           + ", ".join(f"\"{zh}\" as {en}" for en, zh in cols.items())
           + f" from read_csv_auto('{path}', header=true, all_varchar=false)")
    n = con.execute(f"select count(*) from ({sel})").fetchone()[0]
    if dry:
        return {"table": "moneyflow_stock", "rows": int(n), "dry": True}
    con.execute("delete from moneyflow_stock where date = cast(? as date)", [d0])
    con.execute(f"insert into moneyflow_stock {sel}")
    return {"table": "moneyflow_stock", "rows": int(n), "dry": False}


def _imp_theme_zip(con, path, date, dry=False):
    tmp = tempfile.mkdtemp(prefix="packs_theme_")
    try:
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp)
        pattern = os.path.join(tmp, "*.csv")
        # 每行: 股票名称? 需实测列；先按 header 自适应
        sel = (f"select 题材代码, 题材名称, 成分股票名称, 成分股票代码, 交易日期, 描述, 人气值 "
               f"from read_csv_auto('{pattern}', header=true, all_varchar=true, union_by_name=true)")
        n = con.execute(f"select count(*) from ({sel})").fetchone()[0]
        if dry:
            return {"table": "theme_stock", "rows": int(n), "dry": True}
        d0 = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        con.execute("delete from theme_stock where 交易日期 = ?", [d0.replace('-', '')])
        con.execute(f"insert into theme_stock {sel}")
        return {"table": "theme_stock", "rows": int(n), "dry": False}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



# ══════════════════════ 2026-10-28 新增 importer ══════════════════════
# 解决「每天必导却导不进去」的 4 类：每日指标 / 复权因子 / 技术因子单日 / limit_price 派生

def _zip_iter_csv(zpath):
    """遍历「每股票一 csv」的 zip，逐文件 yield (文件名, 行列表)。跳过 macOS 垃圾。"""
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist()
                 if n.lower().endswith(".csv") and not n.startswith("._") and "__MACOSX" not in n]
        for n in names:
            try:
                raw = z.read(n)
            except Exception:
                continue
            txt = None
            for enc in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    txt = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if txt is None:
                continue
            yield n, txt.splitlines()


def _imp_daily_ind_zip(con, path, date, dry=False):
    """每日指标.zip（每票一 csv，25 列）→ daily_ind + daily_mv。
    只抽目标日期的行：逐文件扫，比 DuckDB 全量读 5918 个 csv 快一个量级。"""
    # 🔴 注意：库里 daily_ind 的股息率列名是 div_yield / div_yield_ttm，不是 dv_ratio / dv_ttm
    COLS = ["code", "d", "open", "high", "low", "close", "pre_close", "chg", "pct_chg",
            "vol_hand", "amount_k", "turnover", "turnover_free", "vol_ratio",
            "pe", "pe_ttm", "pb", "ps", "ps_ttm", "div_yield", "div_yield_ttm",
            "total_share", "float_share", "free_share", "total_mv", "circ_mv"]
    rows = []
    for _n, lines in _zip_iter_csv(path):
        for line in lines:
            if not line or line.startswith("股票代码"):
                continue
            p = line.split(",")
            if len(p) < 26:
                continue
            if p[1].replace("-", "").replace("/", "")[:8] != date:
                continue
            rows.append([x.strip() for x in p[:26]])
    n = len(rows)
    if dry:
        return [{"table": "daily_ind", "rows": n, "dry": True},
                {"table": "daily_mv", "rows": n, "dry": True}]
    if n == 0:
        raise RuntimeError(f"每日指标.zip 里没有 {date} 的数据（包可能是旧的）")

    d0 = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    con.execute("CREATE OR REPLACE TEMP TABLE _di ("
                + ", ".join(f"{c} VARCHAR" for c in COLS) + ")")
    con.executemany(f"INSERT INTO _di VALUES ({','.join('?' * len(COLS))})", rows)
    # 数值化 + 日期化
    num_cols = [c for c in COLS if c not in ("code", "d")]
    sel = ("SELECT code, CAST(strptime(d,'%Y-%m-%d') AS DATE) AS date, "
           + ", ".join(f"try_cast({c} AS DOUBLE) AS {c}" for c in num_cols)
           + " FROM _di")
    con.execute(f"CREATE OR REPLACE TEMP TABLE _di2 AS SELECT * FROM ({sel})")
    ins_cols = ["code", "date"] + num_cols

    con.execute("BEGIN")
    try:
        _sd = [r[0] for r in con.execute(
            f"select distinct date from ({sel}) where date is not null").fetchall()]
        for _x in _sd:
            con.execute("DELETE FROM daily_ind WHERE date = cast(? as date)", [_x])
        con.execute(f"INSERT INTO daily_ind ({','.join(ins_cols)}) SELECT {','.join(ins_cols)} FROM _di2")
        got = con.execute(f"SELECT COUNT(*) FROM daily_ind WHERE date = DATE '{d0}'").fetchone()[0]
        if got != n:
            raise RuntimeError(f"daily_ind 行数校验失败：{got} ≠ {n}")
        # daily_mv 派生：000001.SZ → code=sz000001 / market=hs
        con.execute("CREATE OR REPLACE TEMP TABLE _mv AS "
                    "SELECT CASE WHEN code LIKE '%.BJ' THEN 'bj' ELSE 'hs' END AS market, "
                    "lower(split_part(code,'.',2)) || split_part(code,'.',1) AS code, "
                    "date, close, total_mv/10000 AS total_mv_yi, circ_mv/10000 AS circ_mv_yi "
                    "FROM _di2")
        _sd2 = [r[0] for r in con.execute(
            f"select distinct date from ({sel}) where date is not null").fetchall()]
        for _x in _sd2:
            con.execute("DELETE FROM daily_mv WHERE date = cast(? as date)", [_x])
        con.execute("INSERT INTO daily_mv SELECT * FROM _mv")
        got2 = con.execute(f"SELECT COUNT(*) FROM daily_mv WHERE date = DATE '{d0}'").fetchone()[0]
        if got2 != n:
            raise RuntimeError(f"daily_mv 行数校验失败：{got2} ≠ {n}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return [{"table": "daily_ind", "rows": n, "dry": False},
            {"table": "daily_mv", "rows": n, "dry": False}]


def _imp_adj_zip(con, path, date, dry=False):
    """复权因子_前复权.zip → adj_factor.qfq_factor｜复权因子_后复权.zip → adj_factor.hfq_factor
    「复权因子.zip」= 累计因子，写入 tech_factor_* 的 adj 语义，这里不处理。"""
    base = os.path.basename(path)
    if "后复权" in base:
        col = "hfq_factor"
    elif "前复权" in base:
        col = "qfq_factor"
    else:
        raise RuntimeError(f"{base}：无法判定前/后复权，拒绝导入（避免写错列）")
    pairs = []
    for _n, lines in _zip_iter_csv(path):
        # 只看尾部几行（包内按日期升序）
        for line in reversed(lines[-6:]):
            p = line.split(",")
            if len(p) < 3:
                continue
            d = p[1].replace("-", "").replace("/", "")[:8]
            if d == date:
                v = p[2].strip()
                pairs.append([p[0].strip(), None if v in ("", "nan", "None") else float(v)])
                break
    n = len(pairs)
    if dry:
        return {"table": "adj_factor", "rows": n, "dry": True, "column": col}
    if n == 0:
        raise RuntimeError(f"{base} 里没有 {date} 的数据（包可能是旧的）")
    d0 = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    con.execute("CREATE OR REPLACE TEMP TABLE _adj (code VARCHAR, v DOUBLE)")
    con.executemany("INSERT INTO _adj VALUES (?, ?)", pairs)
    con.execute("BEGIN")
    try:
        # 该日行不存在则先补空行，再只更新本包负责的那一列
        con.execute(f"""INSERT INTO adj_factor (code, date, qfq_factor, hfq_factor)
                        SELECT a.code, DATE '{d0}', NULL, NULL FROM _adj a
                        WHERE NOT EXISTS (SELECT 1 FROM adj_factor f
                                          WHERE f.code = a.code AND f.date = DATE '{d0}')""")
        con.execute(f"""UPDATE adj_factor SET {col} = a.v FROM _adj a
                        WHERE adj_factor.code = a.code AND adj_factor.date = DATE '{d0}'""")
        got = con.execute(f"SELECT COUNT({col}) FROM adj_factor WHERE date = DATE '{d0}'").fetchone()[0]
        if got < n:
            raise RuntimeError(f"adj_factor.{col} 写入不足：{got} < {n}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"table": "adj_factor", "rows": n, "dry": False, "column": col}


# ─────────────────────────────────────────────────────────────────────────────
# 通用「每股票一 csv 的全历史快照」导入器
#   覆盖：个股/行业资金流向、13 类财务数据、前十大股东/流通股东、分红配股、累计复权因子
#
# 🔴🔴 核心策略 = 「补缺口」（INSERT ... WHERE NOT EXISTS），**绝不覆盖库内已有行**。
#   理由（2026-10-28 实测）：
#     · moneyflow_stock 库 2,808 万行 / 2007 起，包只有 2010 起 → 库更全
#     · fin_income 库 38.1 万行、fin_schedule 45.5 万行、fin_main_biz 565 万行
#       —— 这些表的既有数据来自更全的渠道，覆盖会**丢数据**
#   → 包的作用是「补库内缺的那些 (股票, 报告期/日期)」，不是替换。
# ─────────────────────────────────────────────────────────────────────────────

# 包 basename → 目标表配置
#   keys  : 主键列（决定「哪些行算已存在」）
#   rename: 包内列名 → 表列名（缺省视为同名）
#   code  : 代码格式归一 dot(000001.SZ) / prefixed(sz000001)
SNAP_ZIP_CFG = {
    # ── 资金流 ──
    "个股资金流向": dict(
        table="moneyflow_stock", keys=["code", "date"], code="dot",
        rename={"股票代码": "code", "交易日期": "date",
                "小单买入量(手)": "s_buy_vol", "小单买入金额(万元)": "s_buy_amt",
                "小单卖出量(手)": "s_sell_vol", "小单卖出金额(万元)": "s_sell_amt",
                "中单买入量(手)": "m_buy_vol", "中单买入金额(万元)": "m_buy_amt",
                "中单卖出量(手)": "m_sell_vol", "中单卖出金额(万元)": "m_sell_amt",
                "大单买入量(手)": "l_buy_vol", "大单买入金额(万元)": "l_buy_amt",
                "大单卖出量(手)": "l_sell_vol", "大单卖出金额(万元)": "l_sell_amt",
                "特大单买入量(手)": "xl_buy_vol", "特大单买入金额(万元)": "xl_buy_amt",
                "特大单卖出量(手)": "xl_sell_vol", "特大单卖出金额(万元)": "xl_sell_amt",
                "净流入量(手)": "net_vol", "净流入金额(万元)": "net_amt"},
        note="资金流明细（小/中/大/特大单买卖量额）"),
    # 🔴🔴 血证（2026-10-28）：这两张表的列名写「(万元)」，**库里实存「元」**
    #   （sz000001 20261022 资金净流入 -210,983,000 = -21,098.3 万元）→ 必须 ×1e4，
    #   否则整表口径差 4 个数量级、与既有行全部失配（首轮 dry-run 待补 226 万行）。
    "个股资金流向_同花顺": dict(
        table="stock_flow_ths", keys=["交易日期", "股票代码"], code="prefixed",
        scale={"资金净流入(万元)": 1e4, "5日主力净额(万元)": 1e4,
               "今日大单净流入额(万元)": 1e4, "今日中单净流入额(万元)": 1e4,
               "今日小单净流入额(万元)": 1e4},
        note="同花顺个股资金流 12 列（金额 ×1e4 换算为元）"),
    "个股资金流向_东财": dict(
        table="stock_flow_em", keys=["交易日期", "股票代码"], code="prefixed",
        scale={"今日主力净流入额（万元）": 1e4, "今日超大单净流入额（万元）": 1e4,
               "今日大单净流入额（万元）": 1e4, "今日中单净流入额（万元）": 1e4,
               "今日小单净流入额（万元）": 1e4},
        note="东财个股资金流 15 列（金额 ×1e4 换算为元）"),
    "行业资金流向_同花顺": dict(
        table="industry_flow_ths", keys=["交易日期", "行业代码"], code="dot",
        compact=["交易日期"],
        note="同花顺行业资金流 12 列"),
    # ── 财务（13 类）──
    "利润表数据":       dict(table="fin_income",      keys=["股票代码", "报告期", "报告类型"], code="dot"),
    "资产负债表数据":   dict(table="fin_balance",     keys=["股票代码", "报告期", "报表类型"], code="dot"),
    "现金流量表数据":   dict(table="fin_cashflow",    keys=["股票代码", "报告期", "报告类型"], code="dot"),
    "财务指标数据":     dict(table="fin_indicator",   keys=["股票代码", "报告期"], code="dot"),
    "业绩预告数据":     dict(table="fin_forecast",    keys=["股票代码", "报告期"], code="dot"),
    "业绩快报数据":     dict(table="fin_express",     keys=["股票代码", "报告期"], code="dot"),
    "财务审计意见数据": dict(table="fin_audit",       keys=["股票代码", "报告期"], code="dot"),
    "财报披露计划":     dict(table="fin_schedule",    keys=["股票代码", "报告期"], code="dot"),
    "主营业务构成数据": dict(table="fin_main_biz",    keys=["股票代码", "报告期", "类型", "主营业务来源"], code="dot"),
    "分红送股数据":     dict(table="fin_dividend",    keys=["股票代码", "分红年度"], code="dot"),
    "股东人数":         dict(table="fin_holder_num",  keys=["股票代码", "截止日期"], code="dot",
                              compact=["公告日期"]),
    "股权质押明细":     dict(table="fin_pledge_detail",
                              keys=["股票代码", "公告日期", "股东名称", "质押开始日期"], code="dot"),
    "股权质押统计":     dict(table="fin_pledge_stat", keys=["股票代码", "截止日期"], code="dot"),
    # ── 股东结构 ──
    "前十大股东":       dict(table="fin_top10_holder", keys=["股票代码", "报告期", "股东名称"], code="dot"),
    "前十大流通股东":   dict(table="fin_top10_float_holder", keys=["股票代码", "报告期", "股东名称"], code="dot"),
    # ── 分红配股（含配股方案，19 列，独立表）──
    "分红配股":         dict(table="fin_dividend_bonus",
                              keys=["股票代码", "公告日期", "类型"], code="bare"),
}

_DATEISH = ("日期", "报告期", "分红年度", "截止日", "首次公告日")


def _code_norm_expr(col, fmt):
    """代码格式归一 SQL 表达式（全 varchar 输入）。"""
    c = f'"{col}"'
    if fmt == "prefixed":
        # 000001.SZ → sz000001 ；已带前缀的原样返回
        return (f"CASE WHEN regexp_matches({c}, '^(sz|sh|bj)') THEN lower({c}) "
                f"WHEN {c} LIKE '%.SZ' THEN 'sz' || split_part({c}, '.', 1) "
                f"WHEN {c} LIKE '%.SH' THEN 'sh' || split_part({c}, '.', 1) "
                f"WHEN {c} LIKE '%.BJ' THEN 'bj' || split_part({c}, '.', 1) "
                f"ELSE {c} END")
    if fmt == "dot":
        # 纯 6 位 → 按首位补 .SZ/.SH/.BJ
        # 🆕 2026-11-01：**前缀式也要能转后缀式**（原来 8 位前缀落进 ELSE 原样返回 →
        #   混存校验形同虚设，血证 moneyflow_stock 14,049,985 行双份）
        return (f"CASE WHEN regexp_matches({c}, '^(sh|sz|bj)[0-9]{{6}}$') "
                f"THEN substr({c}, 3) || '.' || upper(substr({c}, 1, 2)) "
                f"WHEN {c} LIKE '%.%' THEN upper({c}) "
                f"WHEN len({c}) = 6 AND {c} LIKE '6%' THEN {c} || '.SH' "
                f"WHEN len({c}) = 6 AND {c} LIKE '9%' THEN {c} || '.BJ' "
                f"WHEN len({c}) = 6 AND ({c} LIKE '4%' OR {c} LIKE '8%') THEN {c} || '.BJ' "
                f"WHEN len({c}) = 6 THEN {c} || '.SZ' "
                f"ELSE {c} END")
    return c  # bare：原样


# 🆕 2026-11-01：每张表的**规范代码格式**（写库前强制归一 + 写前断言）
#   只登记「会被多条导入路径写入」的表；未登记的表不做任何改写。
TABLE_CODE_FMT = {
    "stock_flow_em": "prefixed",
    "stock_flow_ths": "prefixed",
    "moneyflow_stock": "dot",
}


def _norm_code_df(df, table, col="股票代码"):
    """把 df 的代码列按 TABLE_CODE_FMT 归一（pandas 实现，幂等）。"""
    fmt = TABLE_CODE_FMT.get(table)
    if not fmt or df is None or col not in df.columns or len(df) == 0:
        return df
    s = df[col].astype(str).str.strip()
    if fmt == "prefixed":
        is_pre = s.str.match(r"^(sh|sz|bj)[0-9]{6}$", case=False, na=False)
        out = s.str.lower()
        for suf in ("SZ", "SH", "BJ"):
            m = (~is_pre) & s.str.upper().str.endswith("." + suf, na=False)
            if m.any():
                body = s.str.split(".").str[0]
                out = out.where(~m, suf.lower() + body)
    else:  # dot
        is_pre = s.str.match(r"^(sh|sz|bj)[0-9]{6}$", case=False, na=False)
        out = s.where(~is_pre, s.str[2:8] + "." + s.str[:2].str.upper())
        no_dot = (~is_pre) & (~s.str.contains(r"\.", na=False)) & (s.str.len() == 6)
        if no_dot.any():
            guess = s.str[0].map(lambda ch: "SH" if ch == "6"
                                 else ("BJ" if ch in "489" else "SZ"))
            out = out.where(~no_dot, s + "." + guess.fillna("SZ"))
        has_dot = (~is_pre) & s.str.contains(r"\.", na=False)
        out = out.where(~has_dot, out.str.upper())
    df = df.copy()
    df[col] = out
    return df


def _assert_single_fmt(vals, table, where=""):
    """写库前断言：同一批数据不能同时含前缀式与后缀式（否则必产生双份）。"""
    if not TABLE_CODE_FMT.get(table):
        return
    import pandas as _pd
    s = _pd.Series(vals).astype(str).str.strip()
    n_pre = int(s.str.match(r"^(sh|sz|bj)[0-9]{6}$", case=False, na=False).sum())
    n_dot = int(s.str.match(r"^[0-9]{6}\.(SH|SZ|BJ)$", case=False, na=False).sum())
    if n_pre and n_dot:
        raise RuntimeError(
            f"{table}{where}：批内代码格式混存（前缀式 {n_pre:,} / 后缀式 {n_dot:,}）"
            f"→ 拒绝写入（会造成同一天双份）。请先归一为 {TABLE_CODE_FMT[table]}")


def _cast_expr(col, ty, compact=False):
    """按目标表列类型生成 CAST 表达式（全 varchar 输入 → 目标类型）。

    compact=True：VARCHAR 日期列**保持 8 位**（库里 industry_flow_ths / fin_holder_num
    的日期就是 'YYYYMMDD'，转成 ISO 会与既有行全部失配）。
    """
    c = f'"{col}"'
    is_date = any(k in col for k in _DATEISH)
    tu = ty.upper()
    if tu.startswith("VARCHAR"):
        if is_date and compact:
            return (f"CASE WHEN trim({c}) = '' THEN NULL "
                    f"ELSE replace(replace(trim({c}), '-', ''), '/', '') END")
        if is_date:
            return (f"CASE WHEN regexp_matches(trim({c}), '^\\d{{8}}$') "
                    f"THEN strftime(strptime(trim({c}), '%Y%m%d'), '%Y-%m-%d') "
                    f"ELSE NULLIF(trim({c}), '') END")
        return f"NULLIF(trim({c}), '')"
    if tu in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT"):
        if is_date:
            return f"TRY_CAST(replace(replace(trim({c}), '-', ''), '/', '') AS {ty})"
        return f"TRY_CAST(trim({c}) AS {ty})"
    if tu == "DATE":
        return (f"TRY_CAST(CASE WHEN regexp_matches(trim({c}), '^\\d{{8}}$') "
                f"THEN strftime(strptime(trim({c}), '%Y%m%d'), '%Y-%m-%d') "
                f"ELSE NULLIF(trim({c}), '') END AS DATE)")
    return f"TRY_CAST(trim({c}) AS {ty})"


def _imp_snapshot_zip(con, path, dry=False, limit_files=None):
    """「每股票一 csv 的全历史快照」通用导入器（配置驱动，见 SNAP_ZIP_CFG）。

    返回 {"table", "rows", "note"}；dry=True 只报「待补 N 行 / 已有 M 行」，不写库。
    """
    base = re.sub(r"\.zip$", "", os.path.basename(path))
    cfg = SNAP_ZIP_CFG.get(base)
    if not cfg:
        raise RuntimeError(f"{base}.zip：未在 SNAP_ZIP_CFG 登记，拒绝导入")
    table = cfg["table"]
    keys = cfg["keys"]
    rename = cfg.get("rename") or {}
    codefmt = cfg.get("code", "dot")
    compact = set(cfg.get("compact") or [])
    scale = cfg.get("scale") or {}

    tmpdir = tempfile.mkdtemp(prefix="pkg_snap_")
    flat = os.path.join(tmpdir, "flat.csv")
    hdr = None
    n_src = 0
    n_bad = 0
    try:
        tcols = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {table}").fetchall()]
        tnames = [c for c, _ in tcols]
        with open(flat, "w", encoding="utf-8", newline="") as out:
            w = csv.writer(out)
            nf = 0
            for _nm, lines in _zip_iter_csv(path):
                nf += 1
                if limit_files and nf > limit_files:
                    break
                if not lines:
                    continue
                h = [x.strip() for x in next(csv.reader([lines[0]]))]
                if hdr is None:
                    hdr = [rename.get(x, x) for x in h]
                    # 只保留目标表有的列（顺序按目标表）
                    w.writerow([c for c in tnames if c in hdr])
                    keep = [i for i, c in enumerate(hdr) if c in tnames]
                for line in lines[1:]:
                    if not line.strip():
                        continue
                    try:
                        p = next(csv.reader([line]))
                    except Exception:
                        n_bad += 1
                        continue
                    if len(p) < len(hdr):
                        p = p + [""] * (len(hdr) - len(p))
                    row = [p[i].strip() for i in keep]
                    w.writerow(row)
                    n_src += 1
        if n_src == 0:
            raise RuntimeError(f"{base}.zip 里没有解析出任何行")

        con.execute("CREATE OR REPLACE TEMP TABLE _sn0 AS SELECT * FROM read_csv(?, header=true, all_varchar=true)", [flat])
        avail = [r[0] for r in con.execute("DESCRIBE _sn0").fetchall()]
        sel = []
        for c, ty in tcols:
            if c not in avail:
                sel.append(f"NULL AS \"{c}\"")
            elif c == "股票代码" and codefmt in ("dot", "prefixed"):
                sel.append(f'{_code_norm_expr(c, codefmt)} AS "{c}"')
            elif c in ("code",) and codefmt in ("dot", "prefixed"):
                sel.append(f'{_code_norm_expr(c, codefmt)} AS "{c}"')
            else:
                e = _cast_expr(c, ty, c in compact)
                if c in scale:
                    e = f"({e}) * {scale[c]}"
                sel.append(f'{e} AS "{c}"')
        con.execute("CREATE OR REPLACE TEMP TABLE _sn AS SELECT " + ", ".join(sel) + " FROM _sn0")
        # 🔴 存在性判定 = **整行精确匹配**（IS NOT DISTINCT FROM 逐列），不用主键。
        #   理由：库内这些表本身带重复行（fin_income 差 3.3 万 / fin_cashflow 差 5.7 万 /
        #   fin_schedule 同 (股票,报告期) 有多次公告）→ 用主键判定会漏补；
        #   用整行判定语义才是「包里这行，库里有没有一模一样的」。
        idcol = "股票代码" if "股票代码" in tnames else ("code" if "code" in tnames else None)
        base_filter = f'"{idcol}" IS NOT NULL' if idcol else "TRUE"
        con.execute(f"CREATE OR REPLACE TEMP TABLE _sn2 AS SELECT DISTINCT * FROM _sn WHERE {base_filter}")
        # 🆕 2026-11-01：写前断言代码格式单一（防「同一天前缀+后缀双份」）
        if idcol:
            _assert_single_fmt(
                [r[0] for r in con.execute(f'SELECT DISTINCT "{idcol}" FROM _sn2').fetchall()],
                table, f" {base}.zip")
        want = con.execute("SELECT COUNT(*) FROM _sn2").fetchone()[0]
        match = " AND ".join(f't."{c}" IS NOT DISTINCT FROM f."{c}"' for c in tnames)
        new_rows = con.execute(
            "SELECT COUNT(*) FROM _sn2 f WHERE NOT EXISTS (SELECT 1 FROM " + table + " t WHERE " + match + ")").fetchone()[0]
        dup = want - new_rows

        if dry:
            return {"table": table, "rows": n_src, "dry": True,
                    "note": f"{cfg.get('note','')} · 包内 {n_src:,} 行 / 去重 {want:,} · "
                            f"待补 {new_rows:,} 行 / 库内已有 {dup:,} 行"
                            + (f" · 跳过坏行 {n_bad}" if n_bad else "")}

        con.execute("BEGIN")
        try:
            if new_rows:
                con.execute('INSERT INTO ' + table + ' SELECT * FROM _sn2 f WHERE NOT EXISTS (SELECT 1 FROM '
                            + table + ' t WHERE ' + match + ")")
            got = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return {"table": table, "rows": new_rows,
                "note": (f"{cfg.get('note','')} · 包内 {want:,} 行（去重后）· 补入 {new_rows:,} 行"
                         f" / 库内已有 {dup:,} 行保留 · 表 {got:,} 行"
                         + (f" · 跳过坏行 {n_bad}" if n_bad else ""))}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _imp_stock_flow_zip(con, path, date, dry=False):
    """个股资金流向(.zip) / _东财 / _同花顺 / 行业资金流向_同花顺 → 4 张资金流表。"""
    return _imp_snapshot_zip(con, path, dry=dry)


def _imp_fin_zip(con, path, date, dry=False):
    """13 类财务数据 + 2 个股东结构 + 分红配股 → fin_* 表（补缺口）。"""
    return _imp_snapshot_zip(con, path, dry=dry)


def _imp_adj_cum_zip(con, path, date, dry=False):
    """复权因子.zip（累计后复权因子，每股票一 csv 全历史）→ adj_factor.hfq_factor。

    🔴 只写 hfq_factor 列，**不动 qfq_factor**（前复权因子每日重定基，必须整包重刷，
       用 scripts/resync_adj_full.py；单日/增量 upsert 会让历史永停在 1.0）
    🔴 源包后缀不可信：37 只深 B（200xxx/201xxx）被标成 .BJ → 强制归一 .SZ
    """
    tmpdir = tempfile.mkdtemp(prefix="pkg_adjcum_")
    flat = os.path.join(tmpdir, "flat.csv")
    n_src = 0
    try:
        with open(flat, "w", encoding="utf-8", newline="") as out:
            w = csv.writer(out)
            w.writerow(["code", "d8", "v"])
            for _nm, lines in _zip_iter_csv(path):
                for line in lines:
                    p = line.split(",")
                    if len(p) < 3:
                        continue
                    code, d, v = p[0].strip(), p[1].strip(), p[2].strip()
                    if not code or code == "股票代码":
                        continue
                    d8 = d.replace("-", "").replace("/", "")[:8]
                    if len(d8) != 8 or not d8.isdigit():
                        continue
                    # 🔴 深 B 股错标归一：200xxx/201xxx.BJ → .SZ
                    if code.endswith(".BJ") and code[:3] in ("200", "201"):
                        code = code[:6] + ".SZ"
                    w.writerow([code, d8, v])
                    n_src += 1
        if n_src == 0:
            raise RuntimeError("复权因子.zip 里没有解析出任何行")
        con.execute("CREATE OR REPLACE TEMP TABLE _ac0 AS "
                    "SELECT code, d8, NULLIF(trim(v), '') AS v FROM read_csv(?, header=true, all_varchar=true)",
                    [flat])
        con.execute("CREATE OR REPLACE TEMP TABLE _ac AS "
                    "SELECT code, CAST(strptime(d8, '%Y%m%d') AS DATE) AS date, TRY_CAST(v AS DOUBLE) AS v "
                    "FROM _ac0 QUALIFY row_number() OVER (PARTITION BY code, d8) = 1")
        n_new = con.execute("SELECT COUNT(*) FROM _ac f WHERE NOT EXISTS "
                            "(SELECT 1 FROM adj_factor t WHERE t.code = f.code AND t.date = f.date)").fetchone()[0]
        n_upd = con.execute("SELECT COUNT(*) FROM _ac f WHERE EXISTS "
                            "(SELECT 1 FROM adj_factor t WHERE t.code = f.code AND t.date = f.date)").fetchone()[0]
        dmin, dmax = con.execute("SELECT MIN(date), MAX(date) FROM _ac").fetchone()
        nc = con.execute("SELECT COUNT(DISTINCT code) FROM _ac").fetchone()[0]
        if dry:
            return {"table": "adj_factor", "rows": n_src, "dry": True,
                    "note": f"{nc} 只 · {dmin}~{dmax} · 补 {n_new:,} 行 / 更新 hfq {n_upd:,} 行（qfq 不动）"}
        con.execute("BEGIN")
        try:
            con.execute("INSERT INTO adj_factor (code, date, qfq_factor, hfq_factor) "
                        "SELECT f.code, f.date, NULL, f.v FROM _ac f WHERE NOT EXISTS "
                        "(SELECT 1 FROM adj_factor t WHERE t.code = f.code AND t.date = f.date)")
            con.execute("UPDATE adj_factor SET hfq_factor = f.v FROM _ac f "
                        "WHERE adj_factor.code = f.code AND adj_factor.date = f.date "
                        "AND adj_factor.hfq_factor IS DISTINCT FROM f.v")
            got = con.execute("SELECT COUNT(*) FROM adj_factor").fetchone()[0]
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return {"table": "adj_factor", "rows": n_new,
                "note": f"{nc} 只 · {dmin}~{dmax} · 补 {n_new:,} 行 + 校正 hfq {n_upd:,} 行 · 表 {got:,} 行"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


TECH_ZIP_MAP = {
    "技术因子_前复权": ("tech_factor_qfq", False),
    "技术因子_后复权": ("tech_factor_hfq", False),
}
TECH_COLS = ["open", "high", "low", "close", "vol_hand", "amount_k", "pct_chg", "turnover",
             "vol_ratio", "adj", "ma5", "ma10", "ma20", "ma60", "ema5", "ema10", "ema20", "ema60",
             "macd_dif", "macd_dea", "macd", "rsi6", "rsi12", "rsi24", "kdj_k", "kdj_d", "kdj_j",
             "boll_up", "boll_mid", "boll_low", "atr", "obv", "up_days", "down_days",
             "new_high_days", "new_low_days"]


def _imp_tech_zip(con, path, date, dry=False, limit_files=None):
    """技术因子_前复权.zip / _后复权.zip（38 列·每股票一 csv 全历史）→ tech_factor_qfq / _hfq。

    🔴 只补缺口，不覆盖：库内该表 10-21 起 vol_hand 口径污染（存「股」），
       覆盖会把历史正确行也一起写坏；全表重刷用 scripts/recalc_tech_factor_*.py。
    """
    # 🔴 2026-10-31：传了 date → 只抽该日（内存 O(股票数)）；date 空 → 全历史 diff（O(5000万行)，慎用）
    only_date = (date or "").replace("-", "")[:8] or None
    base = re.sub(r"\.zip$", "", os.path.basename(path))
    if base not in TECH_ZIP_MAP:
        raise RuntimeError(f"{base}：未登记的技术因子包")
    table, _ = TECH_ZIP_MAP[base]
    tmpdir = tempfile.mkdtemp(prefix="pkg_tech_")
    flat = os.path.join(tmpdir, "flat.csv")
    n_src = 0
    try:
        with open(flat, "w", encoding="utf-8", newline="") as out:
            w = csv.writer(out)
            w.writerow(["code", "d8"] + TECH_COLS)
            _nf = 0
            for _nm, lines in _zip_iter_csv(path):
                _nf += 1
                if limit_files and _nf > limit_files:
                    break
                if not lines:
                    continue
                for line in lines[1:]:
                    if not line.strip():
                        continue
                    p = next(csv.reader([line]))
                    if len(p) < 12:
                        continue
                    code, d = p[0].strip(), p[1].strip()
                    d8 = d.replace("-", "/").replace("/", "")[:8]
                    if len(d8) != 8 or not d8.isdigit():
                        continue
                    # 🔴 2026-10-31 修 OOM：传了 date 就只留那天（5919 行 vs 5000 万行）
                    if only_date and d8 != only_date:
                        continue
                    # 包内顺序：代码,日期,开,高,低,收,量,额,涨跌幅,换手,量比,复权因子,MA5...
                    vals = p[2:12] + p[12:len(p)]
                    vals = (vals + [""] * len(TECH_COLS))[:len(TECH_COLS)]
                    w.writerow([code, d8] + vals)
                    n_src += 1
        if n_src == 0:
            raise RuntimeError(f"{base}.zip 里没有 {only_date or '任何'} 的数据（包可能是旧的）")
        con.execute("CREATE OR REPLACE TEMP TABLE _tf0 AS SELECT * FROM read_csv(?, header=true, all_varchar=true)", [flat])
        cast = ", ".join(f"TRY_CAST(NULLIF(trim({c}), '') AS DOUBLE) AS {c}" for c in TECH_COLS)
        con.execute("CREATE OR REPLACE TEMP TABLE _tf AS SELECT code, CAST(strptime(d8, '%Y%m%d') AS DATE) AS date, "
                    + cast + " FROM _tf0 QUALIFY row_number() OVER (PARTITION BY code, d8) = 1")
        n_new = con.execute("SELECT COUNT(*) FROM _tf f WHERE NOT EXISTS "
                            "(SELECT 1 FROM " + table + " t WHERE t.code = f.code AND t.date = f.date)").fetchone()[0]
        n_dup = con.execute("SELECT COUNT(*) FROM _tf f WHERE EXISTS "
                            "(SELECT 1 FROM " + table + " t WHERE t.code = f.code AND t.date = f.date)").fetchone()[0]
        dmin, dmax = con.execute("SELECT MIN(date), MAX(date) FROM _tf").fetchone()
        nc = con.execute("SELECT COUNT(DISTINCT code) FROM _tf").fetchone()[0]
        if dry:
            return {"table": table, "rows": n_src, "dry": True,
                    "note": f"{nc} 只 · {dmin}~{dmax} · 包内 {n_src:,} 行 · 待补 {n_new:,} / 已有 {n_dup:,}"}
        con.execute("BEGIN")
        try:
            if n_new:
                con.execute("INSERT INTO " + table + " SELECT f.* FROM _tf f WHERE NOT EXISTS "
                            "(SELECT 1 FROM " + table + " t WHERE t.code = f.code AND t.date = f.date)")
            got = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return {"table": table, "rows": n_new,
                "note": f"{nc} 只 · {dmin}~{dmax} · 补入 {n_new:,} 行 / 已有 {n_dup:,} 行保留 · 表 {got:,} 行"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _imp_board_flow_zip(con, path, date, dry=False):
    """板块资金流向_东财.zip / _同花顺.zip（每板块一 csv 的全历史快照）→ board_flow_em / board_flow_ths。

    与日常 YYYYMMDD(5).csv(同花顺) / YYYYMMDD(6).csv(东财) 同源，但这里是全历史。
    → 策略：**整包全量 upsert（覆盖）**。包是权威全历史快照，且能纠正库内历史口径错乱。
      冲突行数（主字段与库内不同的行）会打印出来供对证。
    CSV 列名与表列名**完全一致**（东财 18 列 / 同花顺 12 列），可按名直插。
    """
    base = os.path.basename(path)
    if "东财" in base:
        table = "board_flow_em"
    elif "同花顺" in base:
        table = "board_flow_ths"
    else:
        raise RuntimeError(f"{base}：无法判定东财/同花顺，拒绝导入")

    tmpdir = tempfile.mkdtemp(prefix="pkg_bflow_")
    flat = os.path.join(tmpdir, "flat.csv")
    hdr = None
    n_src = 0
    K1, K2 = "交易日期", "板块代码"
    try:
        with open(flat, "w", encoding="utf-8", newline="") as out:
            w = csv.writer(out)
            for _nm, lines in _zip_iter_csv(path):
                if not lines:
                    continue
                h = [x.strip() for x in lines[0].split(",")]
                if hdr is None:
                    hdr = h
                    w.writerow(hdr)
                elif h != hdr:
                    raise RuntimeError(f"{table}：包内 CSV 表头不一致（{_nm}），拒绝导入")
                for line in lines[1:]:
                    if not line.strip():
                        continue
                    p = line.split(",")
                    p = [x.strip() for x in p] + [""] * (len(hdr) - len(p))
                    d8 = p[0].replace("-", "").replace("/", "")[:8]
                    if len(d8) != 8 or not d8.isdigit():
                        continue
                    p[0] = d8
                    w.writerow(p[:len(hdr)])
                    n_src += 1
        if n_src == 0:
            raise RuntimeError(f"{base} 里没有解析出任何行")

        con.execute("CREATE OR REPLACE TEMP TABLE _bf0 AS "
                    "SELECT * FROM read_csv(?, header=true, all_varchar=true)", [flat])
        tcols = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {table}").fetchall()]
        sel = []
        for c, ty in tcols:
            if c not in hdr:
                sel.append("NULL")
            elif ty.upper().startswith(("VARCHAR", "BIGINT", "INTEGER", "DOUBLE", "DECIMAL")):
                sel.append(f'TRY_CAST("{c}" AS {ty})')
            else:
                sel.append(f'"{c}"')
        con.execute("CREATE OR REPLACE TEMP TABLE _bf AS SELECT "
                    + ", ".join(f'{e} AS "{c}"' for e, (c, _ty) in zip(sel, tcols))
                    + " FROM _bf0")
        con.execute('CREATE OR REPLACE TEMP TABLE _bf2 AS SELECT * FROM _bf '
                    'QUALIFY row_number() OVER (PARTITION BY "交易日期", "板块代码") = 1')
        want = con.execute("SELECT COUNT(*) FROM _bf2").fetchone()[0]
        new_rows = con.execute(
            'SELECT COUNT(*) FROM _bf2 f WHERE NOT EXISTS (SELECT 1 FROM ' + table +
            ' t WHERE t."交易日期" = f."交易日期" AND t."板块代码" = f."板块代码")').fetchone()[0]
        dup_rows = want - new_rows
        dmin, dmax = con.execute('SELECT MIN("交易日期"), MAX("交易日期") FROM _bf2').fetchone()
        nb = con.execute('SELECT COUNT(DISTINCT "板块代码") FROM _bf2').fetchone()[0]

        # 值冲突对证：库内已有行与包内值是否一致（挑主字段比）
        cmp_col = "主力净流入净额(元)" if table == "board_flow_em" else "净额(亿元)"
        conflict = 0
        if dup_rows and cmp_col in hdr:
            conflict = con.execute(
                'SELECT COUNT(*) FROM _bf2 f JOIN ' + table + ' t '
                'ON t."交易日期" = f."交易日期" AND t."板块代码" = f."板块代码" '
                f'WHERE t."{cmp_col}" IS DISTINCT FROM f."{cmp_col}"').fetchone()[0]
        tail = f" · 与库内不同 {conflict:,} 行（已按包内值覆盖）" if dup_rows else ""

        if dry:
            return {"table": table, "rows": n_src, "dry": True,
                    "note": f"{nb} 板块 · {dmin}~{dmax} · 待补 {new_rows:,} 行 / 已存在 {dup_rows:,} 行" + tail}

        # 🔴 全量 upsert（覆盖）：包是权威全历史快照，且能纠正库内历史口径错乱
        #    （血证 2026-10-28：board_flow_ths 20240910~20261022 的历史行存的是「元」，
        #      列名却写「亿元」，比值精确 1e8；20261023 起才是亿元）
        con.execute("BEGIN")
        try:
            con.execute('DELETE FROM ' + table + ' USING _bf2 f '
                        'WHERE ' + table + '."交易日期" = f."交易日期" '
                        'AND ' + table + '."板块代码" = f."板块代码"')
            con.execute('INSERT INTO ' + table + ' SELECT * FROM _bf2')
            got_k = con.execute('SELECT COUNT(*) FROM ' + table + ' t JOIN _bf2 f '
                                'ON f."交易日期" = t."交易日期" AND f."板块代码" = t."板块代码"').fetchone()[0]
            if got_k != want:
                raise RuntimeError(f"{table} 覆盖校验失败：{got_k} ≠ {want}")
            got = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        note = (f"{nb} 板块 · {dmin}~{dmax} · 覆盖 {dup_rows:,} 行 + 新增 {new_rows:,} 行")
        return {"table": table, "rows": want, "note": note + tail + f" · 全表 {got:,} 行"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _imp_fund_adj_zip(con, path, date, dry=False):
    """ETF/LOF 复权因子包 → etf_adj_factor / lof_adj_factor。

    包结构：每基金一 csv，`基金代码,交易日期,复权因子`，日期 `YYYYMMDD`，
            是**全历史快照**（每只基金完整时间序列），不是单日增量。
    表结构：code VARCHAR / date VARCHAR('YYYY-MM-DD') / qfq_factor VARCHAR / hfq_factor VARCHAR
            → 两个包（前/后复权）各只负责一列，另一列必须保留！

    策略（幂等·顺序无关）：
        1) 把包内所有行归一成一个临时 CSV（code,date8,factor）
        2) DuckDB read_csv 一次性载入（比 executemany 快一个量级）
        3) UPDATE 命中行（只动本包负责的列）→ INSERT 缺失行（另一列补 NULL）
    """
    base = os.path.basename(path)
    if "ETF" in base:
        table = "etf_adj_factor"
    elif "LOF" in base:
        table = "lof_adj_factor"
    else:
        raise RuntimeError(f"{base}：无法判定 ETF/LOF，拒绝导入")
    if "后复权" in base:
        col = "hfq_factor"
    elif "前复权" in base:
        col = "qfq_factor"
    else:
        raise RuntimeError(f"{base}：无法判定前/后复权，拒绝导入（避免写错列）")

    tmpdir = tempfile.mkdtemp(prefix="pkg_fadj_")
    flat = os.path.join(tmpdir, "flat.csv")
    n_src = 0
    try:
        with open(flat, "w", encoding="utf-8", newline="") as out:
            w = csv.writer(out)
            w.writerow(["code", "date8", "v"])
            for _nm, lines in _zip_iter_csv(path):
                for line in lines:
                    p = line.split(",")
                    if len(p) < 3:
                        continue
                    code, d, v = p[0].strip(), p[1].strip(), p[2].strip()
                    if not code or not d or code == "基金代码":
                        continue
                    d8 = d.replace("-", "").replace("/", "")[:8]
                    if len(d8) != 8 or not d8.isdigit():
                        continue
                    w.writerow([code, d8, v])
                    n_src += 1
        if n_src == 0:
            raise RuntimeError(f"{base} 里没有解析出任何复权因子行")

        con.execute("CREATE OR REPLACE TEMP TABLE _fa0 AS "
                    "SELECT code, date8, v FROM read_csv(?, header=true, all_varchar=true)",
                    [flat])
        # 归一：日期 → 'YYYY-MM-DD'（与表内既有格式一致）；同 (code,date) 去重留最后一条
        con.execute("""CREATE OR REPLACE TEMP TABLE _fa AS
                       SELECT code,
                              strftime(strptime(date8, '%Y%m%d'), '%Y-%m-%d') AS date,
                              NULLIF(trim(v), '') AS v
                       FROM _fa0
                       QUALIFY row_number() OVER (PARTITION BY code, date8
                                                  ORDER BY date8) = 1""")
        n_new_keys = con.execute("""SELECT COUNT(*) FROM _fa f WHERE NOT EXISTS (
                                      SELECT 1 FROM """ + table + """ t
                                      WHERE t.code = f.code AND t.date = f.date)""").fetchone()[0]
        n_upd = con.execute("""SELECT COUNT(*) FROM _fa f WHERE EXISTS (
                                 SELECT 1 FROM """ + table + """ t
                                 WHERE t.code = f.code AND t.date = f.date)""").fetchone()[0]
        dmin, dmax = con.execute("SELECT MIN(date), MAX(date) FROM _fa").fetchone()
        n_codes = con.execute("SELECT COUNT(DISTINCT code) FROM _fa").fetchone()[0]

        if dry:
            return {"table": table, "rows": n_src, "dry": True, "column": col,
                    "note": f"{n_codes} 只 · {dmin}~{dmax} · 新增 {n_new_keys} / 更新 {n_upd}"}

        con.execute("BEGIN")
        try:
            if n_upd:
                con.execute(f"""UPDATE {table} SET {col} = f.v FROM _fa f
                                WHERE {table}.code = f.code AND {table}.date = f.date""")
            if n_new_keys:
                other = "hfq_factor" if col == "qfq_factor" else "qfq_factor"
                con.execute(f"""INSERT INTO {table} (code, date, {col}, {other})
                                SELECT f.code, f.date, f.v, NULL FROM _fa f
                                WHERE NOT EXISTS (SELECT 1 FROM {table} t
                                                  WHERE t.code = f.code AND t.date = f.date)""")
            # 校验：落在包内日期区间的行数必须 ≥ 包内唯一 key 数
            got = con.execute("""SELECT COUNT(*) FROM """ + table +
                               """ t WHERE EXISTS (SELECT 1 FROM _fa f
                                    WHERE f.code = t.code AND f.date = t.date)""").fetchone()[0]
            want = con.execute("SELECT COUNT(*) FROM _fa").fetchone()[0]
            if got != want:
                raise RuntimeError(f"{table} 校验失败：覆盖 {got} ≠ 源 {want}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return {"table": table, "rows": n_src, "column": col,
                "note": f"{n_codes} 只 · {dmin}~{dmax} · 新增 {n_new_keys} / 更新 {n_upd}"}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _imp_tech_factor_day(con, path, date, dry=False):
    """技术因子_YYYYMMDD.csv（38 列 = 后复权口径）→ tech_factor_hfq 单日增量。
    🔴 该单日文件只有后复权一套；tech_factor_qfq 需另由 daily 重算，不在此处理。"""
    COLS = ["code", "date", "open", "high", "low", "close", "vol_hand", "amount_k",
            "pct_chg", "turnover", "vol_ratio", "adj", "ma5", "ma10", "ma20", "ma60",
            "ema5", "ema10", "ema20", "ema60", "macd_dif", "macd_dea", "macd",
            "rsi6", "rsi12", "rsi24", "kdj_k", "kdj_d", "kdj_j",
            "boll_up", "boll_mid", "boll_low", "atr", "obv",
            "up_days", "down_days", "new_high_days", "new_low_days"]
    ZH = ["股票代码", "交易日期", "开盘价", "最高价", "最低价", "收盘价", "成交量(手)", "成交额(千元)",
          "涨跌幅(%)", "换手率(%)", "量比", "复权因子", "MA5", "MA10", "MA20", "MA60",
          "EMA5", "EMA10", "EMA20", "EMA60", "MACD_DIF", "MACD_DEA", "MACD",
          "RSI6", "RSI12", "RSI24", "KDJ_K", "KDJ_D", "KDJ_J",
          "BOLL_UPPER", "BOLL_MID", "BOLL_LOWER", "ATR", "OBV",
          "连涨天数", "连跌天数", "阶段新高天数", "阶段新低天数"]
    sel_cols = ", ".join(f'try_cast("{zh}" AS DOUBLE) AS {en}' for zh, en in zip(ZH, COLS))
    src = (f"SELECT CAST(股票代码 AS VARCHAR) AS code, "
           f"CAST(strptime(CAST(交易日期 AS VARCHAR),'%Y%m%d') AS DATE) AS date, "
           + ", ".join(f'try_cast("{zh}" AS DOUBLE) AS {en}' for zh, en in list(zip(ZH, COLS))[2:])
           + f" FROM read_csv_auto('{path}', header=true, all_varchar=true)")
    n = con.execute(f"SELECT COUNT(*) FROM ({src})").fetchone()[0]
    if dry:
        return {"table": "tech_factor_hfq", "rows": int(n), "dry": True}
    d0 = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    con.execute("BEGIN")
    try:
        con.execute(f"DELETE FROM tech_factor_hfq WHERE date = DATE '{d0}'")
        con.execute(f"INSERT INTO tech_factor_hfq ({','.join(COLS)}) SELECT {','.join(COLS)} FROM ({src})")
        got = con.execute(f"SELECT COUNT(*) FROM tech_factor_hfq WHERE date = DATE '{d0}'").fetchone()[0]
        if got != n:
            raise RuntimeError(f"tech_factor_hfq 行数校验失败：{got} ≠ {n}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"table": "tech_factor_hfq", "rows": int(n), "dry": False}


def derive_limit_price(con, date):
    """由 daily 派生 limit_price（无源包）。
    prev_close = 前一交易日 daily.close（未复权）；档位照抄「最近一个已有 limit_price 交易日」的实测值，
    缺失回退前缀规则 + st_stock 名单。"""
    d0 = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    prev = con.execute("SELECT MAX(date) FROM daily WHERE date < CAST(? AS DATE)", [d0]).fetchone()[0]
    if prev is None:
        raise RuntimeError(f"daily 里找不到 {d0} 之前的交易日")
    con.execute("CREATE OR REPLACE TEMP TABLE _pc AS SELECT code, close FROM daily WHERE date = ?", [prev])
    base_day = con.execute("SELECT MAX(date) FROM limit_price WHERE date < CAST(? AS DATE)", [d0]).fetchone()[0]
    con.execute("CREATE OR REPLACE TEMP TABLE _lvl (code VARCHAR, lvl INTEGER)")
    if base_day is not None:
        con.execute("""INSERT INTO _lvl
            SELECT code, CASE WHEN prev_close <= 0.5 OR prev_close IS NULL THEN NULL
                 WHEN limit_up/prev_close-1 > 0.25 THEN 30
                 WHEN limit_up/prev_close-1 > 0.15 THEN 20
                 WHEN limit_up/prev_close-1 > 0.07 THEN 10
                 WHEN limit_up/prev_close-1 > 0.03 THEN 5 END
            FROM limit_price WHERE date = ?""", [base_day])
    st = set()
    try:
        cur = con.execute("""SELECT DISTINCT upper(split_part("股票代码",'.',1))||'.'||split_part("股票代码",'.',2)
                             FROM st_stock WHERE replace(CAST("交易日期" AS VARCHAR),'-','') = ?""", [date]).fetchall()
        st = {r[0] for r in cur}
    except Exception:
        pass
    con.execute("CREATE OR REPLACE TEMP TABLE _st (code VARCHAR)")
    if st:
        con.executemany("INSERT INTO _st VALUES (?)", [[c] for c in st])

    con.execute("BEGIN")
    try:
        con.execute(f"""INSERT INTO limit_price
            SELECT * FROM (
              SELECT CASE WHEN p.code LIKE 'bj%' THEN 'bj' ELSE 'hs' END AS market,
                     p.code AS code, DATE '{d0}' AS date, p.close AS prev_close,
                     round(p.close * (1 + COALESCE(l.lvl,
                        CASE WHEN p.code LIKE 'bj%' THEN 30
                             WHEN p.code LIKE 'sh68%' OR p.code LIKE 'sz30%' THEN 20
                             WHEN s.code IS NOT NULL THEN 5 ELSE 10 END)/100.0), 2) AS limit_up,
                     round(p.close * (1 - COALESCE(l.lvl,
                        CASE WHEN p.code LIKE 'bj%' THEN 30
                             WHEN p.code LIKE 'sh68%' OR p.code LIKE 'sz30%' THEN 20
                             WHEN s.code IS NOT NULL THEN 5 ELSE 10 END)/100.0), 2) AS limit_down,
                     CAST(COALESCE(l.lvl,
                        CASE WHEN p.code LIKE 'bj%' THEN 30
                             WHEN p.code LIKE 'sh68%' OR p.code LIKE 'sz30%' THEN 20
                             WHEN s.code IS NOT NULL THEN 5 ELSE 10 END) AS DECIMAL(5,2)) AS limit_pct,
                     NULL::BOOLEAN AS is_new
              FROM _pc p
              LEFT JOIN _lvl l ON l.code = p.code
              LEFT JOIN _st  s ON s.code = upper(substr(p.code,3))||'.'||
                                   CASE WHEN p.code LIKE 'sh%' THEN 'SH' ELSE 'SZ' END
              WHERE p.close > 0) t
            WHERE NOT EXISTS (SELECT 1 FROM limit_price x WHERE x.date = DATE '{d0}' AND x.code = t.code)""")
        con.execute(f"DELETE FROM limit_price WHERE date = DATE '{d0}' AND code NOT IN (SELECT code FROM _pc)")
        got = con.execute(f"SELECT COUNT(*) FROM limit_price WHERE date = DATE '{d0}'").fetchone()[0]
        con.execute("COMMIT")
        return got
    except Exception:
        con.execute("ROLLBACK")
        raise


FIN_REPORT_FULL = "fin_report_full"


def _fin_full_sel():
    """「全部上市公司财务信息」397 列宽表 → 统一加工后的 SELECT。

    🔴 股票代码在 CSV 里是**裸数字**（Excel/导出把前导 0 丢了：1 = 000001）
       → lpad 6 位 + 按「市场」补后缀（深市 SZ / 沪市 SH / 京市 BJ）。
    🔴 列名带全角括号/冒号（`其中：消耗性生物资产` / `减：营业成本`）→ 全部交给
       DuckDB 原样保留，建表/插入一律 BY NAME，不做任何手工列名映射。
    """
    return ('SELECT lpad(CAST("股票代码" AS VARCHAR), 6, \'0\') || \'.\' || '
            'CASE "市场" WHEN \'深市\' THEN \'SZ\' WHEN \'沪市\' THEN \'SH\' '
            'WHEN \'京市\' THEN \'BJ\' ELSE \'XX\' END AS code, '
            'CAST("更新时间" AS VARCHAR) AS "报告期", '
            '"股票名称", * EXCLUDE ("股票代码", "市场", "更新时间") '
            "FROM read_csv(?, header=true, sample_size=-1)")


def _imp_fin_report_full(con, path, date, dry=False):
    """全部上市公司财务信息_YYYYMMDD.csv（397 列）→ fin_report_full（主键 code + 报告期）。"""
    table = FIN_REPORT_FULL
    sel = _fin_full_sel()
    n = con.execute(f"SELECT COUNT(*) FROM ({sel})", [path]).fetchone()[0]
    nc = con.execute(f'SELECT COUNT(DISTINCT code) FROM ({sel})', [path]).fetchone()[0]
    periods = [r[0] for r in con.execute(
        f'SELECT DISTINCT "报告期" FROM ({sel}) ORDER BY 1', [path]).fetchall()]
    if dry:
        return {"table": table, "rows": int(n), "dry": True,
                "note": f"{nc:,} 只 · 报告期 {','.join(periods)} · 按报告期整段替换"}
    if n == 0:
        raise RuntimeError(f"{os.path.basename(path)} 没有数据行")

    exists = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [table]).fetchone()[0]
    con.execute("BEGIN")
    try:
        if not exists:
            con.execute(f"CREATE TABLE {table} AS {sel} LIMIT 0", [path])
        con.execute("CREATE OR REPLACE TEMP TABLE _ffr AS " + sel, [path])
        con.execute(f'DELETE FROM {table} WHERE "报告期" IN '
                    '(SELECT DISTINCT "报告期" FROM _ffr)')
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _ffr")
        got = con.execute(f'SELECT COUNT(*) FROM {table} WHERE "报告期" IN '
                          '(SELECT DISTINCT "报告期" FROM _ffr)').fetchone()[0]
        if got != n:
            raise RuntimeError(f"{table} 行数校验失败：入库 {got} ≠ 源 {n}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.execute("DROP TABLE IF EXISTS _ffr")
    return {"table": table, "rows": int(n), "dry": False,
            "note": f"{nc:,} 只 · 报告期 {','.join(periods)} · 共 {len(con.execute(f'DESCRIBE {table}').fetchall())} 列"}


def _imp_fin_snapshot(con, path, date, dry=False):
    """上市公司财务信息_YYYYMMDD.csv（35 列快照）→ fin_company_snapshot（整表刷新）"""
    src = f"SELECT * FROM read_csv_auto('{path}', header=true, all_varchar=true)"
    n = con.execute(f"SELECT COUNT(*) FROM ({src})").fetchone()[0]
    if dry:
        return {"table": "fin_company_snapshot", "rows": int(n), "dry": True}
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM fin_company_snapshot")
        con.execute(f"INSERT INTO fin_company_snapshot BY NAME {src}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"table": "fin_company_snapshot", "rows": int(n), "dry": False}


IMPORTERS = {
    "minute": None,          # 特殊处理（需 market 参数）
    "daily26": "daily26",
    "daily_ind_zip": "daily_ind_zip",   # 每日指标.zip → daily_ind + daily_mv
    "adj_zip": "adj_zip",               # 复权因子_前/后复权.zip → adj_factor
    "fund_adj_zip": "fund_adj_zip",     # ETF/LOF 复权因子 → etf_/lof_adj_factor
    "board_flow_zip": "board_flow_zip",   # 板块资金流向_东财/同花顺.zip（全历史补缺口）
    "stock_flow_zip": "stock_flow_zip",   # 个股/行业资金流 4 包（每票一 csv 全历史，补缺口）
    "fin_zip": "fin_zip",                 # 13 类财务 + 2 股东 + 分红配股（补缺口）
    "adj_cum_zip": "adj_cum_zip",         # 复权因子.zip（累计后复权 → 只写 hfq_factor）
    "tech_zip": "tech_zip",               # 技术因子_前/后复权.zip（补缺口）
    "tech_factor_day": "tech_factor_day",  # 技术因子_YYYYMMDD.csv → tech_factor_hfq
    "fin_snapshot": "fin_snapshot",     # 上市公司财务信息_YYYYMMDD.csv（35 列）
    "fin_report_full": "fin_report_full",  # 全部上市公司财务信息_YYYYMMDD.csv（397 列）
    "st_susp": "st_susp",             # ST股票列表_/每日停复牌_.csv → st_stock/suspension
    "limit_price": "limit_price",     # 每日涨跌停价格_.csv → limit_price（官方权威）
    "limit_price_zip": "limit_price_zip",  # 每日涨跌停价格.zip → limit_price（全历史·补缺口）
    "susp_zip": "susp_zip",           # 每日停复牌.zip → suspension（全历史·补缺口）
    "stock_rename": "stock_rename",   # 股票曾用名汇总.csv → stock_rename
    "fund_list": "fund_list",         # ETF基础信息/ETF基准指数/LOF基金列表.csv → fund_info / etf_benchmark_index
    "info_table": "info_table",       # 指数列表/板块列表/市场统计指数/可转债基础信息 → index_info/board_info/cb_info

    "moneyflow": "moneyflow",
    "market_flow": "market_flow",     # 大盘资金流向.csv → market_flow_daily
    "hk_flow": "hk_flow",             # 沪深港通资金流向.csv → hk_connect_flow
    "hk_daily": "hk_daily",           # 港股通每日成交统计.csv → hk_connect_daily
    "theme_zip": "theme_zip",
    "restricted_zip": None,
    "generic": "generic",
    "unsupported": None,
}


# ─────────────────────────────────────────────────────────────────────────────
# 全历史资金流快照：大盘资金流向 / 沪深港通 / 港股通每日成交
#   这三个包每次下载都是「全历史覆盖」（不是增量），所以按「交易日期」整段 upsert：
#   DELETE 掉 CSV 里出现过的所有日期 → INSERT → 校验行数，事务内完成。
# ─────────────────────────────────────────────────────────────────────────────
HK_FLOW_MAP = {
    "港股通（上海）(百万元)": "港股通_上海_百万",
    "港股通（深圳）(百万元)": "港股通_深圳_百万",
    "沪股通(百万元)": "沪股通_百万",
    "深股通(百万元)": "深股通_百万",
    "北向资金(百万元)": "北向资金_百万",
    "南向资金(百万元)": "南向资金_百万",
}


# 🔴🔴 血证（2026-10-28）：hk_connect_daily 的列名写「(亿元)/(万笔)」，但库里实际存的是
#   **元 / 笔**（2014-11-17 原表 1,772,000,000 元 = CSV 的 17.72 亿元；38,800 笔 = 3.88 万笔）。
#   → 必须 ×1e8 / ×1e4 换算，否则整表口径分裂（差 8 个数量级）。
HK_DAILY_SCALE = {
    "买入成交金额(亿元)": 1e8,
    "卖出成交金额(亿元)": 1e8,
    "买入成交笔数(万笔)": 1e4,
    "卖出成交笔数(万笔)": 1e4,
}


def _imp_flow_snapshot(con, path, table, colmap=None, scale=None, dry=False):
    """全历史资金流 CSV → table（按「交易日期」整段 upsert）。

    scale: {列名: 乘数}，用于「CSV 单位 ≠ 表内单位」的换算（见 HK_DAILY_SCALE）。
    """
    colmap = colmap or {}
    scale = scale or {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        hdr = [h.strip() for h in next(rd)]
        body = [[x.strip() for x in r] for r in rd if r and any(x.strip() for x in r)]
    mapped = [colmap.get(h, h) for h in hdr]
    if "交易日期" not in mapped:
        raise RuntimeError(f"{os.path.basename(path)} 缺少「交易日期」列，实际表头：{hdr[:5]}")
    di = mapped.index("交易日期")
    dates = sorted({r[di].replace("-", "").replace("/", "")[:8] for r in body if len(r) > di and r[di]})
    if not dates:
        raise RuntimeError(f"{os.path.basename(path)} 没有有效交易日期")

    if dry:
        return {"table": table, "rows": len(body), "dry": True,
                "note": f"全历史快照 · 覆盖 {dates[0]}~{dates[-1]}（{len(dates)} 个交易日）"}

    tcols = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {table}").fetchall()]
    width = len(mapped)
    con.execute("CREATE OR REPLACE TEMP TABLE _fs ("
                + ", ".join(f'"{c}" VARCHAR' for c in mapped) + ")")
    fixed = []
    for r in body:
        r = list(r) + [""] * (width - len(r))
        r[di] = r[di].replace("-", "").replace("/", "")[:8]
        fixed.append(r[:width])
    if fixed:
        con.executemany(f"INSERT INTO _fs VALUES ({','.join('?' * width)})", fixed)
    # 按交易日期去重（同一天多行只留一行，防脏包）
    con.execute("CREATE OR REPLACE TEMP TABLE _fs2 AS "
                "SELECT * FROM _fs QUALIFY row_number() OVER (PARTITION BY \"交易日期\" "
                "ORDER BY \"交易日期\") = 1")
    n_src = con.execute("SELECT COUNT(*) FROM _fs2").fetchone()[0]

    sel = []
    for c, ty in tcols:
        if c not in mapped:
            sel.append("NULL")
        elif ty.upper().startswith("VARCHAR"):
            sel.append(f'"{c}"')
        elif c in scale:
            sel.append(f'(TRY_CAST("{c}" AS {ty}) * {scale[c]})')
        else:
            sel.append(f'TRY_CAST("{c}" AS {ty})')
    ins = ",".join(f'"{c}"' for c, _ in tcols)

    # 🔴 三张表的「交易日期」都是 BIGINT（不是 VARCHAR）→ DELETE/校验必须显式 CAST
    dty = dict(tcols).get("交易日期", "BIGINT")
    dt_expr = f'TRY_CAST("交易日期" AS {dty})'
    con.execute("BEGIN")
    try:
        con.execute('DELETE FROM ' + table +
                    f' WHERE "交易日期" IN (SELECT DISTINCT {dt_expr} FROM _fs2)')
        con.execute(f"INSERT INTO {table} ({ins}) SELECT {','.join(sel)} FROM _fs2")
        got = con.execute('SELECT COUNT(*) FROM ' + table +
                          f' WHERE "交易日期" IN (SELECT DISTINCT {dt_expr} FROM _fs2)').fetchone()[0]
        if got != n_src:
            raise RuntimeError(f"{table} 行数校验失败：入库 {got} ≠ 源 {n_src}")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    con.execute("DROP TABLE IF EXISTS _fs")
    con.execute("DROP TABLE IF EXISTS _fs2")
    return {"table": table, "rows": n_src,
            "note": f"覆盖 {dates[0]}~{dates[-1]}"}


def _imp_market_flow(con, path, date, dry=False):
    return _imp_flow_snapshot(con, path, "market_flow_daily", dry=dry)


def _imp_hk_flow(con, path, date, dry=False):
    return _imp_flow_snapshot(con, path, "hk_connect_flow", colmap=HK_FLOW_MAP, dry=dry)


def _imp_hk_daily(con, path, date, dry=False):
    # ⚠️ CSV 是亿元/万笔，库里是元/笔 → 必须换算
    return _imp_flow_snapshot(con, path, "hk_connect_daily",
                              scale=HK_DAILY_SCALE, dry=dry)


def _imp_restricted(con, path, date, dry=False):
    tmp = tempfile.mkdtemp(prefix="packs_rst_")
    try:
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp)
        pattern = os.path.join(tmp, "*.csv")
        sel = (f'select "股票代码", "公告日期", "解禁日期", "流通股份(股)", '
               f'"流通股份占总股本比例(%)", "股东名称", "股份类型" '
               f"from read_csv_auto('{pattern}', header=true, all_varchar=true, union_by_name=true)")
        n = con.execute(f"select count(*) from ({sel})").fetchone()[0]
        if dry:
            return {"table": "fin_restricted", "rows": int(n), "dry": True}
        con.execute("delete from fin_restricted")
        con.execute(f"insert into fin_restricted {sel}")
        return {"table": "fin_restricted", "rows": int(n), "dry": False}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


STOCK_LIST_RENAME = {
    "TS代码": "ts_code", "股票代码": "code", "股票名称": "name", "地域": "area",
    "所属行业": "industry", "股票全称": "fullname", "英文全称": "enname", "拼音缩写": "pinyin",
    "市场类型": "market", "交易所代码": "exchange", "交易货币": "currency",
    "上市状态": "list_status", "上市日期": "list_date", "退市日期": "delist_date",
    "沪深港通标的": "hs_connect", "实控人名称": "controller", "实控人企业性质": "controller_type",
}



# ─────────────────────────────────────────────────────────────────────────────
# 基础三表：ST 名单 / 每日停复牌 / 每日涨跌停价格
#   2026-10-28：这三类包一直「未识别不支持」→ 接入
#   策略：按整行精确匹配补缺口（IS NOT DISTINCT FROM），不覆盖库内已有行
# ─────────────────────────────────────────────────────────────────────────────
TIERS = (5.0, 10.0, 20.0, 30.0)
# 🔴 无涨跌幅限制哨兵阈值。库内历史实测存在 5 档写法：
#    100000 / 1000000 / 999999.999 / 99999.999 / 99999.99
#    阈值取 >=999999 会漏掉后 3 档（血证 2026-10-29：北交所 920306 给 99999.99
#    → 被当 30% 档、limit_down 落 0.0）。A 股最高股价约 1700 元，>=99999 无歧义。
SENTINEL_UP = 99999.0


def _imp_st_susp(con, path, table, date, dry=False):
    """ST股票列表_YYYYMMDD.csv → st_stock ／ 每日停复牌_YYYYMMDD.csv → suspension

    中文列名 + 8 位 VARCHAR 日期；同一天可能多行（停牌/复牌分别一行）。
    """
    import pandas as pd
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    schema = _schema(con, table)
    keep = [c for c in df.columns if c in schema]
    if not keep:
        raise RuntimeError(f"{os.path.basename(path)} 与 {table} 无同名列")
    df = df[keep].copy()
    if "交易日期" in df.columns:
        df["交易日期"] = df["交易日期"].astype(str).str.replace("-", "", regex=False).str[:8]
    df = _cast_df(df, schema)
    if dry:
        return {"table": table, "rows": int(len(df)), "dry": True}
    collist = ",".join(f'"{c}"' for c in keep)
    # 🔴 空串与 NULL 必须视为等价：库内历史写 ''，pandas 读空值给 NULL
    #    → 不加 COALESCE 会每次重跑都插一遍（血证 2026-10-28：suspension 重复 8 行）
    cond = " AND ".join(
        f"COALESCE(t.\"{c}\", '') IS NOT DISTINCT FROM COALESCE(x.\"{c}\", '')" for c in keep)
    con.register("_x_st", df)
    try:
        before = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        con.execute(f'INSERT INTO {table} ({collist}) SELECT {collist} FROM _x_st x '
                    f'WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {cond})')
        after = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    finally:
        con.unregister("_x_st")
    return {"table": table, "rows": int(after - before), "dry": False}


def _imp_limit_price(con, path, date, dry=False):
    """每日涨跌停价格_YYYYMMDD.csv → limit_price

    🩸 2026-10-26/27 自算把 sz002731(萃华退) / sz301718(新股) 当成 30% 档，
       源包给哨兵 999999.999（= 无涨跌幅限制）→ 必须用官方源。

    档位 = 从官方涨停价反算后归到最近档位（5/10/20/30）。
      🔴 不用 st_stock 名单判 5%：2026-10-28 实证 195 只 ST 名单股里 0 只按 5% 交易
         （*ST九鼎 max 10.07% / ST合力泰 5.96% / *ST天箭 8.72%）→ 名单口径已失真。
    哨兵约定（与库内历史一致）：涨停价 >= SENTINEL_UP(99999) → limit_up=999999.999 / limit_down=0.01
                                   / limit_pct=NULL / is_new=NULL
    """
    import numpy as np
    import pandas as pd
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    need = ["股票代码", "昨日收盘价", "涨停价", "跌停价"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise RuntimeError(f"{os.path.basename(path)} 缺列 {missing}")
    for c in need[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=need[1:])

    s = df["股票代码"].astype(str).str.strip().str.upper()
    suf = s.str[-2:]
    core = s.str[:6]
    df["market"] = np.where(suf == "BJ", "bj", "hs")
    pref = np.select([suf == "BJ", suf == "SH"], ["bj", "sh"], default="sz")
    df["code"] = np.char.add(pref.astype(str), core.astype(str))

    prev = df["昨日收盘价"].astype(float)
    up = df["涨停价"].astype(float)
    dn = df["跌停价"].astype(float)
    sent = (up >= SENTINEL_UP).to_numpy()
    raw = np.where(prev > 0, (up / prev - 1) * 100, 10.0)
    tier = np.array([min(TIERS, key=lambda t: abs(t - r)) for r in raw], dtype=float)

    iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    out = pd.DataFrame({
        "market": df["market"].to_numpy(),
        "code": df["code"].to_numpy(),
        "date": iso,
        "prev_close": prev.to_numpy(),
        "limit_up": np.where(sent, 999999.999, up.to_numpy()),
        "limit_down": np.where(sent, 0.01, dn.to_numpy()),
        "limit_pct": np.where(sent, np.nan, tier),
        "is_new": np.nan,
    })
    out = _cast_df(out, _schema(con, "limit_price"))
    if dry:
        return {"table": "limit_price", "rows": int(len(out)), "dry": True}

    con.register("_x_lp", out)
    try:
        con.execute("CREATE OR REPLACE TEMP TABLE _lp_codes AS SELECT DISTINCT code FROM _x_lp")
        con.execute("DELETE FROM limit_price WHERE date = CAST(? AS DATE) "
                    "AND code IN (SELECT code FROM _lp_codes)", [iso])
        con.execute("INSERT INTO limit_price BY NAME SELECT * FROM _x_lp")
        con.execute("DROP TABLE IF EXISTS _lp_codes")
    finally:
        con.unregister("_x_lp")
    return {"table": "limit_price", "rows": int(len(out)), "dry": False}


# ─────────────────────────────────────────────────────────────────────────────
# 2026-10-29：三个「未识别不支持」包接入
#   股票曾用名汇总.csv / 每日停复牌.zip / 每日涨跌停价格.zip
#   🔴 两个 zip 都是「每只股票一 csv 的全历史包」→ 一律**只补缺口，不覆盖**：
#      库内 limit_price 17.6M 行（含派生补的 2000-2006 年）、zip 只有 16.0M 行（2007 起）
#      → 若按「日」删了重灌会永久丢掉 2000-2006 那 1.6M 行。
# ─────────────────────────────────────────────────────────────────────────────

def _imp_stock_rename(con, path, dry=False):
    """股票曾用名汇总.csv → stock_rename（7 列，更名日期是 DATE）。

    🔴🔴 主键 = **(代码, 更名日期)**，`序号` **绝不能进匹配条件**：
       库内 `序号` 是当时导入的自增 ID，源包每次导出都会重排
       （血证 2026-10-29：CSV 序号 260 ↔ 库内 261，**整体错位 1 位** →
        整行匹配只命中 3% → 一次导入插入 7,632 行重复，7,889 → 15,521）。
    🔴 CSV 不是库内超集：库内有 5 行「G股/S股改革」记录新版已剔除
       （000062.SZ / 000820.SZ ×2 / 000929.SZ ×2）→ **禁整表替换**，会丢。
    策略 = MERGE：同键 UPDATE 差异列 + 键缺口 INSERT（序号接 MAX(序号) 之后）。
    """
    import pandas as pd
    table = "stock_rename"
    schema = _schema(con, table)
    BIZ = ["代码", "更名日期", "名称", "更名前简称", "更名后简称", "证券类型"]
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    need = [c for c in BIZ if c in df.columns]
    if len(need) < len(BIZ):
        raise RuntimeError(f"{os.path.basename(path)} 缺列：{set(BIZ) - set(need)}")
    df = df[need].copy()
    df = _cast_df(df, schema)
    if dry:
        return {"table": table, "rows": int(len(df)), "dry": True}

    KEY = ["代码", "更名日期"]
    DIFF = ["名称", "更名前简称", "更名后简称", "证券类型"]
    con.register("_x_rn", df)
    try:
        before = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        # ① 同键 UPDATE（只改业务列，不动 序号）
        con.execute("UPDATE " + table + " AS t SET "
                    + ", ".join('"' + c + '" = x."' + c + '"' for c in DIFF)
                    + " FROM _x_rn x WHERE t.\"代码\" = x.\"代码\" AND t.\"更名日期\" = x.\"更名日期\""
                    + " AND (" + " OR ".join('t."' + c + '" IS DISTINCT FROM x."' + c + '"' for c in DIFF) + ")")
        # ② 键缺口 INSERT（序号接在 MAX 之后，保持连续）
        con.execute("CREATE OR REPLACE TEMP TABLE _rn_ins AS SELECT "
                    + '(SELECT COALESCE(MAX("序号"), 0) FROM ' + table + ") + "
                    + 'row_number() OVER (ORDER BY x."代码", x."更名日期") AS "序号", '
                    + ", ".join('x."' + c + '"' for c in ["代码", "名称", "更名日期",
                                                          "更名前简称", "更名后简称", "证券类型"])
                    + " FROM _x_rn x WHERE NOT EXISTS (SELECT 1 FROM " + table + ' t'
                    + ' WHERE t."代码" = x."代码" AND t."更名日期" = x."更名日期")')
        n_ins = con.execute("SELECT COUNT(*) FROM _rn_ins").fetchone()[0]
        if n_ins:
            con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _rn_ins")
        con.execute("DROP TABLE IF EXISTS _rn_ins")
        after = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.unregister("_x_rn")
    return {"table": table, "rows": int(after - before), "dry": False,
            "n_ins": int(n_ins)}


def _imp_susp_zip(con, path, date, dry=False):
    """每日停复牌.zip（4,484 只 × 全历史）→ suspension。

    CSV 4 列：股票代码,交易日期,停复牌时间段,停复牌类型
    🔴 `停复牌时间段` 里**有逗号**（`"11:07-11:17,11:22-13:02"`）→ 必须 csv.reader，
       用 split(",") 会把一行拆成 5 段（血证：000004.SZ 20260623）。
    策略：整行级 NOT EXISTS 补缺口，不覆盖。
    """
    import pandas as pd
    table = "suspension"
    schema = _schema(con, table)
    HDR = ["股票代码", "交易日期", "停复牌时间段", "停复牌类型"]
    rows = []
    bad_hdr = 0
    for _nm, lines in _zip_iter_csv(path):
        if not lines:
            continue
        parsed = list(csv.reader(lines))
        if not parsed:
            continue
        h = [x.strip() for x in parsed[0]]
        if h[:4] != HDR:
            bad_hdr += 1
            continue
        for p in parsed[1:]:
            if not p or not p[0].strip():
                continue
            p = [x.strip() for x in p] + [""] * (4 - len(p))
            d8 = p[1].replace("-", "").replace("/", "")[:8]
            if len(d8) != 8 or not d8.isdigit():
                continue
            rows.append((p[0].upper(), d8, p[2], p[3]))
    if not rows:
        raise RuntimeError(f"{os.path.basename(path)} 里没解析出任何行（表头不符 {bad_hdr} 个）")
    df = pd.DataFrame(rows, columns=HDR)
    df = _cast_df(df, schema)
    if dry:
        return {"table": table, "rows": int(len(df)), "dry": True}
    collist = ",".join(f'"{c}"' for c in HDR)
    cond = " AND ".join(
        f"COALESCE(t.\"{c}\", '') IS NOT DISTINCT FROM COALESCE(x.\"{c}\", '')" for c in HDR)
    con.register("_x_sz", df)
    try:
        before = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.execute(f'INSERT INTO {table} ({collist}) SELECT {collist} FROM _x_sz x '
                    f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {cond})")
        after = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.unregister("_x_sz")
    return {"table": table, "rows": int(after - before), "dry": False}


def _imp_limit_price_zip(con, path, date, dry=False):
    """每日涨跌停价格.zip（5,907 只 × 全历史，约 1,600 万行）→ limit_price。

    CSV 5 列：股票代码,交易日期,昨日收盘价,涨停价,跌停价
    🔴 只按 (code, date) 补缺口，**绝不按日删**：库内 17.6M 行含 2000-2006 派生数据，
       zip 只有 2007 起 16.0M 行 → 按日重灌会永久丢掉那 1.6M 行。
    档位/哨兵口径与 _imp_limit_price 完全一致（从官方涨停价反算，不查 st_stock 名单）。
    内存策略：16M 行不能进 pandas（本机 16 GB）→ 摊平到外盘 flat.csv 再用 DuckDB 流式读。
    """
    import numpy as np
    import pandas as pd
    table = "limit_price"
    HDR = ["股票代码", "交易日期", "昨日收盘价", "涨停价", "跌停价"]
    tmpdir = tempfile.mkdtemp(prefix="pkg_lpzip_", dir=getattr(config, "DUCK_TMP", None) or None)
    flat = os.path.join(tmpdir, "flat.csv")
    n_src = 0
    try:
        with open(flat, "w", encoding="utf-8", newline="") as out:
            w = csv.writer(out)
            w.writerow(HDR)
            for _nm, lines in _zip_iter_csv(path):
                if not lines:
                    continue
                parsed = list(csv.reader(lines))
                if not parsed:
                    continue
                if [x.strip() for x in parsed[0]][:5] != HDR:
                    continue
                for p in parsed[1:]:
                    if not p or not p[0].strip():
                        continue
                    p = [x.strip() for x in p] + [""] * (5 - len(p))
                    d8 = p[1].replace("-", "").replace("/", "")[:8]
                    if len(d8) != 8 or not d8.isdigit():
                        continue
                    w.writerow([p[0].upper(), d8, p[2], p[3], p[4]])
                    n_src += 1
        if n_src == 0:
            raise RuntimeError(f"{os.path.basename(path)} 里没解析出任何行")

        con.execute("CREATE OR REPLACE TEMP TABLE _lz0 AS "
                    "SELECT * FROM read_csv(?, header=true, all_varchar=true)", [flat])
        df = con.execute("SELECT * FROM _lz0").fetchdf()
        for c in HDR[2:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=HDR[2:])

        s_ = df["股票代码"].astype(str).str.strip().str.upper()
        suf = s_.str[-2:]
        core = s_.str[:6]
        market = np.where(suf == "BJ", "bj", "hs")
        pref = np.select([suf == "BJ", suf == "SH"], ["bj", "sh"], default="sz")
        pcode = np.char.add(pref.astype(str), core.astype(str))

        prev = df["昨日收盘价"].astype(float)
        up = df["涨停价"].astype(float)
        dn = df["跌停价"].astype(float)
        sent = (up >= SENTINEL_UP).to_numpy()
        raw = np.where(prev > 0, (up / prev - 1) * 100, 10.0)
        tier = np.array([min(TIERS, key=lambda t: abs(t - r)) for r in raw], dtype=float)

        dts = df["交易日期"].astype(str).str.strip()
        iso = dts.str[:4] + "-" + dts.str[4:6] + "-" + dts.str[6:8]
        out = pd.DataFrame({
            "market": market,
            "code": pcode,
            "date": iso,
            "prev_close": prev.to_numpy(),
            "limit_up": np.where(sent, 999999.999, up.to_numpy()),
            "limit_down": np.where(sent, 0.01, dn.to_numpy()),
            "limit_pct": np.where(sent, np.nan, tier),
            "is_new": np.nan,
        })
        out = _cast_df(out, _schema(con, table))
        if dry:
            return {"table": table, "rows": int(len(out)), "dry": True}

        con.register("_x_lz", out)
        try:
            # 🔴 (code, date) 级补缺口；不清空、不按日删
            #    gap 必须在 INSERT **之前**统计（INSERT 后恒为 0 → 曾误报「插入 15,920,955 行」）
            gap = con.execute(
                "SELECT COUNT(*) FROM _x_lz n WHERE NOT EXISTS "
                f"(SELECT 1 FROM {table} l WHERE l.code = n.code AND l.date = n.date)"
            ).fetchone()[0]
            if gap:
                _sql = ("INSERT INTO " + table + " BY NAME "
                        "SELECT * FROM _x_lz n WHERE NOT EXISTS "
                        "(SELECT 1 FROM " + table + " l WHERE l.code = n.code AND l.date = n.date)")
                con.execute(_sql)
            chk = con.execute(
                "SELECT COUNT(*) FROM _x_lz n WHERE NOT EXISTS "
                f"(SELECT 1 FROM {table} l WHERE l.code = n.code AND l.date = n.date)"
            ).fetchone()[0]
        finally:
            con.unregister("_x_lz")
        if chk:
            raise RuntimeError(f"limit_price 补缺口后仍有 {chk:,} 行未落库")
        return {"table": table, "rows": int(gap), "dry": False}
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass



def _imp_stock_list(con, path, date, dry=False):
    """股票列表.csv / 退市股票列表.csv → stock_info（按 ts_code 幂等：删后插）

    🩸 2026-10-28：原来走 _imp_generic(date_col=None) 盲插 → 5,919 涨到 11,453 行。
    """
    import pandas as pd
    schema = _schema(con, "stock_info")
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df = df.rename(columns=STOCK_LIST_RENAME)
    keep = [c for c in df.columns if c in schema]
    if "ts_code" not in keep:
        raise RuntimeError(f"{os.path.basename(path)} 缺 ts_code 列")
    df = df[keep].copy()
    df = df.replace("", None)
    df = _cast_df(df, schema)
    if dry:
        return {"table": "stock_info", "rows": int(len(df)), "dry": True}
    # 🔴 MERGE upsert（2026-10-28 改）：删后插会把 CSV 没提供的列冲成 NULL。
    #    血证：股票列表_沪深/京市.csv 只有 13 列（无 list_status/currency/hs_connect/delist_date），
    #    删后插会让这 4 列 100% 非空变成大面积 NULL。
    cols = [c for c in df.columns if c != "ts_code"]
    con.register("_x_sl", df)
    try:
        if cols:
            set_clause = ", ".join(
                f'"{c}" = COALESCE(x."{c}", stock_info."{c}")' for c in cols)
            con.execute(
                f'UPDATE stock_info SET {set_clause} FROM _x_sl x '
                f'WHERE stock_info.ts_code = x.ts_code')
        con.execute(
            "INSERT INTO stock_info BY NAME SELECT * FROM _x_sl x "
            "WHERE NOT EXISTS (SELECT 1 FROM stock_info s WHERE s.ts_code = x.ts_code)")
        n_new = con.execute(
            "SELECT COUNT(*) FROM stock_info").fetchone()[0]
    finally:
        con.unregister("_x_sl")
    return {"table": "stock_info", "rows": int(len(df)), "total": int(n_new), "dry": False}


FUND_INFO_DDL = """
CREATE TABLE IF NOT EXISTS fund_info (
  code             VARCHAR,
  code_norm        VARCHAR,
  name             VARCHAR,
  ext_name         VARCHAR,
  full_name        VARCHAR,
  track_index_code VARCHAR,
  track_index_name VARCHAR,
  setup_date       VARCHAR,
  list_date        VARCHAR,
  list_status      VARCHAR,
  exchange         VARCHAR,
  manager          VARCHAR,
  custodian        VARCHAR,
  mgmt_fee         DOUBLE,
  fund_kind        VARCHAR,
  category         VARCHAR
)
"""

BENCH_INDEX_DDL = """
CREATE TABLE IF NOT EXISTS etf_benchmark_index (
  index_code   VARCHAR,
  full_name    VARCHAR,
  short_name   VARCHAR,
  publisher    VARCHAR,
  publish_date VARCHAR,
  base_date    VARCHAR,
  base_point   DOUBLE,
  adjust_cycle VARCHAR
)
"""

FUND_INFO_RENAME = {
    "ETF代码": "code", "代码": "code",
    "ETF简称": "name", "名称": "name",
    "扩位简称": "ext_name",
    "基金全称": "full_name",
    "跟踪指数代码": "track_index_code",
    "跟踪指数名称": "track_index_name",
    "设立日期": "setup_date",
    "上市日期": "list_date",
    "上市状态": "list_status",
    "交易所": "exchange",
    "基金管理人": "manager",
    "基金托管人": "custodian",
    "管理费率": "mgmt_fee",
    "ETF类型": "fund_kind",
}

BENCH_RENAME = {
    "指数代码": "index_code",
    "指数全称": "full_name",
    "指数简称": "short_name",
    "指数发布机构": "publisher",
    "指数发布日期": "publish_date",
    "指数基日": "base_date",
    "指数基点": "base_point",
    "指数成份证券调整周期": "adjust_cycle",
}


def _norm_fund_code(c):
    """统一成 6位数字.交易所（后缀式）。159001.SZ / sz169201 / 510300 → 规范式。"""
    s = (c or "").strip().upper()
    if not s:
        return None
    if s.endswith(".SH") or s.endswith(".SZ") or s.endswith(".BJ"):
        return s
    m = re.match(r"^(SH|SZ|BJ)(\d{6})$", s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    m = re.match(r"^(\d{6})$", s)
    if m:
        d = m.group(1)
        return f"{d}." + ("SH" if d[0] in ("5", "6", "9") else "SZ")
    return s


def _imp_fund_list(con, path, date, dry=False):
    """ETF基础信息列表.csv / ETF基准指数列表.csv / LOF基金列表.csv

    两张表都没有日期列 → 按主键「删后插」（同 _imp_stock_list，避免盲插膨胀）。
    🩸 盲插血证：stock_info 曾 5,919 → 11,453。
    """
    import pandas as pd

    base = os.path.basename(path)
    is_bench = base.startswith("ETF基准指数")
    is_lof = base.startswith("LOF基金列表")

    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]

    if is_bench:
        tbl, key, rename = "etf_benchmark_index", "index_code", BENCH_RENAME
        ddl = BENCH_INDEX_DDL
    else:
        tbl, key, rename = "fund_info", "code_norm", FUND_INFO_RENAME
        ddl = FUND_INFO_DDL

    df = df.rename(columns=rename)

    if not is_bench:
        if "code" not in df.columns:
            raise RuntimeError(f"{base} 缺代码列（实际列：{list(df.columns)[:6]}）")
        df["code_norm"] = df["code"].map(_norm_fund_code)
        if "mgmt_fee" in df.columns:
            df["mgmt_fee"] = pd.to_numeric(df["mgmt_fee"], errors="coerce")
        df["category"] = "LOF" if is_lof else "ETF"
        cols = ["code", "code_norm", "name", "ext_name", "full_name",
                "track_index_code", "track_index_name", "setup_date", "list_date",
                "list_status", "exchange", "manager", "custodian", "mgmt_fee",
                "fund_kind", "category"]
    else:
        if "base_point" in df.columns:
            df["base_point"] = pd.to_numeric(df["base_point"], errors="coerce")
        cols = ["index_code", "full_name", "short_name", "publisher",
                "publish_date", "base_date", "base_point", "adjust_cycle"]

    for c in cols:
        if c not in df.columns:
            df[c] = None
    if key not in df.columns:
        raise RuntimeError(f"{base} 缺主键列 {key}（实际列：{list(df.columns)[:6]}）")
    df = df[cols].copy()
    df = df.replace("", None)
    df = df[df[key].notna()].drop_duplicates(subset=[key], keep="last")

    if dry:
        return {"table": tbl, "rows": int(len(df)), "dry": True}

    con.execute(ddl)
    con.register("_x_fl", df)
    try:
        con.execute(f"CREATE OR REPLACE TEMP TABLE _fl_keys AS SELECT DISTINCT {key} FROM _x_fl")
        con.execute(f"DELETE FROM {tbl} WHERE {key} IN (SELECT {key} FROM _fl_keys)")
        con.execute(f"INSERT INTO {tbl} BY NAME SELECT * FROM _x_fl")
        con.execute("DROP TABLE IF EXISTS _fl_keys")
    finally:
        con.unregister("_x_fl")
    return {"table": tbl, "rows": int(len(df)), "dry": False}


INDEX_INFO_DDL = """
CREATE TABLE IF NOT EXISTS index_info (
  ts_code     VARCHAR,
  name        VARCHAR,
  market      VARCHAR,
  publisher   VARCHAR,
  category    VARCHAR,
  base_date   VARCHAR,
  base_point  DOUBLE,
  list_date   VARCHAR,
  symbol_num  VARCHAR,
  market_id   VARCHAR
)
"""

BOARD_INFO_DDL = """
CREATE TABLE IF NOT EXISTS board_info (
  board_code     VARCHAR,
  src            VARCHAR,
  board_name     VARCHAR,
  board_type     VARCHAR,
  trade_date     VARCHAR,
  member_cnt     INTEGER,
  total_share_yi DOUBLE,
  float_share_yi DOUBLE,
  total_mv_yi    DOUBLE,
  float_mv_yi    DOUBLE
)
"""

CB_INFO_DDL = """
CREATE TABLE IF NOT EXISTS cb_info (
  cb_code       VARCHAR,
  cb_name       VARCHAR,
  cb_short      VARCHAR,
  apply_code    VARCHAR,
  stock_code    VARCHAR,
  stock_short   VARCHAR,
  term_year     DOUBLE,
  par_value     DOUBLE,
  issue_price   DOUBLE,
  issue_amount  DOUBLE,
  bond_balance  DOUBLE,
  value_date    VARCHAR,
  maturity_date VARCHAR,
  rate_type     VARCHAR,
  coupon_rate   DOUBLE,
  compensate_rate DOUBLE,
  pay_freq      INTEGER,
  list_date     VARCHAR,
  delist_date   VARCHAR,
  list_place    VARCHAR,
  conv_start    VARCHAR,
  conv_end      VARCHAR,
  conv_stop     VARCHAR,
  conv_price_init DOUBLE,
  conv_price_now  DOUBLE,
  rate_desc     VARCHAR,
  redeem_price  DOUBLE,
  issue_rating  VARCHAR,
  now_rating    VARCHAR,
  rating_org    VARCHAR
)
"""

INFO_TABLE_CFG = {
    "指数列表_沪深京": {
        "table": "index_info", "ddl": INDEX_INFO_DDL, "key": "ts_code",
        "cat": "base", "note": "532 只指数基础信息 → index_info（按 ts_code MERGE）",
        "rename": {},
        "dbl": ["base_point"], "int": [],
    },
    "板块列表_通达信": {
        "table": "board_info", "ddl": BOARD_INFO_DDL, "key": "board_code",
        "cat": "base", "note": "619 个通达信板块 → board_info（src='tdx'，按 board_code+src MERGE）",
        "rename": {
            "板块代码": "board_code", "交易日期": "trade_date", "板块名称": "board_name",
            "板块类型": "board_type", "成分个数": "member_cnt",
            "总股本(亿)": "total_share_yi", "流通股(亿)": "float_share_yi",
            "总市值(亿)": "total_mv_yi", "流通市值(亿)": "float_mv_yi",
        },
        "dbl": ["total_share_yi", "float_share_yi", "total_mv_yi", "float_mv_yi"],
        "int": ["member_cnt"],
        "const": {"src": "tdx"},
    },
    "市场统计指数_通达信": {
        "table": "board_info", "ddl": BOARD_INFO_DDL, "key": "board_code",
        "cat": "base", "note": "69 个市场统计指数 → board_info（src='tdx_stat'）",
        "rename": {"板块代码": "board_code", "板块名称": "board_name"},
        "dbl": [], "int": [],
        "const": {"src": "tdx_stat"},
    },
    "可转债基础信息列表": {
        "table": "cb_info", "ddl": CB_INFO_DDL, "key": "cb_code",
        "cat": "base", "note": "1,161 只可转债基础信息 → cb_info（按 cb_code MERGE）",
        "rename": {
            "转债代码": "cb_code", "转债名称": "cb_name", "转债简称": "cb_short",
            "转股申报代码": "apply_code", "正股代码": "stock_code", "正股简称": "stock_short",
            "发行期限(年)": "term_year", "面值": "par_value", "发行价格": "issue_price",
            "发行总额(元)": "issue_amount", "债券余额(元)": "bond_balance",
            "起息日期": "value_date", "到期日期": "maturity_date",
            "利率类型": "rate_type", "票面利率(%)": "coupon_rate",
            "补偿利率(%)": "compensate_rate", "年付息次数": "pay_freq",
            "上市日期": "list_date", "摘牌日": "delist_date", "上市地点": "list_place",
            "转股起始日": "conv_start", "转股截止日": "conv_end",
            "停止转股日(提前到期)": "conv_stop",
            "初始转股价": "conv_price_init", "最新转股价": "conv_price_now",
            "利率说明": "rate_desc", "到期赎回价格(含税)": "redeem_price",
            "发行信用等级": "issue_rating", "最新信用等级": "now_rating",
            "最新评级机构": "rating_org",
        },
        "dbl": ["term_year", "par_value", "issue_price", "issue_amount", "bond_balance",
                "coupon_rate", "compensate_rate", "conv_price_init", "conv_price_now",
                "redeem_price"],
        "int": ["pay_freq"],
    },
}


def _imp_info_table(con, path, date, dry=False):
    """指数列表 / 板块列表 / 市场统计指数 / 可转债基础信息 → 配置驱动

    全部无增量日期（快照）→ 按主键 MERGE upsert：
      已存在的行只更新本次 CSV 提供的非空列，不存在则插入。
    🔴 带括号列名（总股本(亿) / 票面利率(%)）必须双引号包裹。
    """
    import pandas as pd

    base = re.sub(r"\.csv$", "", os.path.basename(path))
    cfg = INFO_TABLE_CFG.get(base)
    if not cfg:
        raise RuntimeError(f"{base}：INFO_TABLE_CFG 未登记")
    tbl, key, ddl = cfg["table"], cfg["key"], cfg["ddl"]

    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.rename(columns=cfg["rename"])

    for c in cfg.get("const", {}):
        df[c] = cfg["const"][c]

    for c in cfg["dbl"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = None
    for c in cfg["int"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
        else:
            df[c] = None

    if key not in df.columns:
        raise RuntimeError(f"{base} 缺主键列 {key}（实际列：{list(df.columns)[:8]}）")

    if dry:
        # 只读连接不能建表 → dry 只校验「文件可解析 + 主键存在 + 行数」
        return {"table": tbl, "rows": int(len(df)), "dry": True}

    # 表结构固定 → 只保留表内列，缺失补 None
    con.execute(ddl)
    schema = {r[0] for r in con.execute(f"DESCRIBE {tbl}").fetchall()}
    for c in schema:
        if c not in df.columns:
            df[c] = None
    df = df[[c for c in df.columns if c in schema]].copy()
    df = df.replace("", None)
    df = df[df[key].notna()].drop_duplicates(subset=[key], keep="last")

    upd_cols = [c for c in df.columns if c != key]
    con.register("_x_it", df)
    try:
        # 多一列 src 参与匹配（board_info 两源共用一表）
        match = f't."{key}" = x."{key}"'
        if "src" in schema and "src" in df.columns:
            match += ' AND t."src" IS NOT DISTINCT FROM x."src"'
        if upd_cols:
            set_clause = ", ".join(
                f'"{c}" = COALESCE(x."{c}", t."{c}")' for c in upd_cols)
            con.execute(
                f'UPDATE {tbl} AS t SET {set_clause} FROM _x_it x WHERE {match}')
        con.execute(
            f'INSERT INTO {tbl} BY NAME SELECT * FROM _x_it x '
            f'WHERE NOT EXISTS (SELECT 1 FROM {tbl} t WHERE {match})')
        total = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    finally:
        con.unregister("_x_it")
    return {"table": tbl, "rows": int(len(df)), "total": int(total), "dry": False}


def write_probe(db_path=None):
    """🔴 2026-11-03：探测当前**进程**能否以写方式打开 DB。

    血证：PID 14232（非 launch_saa.py 拉起）读接口全部 200，
    但 `/api/upload/import` 两次都失败：
      IOException: Cannot open file ".../taobao_minute.duckdb": Operation not permitted
    同一批次换进程后 5/5 成功 → 是**进程级写权限**问题，不是数据/代码问题。
    所以启动时必须自检，并在写库入口提前拦截，避免用户反复点导入无提示。
    """
    db_path = db_path or config.DUCKDB
    try:
        con = duckdb.connect(db_path)
        try:
            con.execute("select 1").fetchone()
        finally:
            con.close()
        return {"ok": True, "error": None, "db": db_path}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "db": db_path}


_PERM_HINT = (
    "当前服务进程**没有外置盘写权限**（能读不能写）。"
    "这是进程上下文问题，不是数据问题：同一批次用 launch_saa.py 重启后即可导入。"
    "请执行 `python3 /Users/zenghui/.openclaw-autoclaw/workspace/.openclaw/tmp/launch_saa.py` 重启服务。"
)


def _connect_db(db_path, dry, max_wait=120):
    """真实导入需要写连接：被其它任务占锁时按 max_wait 秒轮询等待。"""
    if dry:
        return config.tune_duck(duckdb.connect(db_path, read_only=True))
    tries = max(1, int(max_wait / 3))
    last = None
    for i in range(tries):
        try:
            return config.tune_duck(duckdb.connect(db_path))
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e).lower()
            # 🆕 2026-11-03：EPERM 不是锁 —— 是进程无写权限。必须立刻给出可执行指引，
            #   否则用户只会看到裸的 IOException 反复重试。
            if "not permitted" in msg or "permission denied" in msg or "eperm" in msg:
                raise RuntimeError(f"数据库无法写入：{e}\n{_PERM_HINT}") from e
            if "lock" in msg or "conflicting" in msg:
                with _LOCK:
                    _PROGRESS["current"] = f"等待数据库锁释放…（已等 {i * 3}s）"
                if i < tries - 1:
                    time.sleep(3)
                    continue
                raise RuntimeError(
                    f"数据库被其他进程占用超过 {tries * 3} 秒（可能是你的挖矿/同步任务在写），"
                    "请等它结束后重试，或勾选「自动排队等锁」") from e
            raise
    raise RuntimeError(f"数据库连接失败：{last}")


def _import_items(items, dry, db_path, max_wait=120):
    """导入一组已分类文件（每项含 name/path/kind/tables/importer/date/market）。"""
    results = []
    with _LOCK:
        _PROGRESS["total"] = len(items)
    con = _connect_db(db_path, dry, max_wait=max_wait)
    try:
        for it in items:
            with _LOCK:
                _PROGRESS["current"] = it["name"]
            try:
                path, date = it["path"], it["date"]
                imp = it["importer"]
                if imp == "minute":
                    res = _imp_minute(con, path, it["tables"][0], date, it.get("market", "hs"), dry=dry)
                elif imp == "daily26":
                    res = _imp_daily26(con, path, date, dry=dry)
                elif imp == "moneyflow":
                    res = _imp_moneyflow(con, path, date, dry=dry)
                elif imp == "market_flow":
                    res = _imp_market_flow(con, path, date, dry=dry)
                elif imp == "hk_flow":
                    res = _imp_hk_flow(con, path, date, dry=dry)
                elif imp == "hk_daily":
                    res = _imp_hk_daily(con, path, date, dry=dry)
                elif imp == "daily_ind_zip":
                    res = _imp_daily_ind_zip(con, path, date, dry=dry)
                elif imp == "adj_zip":
                    res = _imp_adj_zip(con, path, date, dry=dry)
                elif imp == "fund_adj_zip":
                    res = _imp_fund_adj_zip(con, path, date, dry=dry)
                elif imp == "board_flow_zip":
                    res = _imp_board_flow_zip(con, path, date, dry=dry)
                elif imp == "stock_flow_zip":
                    res = _imp_stock_flow_zip(con, path, date, dry=dry)
                elif imp == "fin_zip":
                    res = _imp_fin_zip(con, path, date, dry=dry)
                elif imp == "adj_cum_zip":
                    res = _imp_adj_cum_zip(con, path, date, dry=dry)
                elif imp == "tech_zip":
                    res = _imp_tech_zip(con, path, date, dry=dry)
                elif imp == "tech_factor_day":
                    res = _imp_tech_factor_day(con, path, date, dry=dry)
                elif imp == "fin_snapshot":
                    res = _imp_fin_snapshot(con, path, date, dry=dry)
                elif imp == "fin_report_full":
                    res = _imp_fin_report_full(con, path, date, dry=dry)
                elif imp == "theme_zip":
                    res = _imp_theme_zip(con, path, date, dry=dry)
                elif imp == "restricted_zip":
                    res = _imp_restricted(con, path, date, dry=dry)
                elif imp == "st_susp":
                    res = _imp_st_susp(con, path, it["tables"][0], date, dry=dry)
                elif imp == "limit_price":
                    res = _imp_limit_price(con, path, date, dry=dry)
                elif imp == "limit_price_zip":
                    res = _imp_limit_price_zip(con, path, date, dry=dry)
                elif imp == "susp_zip":
                    res = _imp_susp_zip(con, path, date, dry=dry)
                elif imp == "stock_rename":
                    res = _imp_stock_rename(con, path, dry=dry)
                elif imp == "stock_list":
                    res = _imp_stock_list(con, path, date, dry=dry)
                elif imp == "fund_list":
                    res = _imp_fund_list(con, path, date, dry=dry)
                elif imp == "info_table":
                    res = _imp_info_table(con, path, date, dry=dry)
                else:
                    res = _imp_generic(con, path, it["tables"][0], date,
                                       const=const_for(it), date_col=date_col_for(it),
                                       dry=dry, scale=it.get("scale"))
                if isinstance(res, list):
                    results.extend([{**r, "file": it["name"]} for r in res])
                else:
                    results.append({**res, "file": it["name"]})
                _log(f"{'预演' if dry else '导入'} {it['name']} → "
                     f"{res['table'] if not isinstance(res, list) else ','.join(r['table'] for r in res)}")
            except Exception as e:  # noqa: BLE001
                _log(f"失败 {it['name']}: {type(e).__name__}: {e}")
                results.append({"file": it["name"], "error": f"{type(e).__name__}: {e}"})
            with _LOCK:
                _PROGRESS["done"] += 1
        if not dry:
            invalidate_meta()
            try:
                touched = {r.get("table") for r in results if not r.get("error")}
                _update_meta(con, touched)
            except Exception as e:  # noqa: BLE001
                _log(f"更新 table_meta 失败: {e}")
            try:
                con.execute("checkpoint")
            except Exception:
                pass
    finally:
        con.close()
    with _LOCK:
        _PROGRESS["results"] = results
    return results


# ══════════════════ 每日必备清单 · 覆盖度检查 ══════════════════
# key=表名, value=(中文名, 归类, 缺失时该补哪个包)
DAILY_REQUIRED = {
    # ── 日线/估值/因子 ──
    "daily": ("日线行情", "日线", "YYYYMMDD.csv（26 列日线）"),
    "daily_basic": ("估值", "日线", "YYYYMMDD.csv"),
    "daily_mv": ("市值", "日线", "YYYYMMDD.csv 或 每日指标.zip"),
    "daily_ind": ("每日指标", "日线", "每日指标.zip（1.1G·每票一 csv）"),
    "tech_factor_hfq": ("技术因子(后复权)", "因子", "技术因子_YYYYMMDD.csv"),
    "adj_factor": ("复权因子", "因子", "复权因子_前复权.zip + 复权因子_后复权.zip（两个都要）"),
    "etf_adj_factor": ("ETF复权因子", "因子", "ETF_复权因子_前复权.zip + ETF_复权因子_后复权.zip"),
    "lof_adj_factor": ("LOF复权因子", "因子", "LOF_复权因子_前复权.zip + LOF_复权因子_后复权.zip"),
    "limit_price": ("涨跌停价(派生)", "因子", "无需上传：导入 YYYYMMDD.csv 后自动派生"),
    # ── 分钟 ──
    "minute_1min": ("1 分钟线", "分钟", "YYYYMMDD_1min.zip + (1)~(6) 共 7 个分片"),
    "minute_5min": ("5 分钟线", "分钟", "YYYYMMDD_5min.zip + (1)~(6)"),
    "minute_15min": ("15 分钟线", "分钟", "YYYYMMDD_15min.zip + (1)~(6)"),
    "minute_30min": ("30 分钟线", "分钟", "YYYYMMDD_30min.zip + (1)~(6)"),
    "minute_60min": ("60 分钟线", "分钟", "YYYYMMDD_60min.zip + (1)~(6)"),
    # ── 资金流 ──
    "industry_flow_ths": ("行业资金流(同花顺)", "资金流", "YYYYMMDD*.csv（表头：行业代码+收盘指数）"),
    "stock_flow_ths": ("个股资金流(同花顺)", "资金流", "YYYYMMDD*.csv（表头：5日主力净额）"),
    "stock_flow_em": ("个股资金流(东财)", "资金流", "YYYYMMDD*.csv（表头：今日主力净流入额）"),
    "moneyflow_stock": ("资金流明细(大中小单)", "资金流", "YYYYMMDD*.csv（表头：小单买入量+净流入量）"),
    "board_flow_ths": ("板块资金流(同花顺)", "资金流", "YYYYMMDD*.csv（表头：板块代码+流入资金）"),
    "board_flow_em": ("板块资金流(东财)", "资金流", "YYYYMMDD*.csv（表头：数据类型+主力净流入净额）"),
    "market_flow_daily": ("大盘资金流", "资金流", "大盘资金流向.csv（全历史快照）"),
    "hk_connect_flow": ("沪深港通资金流", "资金流", "沪深港通资金流向.csv（全历史快照）"),
    # ── 涨停池（开盘啦）──
    "zt_pool_limit_up": ("开盘啦·涨停", "涨停池", "涨停_YYYYMMDD.csv（24 列含竞价）"),
    "zt_pool_natural_up": ("开盘啦·自然涨停", "涨停池", "自然涨停_YYYYMMDD.csv"),
    "zt_pool_broken": ("开盘啦·炸板", "涨停池", "炸板_YYYYMMDD.csv"),
    "zt_pool_limit_down": ("开盘啦·跌停", "涨停池", "跌停_YYYYMMDD.csv"),
    "zt_pool_auction": ("开盘啦·竞价", "涨停池", "竞价_YYYYMMDD.csv"),
    # ── 涨停池（同花顺）──
    "zt_ths_limit_up": ("同花顺·涨停池", "涨停池", "涨停池_YYYYMMDD.csv（18 列）"),
    "zt_ths_broken": ("同花顺·炸板池", "涨停池", "炸板池_YYYYMMDD.csv"),
    "zt_ths_ladder_pool": ("同花顺·连板池", "涨停池", "连扳池_YYYYMMDD.csv"),
    "zt_ths_limit_down": ("同花顺·跌停池", "涨停池", "跌停池_YYYYMMDD.csv"),
    "zt_ths_rush": ("同花顺·冲刺涨停", "涨停池", "冲刺涨停_YYYYMMDD.csv"),
    "ladder": ("连板天梯", "涨停池", "连板天梯_YYYYMMDD.csv"),
    "zt_strong_board": ("涨停最强板块", "涨停池", "涨停最强板块_YYYYMMDD.csv"),
    # ── 龙虎榜/题材 ──
    "lhb_daily": ("龙虎榜明细", "龙虎榜", "龙虎榜明细_YYYYMMDD.csv"),
    "lhb_inst": ("龙虎榜机构", "龙虎榜", "龙虎榜机构明细_YYYYMMDD.csv"),
    "theme_stock": ("题材成分", "题材", "题材成分_KPL_YYYYMMDD.zip"),
}

# 低频/可选（不参与每日覆盖度，但页面会显示）
OPTIONAL_TABLES = {
    "tech_factor_qfq": ("技术因子(前复权)", "因子", "由 daily 重算（无单日包）"),
    "hot_rank_ths": ("热股榜(同花顺)", "热度", "热股_YYYYMMDD.csv"),
    "hot_rank_em": ("热榜(东财)", "热度", "A股市场_/热股_ 等 CSV"),
    "fin_company_snapshot": ("财务快照", "财务", "上市公司财务信息_YYYYMMDD.csv"),
    "fin_restricted": ("限售解禁", "财务", "限售股解禁.zip"),
    "stock_info": ("股票列表", "基础", "股票列表.csv"),
    # ⚠️ 源包比行情滞后一天（10-28 的包里最新只到 10-26）→ 不进必备，免得天天误报
    "hk_connect_daily": ("港股通成交统计", "资金流", "港股通每日成交统计.csv（全历史快照）"),
}


def coverage(date, status=None, refresh=False):
    """某交易日的每日必备表覆盖度。用 table_meta 缓存（minute 表 max 查询要 40s，不能实时算）。
    refresh=True 时强制重算指定表。"""
    status = db_status(force=refresh) if status is None else status
    ok, missing = [], []
    for tbl, (label, cat, hint) in DAILY_REQUIRED.items():
        mx = _norm_date((status.get(tbl) or {}).get("max"))
        got = bool(mx) and mx >= date
        rec = {"table": tbl, "label": label, "cat": cat, "max": mx or None, "hint": hint}
        (ok if got else missing).append(rec)
    total = len(DAILY_REQUIRED)
    expect = expect_date()
    latest_daily = _norm_date((status.get("daily") or {}).get("max"))
    # 「今天该导但还没导」≠「真滞后」：前者盘后自然会补，用弱提示即可
    _today = datetime.date.today().strftime("%Y%m%d")
    expect_is_today = (expect == _today)
    return {
        "date": date,
        "expect": expect,
        "latest_daily": latest_daily,
        "stale": bool(latest_daily) and latest_daily < expect,
        "expect_is_today": expect_is_today,
        "lag_days": lag_trade_days(latest_daily, expect),
        "total": total,
        "ok_count": len(ok),
        "pct": round(100.0 * len(ok) / total, 1) if total else 0.0,
        "ok": ok,
        "missing": missing,
        "by_cat": _coverage_by_cat(ok, missing),
        "optional": [{"table": t, "label": v[0], "cat": v[1], "hint": v[2],
                      "max": _norm_date((status.get(t) or {}).get("max")) or None}
                     for t, v in OPTIONAL_TABLES.items()],
    }


# ─────────────── 覆盖度「应到基准日」 ───────────────
# 🩸 血证 2026-10-31：原 coverage() 无 date 时用「库里 daily 最新日」当基准
#    → 数据停更时基准跟着停 → 拿 10-29 判 10-29 = 恒 100% 全绿，永不报警。
#    基准必须是「按日历应该到哪一天」，不能是「库里已经到哪一天」。
_EXPECT = {"key": None, "val": None}


def expect_date(now=None, cutoff_hour=18):
    """应到的最新交易日（YYYYMMDD）。

    判据：trade_cal 中 <= today 的最大交易日；
         若 today 本身就是交易日但还没过 cutoff（默认 18:00，盘后数据未出）
         → 退到前一个交易日（否则每个交易日白天都会误报「缺今天」）。
    """
    now = now or datetime.datetime.now()
    key = now.strftime("%Y%m%d%H")          # 每小时变一次，缓存足够
    if _EXPECT["val"] and _EXPECT["key"] == key:
        return _EXPECT["val"]
    today = now.date()
    rows = []
    try:
        con = config.tune_duck(duckdb.connect(config.DUCKDB, read_only=True))
        try:
            rows = [r[0] for r in con.execute(
                "select date from trade_cal where date <= ? order by date desc limit 5",
                [today]).fetchall()]
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        val = _norm_date(today)
    elif rows[0] == today and now.hour < cutoff_hour:
        val = _norm_date(rows[1]) if len(rows) > 1 else _norm_date(rows[0])
    else:
        val = _norm_date(rows[0])
    _EXPECT.update({"key": key, "val": val})
    return val


def lag_trade_days(latest, expect):
    """(latest, expect] 之间的交易日数 = 数据滞后几个交易日。"""
    if not latest or not expect or latest >= expect:
        return 0
    try:
        d1 = datetime.date(int(latest[:4]), int(latest[4:6]), int(latest[6:8]))
        d2 = datetime.date(int(expect[:4]), int(expect[4:6]), int(expect[6:8]))
        con = config.tune_duck(duckdb.connect(config.DUCKDB, read_only=True))
        try:
            return con.execute(
                "select count(*) from trade_cal where date > ? and date <= ?",
                [d1, d2]).fetchone()[0]
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return 0


def _coverage_by_cat(ok, missing):
    cats = {}
    for rec, hit in [(r, True) for r in ok] + [(r, False) for r in missing]:
        c = cats.setdefault(rec["cat"], {"cat": rec["cat"], "ok": 0, "total": 0, "missing": []})
        c["total"] += 1
        if hit:
            c["ok"] += 1
        else:
            c["missing"].append(rec["table"])
    return sorted(cats.values(), key=lambda x: (-x["total"], x["cat"]))


def refresh_meta(date=None, tables=None):
    """重算 table_meta（minute_1min 的 max(dt) 约 40s，慎调）。tables=None → 只刷每日必备清单。"""
    targets = tables or list(DAILY_REQUIRED) + list(OPTIONAL_TABLES)
    con = config.tune_duck(duckdb.connect(config.DUCKDB))
    done = []
    try:
        for tbl in targets:
            try:
                cols = [c[0] for c in con.execute(f"describe {tbl}").fetchall()]
                dc = next((c for c in ["date", "dt", "trade_date", "交易日期", "交易时间",
                                        "更新日期", "报告期", "month"]
                           if c in cols), None)
                if not dc:
                    continue
                mx = con.execute(f'select max("{dc}") from {tbl}').fetchone()[0]
                if mx is None:
                    continue
                m = _norm_date(str(mx))
                con.execute("delete from table_meta where table_name = ?", [tbl])
                con.execute("insert into table_meta values (?, ?, ?, now())", [tbl, dc, m])
                done.append({"table": tbl, "max": m})
            except Exception as e:  # noqa: BLE001
                done.append({"table": tbl, "error": f"{type(e).__name__}: {e}"})
        try:
            con.execute("checkpoint")
        except Exception:
            pass
    finally:
        con.close()
    invalidate_meta()
    return done


def _update_meta(con, tables):
    """导入成功后刷新 table_meta（每张表的最新日期）"""
    cands = ["date", "dt", "trade_date", "交易日期", "交易时间", "更新日期", "报告期"]
    for t in sorted(x for x in tables if x):
        try:
            cols = [c[0] for c in con.execute(f"describe {t}").fetchall()]
            dc = next((c for c in cands if c in cols), None)
            if not dc:
                continue
            mx = con.execute(f"select max({dc}) from {t}").fetchone()[0]
            if mx is None:
                continue
            m = _norm_date(str(mx))
            con.execute("delete from table_meta where table_name = ?", [t])
            con.execute("insert into table_meta values (?, ?, ?, now())", [t, dc, m])
        except Exception as e:  # noqa: BLE001
            _log(f"meta {t}: {e}")


def run_import(date, keys=None, dry=True, db_path=None, pack_dir=None):
    """导入某一天指定类型的增量包。keys=None 表示全部可导入类型。"""
    db_path = db_path or config.DUCKDB
    pack_dir = pack_dir or getattr(config, "PACK_DIR", "")
    with _LOCK:
        _PROGRESS.update({"running": True, "date": date, "done": 0, "total": 0,
                          "current": "", "log": [], "error": None, "results": []})
    try:
        scan_res = scan(pack_dir)
        day = next((d for d in scan_res["dates"] if d["date"] == date), None)
        if not day:
            raise RuntimeError(f"没有 {date} 的增量包")
        items = [f for c in day["cats"] for f in c["items"]]
        if keys:
            items = [f for f in items if f["kind"] in keys]
        items = [f for f in items if f["importer"] != "unsupported" and not f.get("dup")]
        for it in items:
            it["path"] = os.path.join(pack_dir, it["name"])
            it["date"] = date
        results = _import_items(items, dry, db_path)
        return {"date": date, "dry": dry, "results": results}
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            _PROGRESS["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        with _LOCK:
            _PROGRESS["running"] = False


def const_for(item):
    k = item["kind"]
    if k in ("pool_涨停", "pool_炸板", "pool_跌停"):
        return {"src_type": "开盘啦"}
    if k.startswith("ths_"):
        return {"src_type": "同花顺"}
    return None


def date_col_for(item):
    t = item["tables"][0] if item["tables"] else ""
    if t.startswith("zt_pool_"):
        return "交易时间"
    if t in ("zt_strong_board", "hot_rank_em", "hot_rank_ths", "lhb_daily", "lhb_inst",
             "ladder") or t.startswith("zt_ths_"):
        return "交易日期"
    if t.startswith(("stock_flow_", "industry_flow_", "board_flow_")):
        return "交易日期"
    return None


# ────────────────────────── pandas 版导入工具 ──────────────────────────

def _schema(con, table):
    rows = con.execute(f"describe {table}").fetchall()
    return {r[0]: r[1] for r in rows}


def _cast_df(df, schema):
    """按目标表类型转换 DataFrame 列。"""
    import pandas as pd
    for col in list(df.columns):
        typ = schema.get(col, "VARCHAR")
        if typ in ("DATE",):
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
        elif typ.startswith("TIMESTAMP"):
            df[col] = pd.to_datetime(df[col], errors="coerce")
        elif typ in ("DOUBLE", "FLOAT", "REAL", "DECIMAL"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        elif typ in ("BIGINT", "INTEGER", "HUGEINT", "SMALLINT", "TINYINT", "UBIGINT", "UINTEGER"):
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df


def _imp_generic(con, path, table, date, const=None, date_col=None, rename=None,
                 dry=False, scale=None):
    import pandas as pd
    schema = _schema(con, table)
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    if rename:
        df = df.rename(columns=rename)
    keep = [c for c in df.columns if c in schema]
    if not keep:
        raise RuntimeError(f"{os.path.basename(path)} 与 {table} 无同名列")
    # 🔴🔴 2026-10-28 血证：只要求「有同名列」太松 —— 两张表都有「交易日期」，
    #   于是个股资金流 4,656 行被整批灌进 industry_flow_ths（其余列全 NULL）。
    #   现在：同名列要有业务列（不只是日期/代码），且占比 ≥50%，否则判定错表、拒收。
    _biz = [c for c in keep if c not in ("交易日期", "date", "trade_date", "股票代码", "板块代码", "行业代码")]
    _ratio = len(keep) / max(len(list(df.columns)), 1)
    if not _biz or len(keep) < 2 or _ratio < 0.5:
        raise RuntimeError(
            f"{os.path.basename(path)} → {table} 疑似错表，拒绝导入"
            f"（同名列 {len(keep)}/{len(list(df.columns))} = {_ratio:.0%}：{keep[:5]}）")
    df = df[keep]
    # 🆕 2026-11-01：代码格式归一（血证：不归一时同一只票两种格式并存，
    #   count(distinct 代码||日期) 查不出来 —— 代码字符串不同）
    _cc = "股票代码" if "股票代码" in df.columns else ("code" if "code" in df.columns else None)
    if _cc:
        df = _norm_code_df(df, table, _cc)
        _assert_single_fmt(df[_cc], table, f" {os.path.basename(path)}")
    # 单位换算：CSV 列名写「万元」但库里实存「元」等情形（key 用归一化列名匹配）
    if scale:
        _sc = {_norm_col(k): v for k, v in scale.items()}
        for c in list(df.columns):
            m = _sc.get(_norm_col(c))
            if m:
                df[c] = pd.to_numeric(df[c], errors="coerce") * m
    for k, v in (const or {}).items():
        df[k] = v
    df = _cast_df(df, schema)
    if dry:
        return {"table": table, "rows": int(len(df)), "dry": True}
    if date_col:
        d_iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
        # 🔴 榜单族（热榜）一天多文件写同一张表 → 只能删「本文件的数据类型」那一块
        #    🩸 2026-10-28：整天 DELETE 导致 13 个热榜文件互相覆盖，只剩最后一个
        if "数据类型" in keep:
            con.register("_del_types", pd.DataFrame({"t": sorted(set(df["数据类型"].astype(str)))}))
            try:
                con.execute(
                    f'delete from {table} where (cast({date_col} as varchar) like ? '
                    f'or cast({date_col} as varchar) like ?) '
                    f'and "数据类型" in (select t from _del_types)',
                    [d_iso + "%", date + "%"])
            finally:
                con.unregister("_del_types")
        else:
            con.execute(f"delete from {table} where cast({date_col} as varchar) like ? "
                        f"or cast({date_col} as varchar) like ?",
                        [d_iso + "%", date + "%"])
    con.register("_pack_df", df)
    try:
        con.execute(f"insert into {table} by name select * from _pack_df")
    finally:
        con.unregister("_pack_df")
    return {"table": table, "rows": int(len(df)), "dry": False}


# ────────────────────────── 手动上传（PC 上传台） ──────────────────────────

UPLOAD_DIR = os.path.join(config.DATA, "uploads")
LEDGER = os.path.join(UPLOAD_DIR, "ledger.json")
_LEDGER_LOCK = threading.Lock()
_HASH_CACHE = {}


def file_sha256(path):
    """文件指纹（用于防重）；按 (path, size, mtime) 缓存。"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_size, int(st.st_mtime))
    if key in _HASH_CACHE:
        return _HASH_CACHE[key]
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    _HASH_CACHE[key] = h.hexdigest()
    return _HASH_CACHE[key]


def load_ledger():
    with _LEDGER_LOCK:
        try:
            with open(LEDGER, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


def save_ledger(d):
    with _LEDGER_LOCK:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        tmp = LEDGER + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, LEDGER)


def ledger_add(entries):
    """记录已导入文件指纹"""
    if not entries:
        return
    d = load_ledger()
    for e in entries:
        if e.get("hash"):
            d[e["hash"]] = {k: e.get(k) for k in ("name", "date", "tables", "rows", "at")}
    save_ledger(d)


def _rm_pack(path, name, deleted, failed):
    """删一个数据包；失败时记录原因，绝不静默。"""
    try:
        os.remove(path)
        deleted.append(name)
        return True
    except FileNotFoundError:
        return True                      # 已不存在 = 目标达成
    except OSError as e:
        failed.append({"file": name, "error": f"{type(e).__name__}: {e}",
                       "path": path})
        return False


def _rmdir_if_empty(d):
    try:
        if os.path.isdir(d) and not [f for f in os.listdir(d) if not f.startswith(".")]:
            os.rmdir(d)
            return True
    except OSError:
        pass
    return False


def purge_imported_uploads(dry=True):
    """清理历史残留：uploads 下「已成功导入过」（ledger 有 SHA 记录）的数据包。

    为什么需要：删除逻辑上线前的旧进程遗留了数 GB 副本；且删除失败也可能留下。
    安全边界：
      · 只删 **SHA 在 ledger 里** 的文件 → 未导入的一律不动
      · 只删 uploads 目录内的文件（不碰源包目录）
      · 默认 dry=True，必须显式 dry=False 才真删
    """
    led = load_ledger()
    hashes = set(k for k in led.keys() if k)
    files, keep = [], []
    total_bytes = 0
    if not os.path.isdir(UPLOAD_DIR):
        return {"dry": dry, "deleted": [], "failed": [], "kept": [],
                "count": 0, "bytes": 0}
    for b in sorted(os.listdir(UPLOAD_DIR)):
        bd = os.path.join(UPLOAD_DIR, b)
        if not os.path.isdir(bd) or b.startswith("."):
            continue
        for f in sorted(os.listdir(bd)):
            if f.startswith("."):
                continue
            p = os.path.join(bd, f)
            if not os.path.isfile(p):
                continue
            try:
                sz = os.path.getsize(p)
            except OSError:
                sz = 0
            h = file_sha256(p)
            if h and h in hashes:
                files.append({"file": f, "batch": b, "path": p, "bytes": sz})
                total_bytes += sz
            else:
                keep.append({"file": f, "batch": b, "bytes": sz})
    deleted, failed = [], []
    if not dry:
        for it in files:
            if _rm_pack(it["path"], it["file"], deleted, failed):
                _rmdir_if_empty(os.path.join(UPLOAD_DIR, it["batch"]))
        _rmdir_if_empty(UPLOAD_DIR)
    return {"dry": dry, "deleted": deleted if not dry else [i["file"] for i in files],
            "failed": failed, "kept": keep,
            "count": len(files), "bytes": total_bytes}


def new_batch():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    b = time.strftime("%Y%m%d-%H%M%S")
    d = os.path.join(UPLOAD_DIR, b)
    os.makedirs(d, exist_ok=True)
    return b, d


def stage_upload(batch, file_storage):
    """保存一个上传文件到批次目录，返回落盘信息。"""
    name = os.path.basename(file_storage.filename or "upload.bin")
    name = name.replace("/", "_").replace("\\", "_")
    d = os.path.join(UPLOAD_DIR, batch)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    file_storage.save(path)
    return {"name": name, "path": path, "size": os.path.getsize(path)}


def describe_staged(batch):
    """列出批次内文件并给出分类/映射/入库状态。"""
    d = os.path.join(UPLOAD_DIR, batch)
    if not os.path.isdir(d):
        return []
    meta = db_status()
    out = []
    for name in sorted(os.listdir(d)):
        if name.startswith("."):
            continue
        path = os.path.join(d, name)
        if not os.path.isfile(path):
            continue
        date = _file_date(name)
        info = classify(name, date, path)
        item = {
            "name": name, "size": os.path.getsize(path), "date": date,
            "kind": info["key"] if info else "other",
            "cat": info["cat"] if info else "other",
            "cat_label": CATEGORY_LABEL.get(info["cat"] if info else "other", "其他"),
            "label": info["label"] if info else "未识别",
            "tables": info["tables"] if info else [],
            "importer": info["importer"] if info else "unsupported",
            "supported": bool(info) and info["importer"] != "unsupported",
            "note": info["note"] if info else "未识别文件类型",
        }
        if info and info.get("freq"):
            item.update({"freq": info["freq"], "part": info["part"],
                         "market": info["market"], "content": info["content"]})
        if name.lower().endswith(".zip"):
            n, _ = _zip_entries(path)
            item["entries"] = n
        item["imported"] = {t: bool(meta.get(t) and (
            True if t in SNAPSHOT_TABLES          # 快照表：非空即已导
            else _norm_date(meta[t].get("max")) >= date))
                            for t in item["tables"]} if date else {}
        item["hash"] = file_sha256(path)
        led = load_ledger().get(item["hash"] or "")
        item["imported_before"] = led
        item["duplicate"] = bool(led)
        out.append(item)
    return out


def import_batch(batch, names=None, date_override=None, dry=False, db_path=None,
                 allow_duplicates=False, wait_lock=120):
    """导入某个上传批次（names 为空表示全部）。

    防重：默认跳过账本里已成功导入过的文件（内容 SHA-256 命中）；
    导入成功（该文件所有目标表都成功）后删除上传副本（config.DELETE_AFTER_IMPORT）。
    """
    db_path = db_path or config.DUCKDB
    with _LOCK:
        _PROGRESS.update({"running": True, "date": "", "done": 0, "total": 0,
                          "current": "", "log": [], "error": None, "results": [],
                          "skipped": [], "deleted": [], "delete_failed": []})
    try:
        items = [it for it in describe_staged(batch) if it["supported"]]
        if names:
            items = [it for it in items if it["name"] in set(names)]
        skipped = []
        if not allow_duplicates:
            keep = []
            for it in items:
                if it.get("duplicate"):
                    skipped.append({
                        "file": it["name"], "skipped": True,
                        "reason": f"已导入过（{it['imported_before'].get('at', '')}）",
                        "tables": it["tables"],
                    })
                else:
                    keep.append(it)
            items = keep
        override_warn = []
        for it in items:
            it["path"] = os.path.join(UPLOAD_DIR, batch, it["name"])
            # 🔴 2026-10-28 修：date_override 只对「文件名解析不出日期」的文件生效。
            #   原实现 override 优先于文件名 → 页面自动填「最新交易日」时，
            #   20261028.csv 会被当成 20261027 处理，DELETE 掉 10-27 的完好数据
            #   （血证：daily/daily_mv/lhb_*/ladder/zt_* 共 12 表 10-27 被清空）。
            own = it.get("date") or ""
            if own:
                if date_override and date_override != own:
                    override_warn.append(f"{it['name']}: 文件名日期 {own} ≠ 指定 {date_override}，按 {own} 处理")
                it["date"] = own
            else:
                # 🔴 2026-10-31 修：原 `time.strftime("%Y%m%d")` = **今天**。
                #   血证：周六导入全历史 zip（复权因子/每日指标/技术因子）→ date=周六
                #   → 包内最新只到周五 → 报「包里没有 XX 的数据（包可能是旧的）」→ 4 项失败。
                #   改为「应到的最新已收盘交易日」= 周五，与包内容对齐。
                if date_override:
                    it["date"] = date_override
                else:
                    try:
                        it["date"] = expect_date()
                    except Exception:  # noqa: BLE001
                        it["date"] = time.strftime("%Y%m%d")
        with _LOCK:
            _PROGRESS["date"] = batch
        results = _import_items(items, dry, db_path, max_wait=wait_lock) if items else []
        # 成功文件：记账 + 删除数据包（副本）
        # 🔴 2026-10-28 增强：删除失败不再静默吞掉（原 except OSError: pass 会把
        #   权限/占用/路径错误全部吃掉 → uploads 目录静默涨到 7GB 无人知）；
        #   skip（已导入过）的文件同样删除，因为内容已在库里，副本只剩占空间。
        deleted, delete_failed = [], []
        if not dry:
            led_entries, ok_names = [], set()
            do_del = getattr(config, "DELETE_AFTER_IMPORT", True)
            for it in items:
                rows = [r for r in results if r.get("file") == it["name"]]
                ok = bool(rows) and all(not r.get("error") for r in rows)
                if ok:
                    ok_names.add(it["name"])
                    led_entries.append({
                        "hash": it.get("hash"), "name": it["name"], "date": it["date"],
                        "tables": it["tables"],
                        "rows": sum(int(r.get("rows") or 0) for r in rows),
                        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                if ok and do_del:
                    _rm_pack(it["path"], it["name"], deleted, delete_failed)
            ledger_add(led_entries)
            # skip 的（SHA 判重 = 内容已入库）也清掉副本
            if do_del:
                for s in skipped:
                    _rm_pack(os.path.join(UPLOAD_DIR, batch, s["file"]),
                             s["file"], deleted, delete_failed)
            # 批次目录空了就删掉
            _rmdir_if_empty(os.path.join(UPLOAD_DIR, batch))
        with _LOCK:
            _PROGRESS["skipped"] = skipped
            _PROGRESS["deleted"] = deleted
            _PROGRESS["delete_failed"] = delete_failed
            _PROGRESS["date_override_warn"] = override_warn
        return {"batch": batch, "dry": dry, "results": results,
                "skipped": skipped, "deleted": deleted,
                "delete_failed": delete_failed,
                "date_override_warn": override_warn}
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            _PROGRESS["error"] = f"{type(e).__name__}: {e}"
        raise
    finally:
        with _LOCK:
            _PROGRESS["running"] = False
