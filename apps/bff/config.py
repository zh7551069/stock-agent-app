"""stock-advice-agent 配置。

数据来源两条腿：
  1. Obsidian 知识库（只读）—— 选股知识点的唯一事实源
  2. 本地 DuckDB（只读）—— 行情/资金/涨停池；ETL 到 data/cache 后网站只读缓存
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "cache")
OUT = os.path.join(ROOT, "out")
WEB = os.path.join(ROOT, "web")
# 同机账本（decision_log / positions / watchlist）。手机不直连；只由本机 BFF 与脚本打开。
SQLITE = os.environ.get("LIUPAN_SQLITE", os.path.join(DATA, "liupan.sqlite"))

# 1) Obsidian 知识库
VAULT = os.environ.get(
    "STOCK_ADVICE_VAULT",
    os.path.expanduser("~/Documents/stock/stock/知识库/知识地图"))
KB_INDEX = os.path.join(VAULT, "00-知识点卡总览.md")

# 2) 行情库（外盘；未挂载时 ETL 会报错，但网站可用旧缓存）
DUCKDB = os.environ.get(
    "STOCK_ADVICE_DB",
    "/Volumes/My PSSD/本地数据库_v2版（淘宝购买）/duckdb/taobao_minute.duckdb")

# 每日增量包目录（淘宝/赛博空间下载的 zip/csv 落在这里）
PACK_DIR = os.environ.get("STOCK_ADVICE_PACK_DIR", "/Volumes/My PSSD/downlodData/taobao")

# ETL 参数
DAILY_DAYS = 320          # 日线回溯交易日
MINUTE_DAYS = 12          # 分钟线回溯交易日
FLOW_DAYS = 30            # 资金流/龙虎榜回溯

# 服务
HOST = "127.0.0.1"
PORT = 8770

# ── 自动刷新 ────────────────────────────────────────────────
AUTO_REFRESH = True        # 后台看护线程：检测源库/缓存变化并自动重载
WATCH_INTERVAL = 120       # 轮询秒数
AUTO_ETL = True            # 源库出现新交易日时，自动跑 ETL（DuckDB → parquet）
ETL_QUIET_SECONDS = 180    # 源库文件需静默这么久才敢读（避免和你的同步任务抢锁）

# ── 盘中实时行情（东财 push2）──────────────────────────────
REALTIME = True            # 盘中策略是否使用实时行情（False = 只用本地历史回放）
REALTIME_TTL = 20          # 全市场快照缓存秒数（原 12s，降频避免东财限流）

# 个股详情 K 线：是否允许短连接读取本地 DuckDB（1min 数据）
KLINE_USE_DB = True

# 数据导入台：导入成功后删除上传副本（防重复 import）
DELETE_AFTER_IMPORT = True


# ── DuckDB 溢写目录（2026-10-28 立）──────────────────────────
# 病因：DuckDB 默认 temp_directory = ".tmp" 是相对路径 → 解析成「进程 cwd 下 .tmp」，
#   全部落在内盘；且 max_temp_directory_size 默认 = 可用空间的 90% → 一次大 join
#   溢写就能把内盘写满（实测根分区曾被挤到只剩 20GB）。
# 处置：溢写一律走外盘，并把上限压到 20GB。
DUCK_TMP = os.environ.get("DUCK_TMP", "/Volumes/My PSSD/duck_tmp")
DUCK_TMP_MAX = os.environ.get("DUCK_TMP_MAX", "20GB")


def tune_duck(con):
    """把连接的溢写目录指到外盘并限流。失败不阻断（外盘未挂载时退回默认行为）。"""
    try:
        os.makedirs(DUCK_TMP, exist_ok=True)
        con.execute(f"SET temp_directory='{DUCK_TMP}'")
        con.execute(f"SET max_temp_directory_size='{DUCK_TMP_MAX}'")
    except Exception:
        pass
    return con
