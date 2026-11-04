"""自动刷新看护线程。

解决三层陈旧：
  L1 DuckDB → parquet    ：检测源库出现「更新交易日」→ 自动跑 ETL
  L2 parquet → 内存      ：检测 parquet 变化 → reset_ctx()
  L3 内存 → 推荐          ：回调 server 重算推荐并 bump version

设计约束：
- 只读源库；读之前要求源库文件静默 ETL_QUIET_SECONDS，避免和用户的同步任务抢锁
- ETL 在子进程里跑，失败不影响网站继续用旧数据
- ETL 期间网站照常服务（旧数据）
"""
import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from core import data as data_layer


def _log(*a):
    print("[auto-refresh]", *a, flush=True)


class AutoRefresher(threading.Thread):
    def __init__(self, on_changed=None):
        super().__init__(daemon=True, name="auto-refresher")
        self.on_changed = on_changed
        self.interval = max(20, int(config.WATCH_INTERVAL))
        self.enabled = bool(config.AUTO_REFRESH)
        self.auto_etl = bool(config.AUTO_ETL)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_cache_mtime = self._cache_mtime()
        self._last_src_mtime = self._src_mtime()
        self._source_date = None
        self._cache_date = self._read_cache_date()
        self.status = {
            "enabled": self.enabled, "interval": self.interval, "auto_etl": self.auto_etl,
            "source_date": self._source_date, "cache_date": self._cache_date,
            "last_check": None, "last_refresh": None, "refreshing": False,
            "message": "看护线程已启动", "version": 0,
        }

    # ────────────── 基础探测 ──────────────
    def _cache_mtime(self):
        m = 0.0
        d = config.CACHE
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.endswith(".parquet"):
                    try:
                        m = max(m, os.path.getmtime(os.path.join(d, f)))
                    except OSError:
                        pass
        return m

    def _src_mtime(self):
        m = 0.0
        for p in (config.DUCKDB, config.DUCKDB + ".wal"):
            try:
                m = max(m, os.path.getmtime(p))
            except OSError:
                pass
        return m

    def _read_cache_date(self):
        try:
            man = json.load(open(os.path.join(config.CACHE, "manifest.json"), encoding="utf-8"))
            return man.get("_meta", {}).get("last_trade_date")
        except Exception:
            return None

    def _probe_source_date(self):
        """只读探一下源库最新交易日；被写锁占用时返回 None

        🔴 2026-11-03 修（血证）：原来查的是 `tech_factor_qfq` —— 那是**派生表**，
        前复权因子包没导时它的 max 就停在旧日期，于是：
          · 探针说「源库最新 = 11-02」
          · parquet 缓存也是 11-02（daily.parquet 唯一来源就是这张表）
          → 两边互相印证「已是最新」，**永远不触发 ETL**，页面挂着一整天的旧数据。
        探针必须用**原始主表 `daily`**（每天必导、且是所有推荐的基础）。

        另外顺手把因子表日期暴露成 `factor_date`：daily 到了但因子表没到时，
        页面能直接看到「源库 11-03 / 因子表 11-02」而不是含糊的「已是最新」。
        """
        if not os.path.exists(config.DUCKDB):
            return None
        try:
            import duckdb
            con = config.tune_duck(duckdb.connect(config.DUCKDB, read_only=True))
            try:
                r = con.execute("select max(date) from daily").fetchone()
                d_raw = str(r[0]) if r and r[0] else None
                try:
                    r2 = con.execute("select max(date) from tech_factor_qfq").fetchone()
                    self.status["factor_date"] = str(r2[0]) if r2 and r2[0] else None
                except Exception:
                    self.status["factor_date"] = None
                return d_raw
            finally:
                con.close()
        except Exception as e:
            self.status["message"] = f"源库暂不可读（可能正在同步）：{type(e).__name__}"
            return None

    def _source_idle(self):
        """源库文件需静默 ETL_QUIET_SECONDS，避免和用户的同步任务抢锁"""
        age = time.time() - self._src_mtime()
        return age >= config.ETL_QUIET_SECONDS, int(age)

    # ────────────── 动作 ──────────────
    def _reload(self, reason):
        with self._lock:
            data_layer.reset_ctx()
            self.status["version"] = data_layer.ctx_version()
            self.status["last_refresh"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.status["message"] = reason
        _log(reason)
        if self.on_changed:
            try:
                self.on_changed()
            except Exception as e:
                _log("重算回调失败:", e)

    def _run_etl(self):
        self.status["refreshing"] = True
        self.status["message"] = "检测到源库新日期，正在跑 ETL…"
        _log("开始 ETL")
        t = time.time()
        try:
            r = subprocess.run(
                [sys.executable, os.path.join(config.ROOT, "ingest", "build_cache.py")],
                cwd=config.ROOT, capture_output=True, text=True, timeout=1800)
            ok = r.returncode == 0
            tail = (r.stdout or "").strip().splitlines()[-3:]
            _log(f"ETL {'成功' if ok else '失败'} {time.time()-t:.0f}s", tail)
            if ok:
                self._last_cache_mtime = self._cache_mtime()
                self._cache_date = self._read_cache_date()
                self.status["cache_date"] = self._cache_date
                # 🔴 ETL 成功但缓存仍落后 = 上游缺表（如 daily 到了、因子表没到）
                #   原逻辑会在下个 tick 再跑一遍 ETL，无限循环烧 CPU → 加退避并说清卡在哪
                if self._cache_date and self._source_date and self._cache_date < self._source_date:
                    self._etl_stuck = (self._source_date, self._cache_date,
                                       self.status.get("factor_date"))
                    self.status["message"] = (
                        f"ETL 完成但缓存仍停在 {self._cache_date}（源库已到 {self._source_date}）"
                        f"｜因子表 tech_factor_qfq 最新 = {self.status.get('factor_date')}"
                        f" → 补齐因子表后再刷新")
                    _log("ETL 后缓存仍落后，进入退避：", self._etl_stuck)
                    return
                self._etl_stuck = None
                self._reload(f"源库已更新到 {self._source_date}，ETL 完成并重载")
            else:
                self.status["message"] = f"ETL 失败（{r.returncode}），继续使用旧数据"
        except subprocess.TimeoutExpired:
            self.status["message"] = "ETL 超时，继续使用旧数据"
            _log("ETL 超时")
        except Exception as e:
            self.status["message"] = f"ETL 异常：{e}"
            _log("ETL 异常", e)
        finally:
            self.status["refreshing"] = False

    def refresh_now(self, full=False):
        """手动刷新：full=True 先跑 ETL"""
        if self.status["refreshing"]:
            return {"ok": False, "message": "正在刷新中"}
        if full:
            self._source_date = self._probe_source_date()
            self._run_etl()
        else:
            self._last_cache_mtime = self._cache_mtime()
            self._cache_date = self._read_cache_date()
            self.status["cache_date"] = self._cache_date
            self._reload("手动重载 parquet 并重算")
        return {"ok": True, "status": self.status}

    # ────────────── 主循环 ──────────────
    def tick(self):
        if self.status["refreshing"]:
            return
        self.status["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")

        # 1) parquet 变了（自动 ETL 或用户手动跑了 build_cache）
        cm = self._cache_mtime()
        if cm and cm != self._last_cache_mtime:
            self._last_cache_mtime = cm
            self._cache_date = self._read_cache_date()
            self.status["cache_date"] = self._cache_date
            self._reload("检测到 parquet 缓存更新，已重载数据并重算")
            return

        # 2) 源库有没有新交易日
        sm = self._src_mtime()
        if sm != self._last_src_mtime or self._source_date is None:
            self._last_src_mtime = sm
            self._source_date = self._probe_source_date()
            self.status["source_date"] = self._source_date

        src, cache = self._source_date, self._cache_date
        # 退避中：源库没再前进就不再重跑 ETL（否则每 interval 烧一次）
        if getattr(self, "_etl_stuck", None):
            _stuck_src = self._etl_stuck[0]
            if src and src <= _stuck_src:
                return
            self._etl_stuck = None
        if not src or not cache or src <= cache:
            self.status["message"] = "数据已是最新" if src and cache else self.status["message"]
            return
        if not self.auto_etl:
            self.status["message"] = f"源库已更新到 {src}，缓存仍是 {cache}（自动 ETL 已关闭）"
            return
        idle, age = self._source_idle()
        if not idle:
            self.status["message"] = f"源库刚被写入（{age}s 前），等待静默 {config.ETL_QUIET_SECONDS}s 后再同步"
            return
        self._run_etl()

    def run(self):
        while not self._stop.is_set():
            try:
                if self.enabled:
                    self.tick()
            except Exception as e:
                _log("tick 异常:", e)
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()


_WATCHER = None


def start(on_changed=None):
    global _WATCHER
    if _WATCHER is None:
        _WATCHER = AutoRefresher(on_changed)
        _WATCHER.start()
    return _WATCHER


def get():
    return _WATCHER
