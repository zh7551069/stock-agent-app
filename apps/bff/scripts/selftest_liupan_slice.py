#!/usr/bin/env python3
"""第一切片自测。不读 DuckDB、不联网、不写仓库 data/。

  cd apps/bff && python3 scripts/selftest_liupan_slice.py
"""
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

_TMP = tempfile.mkdtemp(prefix="liupan-slice-")
os.environ["LIUPAN_SQLITE"] = os.path.join(_TMP, "liupan.sqlite")
os.environ["LIUPAN_TRACE"] = os.path.join(_TMP, "trace.jsonl")
os.environ["STOCK_ADVICE_SECRET"] = "slice-selftest-not-a-credential"
os.environ["STOCK_ADVICE_AUTH"] = ""
os.environ.pop("OPENCLAW_WORKSPACE", None)
os.environ.pop("LIUPAN_SCRIPTS", None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import liupan  # noqa: E402
from core import liupan_scan_append as scan  # noqa: E402
from core import liupan_store as store  # noqa: E402
from server import app  # noqa: E402

SH = ZoneInfo("Asia/Shanghai")
FAILS = []


def check(name, cond):
    if cond:
        print(f"ok  {name}")
    else:
        print(f"FAIL {name}")
        FAILS.append(name)


def main():
    check("trace 路径走环境变量", liupan.TRACE_FILE == os.environ["LIUPAN_TRACE"])
    check("建议 SQL 不含 confirmed", "confirmed" not in store.SUGGEST_UPDATE_SQL.lower())
    check("买卖走同一事务", "with _tx()" in _src(store.buy) and "with _tx()" in _src(store.sell))
    check("5min 不覆盖 latest", "LATEST_FILE" not in _src(scan.append_successful_scan)
          and "_write_trace" not in _src(scan.append_successful_scan))

    wed_open = datetime.datetime(2026, 11, 4, 10, 0, tzinfo=SH)
    wed_lunch = datetime.datetime(2026, 11, 4, 12, 0, tzinfo=SH)
    wed_close = datetime.datetime(2026, 11, 4, 15, 0, tzinfo=SH)
    wed_after = datetime.datetime(2026, 11, 4, 15, 1, tzinfo=SH)
    sat = datetime.datetime(2026, 11, 7, 10, 0, tzinfo=SH)
    check("交易时段 10:00", scan.in_session(wed_open))
    check("午休不算", not scan.in_session(wed_lunch))
    check("15:00 仍算", scan.in_session(wed_close))
    check("15:01 不算", not scan.in_session(wed_after))
    check("周六跳过", scan.skip_reason(sat) == "weekend")

    cmd = scan.scanner_command(sys.executable, "/scripts/liupan", "锐利")
    check("扫描命令是 auction_rank --live",
          cmd[1].endswith("liupan_auction_rank.py") and "--live" in cmd and "--json" in cmd
          and "momentum" not in " ".join(cmd))

    missed = scan.append_successful_scan(
        {"status": "no_disk", "reason": "外置盘未挂载"}, tier="锐利", now=wed_open)
    check("no_disk 不落库", missed["appended"] is False and not os.path.exists(liupan.TRACE_FILE))

    empty = scan.append_successful_scan(
        {"status": "ok", "asof": "2026-11-03", "mode": "intraday", "n": 0, "buy": [],
         "level": {"weak_pool": True}},
        tier="锐利", now=wed_open)
    check("成功但无 buy：写节点且 top1 为空", empty["appended"] and empty["top1"] is None)
    rows = _trace_rows()
    check("空 buy 不入观察", len(rows) == 1 and rows[0]["top1"] is None and rows[0]["kind"] == "scan_5min"
          and rows[0]["source"] == "server_5min")
    wl = store.list_watchlist("2026-11-04")
    check("空 buy 观察列表仍空", wl["empty"] is True)

    first = scan.append_successful_scan(_ok_scan(0.42), tier="锐利", now=wed_open)
    second = scan.append_successful_scan(_ok_scan(0.55), tier="锐利", now=wed_open.replace(minute=5))
    check("top1 来自 buy[0]，不另造", first["top1"]["code"] == "600000.SH" and first["top1"]["score_adj"] == 0.42)
    items = store.list_watchlist("2026-11-04")["items"]
    check("同日同 code 只一行", len(items) == 1 and items[0]["source"] == "auto_top1")
    check("upsert 更新 last_score、保留 first",
          items[0]["last_score"] == 0.55
          and items[0]["first_top1_asof"] == second["watchlist"]["first_top1_asof"]
          and items[0]["first_top1_asof"] == "2026-11-04 10:00:00")
    check("水位门未过仍记排名第一，不改分数",
          json.loads(open(liupan.TRACE_FILE, encoding="utf-8").read().strip().splitlines()[-1])["level"]["weak_pool"] is True)

    conn = sqlite3.connect(os.environ["LIUPAN_SQLITE"])
    conn.execute(
        "UPDATE watchlist SET removed_at = ? WHERE code = ?",
        ("2026-11-04 10:06:00", "600000.SH"))
    conn.commit()
    conn.close()
    scan.append_successful_scan(_ok_scan(0.40), tier="锐利", now=wed_open.replace(minute=10))
    back = store.list_watchlist("2026-11-04")["items"][0]
    check("移除后同日再 top1 以 auto_top1 再入", back["removed_at"] is None and back["source"] == "auto_top1")

    conn = sqlite3.connect(os.environ["LIUPAN_SQLITE"])
    conn.execute(
        """
        INSERT INTO watchlist (trade_date, code, name, source, first_top1_asof, last_top1_asof, last_score)
        VALUES ('2026-11-04', '601398.SH', '工商银行', 'manual', NULL, NULL, NULL)
        """)
    conn.commit()
    conn.close()
    store.upsert_auto_top1("2026-11-04", "601398.SH", "工商银行", "2026-11-04 10:10:00", 0.1)
    manual = [x for x in store.list_watchlist("2026-11-04")["items"] if x["code"] == "601398.SH"][0]
    check("未移除的手动来源不被改成 auto_top1", manual["source"] == "manual" and manual["last_score"] == 0.1)

    client = app.test_client()
    created = client.post("/api/liupan/positions/buy", json={
        "code": "000001.SZ", "name": "平安银行", "price": 10, "qty": 100, "note": "仅记账",
    })
    body = created.get_json()
    check("买入 200", created.status_code == 200 and body["position"]["qty"] == 100
          and body["position"]["qty_unit"] == "share"
          and body["decision_log"]["side"] == "buy"
          and body["decision_log"]["qty"] == 100
          and body["disclaimer"] == "仅记账，不代下单"
          and body["decision_log"]["r1_a"] == "pending")

    again = client.post("/api/liupan/positions/buy", json={
        "code": "000001.SZ", "price": 20, "qty": 100,
    })
    check("加权成本", again.status_code == 200 and again.get_json()["position"]["qty"] == 200
          and again.get_json()["position"]["avg_cost"] == 15)

    bad_qty = client.post("/api/liupan/positions/sell", json={
        "code": "000001.SZ", "price": 11, "qty": 1.5,
    })
    check("股数必须是整数", bad_qty.status_code == 400 and bad_qty.get_json()["error"]["code"] == "bad_request")

    before = _count_logs("000001.SZ")
    over = client.post("/api/liupan/positions/sell", json={
        "code": "000001.SZ", "price": 11, "qty": 100000,
    })
    after = _count_logs("000001.SZ")
    over_body = over.get_json()
    check("超量 422", over.status_code == 422 and over_body["error"]["code"] == "qty_exceeds_position"
          and after == before and _qty("000001.SZ") == 200)

    sold = client.post("/api/liupan/positions/sell", json={
        "code": "000001.SZ", "price": 12, "qty": 200,
    })
    flat = client.get("/api/liupan/positions").get_json()
    check("清仓后列表为空但行还在", sold.status_code == 200 and sold.get_json()["flat"] is True
          and flat["empty"] is True and _qty("000001.SZ") == 0
          and sold.get_json()["position"]["avg_cost"] == 15)

    replay = client.post("/api/liupan/positions/buy", json={
        "code": "000001.SZ", "price": 10, "qty": 100,
    }, headers={"Idempotency-Key": "buy-once"})
    replay2 = client.post("/api/liupan/positions/buy", json={
        "code": "000001.SZ", "price": 99, "qty": 100,
    }, headers={"Idempotency-Key": "buy-once"})
    check("幂等键不重复加仓", replay.status_code == 200 and replay2.get_json()["idempotent_replay"] is True
          and _qty("000001.SZ") == 100)

    none = client.get("/api/liupan/decision_log/suggest?date=2026-11-04&code=000001.SZ")
    check("未跑批任务不编造建议", none.status_code == 200 and none.get_json()["status"] == "no_data"
          and none.get_json()["suggested_tags"] == [] and none.get_json()["confirmed_tags"] is None)

    store.refresh_suggested("2026-11-04", "000001.SZ", ["竞价高开", "板块共振"],
                            evidence={"from": "selftest"}, asof="2026-11-04 10:05:00")
    sug = client.get("/api/liupan/decision_log/suggest?date=2026-11-04&code=000001.SZ").get_json()
    check("GET suggest 返回预勾", sug["status"] == "ok" and sug["suggested_tags"] == ["竞价高开", "板块共振"]
          and sug["evidence"]["from"] == "selftest" and sug["confirmed_tags"] is None)

    conf = client.post("/api/liupan/decision_log/confirm", json={
        "date": "2026-11-04", "code": "000001.SZ", "confirmed_tags": ["竞价高开", "板块共振"],
    })
    conf_body = conf.get_json()
    stamped = conf_body["confirmed_at"]
    check("确认落库且 tag_source=suggested_accepted",
          conf.status_code == 200 and conf_body["tag_source"] == "suggested_accepted"
          and conf_body["attr_tags"] == ["竞价高开", "板块共振"] and conf_body["confirmed_tags"] == conf_body["attr_tags"])

    store.refresh_suggested("2026-11-04", "000001.SZ", ["改写建议"], evidence={"from": "second"})
    sug2 = client.get("/api/liupan/decision_log/suggest?date=2026-11-04&code=000001.SZ").get_json()
    check("批任务只改 suggested，不动 confirmed",
          sug2["suggested_tags"] == ["改写建议"] and sug2["confirmed_tags"] == ["竞价高开", "板块共振"]
          and sug2["confirmed_at"] == stamped)

    mixed = client.post("/api/liupan/decision_log/confirm", json={
        "date": "2026-11-04", "code": "000001.SZ", "confirmed_tags": ["改写建议", "手补"],
    }).get_json()
    check("人工再次确认覆盖确认集", mixed["tag_source"] == "mixed"
          and mixed["confirmed_tags"] == ["改写建议", "手补"]
          and mixed["suggested_tags"] == ["改写建议"])

    manual = client.post("/api/liupan/decision_log/confirm", json={
        "date": "2026-11-04", "code": "600519.SH", "confirmed_tags": ["手填"],
    }).get_json()
    check("无建议时 tag_source=manual", manual["tag_source"] == "manual" and manual["suggested_tags"] == [])

    logs = client.get("/api/liupan/decision_log?date=2026-11-04&code=000001.SZ").get_json()
    check("流水可读且收益不是 0", logs["ok"] and logs["rows"]
          and all(r["r1_a"] == "pending" for r in logs["rows"] if r["side"] in ("buy", "sell")))

    _cli_checks()

    if FAILS:
        print(f"\n{len(FAILS)} failed: {FAILS}")
        return 1
    print(f"\nall ok · sqlite={os.environ['LIUPAN_SQLITE']}")
    return 0


def _ok_scan(score):
    return {
        "status": "ok",
        "asof": "2026-11-03",
        "mode": "intraday",
        "n": 1,
        "level": {"weak_pool": True},
        "buy": [{"code": "600000.SH", "name": "浦发银行", "score": score, "score_adj": score}],
    }


def _trace_rows():
    with open(liupan.TRACE_FILE, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _count_logs(code):
    conn = sqlite3.connect(os.environ["LIUPAN_SQLITE"])
    try:
        return conn.execute("SELECT COUNT(*) FROM decision_log WHERE code = ?", (code,)).fetchone()[0]
    finally:
        conn.close()


def _qty(code):
    conn = sqlite3.connect(os.environ["LIUPAN_SQLITE"])
    try:
        return conn.execute("SELECT qty FROM positions WHERE code = ?", (code,)).fetchone()[0]
    finally:
        conn.close()


def _src(fn):
    import inspect
    return inspect.getsource(fn)


def _cli_checks():
    cli_tmp = tempfile.mkdtemp(prefix="liupan-cli-")
    script = os.path.join(ROOT, "scripts", "liupan_5min_append.py")
    env = os.environ.copy()
    env["LIUPAN_SQLITE"] = os.path.join(cli_tmp, "cli.sqlite")
    env["LIUPAN_TRACE"] = os.path.join(cli_tmp, "cli.jsonl")
    trace = env["LIUPAN_TRACE"]

    bogus_now = _run(script, ["--now", "2026-11-04 10:00:00"], env)
    check("真实扫描不能配 --now", bogus_now.returncode == 2 and not os.path.exists(trace))

    bad = os.path.join(cli_tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as handle:
        json.dump({"status": "no_data"}, handle)
    no_data = _run(script, ["--input", bad, "--now", "2026-11-04 10:00:00"], env)
    check("回放 no_data 不写", no_data.returncode == 3 and "[no-append]" in no_data.stdout
          and not os.path.exists(trace))

    good = os.path.join(cli_tmp, "ok.json")
    with open(good, "w", encoding="utf-8") as handle:
        json.dump(_ok_scan(0.61), handle)
    ok = _run(script, ["--input", good, "--now", "2026-11-04 10:05:00"], env)
    line = json.loads(open(trace, encoding="utf-8").read().strip()) if os.path.exists(trace) else {}
    check("回放 ok 才 append，并标 replay", ok.returncode == 0 and line.get("replay") is True
          and line.get("top1", {}).get("code") == "600000.SH" and line.get("kind") == "scan_5min")

    batch = _run(os.path.join(ROOT, "scripts", "refresh_suggested.py"), [
        "--date", "2026-11-04", "--code", "000001.SZ",
        "--tags", '["批任务"]', "--evidence", '{"via":"cli"}',
    ], env)
    payload = json.loads(batch.stdout) if batch.returncode == 0 else {}
    check("批任务 CLI 只返回 suggested", batch.returncode == 0 and payload.get("suggested_tags") == ["批任务"]
          and payload.get("confirmed_tags") is None)


def _run(script, args, env):
    return subprocess.run(
        [sys.executable, script, *args], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )


if __name__ == "__main__":
    sys.exit(main())
