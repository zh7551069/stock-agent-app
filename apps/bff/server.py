"""stock-advice-agent 服务（Flask）—— 移动端 App（PWA）后端。

启动：python3 server.py            # http://127.0.0.1:8770
"""
import hmac
import json
import math
import os
import re
import secrets
import threading
import sys
import time

import base64
from datetime import timedelta
from urllib.parse import quote

from flask import (Flask, jsonify, redirect, request, session,
                   send_from_directory)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from core import autorefresh, datapacks, engine, realtime, recommend, liupan
from core import liupan_store
from core.data import get_ctx, reset_ctx

app = Flask(__name__, static_folder=config.WEB, static_url_path="/static")
app.config["JSON_AS_ASCII"] = False
app.json.ensure_ascii = False


def _load_auth():
    """公网访问凭据：优先环境变量 STOCK_ADVICE_AUTH，其次项目下 .auth（格式 user:password）。"""
    v = os.environ.get("STOCK_ADVICE_AUTH", "").strip()
    if v:
        return v
    try:
        with open(os.path.join(config.ROOT, ".auth"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


AUTH = _load_auth()
AUTH_USER, _, AUTH_PASS = AUTH.partition(":")


def _load_secret_key():
    """会话签名密钥：优先环境变量，其次项目根 .secret（首次自动生成，权限 0600）。

    密钥不落盘的话每次重启都会换 key，所有已登录会话立刻失效。
    """
    v = os.environ.get("STOCK_ADVICE_SECRET", "").strip()
    if v:
        return v
    path = os.path.join(config.ROOT, ".secret")
    try:
        with open(path, encoding="utf-8") as f:
            s = f.read().strip()
        if s:
            return s
    except Exception:
        pass
    s = secrets.token_urlsafe(48)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(s)
    except Exception:
        pass
    return s


app.secret_key = _load_secret_key()
app.config.update(
    SESSION_COOKIE_NAME="saa_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)


def _is_local_direct():
    """本机直连豁免登录。

    判断依据 = Host 是本机 + 没有反代头。
    隧道（ngrok / cloudflared / localhost.run）转发时 Host 会变成公网域名，
    即使 remote_addr 仍是 127.0.0.1，也不能当作本机。
    """
    if request.headers.get("X-Forwarded-For") or request.headers.get("X-Real-Ip"):
        return False
    host = (request.host or "").rsplit(":", 1)[0].strip("[]").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        return False
    return request.remote_addr in ("127.0.0.1", "::1", "localhost")


def _basic_ok():
    """保留 Basic Auth：curl / 脚本 / 老书签仍然能用。"""
    h = request.headers.get("Authorization", "")
    if not h.startswith("Basic "):
        return False
    try:
        u, _, pw = base64.b64decode(h[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    return hmac.compare_digest(f"{u}:{pw}".encode(), AUTH.encode())


def _session_ok():
    return bool(session.get("sa_ok"))


def _logged_in():
    return _session_ok() or _basic_ok()


# 登录页自身、静态资源、PWA 外壳必须放行：
# 否则登录页加载不到样式表，service worker 也装不上。
_PUBLIC_EXACT = {"/login", "/logout", "/manifest.webmanifest", "/sw.js", "/favicon.ico"}
_PUBLIC_PREFIX = ("/static/",)


@app.before_request
def _require_auth():
    if not AUTH or _is_local_direct():
        return None
    p = request.path
    if p in _PUBLIC_EXACT or p.startswith(_PUBLIC_PREFIX):
        return None
    if _logged_in():
        return None
    if p.startswith("/api/"):
        # 前端 fetch 收到 401 后自己跳登录页，不再触发浏览器原生弹框
        return jsonify({"ok": False, "error": "需要登录", "login": "/login"}), 401
    nxt = p + (("?" + request.query_string.decode("utf-8", "ignore"))
               if request.query_string else "")
    return redirect("/login?next=" + quote(nxt, safe=""))


# ─────────────────────────── 登录 / 退出 ───────────────────────────
_FAILS = {}            # ip -> (失败次数, 首次失败时间)
_FAIL_WINDOW = 600     # 统计窗口：10 分钟
_FAIL_MAX = 8          # 窗口内失败 8 次即临时锁定


def _client_ip():
    return (request.headers.get("CF-Connecting-IP")
            or (request.headers.get("X-Forwarded-For", "").split(",")[0].strip())
            or request.remote_addr or "?")


def _safe_next(n):
    """只允许站内相对跳转，挡掉 //evil.com 这类开放重定向。"""
    if not n or not n.startswith("/") or n.startswith("//") or "\\" in n:
        return "/"
    return n


@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = _safe_next(request.values.get("next"))
    if request.method == "GET":
        if _logged_in():
            return redirect(nxt)
        return send_from_directory(config.WEB, "login.html")

    ip = _client_ip()
    now = time.time()
    cnt, first = _FAILS.get(ip, (0, now))
    if now - first > _FAIL_WINDOW:
        cnt, first = 0, now
    if cnt >= _FAIL_MAX:
        wait = max(1, int(_FAIL_WINDOW - (now - first)))
        return redirect("/login?err=locked&wait=%d&next=%s"
                        % (wait, quote(nxt, safe="")))

    u = (request.form.get("username") or "").strip()
    pw = request.form.get("password") or ""
    user_ok = hmac.compare_digest(u.encode(), AUTH_USER.encode())
    pass_ok = hmac.compare_digest(pw.encode(), AUTH_PASS.encode())
    if user_ok and pass_ok:
        _FAILS.pop(ip, None)
        session.clear()
        session["sa_ok"] = True
        session["sa_at"] = int(now)
        # 勾了「记住我」才发持久 cookie，否则关浏览器即失效
        session.permanent = bool(request.form.get("remember"))
        return redirect(nxt)

    _FAILS[ip] = (cnt + 1, first)
    return redirect("/login?err=bad&next=" + quote(nxt, safe=""))


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect("/login")

_CACHE = {"rec": None, "ts": 0, "ctx_version": None}
# 盘中实时缓存：TTL 与快照一致（默认 12s）
_INTRA = {"ts": 0, "data": None, "last_force": 0.0}


def intraday_data(refresh=False):
    ttl = max(6.0, float(realtime.SNAP_TTL))
    now = time.time()
    # 防连点：强制刷新最小间隔 5s，避免一次点击打出一轮全市场快照
    if refresh and now - _INTRA.get("last_force", 0) < 5:
        refresh = False
    if refresh:
        _INTRA["last_force"] = now
    if refresh or _INTRA["data"] is None or now - _INTRA["ts"] > ttl:
        ctx = get_ctx()
        _INTRA["data"] = recommend.run_intraday(ctx, force=refresh)   # 手动刷新时绕过 TTL
        _INTRA["ts"] = time.time()
    return _INTRA["data"]
_CARDS = None
_STRATS = None


def cards_data():
    global _CARDS
    if _CARDS is None:
        _CARDS = recommend.load_cards()
    return _CARDS


def strats_data():
    global _STRATS
    if _STRATS is None:
        _STRATS = recommend.load_strategies()
    return _STRATS


def rec_data(refresh=False):
    ctx = get_ctx()
    ver = getattr(ctx, "version", 0)
    # ctx 被 autorefresh 重置并重载后会换 version → 自动重算
    if refresh or _CACHE["rec"] is None or _CACHE["ctx_version"] != ver:
        _CACHE["rec"] = recommend.run_all(ctx)
        _CACHE["ts"] = time.time()
        _CACHE["ctx_version"] = ver
    return _CACHE["rec"]


# ─────────────────────────── 页面 / PWA ───────────────────────────
@app.route("/")
def index():
    return send_from_directory(config.WEB, "index.html")


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(config.WEB, "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    r = send_from_directory(config.WEB, "sw.js", mimetype="application/javascript")
    # 允许 SW 控制根作用域（App 从 / 启动）
    r.headers["Service-Worker-Allowed"] = "/"
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.after_request
def _no_store_shell(resp):
    """前端 shell 不缓存，避免改样式后仍看旧版（SW 另有 network-first 策略）。"""
    p = request.path
    if p == "/" or p.endswith((".css", ".js", ".html")):
        resp.headers.setdefault("Cache-Control", "no-cache, must-revalidate")
    return resp


@app.route("/api/overview")
def api_overview():
    rec = rec_data()
    cards = cards_data()
    strats = strats_data()
    want_refresh = request.args.get("refresh") == "1"
    intra = intraday_data(refresh=want_refresh)
    # 大盘环境实时化（盘中）：指数实时价 + 实时涨跌停/涨跌家数/领涨板块
    env = rec["env"]
    env_source = "eod"
    try:
        # 不要 force：上面 intraday_data(refresh) 刚拉过一次快照，这里用同一份，
        # 否则一次刷新会打出两轮全市场请求（30 个请求 → 15 个）。
        live_m = realtime.get_live(get_ctx(), force=False)
        if live_m is not None:
            ix = realtime.fetch_index(force=want_refresh)
            env = recommend.live_env(get_ctx(), live_m, ix)
            env_source = "realtime"
    except Exception:
        pass
    by_bucket = {}
    for b, v in rec["buckets"].items():
        if b == "盘中" and intra is not None:
            v = intra
        by_bucket[b] = {"stocks": len(v["stocks"]),
                        "strategies": len(v["strategies"]),
                        "top": v["stocks"][:3]}
    return jsonify({
        "app": "stock-advice-agent",
        "data_date": rec["data_date"],
        "minute_date": rec["minute_date"],
        "computed_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_CACHE["ts"])),
        "version": getattr(get_ctx(), "version", 0),
        "source_date": (autorefresh.get().status.get("source_date") if autorefresh.get() else None),
        "auto_refresh": (autorefresh.get().status if autorefresh.get() else {"enabled": False}),
        "env": env,
        "env_source": env_source,
        "kb": {
            "total_cards": cards["total_cards"],
            "selection_cards": cards["selection_cards"],
            "by_bucket": cards["stats"]["by_bucket"],
            "by_category": cards["stats"]["by_category"],
            "by_tag": cards["stats"]["by_tag"],
        },
        "strategies": {"total": strats["count"], "buckets": strats["buckets"]},
        "buckets": by_bucket,
        "live": (intra or {}).get("live") if intra else None,
        # 请求计数/退避状态（防封可观测：/api/overview 就能看到发了多少请求）
        "live_status": (intra or {}).get("live_status") if intra else None,
    })


@app.route("/api/recommend/<bucket>")
def api_recommend(bucket):
    if bucket not in ("盘前", "盘中", "盘后"):
        return jsonify({"error": "bucket 必须是 盘前/盘中/盘后"}), 400
    want_refresh = request.args.get("refresh") == "1"
    if bucket == "盘中":
        data = intraday_data(refresh=want_refresh)
        rec = rec_data()
        # 默认只给「可买」：涨停封板/一字板剔除（可买性见 tradable 字段）
        buyable_only = request.args.get("buyable", "1") != "0"
        stocks = list(data.get("stocks", []))
        if buyable_only:
            stocks = [x for x in stocks if x.get("tradable_rank", 0) <= 1]
            stocks.sort(key=lambda x: (-x["score"], -x["hit_count"]))
        payload = {"bucket": bucket, "data_date": rec["data_date"],
                   "minute_date": rec["minute_date"],
                   "source": data.get("source"), "live": data.get("live"),
                   "live_status": data.get("live_status"), **data,
                   "buyable_only": buyable_only,
                   "stocks": stocks[:20]}
    else:
        rec = rec_data(refresh=want_refresh)
        data = rec["buckets"][bucket]
        payload = {"bucket": bucket, "data_date": rec["data_date"],
                   "minute_date": rec["minute_date"], **data}
    # 附带每条策略的说明
    smap = {s["id"]: s for s in strats_data()["strategies"]}
    for st in payload["strategies"]:
        s = smap.get(st["id"], {})
        st["card"] = s.get("card", "")
        st["note"] = s.get("note", "")
        st["bucket"] = s.get("bucket", bucket)
    return jsonify(payload)


@app.route("/api/strategies")
def api_strategies():
    data = strats_data()
    rec = rec_data()
    # 每个策略今日命中数：与 /api/strategy/<sid> 的命中列表同源
    hitcnt = {sid: len(v) for sid, v in (rec.get("hits_by_strategy") or {}).items()}
    capped = {st["id"]: bool(st.get("capped"))
              for v in rec["buckets"].values() for st in v["strategies"]}
    out = []
    for s in data["strategies"]:
        item = {k: s.get(k) for k in
                ("id", "name", "bucket", "card", "note", "params", "card_one",
                 "card_source", "card_tags", "card_thresholds")}
        item["hits_today"] = hitcnt.get(s["id"], 0)
        item["hits_capped"] = capped.get(s["id"], False)
        out.append(item)
    return jsonify({"count": len(out), "buckets": data["buckets"], "strategies": out})


@app.route("/api/strategy/<sid>")
def api_strategy(sid):
    data = strats_data()
    s = next((x for x in data["strategies"] if x["id"] == sid), None)
    if not s:
        return jsonify({"error": "not found"}), 404
    rec = rec_data()
    ctx = get_ctx()
    # 推荐榜个股集合：用于标注「是否进了今日推荐榜」，而不是用来过滤命中
    reco = {st["code"] for v in rec["buckets"].values() for st in v["stocks"]}
    reco_score = {st["code"]: st["score"] for v in rec["buckets"].values() for st in v["stocks"]}

    raw = (rec.get("hits_by_strategy") or {}).get(sid, [])
    hits = []
    for h in raw:
        code = h["code"]
        pos = recommend._pos(ctx, code)
        hits.append({
            "code": code,
            "name": h.get("name") or ctx.name.get(code, code),
            "industry": ctx.industry.get(code, ""),
            "close": recommend._f(ctx.M["close"][-1, pos]),
            "pct_chg": recommend._f(ctx.M["pct_chg"][-1, pos]),
            "score": h["score"],
            "reason": h["reason"],
            "trigger": h["trigger"],
            "risk": h["risk"],
            "in_reco": code in reco,
            "stock_score": reco_score.get(code),
        })
    # 进榜的排前面，其次按策略评分
    hits.sort(key=lambda x: (not x["in_reco"], -x["score"]))
    return jsonify({**s, "hits": hits, "hits_total": len(hits),
                    "hits_capped": len(raw) >= recommend.PER_STRATEGY,
                    "hits_in_reco": sum(1 for x in hits if x["in_reco"])})


@app.route("/api/cards")
def api_cards():
    cards = cards_data()
    q = (request.args.get("q") or "").strip().lower()
    bucket = request.args.get("bucket") or ""
    tag = request.args.get("tag") or ""
    cat = request.args.get("category") or ""
    only_exec = request.args.get("exec") == "1"
    page = max(1, int(request.args.get("page") or 1))
    size = min(100, int(request.args.get("size") or 24))

    # 可执行卡片集合
    exec_cards = {s["card"]: s["id"] for s in strats_data()["strategies"]}

    items = []
    for c in cards["cards"]:
        if bucket and c["bucket"] != bucket:
            continue
        if tag and tag not in c["tags"]:
            continue
        if cat and c.get("category") != cat:
            continue
        if only_exec and c["name"] not in exec_cards:
            continue
        if q:
            hay = (c["name"] + " " + c["one"] + " " + c["body"][:500]).lower()
            if q not in hay:
                continue
        items.append({
            "name": c["name"], "bucket": c["bucket"], "category": c.get("category"),
            "tags": c["tags"], "one": c["one"], "source": c["source"],
            "thresholds": c["thresholds"], "related": c["related"][:6],
            "executable": c["name"] in exec_cards,
            "strategy_id": exec_cards.get(c["name"]),
        })
    total = len(items)
    start = (page - 1) * size
    return jsonify({"total": total, "page": page, "size": size,
                    "items": items[start:start + size],
                    "filters": {"buckets": ["盘前", "盘中", "盘后"],
                                "tags": list(cards["stats"]["by_tag"].keys())}})


@app.route("/api/card/<path:name>")
def api_card(name):
    cards = cards_data()
    c = next((x for x in cards["cards"] if x["name"] == name), None)
    if not c:
        return jsonify({"error": "not found"}), 404
    exec_map = {s["card"]: s["id"] for s in strats_data()["strategies"]}
    return jsonify({**c, "executable": name in exec_map,
                    "strategy_id": exec_map.get(name)})


@app.route("/api/stock/<code>")
def api_stock(code):
    ctx = get_ctx()
    if code not in set(ctx.codes):
        return jsonify({"error": "not found"}), 404
    rec = rec_data()
    hits = []
    for b, v in rec["buckets"].items():
        for st in v["stocks"]:
            if st["code"] == code:
                hits = [{"bucket": b, **h} for h in st["hits"]]
                warnings = st["warnings"]
                score = st["score"]
                break
    pos = list(ctx.codes).index(code)
    return jsonify({
        "code": code, "ts_code": ctx.ts_code.get(code, ""),
        "name": ctx.name.get(code, code), "industry": ctx.industry.get(code, ""),
        "circ_mv": ctx.circ_mv.get(code),
        "close": recommend._f(ctx.M["close"][-1, pos]),
        "pct_chg": recommend._f(ctx.M["pct_chg"][-1, pos]),
        "spark": recommend.sparkline(ctx, code, 60),
        "hits": hits, "warnings": warnings if hits else [],
        "score": score if hits else None,
    })


@app.route("/api/spark/<code>")
def api_spark(code):
    ctx = get_ctx()
    return jsonify({"code": code, "spark": recommend.sparkline(ctx, code, 60)})


@app.route("/api/index")
def api_index():
    """大盘指数近 60 日走势（首页环境卡的小图）。"""
    ctx = get_ctx()
    s = ctx.index_series.dropna()
    n = 60
    s = s.iloc[-n:]
    closes = [round(float(v), 2) for v in s.to_numpy()]
    ma20s = s.rolling(20, min_periods=5).mean().to_numpy()
    ma20 = [None if not math.isfinite(float(x)) else round(float(x), 2) for x in ma20s]
    dates = [d.strftime("%m-%d") for d in s.index]
    return jsonify({"ok": True, "dates": dates, "closes": closes, "ma20": ma20})


@app.route("/api/audit")
def api_audit():
    """首页每个数字的取数与口径，供人工核对（也会在「数据同步」面板里展示）。"""
    rec = rec_data()
    e = rec.get("env") or {}
    ctx = get_ctx()
    rt = {"source": "eod", "live": False}
    try:
        live_m = realtime.get_live(ctx)
        if live_m is not None:
            ix = realtime.fetch_index()
            e = recommend.live_env(ctx, live_m, ix)
            rt = {"source": "realtime", "live": True, **live_m.meta}
    except Exception:
        pass
    rows = [
        {"k": "数据日期", "v": rec.get("data_date"), "src": "daily.parquet", "note": "最后一根日线"},
        {"k": "实时行情", "v": f"{rt['source']} · {rt.get('quote_time') or '—'}",
         "src": f"{rt.get('code') or '本地回放'}",
         "note": f"阶段 {rt.get('phase')} · 盘中策略数据源；{rt.get('bars') or 0} 只涨停候选已拉 5min"},
        {"k": "实时涨跌家数", "v": f"{e.get('up')} / {e.get('down')}",
         "src": "东财快照（全市场）" if rt["live"] else "日线 pct_chg", "note": "盘中每 12s 刷新"},
        {"k": "上证收盘", "v": e.get("index_close"),
         "src": "东财实时 · 1.000001" if rt["live"] else "index_daily.parquet · sh000001",
         "note": f"前收 {e.get('prev_close')} · 今日 {e.get('index_pct')}%"
                 + ("（实时价参与计算）" if rt["live"] else "")},
        {"k": "MA20 / MA60", "v": f"{e.get('ma20')} / {e.get('ma60')}",
         "src": "index_daily 收盘价 + 实时价现算" if rt["live"] else "由 index_daily 收盘价现算",
         "note": "不是外部给定值"},
        {"k": "MA20 斜率(5日)", "v": f"{e.get('ma20_slope_pct')}%",
         "src": "现算", "note": "负值=中期均线仍在下移"},
        {"k": "中期结构", "v": f"{e.get('structure')}（{e.get('trend')}）",
         "src": "收盘 vs MA20 vs MA60", "note": "与「今日涨跌」是两个维度"},
        {"k": "涨 / 跌家数", "v": f"{e.get('up')} / {e.get('down')}",
         "src": "日线 pct_chg（全市场）", "note": "不剔除，含 ST/北交所"},
        {"k": "涨停 / 炸板", "v": f"{e.get('zt_count')} / {e.get('broken_count')}",
         "src": "日线 + 涨跌停价表", "note": e.get("limit_scope")},
        {"k": "曾涨停", "v": e.get("touched_count"),
         "src": "盘中 high ≥ 涨停价", "note": "封板率的分母；占全市场 %.1f%%" % (e.get("touched_share") or 0)},
        {"k": "涨停占全市场", "v": f"{e.get('zt_share')}%",
         "src": f"{e.get('zt_count')} / {e.get('limit_base')} 只",
         "note": "不是 61.9% 那种量级——涨停永远只是极少数"},
        {"k": "封板率", "v": f"{e.get('seal_rate')}%",
         "src": "封住 ÷ 曾涨停（同一批股票）",
         "note": f"摸过涨停 {e.get('touched_count')} 只里封住 {e.get('zt_count')} 只"},
        {"k": "对照：东财池表", "v": f"涨停 {e.get('zt_pool')} / 炸板 {e.get('zb_pool')}",
         "src": "zt_limit_up / zt_broken",
         "note": "池表含回封股、对 ST/北交所取舍不同，故不作为展示口径"},
    ]
    return jsonify({"data_date": rec.get("data_date"), "rows": rows})


SCORE_FORMULAS = {
    "intra_leader_pullback": "40 底分 + RS(0~16) + 贴线(0~14) + 回踩幅度(0~12) + 缩量(0~10) + 涨幅温和(0~6) + 均价线向上(0~5) − 5min 加速下跌(0~6)",
    "intra_vwap_support": "55 + 40×tanh(高于均价线幅度/5%) + 6×均价线向上 + 5×放量站上；均价线明显走低 ×0.9，已封板 ×0.75",
    "intra_volume_breakout": "60 + 30×tanh((量比−2)/6) + 10×tanh(5min涨速/3) + 6×走势动量 + 4×加速度；已封板 ×0.75",
    "intra_speed_surge": "60 + 39×tanh(近5分钟涨速 / 4)",
    "intra_sector_flow": "50 + 45×tanh(个股涨幅 / 8)",
    "intra_tail_strong": "60 + 30×tanh(涨幅/8) + 9×tanh(涨速/3)；已封板 ×0.75",
    "intra_first_touch": "95 − (首触时间−09:30 的分钟数)×0.15（10 点前越早越高）",
    "intra_broken_reseal": "85 固定（触板→开板→回封，承接强）",
    "post_rs80": "RS 分位值本身（1~99）",
    "pre_first_board_next": "90→65 线性（首板封住的先后顺序）",
}


@app.route("/api/scoring")
def api_scoring():
    """评分规则说明（前端「评分规则」弹层）"""
    data = strats_data()["strategies"]
    intra = [s for s in data if s["bucket"] == "盘中"]
    return jsonify({
        "pipeline": [
            {"step": "① 单策略打分", "detail": "每条策略按知识点卡的判据给出 0~100 分；分越高越符合该策略。"},
            {"step": "② 多策略聚合", "detail": "同一只票命中多条时，按策略权重加权平均（首触/杯柄 1.3，放量突破/RS 1.1~1.2，其余 1.0）。"},
            {"step": "③ 共振加成 + 预警折扣",
             "detail": "最终分 = 0.9 × 加权均分 + min(24, (命中数−1)×8)；若同时命中「量价背离」预警，再 ×0.75。"},
            {"step": "④ 盘中走势融合（仅盘中·实时源可用）",
             "detail": "走势分(0~100)由分时序列算：5min 动量 + 加速度 + 均价线方向 + 量能脉冲 + 日内位置 + 相对大盘 − 冲高回落惩罚；"
                       "折算成 ±6 分的排序权重加在③之上，只影响同分区间内的先后，不改任何策略判据。"},
            {"step": "⑤ 可买性与分组（盘中）",
             "detail": "先剔除涨停封板/一字板（可切「全部」查看），再按「回踩低吸 → 突破」分组；追高（≥7%）/高危（≥9%）只做标注，不扣分。"},
        ],
        "aggregation": {"base_factor": 0.9, "bonus_per_extra_hit": 8,
                        "bonus_cap": 24, "risk_discount": 0.75},
        "intraday": [{"id": s["id"], "name": s["name"], "formula": SCORE_FORMULAS.get(s["id"], ""),
                      "note": s.get("note", "")} for s in intra],
        "example": "单命中「龙头回踩均价」：原始分 82.5 → 0.9×82.5 = 74.3（页面显示 74.3）",
    })


# ─────────────────── 连板股锐利档（候选池 / 盘中动量 / 轨迹 / 复盘） ───────────────────

def _liupan_guard(kind, fn, force):
    """连板系接口的统一错误处理。

    🔴 为什么不再是 502（2026-11-03 血证）：
      502 = Bad Gateway，语义是「网关收到的上游响应无效」。这里根本没有网关，
      异常来自本进程内 subprocess → 502 会把排查引向 ngrok / Cloudflare / 反代，
      实际是本地脚本瞬时失败。→ 改用 **503 Service Unavailable**（可重试），
      并把真实异常**写进日志**（原来一点痕迹不留，事后无法回溯）。

    🔴 降级优先于报错：
      查询失败但手上有上一次成功结果 → 返回旧数据并标 `stale_cache=True` + `warn`，
      页面照常可用（只是提示非实时），而不是整块空白 + 502。
    """
    try:
        d = fn(tier=request.args.get("tier") or liupan.TIER, force=force, log=True)
        return jsonify(d)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        liupan._err(f"/api/liupan/{kind} 失败（force={force}）：{msg}")
        try:
            old = liupan.last_good(kind)
        except Exception:
            old = None
        if old:
            return jsonify(dict(old, ok=True, stale_cache=True,
                                warn=f"实时查询失败，下面是上一次成功结果：{msg}"))
        return jsonify({"ok": False, "error": msg,
                        "hint": "本地脚本瞬时失败，稍后重试；详细日志见 data/liupan_error.log"}), 503


@app.route("/api/liupan/pool")
def api_liupan_pool():
    """连板候选池（T-1 收盘截面）。?refresh=1 强制重查并落一条轨迹。"""
    return _liupan_guard("pool", liupan.pool, request.args.get("refresh") == "1")


_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


def _parse_codes(raw):
    """解析 ?codes=000001.SZ,600519.SH —— 严格白名单，防注入/防脏码。

    非法格式一律丢弃（不报错、不猜测）；全部丢弃时返回 None（回退旧行为：自己重扫）。
    """
    if not raw:
        return None
    out = []
    for x in raw.split(","):
        x = x.strip().upper()
        if _CODE_RE.match(x) and x not in out:
            out.append(x)
        if len(out) >= 300:          # 池子不会超过 300 只，超出截断
            break
    return out or None


@app.route("/api/liupan/momentum")
def api_liupan_momentum():
    """盘中动量重排（按先手分排序）。

    ?refresh=1             强制重查并落一条轨迹
    ?codes=a.SZ,b.SH       用前端缓存的候选池名单（跳过全市场扫描，只实时算先手分）
    ?pool_asof=YYYY-MM-DD  该池所属交易日（供惩罚层取 T-1 历史标记）
    """
    _codes = _parse_codes(request.args.get("codes"))
    _pasof = (request.args.get("pool_asof") or "").strip() or None
    if _pasof and not re.match(r"^\d{4}-\d{2}-\d{2}$", _pasof):
        _pasof = None
    try:
        d = liupan.momentum(tier=request.args.get("tier") or liupan.TIER,
                            force=request.args.get("refresh") == "1", log=True,
                            codes=_codes, pool_asof=_pasof)
        return jsonify(d)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        liupan._err(f"/api/liupan/momentum 失败：{msg}")
        old = liupan.last_good("momentum")
        if old:
            return jsonify(dict(old, ok=True, stale_cache=True,
                                warn=f"实时查询失败，下面是上一次成功结果：{msg}"))
        return jsonify({"ok": False, "error": msg,
                        "hint": "本地脚本瞬时失败，稍后重试；详细日志见 data/liupan_error.log"}), 503


@app.route("/api/liupan/latest")
def api_liupan_latest():
    """最近一次查询结果（不触发重查、不落轨迹）——首屏秒开用。"""
    out = {}
    for kind, fn in (("momentum", liupan.momentum), ("pool", liupan.pool)):
        try:
            out[kind] = fn(log=False)
            out[kind]["fromCache"] = True
        except Exception as e:
            out[kind] = {"ok": False, "error": str(e), "kind": kind}
    out["trace"] = liupan.trace_summary()
    return jsonify(out)


@app.route("/api/liupan/trace")
def api_liupan_trace():
    """决策轨迹日志（新→旧）。?limit=100&kind=pool|momentum"""
    lim = max(1, min(500, int(request.args.get("limit", 100))))
    kind = request.args.get("kind") or None
    return jsonify({"ok": True, "rows": liupan.traces(limit=lim, kind=kind),
                    "summary": liupan.trace_summary()})


@app.route("/api/liupan/review")
def api_liupan_review():
    """复盘看板：轨迹 Top N 的 T+1 / T+3 / T+5 事后表现。"""
    lim = max(1, min(200, int(request.args.get("limit", 60))))
    topn = max(1, min(10, int(request.args.get("topn", 5))))
    try:
        d = liupan.review(limit=lim, topn=topn, kind=request.args.get("kind") or None)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "rows": [], "summary": {}}), 500
    return jsonify(d)


# ─────────────────── 第一切片：持仓（股）/ 归因 / 观察读 ───────────────────
# 路径钉死（C v0.2.0-C §2.3 / §2.7）：
#   POST /api/liupan/positions/buy|sell ，GET /api/liupan/positions
#   GET  /api/liupan/decision_log/suggest ，POST /api/liupan/decision_log/confirm
# 批任务刷新 suggested 不走 HTTP，见 scripts/refresh_suggested.py（禁止 UPDATE confirmed_*）。
# 旁注：仅记账，不代下单。


def _liupan_http_error(http, code, message):
    return jsonify({
        "ok": False,
        "status": "error",
        "generated_at": liupan_store.now_text(),
        "disclaimer": liupan_store.DISCLAIMER,
        "error": {"code": code, "message": message},
    }), http


def _req_id(data):
    header = (request.headers.get("Idempotency-Key") or "").strip()
    if header:
        return header
    if isinstance(data, dict) and data.get("client_req_id"):
        return str(data.get("client_req_id")).strip()
    return None


def _call_store(fn):
    try:
        return jsonify(fn())
    except liupan_store.StoreError as e:
        return _liupan_http_error(e.http, e.code, e.message)


@app.route("/api/liupan/positions/buy", methods=["POST"])
def api_liupan_positions_buy():
    """买入记账。qty=股。与 decision_log 同一事务。不代下单。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _liupan_http_error(400, "bad_request", "需要 JSON 对象")
    return _call_store(lambda: liupan_store.buy(data, client_req_id=_req_id(data)))


@app.route("/api/liupan/positions/sell", methods=["POST"])
def api_liupan_positions_sell():
    """卖出记账。超量 → 422 / qty_exceeds_position。不代下单。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _liupan_http_error(400, "bad_request", "需要 JSON 对象")
    return _call_store(lambda: liupan_store.sell(data, client_req_id=_req_id(data)))


@app.route("/api/liupan/positions")
def api_liupan_positions():
    """当前持仓。默认只含 qty>0；空仓 empty=true。单位股。"""
    return jsonify(liupan_store.list_positions())


@app.route("/api/liupan/decision_log")
def api_liupan_decision_log():
    """读 decision_log（含 suggested / confirmed）。不在此接口写入正式归因。"""
    return _call_store(lambda: liupan_store.list_decision_log(
        request.args.get("date"), request.args.get("code")))


@app.route("/api/liupan/decision_log/suggest")
def api_liupan_decision_log_suggest():
    """预勾用 suggested_tags。不写 confirmed_*。无建议时 status=no_data，不编造标签。"""
    return _call_store(lambda: liupan_store.get_suggest(
        request.args.get("date"), request.args.get("code")))


@app.route("/api/liupan/decision_log/confirm", methods=["POST"])
def api_liupan_decision_log_confirm():
    """唯一正式归因落库入口。人工重复确认覆盖确认集；批任务不得调用。"""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _liupan_http_error(400, "bad_request", "需要 JSON 对象")
    return _call_store(lambda: liupan_store.confirm(
        data.get("date"), data.get("code"), data.get("confirmed_tags"), data.get("name")))


@app.route("/api/liupan/watchlist")
def api_liupan_watchlist():
    """观察列表只读。auto_top1 由 5min 扫描写入，不是下单。手动增删本切片未做。"""
    return _call_store(lambda: liupan_store.list_watchlist(request.args.get("date")))


# ─────────────────── 独立 PC 上传台（不依赖 LLM） ───────────────────

@app.route("/upload")
def upload_page():
    return send_from_directory(config.WEB, "upload.html")


@app.route("/api/upload/batch", methods=["POST"])
def api_upload_batch():
    """接收手动上传的文件，落盘到 data/uploads/<batch>/，返回分类预览"""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "没有收到文件"}), 400
    batch, _ = datapacks.new_batch()
    saved = []
    for f in files:
        if not f or not f.filename:
            continue
        try:
            saved.append(datapacks.stage_upload(batch, f))
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": f"{type(e).__name__}: {e}"}), 500
    return jsonify({"batch": batch, "saved": len(saved),
                    "files": datapacks.describe_staged(batch)})


@app.route("/api/health/write")
def api_health_write():
    """🔴 进程级写权限自检（血证 2026-11-03：能读不能写，导入全部 EPERM）"""
    return jsonify(datapacks.write_probe())


@app.route("/api/upload/import", methods=["POST"])
def api_upload_import():
    body = request.get_json(silent=True) or {}
    if datapacks.progress().get("running"):
        return jsonify({"error": "已有导入任务在跑"}), 409
    # 🆕 2026-11-03：写前先探权限，别让线程里静默失败
    pr = datapacks.write_probe()
    if not pr["ok"]:
        print("[upload] 写权限自检失败:", pr["error"], flush=True)
        return jsonify({"error": pr["error"], "hint": datapacks._PERM_HINT,
                        "perm_denied": True}), 503
    batch = str(body.get("batch") or "")
    names = body.get("files") or None
    dry = bool(body.get("dry", False))
    date_override = str(body.get("date") or "") or None
    allow_dup = bool(body.get("allow_duplicates", False))
    wait_lock = int(body.get("wait_lock", 120) or 120)
    if not batch:
        return jsonify({"error": "缺少 batch"}), 400
    threading.Thread(target=_run_upload_import,
                     args=(batch, names, date_override, dry, allow_dup, wait_lock), daemon=True).start()
    return jsonify({"started": True, "batch": batch, "dry": dry})


def _run_upload_import(batch, names, date_override, dry, allow_dup=False, wait_lock=120):
    try:
        datapacks.import_batch(batch, names=names, date_override=date_override, dry=dry,
                               allow_duplicates=allow_dup, wait_lock=wait_lock)
    except Exception as e:  # noqa: BLE001
        print("[upload] import failed:", e, flush=True)


@app.route("/api/upload/status")
def api_upload_status():
    return jsonify(datapacks.progress())


@app.route("/api/upload/batch/<batch>")
def api_upload_batch_detail(batch):
    return jsonify({"batch": batch, "files": datapacks.describe_staged(batch)})


@app.route("/api/upload/tables")
def api_upload_tables():
    """目标表最新日期（上传页右侧参考）"""
    meta = datapacks.db_status()
    keys = list(datapacks.DAILY_REQUIRED) + list(datapacks.OPTIONAL_TABLES)
    return jsonify({"duckdb": config.DUCKDB,
                    "tables": {k: (meta.get(k) or {}).get("max") for k in keys},
                    "labels": {k: v[0] for k, v in
                               list(datapacks.DAILY_REQUIRED.items())
                               + list(datapacks.OPTIONAL_TABLES.items())}})


@app.route("/api/upload/batch/<batch>", methods=["DELETE"])
def api_upload_batch_delete(batch):
    """删除某个上传批次（只删暂存副本，不动 DuckDB 已导入数据）"""
    import shutil as _sh
    d = os.path.join(datapacks.UPLOAD_DIR, batch)
    if not os.path.isdir(d):
        return jsonify({"error": "批次不存在"}), 404
    try:
        _sh.rmtree(d)
        return jsonify({"deleted": batch})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/upload/coverage")
def api_upload_coverage():
    """某交易日的「每日必备表」覆盖度：到位/缺失 + 缺失该补哪个包。"""
    date = (request.args.get("date") or "").strip()
    explicit = bool(date)
    # 🔴 基准绝不能是「库里 daily 最新日」（那等于自己给自己打分，恒满分）
    #    → 用「按日历应到的最新已收盘交易日」。血证 2026-10-31。
    try:
        expect = datapacks.expect_date()
    except Exception:
        expect = time.strftime("%Y%m%d")
    if not date:
        date = expect
    cov = datapacks.coverage(date)
    cov["explicit"] = explicit
    cov["expect"] = cov.get("expect") or expect
    return jsonify(cov)


@app.route("/api/upload/purge", methods=["POST"])
def api_upload_purge():
    """清理已导入成功的数据包残留（upload 副本）。

    body: {"dry": true} 预演（默认）/ {"dry": false} 真删
    安全：只删 SHA 命中 ledger 的文件（= 内容已在库里），未导入的一律不动。
    """
    body = request.get_json(silent=True) or {}
    dry = bool(body.get("dry", True))
    try:
        return jsonify(datapacks.purge_imported_uploads(dry=dry))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/upload/refresh_meta", methods=["POST"])
def api_upload_refresh_meta():
    """强制重算 table_meta（minute_1min 的 max 约 40s，整体 1~2 分钟）→ 后台线程跑。"""
    if datapacks.progress().get("meta_running"):
        return jsonify({"error": "元数据刷新已在跑"}), 409
    body = request.get_json(silent=True) or {}
    tables = body.get("tables") or None
    datapacks._PROGRESS["meta_running"] = True

    def _job():
        try:
            res = datapacks.refresh_meta(tables=tables)
            datapacks._PROGRESS["meta_result"] = res
            datapacks._log(f"元数据刷新完成：{len(res)} 张表")
        except Exception as e:  # noqa: BLE001
            datapacks._log(f"元数据刷新失败：{type(e).__name__}: {e}")
        finally:
            datapacks._PROGRESS["meta_running"] = False

    threading.Thread(target=_job, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/upload/types")
def api_upload_types():
    """支持的文件类型清单（前端渲染「支持格式」面板 + 缺包提示用）"""
    req = []
    seen = {}
    for tbl, (label, cat, hint) in datapacks.DAILY_REQUIRED.items():
        if hint.startswith("无需上传"):
            continue
        seen.setdefault(hint, {"hint": hint, "cat": cat, "labels": []})
        seen[hint]["labels"].append(label)
    return jsonify({"required_tables": len(datapacks.DAILY_REQUIRED),
                    "packs": sorted(seen.values(), key=lambda x: (x["cat"], x["hint"]))})


@app.route("/api/upload/batches")
def api_upload_batches():
    """最近上传批次"""
    root = datapacks.UPLOAD_DIR
    out = []
    if os.path.isdir(root):
        for b in sorted(os.listdir(root), reverse=True)[:20]:
            d = os.path.join(root, b)
            if not os.path.isdir(d):
                continue
            files = [f for f in os.listdir(d) if not f.startswith(".")]
            out.append({"batch": b, "count": len(files),
                        "size": sum(os.path.getsize(os.path.join(d, f)) for f in files
                                    if os.path.isfile(os.path.join(d, f)))})
    return jsonify({"batches": out})


@app.route("/api/packs")
def api_packs():
    """本地增量包目录：按日期分类 + 每张表是否已入库"""
    force = request.args.get("force") == "1"
    scan = datapacks.scan()
    meta = datapacks.db_status(force=force)
    for d in scan.get("dates", []):
        d["importable"] = 0
        for c in d["cats"]:
            for it in c["items"]:
                # 🔴 2026-11-03：这里原来**自己重算一遍**，把 datapacks 里按
                #   SNAPSHOT_TABLES 豁免算好的结果覆盖掉了 → 快照表恒显示「未导入」。
                #   现在：datapacks 算过就沿用，没算才按同规则补。
                if not it.get("imported"):
                    it["imported"] = {t: bool(meta.get(t) and (
                        True if t in datapacks.SNAPSHOT_TABLES
                        else datapacks._norm_date(meta[t].get("max")) >= d["date"]))
                                      for t in it["tables"]}
                it["supported"] = it["importer"] != "unsupported"
                if it["supported"] and not it.get("dup"):
                    d["importable"] += 1
    scan["db_max"] = {t: (v or {}).get("max") for t, v in meta.items() if not t.startswith("_")}
    return jsonify(scan)


@app.route("/api/packs/import", methods=["POST"])
def api_packs_import():
    """导入指定日期的增量包（dry=True 只统计不写库）"""
    body = request.get_json(silent=True) or {}
    if datapacks.progress().get("running"):
        return jsonify({"error": "已有导入任务在跑"}), 409
    date = str(body.get("date") or "")
    dry = bool(body.get("dry", True))
    keys = body.get("keys") or None
    threading.Thread(
        target=_run_pack_import, args=(date, keys, dry), daemon=True).start()
    return jsonify({"started": True, "date": date, "dry": dry})


def _run_pack_import(date, keys, dry):
    try:
        datapacks.run_import(date, keys=keys, dry=dry)
    except Exception as e:  # noqa: BLE001
        print("[packs] import failed:", e, flush=True)


@app.route("/api/packs/import/status")
def api_packs_import_status():
    return jsonify(datapacks.progress())


@app.route("/api/playbook")
def api_playbook():
    pb = cards_data().get("playbook") or {}
    return jsonify(pb)


@app.route("/api/status")
def api_status():
    w = autorefresh.get()
    st = dict(w.status) if w else {"enabled": False, "message": "看护线程未启动"}
    st["data_date"] = (_CACHE["rec"] or {}).get("data_date")
    st["ctx_version"] = getattr(get_ctx(), "version", 0)
    st["now"] = time.strftime("%Y-%m-%d %H:%M:%S")
    st["realtime"] = realtime.status()
    if _INTRA["data"] is not None:
        st["intraday"] = {
            "source": _INTRA["data"].get("source"),
            "live": _INTRA["data"].get("live"),
            "cached_at": time.strftime("%H:%M:%S", time.localtime(_INTRA["ts"])),
        }
    return jsonify(st)


@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    """?full=1 先跑 ETL 再重载；默认只重载 parquet + 重算"""
    t = time.time()
    _INTRA["data"] = None
    full = request.args.get("full") == "1"
    w = autorefresh.get()
    if w:
        res = w.refresh_now(full=full)
        ok = res.get("ok", False)
        msg = res.get("message") or w.status.get("message")
    else:
        if full:
            reset_ctx()
        rec_data(refresh=True)
        ok, msg = True, "已重算（看护线程未启动）"
    return jsonify({"ok": ok, "full": full, "message": msg,
                    "elapsed": round(time.time() - t, 2),
                    "computed_at": time.strftime("%Y-%m-%d %H:%M:%S")})


@app.errorhandler(404)
def _404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(config.WEB, "index.html")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else config.PORT
    host = os.environ.get("SAA_HOST", config.HOST)
    print("预热数据与策略…")
    t = time.time()
    try:
        get_ctx()
        rec_data(refresh=True)
    except Exception as e:
        # 空仓没有 parquet 缓存时，持仓记账 / 归因仍要能听 :8770。盘面接口会在各自路由里失败。
        print(f"行情缓存未就绪，盘面接口暂不可用（{type(e).__name__}: {e}）。"
              f"持仓记账与归因不依赖该缓存，服务仍会监听。")
    watch = config.AUTO_REFRESH and "--no-watch" not in sys.argv
    if watch:
        autorefresh.start(on_changed=lambda: rec_data(refresh=True))
        print(f"自动刷新已开启：每 {config.WATCH_INTERVAL}s 检查一次"
              f"（自动 ETL={'开' if config.AUTO_ETL else '关'}）")
    else:
        print("自动刷新已关闭（--no-watch）")
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"就绪 {time.time()-t:.1f}s → http://{shown}:{port}")
    if host in ("0.0.0.0", "::"):
        print(f"已监听所有网卡：手机同 Wi-Fi 访问 http://<本机内网IP>:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
