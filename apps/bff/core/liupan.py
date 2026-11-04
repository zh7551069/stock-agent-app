"""连板股锐利档：候选池 / 盘中动量 + 决策轨迹日志 + 复盘验证。

三条数据腿：
  1. 候选池   workspace/scripts/liupan_scan.py --tier 锐利 --json      （~3s，T-1 收盘截面）
  2. 盘中动量 workspace/scripts/liupan_auction_rank.py --tier 锐利 --asof now --json（~60-120s，实时）
  3. 复盘验证 data/liupan_trace.jsonl + Ctx.daily                      （T+N 收益）

设计原则：
  * 每次真实查询都 append 一行轨迹（append-only，不覆盖）→ 事后可对账
  * 查询结果落盘 latest.json → 服务重启后首屏秒开
  * 脚本调用一律 subprocess（隔离崩溃，不把 workspace 依赖拉进 Flask 进程）
"""
import json
import os
import subprocess
import sys
import hashlib
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# ── 路径 ────────────────────────────────────────────────────
def _resolve_scripts():
    """liupan_*.py 的位置。不改脚本内容，只决定 subprocess 去哪找它们。

    顺序：LIUPAN_SCRIPTS（目录本身）→ OPENCLAW_WORKSPACE/scripts（种子约定）
    → ~/.openclaw-autoclaw/workspace/scripts（本机已有工作区则沿用）
    → 仓库 scripts/liupan。
    """
    explicit = os.environ.get("LIUPAN_SCRIPTS")
    if explicit:
        return explicit, explicit
    env = os.environ.get("OPENCLAW_WORKSPACE")
    if env:
        return env, os.path.join(env, "scripts")
    home = os.path.expanduser("~/.openclaw-autoclaw/workspace")
    home_scripts = os.path.join(home, "scripts")
    if os.path.isdir(home_scripts):
        return home, home_scripts
    vendored = os.path.abspath(os.path.join(config.ROOT, "..", "..", "scripts", "liupan"))
    if os.path.isdir(vendored):
        return vendored, vendored
    return home, home_scripts


WS, SCRIPTS = _resolve_scripts()
TRACE_FILE = os.environ.get(
    "LIUPAN_TRACE", os.path.join(config.DATA, "liupan_trace.jsonl"))
LATEST_FILE = os.path.join(config.DATA, "liupan_latest.json")

TIER = "锐利"
POOL_TTL = 120        # 候选池缓存秒数
MOM_TTL = 45          # 盘中动量缓存秒数
TIMEOUT_POOL = 120
TIMEOUT_MOM = 300

_lock = threading.Lock()
_mem = {"pool": (0.0, None), "momentum": (0.0, None)}
_names = {"ts": 0.0, "map": {}}
# 最后一次成功结果（不看 TTL）——查询失败时用它降级，避免整页 502
_last_good = {}


# ── 基础 ────────────────────────────────────────────────────
def _script(name):
    p = os.path.join(SCRIPTS, name)
    if not os.path.exists(p):
        raise RuntimeError(f"脚本不存在：{p}")
    return p


def _run_once(script, args, timeout):
    """跑一次 workspace 脚本并解析其 --json 输出。失败抛 RuntimeError（带 stderr 尾部）。"""
    cmd = [sys.executable, _script(script)] + list(args)
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=WS, capture_output=True, text=True,
                           timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{script} 超时（>{timeout}s）")
    out = (r.stdout or "").strip()
    if not out:
        raise RuntimeError(f"{script} 无输出（rc={r.returncode}）：{(r.stderr or '')[-300:]}")
    try:
        data = json.loads(out)
    except Exception:
        raise RuntimeError(f"{script} 输出非 JSON：{out[:200]}")
    data["_cost_ms"] = int((time.time() - t0) * 1000)
    return data


def _run(script, args, timeout, retries=1):
    """带重试的执行外壳。

    🔴 为什么必须重试：外盘是 **ExFAT**，DuckDB 在非原生文件系统上跨进程并发打开
    会偶发 `IO Error / Interrupted system call` / 打开失败 —— 这类失败是**瞬时**的，
    1 秒后重跑基本必成（2026-11-03 实测：两次 502 相隔 4 秒，50 秒后自愈）。
    重试把「偶发一次就整页报错」变成「用户完全无感」。
    超时不重试 —— 那是真慢，重试只会把等待翻倍。
    """
    last = None
    for attempt in range(retries + 1):
        try:
            return _run_once(script, args, timeout)
        except RuntimeError as e:
            last = e
            _err(f"{script} 第 {attempt + 1} 次失败（args={args}）：{e}")
            if "超时" in str(e) or attempt >= retries:
                raise
            time.sleep(1.2)
    raise last


def _name_map():
    """code6 → 名称。直接从 parquet 读，轻量（不触发 Ctx 全量加载）。"""
    now = time.time()
    if _names["map"] and now - _names["ts"] < 3600:
        return _names["map"]
    m = {}
    try:
        import pandas as pd
        df = pd.read_parquet(os.path.join(config.CACHE, "stock_info.parquet"))
        col = "code" if "code" in df.columns else df.columns[0]
        ncol = "name" if "name" in df.columns else df.columns[1]
        for c, n in zip(df[col].astype(str), df[ncol].astype(str)):
            k = c.strip()[-6:]
            if len(k) == 6:
                m[k] = n
    except Exception:
        pass
    _names["map"] = m
    _names["ts"] = now
    return m


def code6(code):
    s = str(code or "")
    return s.split(".")[0].strip()[-6:] if "." in s else s.strip()[-6:]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


ERROR_LOG = os.path.join(config.DATA, "liupan_error.log")


def _err(msg):
    """🔴 失败必须留痕：502 事件曾因无日志而无法回溯（2026-11-03）。"""
    try:
        os.makedirs(os.path.dirname(ERROR_LOG), exist_ok=True)
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{_now()}] {msg}\n")
    except Exception:
        pass
    try:
        sys.stderr.write(f"[liupan] {msg}\n")
    except Exception:
        pass


def last_good(kind):
    """最后一次成功结果（不看 TTL）。查询失败时用于降级，避免整个接口挂掉。"""
    return _last_good.get(kind)


# ── 候选池 ──────────────────────────────────────────────────
def pool(tier=TIER, force=False, log=True):
    """连板候选池（T-1 收盘截面）。force=True 绕过缓存并落一条轨迹。"""
    ck = "pool"
    if not force:
        ts, cached = _mem[ck]
        if cached and time.time() - ts < POOL_TTL:
            return dict(cached, cached=True, cache_age=int(time.time() - ts))

    with _lock:
        if not force:
            ts, cached = _mem[ck]
            if cached and time.time() - ts < POOL_TTL:
                return dict(cached, cached=True, cache_age=int(time.time() - ts))
        raw = _run("liupan_scan.py", ["--tier", tier, "--json"], TIMEOUT_POOL)

    nm = _name_map()
    codes = raw.get("pool", {}).get(tier) or []
    items = [{"code": c, "code6": code6(c), "name": nm.get(code6(c), code6(c))} for c in codes]
    out = {
        "ok": raw.get("status") == "ok",
        "kind": "pool",
        "tier": tier,
        "asof": raw.get("asof"),
        "n": len(items),
        "n_show": raw.get("n_cand", len(items)),
        "n_universe": raw.get("n_universe"),
        "stale_days": raw.get("stale_days"),
        "items": items,
        "text": raw.get("text", ""),
        "cost_ms": raw.get("_cost_ms"),
        "ts": _now(),
    }
    _mem[ck] = (time.time(), out)
    _last_good[ck] = out
    if log:
        _write_trace(out)
    return out


# ── 盘中动量 ────────────────────────────────────────────────
def momentum(tier=TIER, force=False, log=True, codes=None, pool_asof=None):
    """盘中动量重排（按先手分排序）。force=True 绕过缓存并落一条轨迹。

    codes: 指定票池（前端 localStorage 缓存的候选池）→ 跳过 liupan_scan 全市场扫描，
           只对这批票实时算先手分。**不传 = 旧行为（自己重扫全市场）**。
    pool_asof: 该池所属交易日（T-1），供惩罚层 _hist_flags 取历史标记用。
    """
    _cs = [str(c).strip() for c in (codes or []) if str(c).strip()]
    # 🔴 缓存键必须区分票池：不同池算出的动量不可互换（同 tier 不同 codes 是两码事）
    ck = "momentum" if not _cs else "momentum:" + hashlib.md5(
        ("|".join(sorted(_cs)) + "@" + str(pool_asof or "")).encode("utf-8")).hexdigest()[:12]
    _mem.setdefault(ck, (0.0, None))
    if not force:
        ts, cached = _mem[ck]
        if cached and time.time() - ts < MOM_TTL:
            return dict(cached, cached=True, cache_age=int(time.time() - ts))

    with _lock:
        if not force:
            ts, cached = _mem[ck]
            if cached and time.time() - ts < MOM_TTL:
                return dict(cached, cached=True, cache_age=int(time.time() - ts))
        _args = ["--tier", tier, "--asof", "now", "--json"]
        if _cs:
            _args += ["--codes", ",".join(_cs)]
            if pool_asof:
                _args += ["--date", str(pool_asof)]
        raw = _run("liupan_auction_rank.py", _args, TIMEOUT_MOM)

    items = []
    for i, x in enumerate(raw.get("buy") or []):
        items.append({
            "rank": i + 1,
            "code": x.get("code"),
            "code6": code6(x.get("code")),
            "name": x.get("name"),
            "pct": round(float(x.get("open_pct") or 0), 2),
            "room": round(float(x.get("room") or 0), 2),
            "turn": round(float(x.get("turn") or 0), 2),
            "ind": x.get("ind"),
            "sect_med": round(float(x.get("sect_med") or 0), 2),
            "sect_zt": x.get("sect_zt"),
            "consist": x.get("consist"),
            "score": round(float(x.get("score") or 0), 3),
            "score_adj": round(float(x.get("score_adj") or 0), 3),
            "zb": bool(x.get("zb")),
            "zt20": x.get("zt20") or 0,
            "zt10": x.get("zt10") or 0,
        })
    locked = []
    for x in raw.get("locked") or []:
        locked.append({"code": x.get("code"), "code6": code6(x.get("code")),
                       "name": x.get("name"), "pct": round(float(x.get("open_pct") or 0), 2),
                       "turn": round(float(x.get("turn") or 0), 2),
                       "ind": x.get("ind"), "score": round(float(x.get("score") or 0), 3)})
    out = {
        "ok": raw.get("status") == "ok",
        "kind": "momentum",
        "tier": tier,
        "asof": raw.get("asof"),
        "mode": raw.get("mode"),
        "n": len(items),
        "items": items,
        "locked": locked,
        "hot": (raw.get("hot") or [])[:12],
        "level": raw.get("level") or {},
        "text": raw.get("text", ""),
        "cost_ms": raw.get("_cost_ms"),
        "pool_source": ("local_cache" if _cs else "scan"),
        "n_pool": (len(_cs) if _cs else raw.get("n_pool")),
        "pool_asof": (pool_asof or raw.get("asof")),
        "ts": _now(),
    }
    _mem[ck] = (time.time(), out)
    _last_good[ck] = out
    if log:
        _write_trace(out)
    return out


# ── 决策轨迹日志 ────────────────────────────────────────────
def _write_trace(payload):
    """append 一行；失败不阻断（日志坏了不能把查询也拖死）。"""
    try:
        os.makedirs(os.path.dirname(TRACE_FILE), exist_ok=True)
        rec = {
            "ts": payload.get("ts") or _now(),
            "kind": payload.get("kind"),
            "tier": payload.get("tier"),
            "asof": payload.get("asof"),
            "mode": payload.get("mode"),
            "n": payload.get("n"),
            "cost_ms": payload.get("cost_ms"),
            "items": (payload.get("items") or [])[:10],
            "level": payload.get("level") or {},
        }
        with open(TRACE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        snap = {k: v for k, v in payload.items() if k != "text"}
        with open(LATEST_FILE, "w", encoding="utf-8") as f:
            json.dump({"saved_at": _now(), payload.get("kind"): snap}, f,
                      ensure_ascii=False)
    except Exception as e:
        print(f"[liupan] trace 写入失败: {e}", file=sys.stderr)


def traces(limit=100, kind=None):
    """读轨迹（新→旧）。"""
    rows = []
    if not os.path.exists(TRACE_FILE):
        return rows
    try:
        with open(TRACE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if kind and r.get("kind") != kind:
                    continue
                rows.append(r)
    except Exception:
        return []
    rows.reverse()
    return rows[:limit]


def trace_summary():
    rows = traces(limit=100000)
    out = {"total": len(rows), "pool": 0, "momentum": 0, "days": 0, "last": None}
    days = set()
    for r in rows:
        k = r.get("kind")
        if k in ("pool", "momentum"):
            out[k] += 1
        d = str(r.get("ts", ""))[:10]
        if d:
            days.add(d)
    out["days"] = len(days)
    out["last"] = rows[0]["ts"] if rows else None
    return out


# ── 复盘验证：轨迹 × 后续行情 ────────────────────────────────
def review(limit=60, topn=5, kind=None):
    """对每条轨迹的 Top N 算 T+1 / T+3 / T+5 收益（基准 = T 日收盘）。

    口径声明：查询发生在 T 日盘中，基准取 T 日收盘 → 略高估当日追高成本，
    但用于「选出来的票后来涨没涨」的横向比较是稳的。T 日行情未入库 = pending。
    """
    rows = traces(limit=limit, kind=kind)
    if not rows:
        return {"ok": True, "rows": [], "summary": {}, "note": "暂无轨迹"}

    try:
        from core.data import get_ctx
        ctx = get_ctx()
        daily = ctx.daily[["code6", "date", "close"]].copy()
        daily["code6"] = daily["code6"].astype(str).str.zfill(6)
    except Exception as e:
        return {"ok": False, "rows": [], "summary": {}, "note": f"行情层不可用：{e}"}

    daily["date"] = daily["date"].astype("datetime64[ns]")
    by_code = {c: g.sort_values("date").reset_index(drop=True)
               for c, g in daily.groupby("code6")}

    cal = sorted(daily["date"].unique())
    import pandas as pd

    def horizon_close(code, tdate, n):
        g = by_code.get(code)
        if g is None or not len(g):
            return None
        idx = g.index[g["date"] == tdate]
        if not len(idx):
            return None
        i = int(idx[0])
        j = i + n
        if j >= len(g):
            return None
        return float(g.loc[j, "close"])

    out_rows = []
    for r in rows:
        td = pd.Timestamp(str(r.get("ts", ""))[:10])
        items = (r.get("items") or [])[:topn]
        picks = []
        for it in items:
            c = it.get("code6") or code6(it.get("code"))
            base = horizon_close(c, td, 0)
            rec = {"code": it.get("code"), "name": it.get("name"),
                   "score": it.get("score"), "pct": it.get("pct"), "base": base}
            for n in (1, 3, 5):
                cl = horizon_close(c, td, n)
                rec[f"r{n}"] = (round((cl / base - 1) * 100, 2)
                                if (cl is not None and base) else None)
            picks.append(rec)
        done = [p for p in picks if p.get("r1") is not None]
        out_rows.append({
            "ts": r.get("ts"), "kind": r.get("kind"), "asof": r.get("asof"),
            "n": r.get("n"), "top": picks,
            "avg_r1": round(sum(p["r1"] for p in done) / len(done), 2) if done else None,
            "avg_r3": round(sum(p["r3"] for p in done if p.get("r3") is not None)
                            / max(1, len([p for p in done if p.get("r3") is not None])), 2) if done else None,
            "pending": len(done) == 0,
        })

    # 汇总：只统计已有结果的
    def agg(n):
        vals = [p[f"r{n}"] for row in out_rows for p in row["top"] if p.get(f"r{n}") is not None]
        if not vals:
            return None
        vals_sorted = sorted(vals)
        mid = vals_sorted[len(vals_sorted) // 2]
        return {"n": len(vals), "avg": round(sum(vals) / len(vals), 2),
                "median": round(mid, 2),
                "win": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)}

    summary = {
        "queries": len(out_rows),
        "picks": sum(len(r["top"]) for r in out_rows),
        "verified": sum(len([p for p in r["top"] if p.get("r1") is not None]) for r in out_rows),
        "r1": agg(1), "r3": agg(3), "r5": agg(5),
    }
    return {"ok": True, "rows": out_rows, "summary": summary,
            "note": "基准 = T 日收盘（查询发生在 T 日盘中）；T+1 为最早可卖日"}
