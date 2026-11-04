"""盘中池级扫描成功后才 append liupan_trace，并把 buy[0] upsert 进观察列表。

与手机点触 GET /api/liupan/momentum 分开：本模块 kind=scan_5min，不调用该 HTTP，
也不走 liupan._write_trace（那个会覆盖 liupan_latest.json）。
status != ok 时不写节点、不编造 top1。
"""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from core import liupan, liupan_store

SH = ZoneInfo("Asia/Shanghai")
KIND = "scan_5min"


def in_session(now=None):
    """对齐 liupan_live_scan._in_trade_window：09:30–11:30、13:00–15:00。

    用 Asia/Shanghai，并跳过周六日。交易所休市日历本仓没有，不编造。
    """
    moment = _as_sh(now)
    if moment.weekday() >= 5:
        return False
    hm = moment.hour * 60 + moment.minute
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)


def skip_reason(now=None):
    moment = _as_sh(now)
    if moment.weekday() >= 5:
        return "weekend"
    if not in_session(moment):
        return "out_of_window"
    return None


def scanner_command(python, scripts_dir, tier):
    """池级 live：auction_rank --live（内部再调 liupan_live_scan）。不是手机动量接口。"""
    return [
        python,
        os.path.join(scripts_dir, "liupan_auction_rank.py"),
        "--tier", tier,
        "--live",
        "--asof", "now",
        "--json",
    ]


def append_successful_scan(raw, *, tier="锐利", replay=False, now=None, cost_ms=None):
    """只有扫描器给出 status=ok 才落一行。buy 为空则 top1=null，且不写观察列表。"""
    if not isinstance(raw, dict):
        return {"appended": False, "reason": "invalid"}
    status = raw.get("status")
    if status != "ok":
        return {"appended": False, "reason": f"status={status}", "status": status}

    moment = _as_sh(now)
    ts = moment.strftime("%Y-%m-%d %H:%M:%S")
    buy = raw.get("buy") if isinstance(raw.get("buy"), list) else []
    top = _top1(buy)
    items = []
    for row in buy[:10]:
        if not isinstance(row, dict):
            continue
        items.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "score": row.get("score"),
            "score_adj": row.get("score_adj"),
        })
    record = {
        "ts": ts,
        "kind": KIND,
        "tier": tier,
        "asof": raw.get("asof"),
        "mode": raw.get("mode"),
        "n": raw.get("n") if raw.get("n") is not None else len(buy),
        "cost_ms": cost_ms,
        "status": "ok",
        "source": "server_5min",
        "replay": bool(replay),
        "items": items,
        "level": raw.get("level") if isinstance(raw.get("level"), dict) else {},
        "top1": top,
    }
    path = liupan.TRACE_FILE
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()

    watch = None
    if top and top.get("code"):
        watch = liupan_store.upsert_auto_top1(
            trade_date=ts[:10],
            code=str(top["code"]),
            name=top.get("name"),
            asof=ts,
            score=top.get("score_adj"),
        )
    return {
        "appended": True,
        "ts": ts,
        "top1": top,
        "watchlist": watch,
        "replay": bool(replay),
        "trace": path,
    }


def _top1(buy):
    if not buy:
        return None
    row = buy[0]
    if not isinstance(row, dict) or not row.get("code"):
        return None
    score = row.get("score_adj")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = None
    else:
        score = float(score)
    name = row.get("name")
    return {
        "code": str(row.get("code")),
        "name": None if name is None else str(name),
        "score_adj": score,
    }


def _as_sh(now):
    if now is None:
        return datetime.now(SH)
    if now.tzinfo is None:
        return now.replace(tzinfo=SH)
    return now.astimezone(SH)
