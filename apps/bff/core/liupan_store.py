"""同机 SQLite：decision_log / positions / watchlist。

契约 C v0.2.0-C §3。手机不打开这个文件，只走 BFF HTTP。
买卖与流水在同一个 BEGIN IMMEDIATE 里提交。
批任务刷新建议只执行 SUGGEST_UPDATE_SQL，该语句不得出现 confirmed。
"""
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import config

DISCLAIMER = "仅记账，不代下单"
SH = ZoneInfo("Asia/Shanghai")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CODE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
_LIST_LIMIT = 200

# 批任务唯一允许的更新。测试会断言其中不含 confirmed。
SUGGEST_UPDATE_SQL = """
UPDATE decision_log
   SET suggested_tags = ?,
       suggested_at = ?,
       suggested_asof = ?,
       attr_evidence = ?
 WHERE trade_date = ? AND code = ?
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decision_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT,
  source_kind TEXT,
  tier TEXT,
  asof TEXT,
  rank INTEGER,
  score REAL,
  score_adj REAL,
  side TEXT,
  price REAL,
  qty INTEGER,
  ts TEXT,
  note TEXT,
  watched INTEGER,
  r1_a TEXT,
  r3_a TEXT,
  r1_b TEXT,
  r3_b TEXT,
  attr_tags TEXT,
  suggested_tags TEXT,
  suggested_at TEXT,
  suggested_asof TEXT,
  confirmed_tags TEXT,
  confirmed_at TEXT,
  tag_source TEXT,
  attr_evidence TEXT,
  client_req_id TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_decision_date_code ON decision_log(trade_date, code);

CREATE TABLE IF NOT EXISTS positions (
  code TEXT PRIMARY KEY,
  name TEXT,
  qty INTEGER NOT NULL CHECK (qty >= 0),
  avg_cost REAL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
  trade_date TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT,
  source TEXT NOT NULL,
  first_top1_asof TEXT,
  last_top1_asof TEXT,
  last_score REAL,
  removed_at TEXT,
  PRIMARY KEY (trade_date, code)
);
"""

_ATTR_KEYS = (
    "attr_tags", "suggested_tags", "suggested_at", "suggested_asof",
    "confirmed_tags", "confirmed_at", "tag_source", "attr_evidence",
)


class StoreError(Exception):
    def __init__(self, http, code, message):
        super().__init__(message)
        self.http = http
        self.code = code
        self.message = message


def now_dt():
    return datetime.now(SH)


def now_text():
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def connect():
    path = config.SQLITE
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None  # 事务由 _tx 显式 BEGIN IMMEDIATE
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def _tx():
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _loads(raw, default):
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _require_date(value, field="date"):
    text = str(value or "").strip()
    if not _DATE.match(text):
        raise StoreError(400, "bad_request", f"{field} 必须为 YYYY-MM-DD")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise StoreError(400, "bad_request", f"{field} 不是合法日期")
    return text


def _optional_date(value, field="date"):
    if value is None or str(value).strip() == "":
        return None
    return _require_date(value, field)


def _require_code(value):
    text = str(value or "").strip().upper()
    if not _CODE.match(text):
        raise StoreError(400, "bad_request", "code 必须为后缀式，如 000001.SZ")
    return text


def _opt_str(payload, key, limit):
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if not isinstance(value, str):
        raise StoreError(400, "bad_request", f"{key} 必须为字符串")
    text = value.strip()
    if len(text) > limit:
        raise StoreError(400, "bad_request", f"{key} 最长 {limit}")
    return text or None


def _opt_float(payload, key):
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoreError(400, "bad_request", f"{key} 必须为数字")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise StoreError(400, "bad_request", f"{key} 必须为有限数字")
    return number


def _opt_int(payload, key):
    if key not in payload or payload.get(key) is None:
        return None
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreError(400, "bad_request", f"{key} 必须为整数")
    return value


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoreError(400, "bad_request", "price 必须大于 0")
    number = float(value)
    if not number > 0 or number != number or number == float("inf"):
        raise StoreError(400, "bad_request", "price 必须大于 0")
    return round(number, 4)


def _qty_shares(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoreError(400, "bad_request", "qty 必须为正整数（股）")
    if isinstance(value, float) and not value.is_integer():
        raise StoreError(400, "bad_request", "qty 必须为正整数（股）")
    qty = int(value)
    if qty <= 0 or qty > 100_000_000:
        raise StoreError(400, "bad_request", "qty 必须为正整数（股）")
    return qty


def _clean_req_id(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 128:
        raise StoreError(400, "bad_request", "Idempotency-Key / client_req_id 最长 128")
    return text


def tag_list(tags):
    """确认或建议标签。允许空数组（显式确认「无标签」）。不猜词表。"""
    if not isinstance(tags, list):
        raise StoreError(400, "bad_request", "tags 必须为字符串数组")
    if len(tags) > 40:
        raise StoreError(400, "bad_request", "tags 最多 40 个")
    out = []
    seen = set()
    for item in tags:
        if not isinstance(item, str):
            raise StoreError(400, "bad_request", "tags 的每一项必须是字符串")
        text = item.strip()
        if not text:
            continue
        if len(text) > 64:
            raise StoreError(400, "bad_request", "单个 tag 最长 64 字")
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def tag_source(confirmed, suggested):
    """manual | suggested_accepted | mixed。集合比较，忽略顺序。"""
    confirmed_set = set(confirmed or [])
    suggested_set = set(suggested or [])
    if not confirmed_set or not suggested_set or confirmed_set.isdisjoint(suggested_set):
        return "manual"
    if confirmed_set == suggested_set:
        return "suggested_accepted"
    return "mixed"


def _trade_fields(payload):
    if not isinstance(payload, dict):
        raise StoreError(400, "bad_request", "需要 JSON 对象")
    ts = _opt_str(payload, "ts", 32) or now_text()
    trade_date = _require_date(payload["trade_date"], "trade_date") if payload.get("trade_date") else ts[:10]
    if not _DATE.match(trade_date):
        raise StoreError(400, "bad_request", "ts 需以 YYYY-MM-DD 开头，或另传 trade_date")
    watched = payload.get("watched") if "watched" in payload else None
    if watched is None:
        watched_i = None
    elif isinstance(watched, bool):
        watched_i = 1 if watched else 0
    else:
        raise StoreError(400, "bad_request", "watched 必须为布尔")
    return {
        "trade_date": trade_date,
        "code": _require_code(payload.get("code")),
        "name": _opt_str(payload, "name", 64),
        "price": _price(payload.get("price")),
        "qty": _qty_shares(payload.get("qty")),
        "ts": ts,
        "note": _opt_str(payload, "note", 2000),
        "source_kind": _opt_str(payload, "source_kind", 64),
        "tier": _opt_str(payload, "tier", 32),
        "asof": _opt_str(payload, "asof", 40),
        "rank": _opt_int(payload, "rank"),
        "score": _opt_float(payload, "score"),
        "score_adj": _opt_float(payload, "score_adj"),
        "watched": watched_i,
    }


def _attr_forward(conn, trade_date, code):
    """同一交易日同一代码的归因是一份。新成交流水带上已有建议/确认，避免同日拆成两套。"""
    row = conn.execute(
        f"""
        SELECT {", ".join(_ATTR_KEYS)}
          FROM decision_log
         WHERE trade_date = ? AND code = ?
         ORDER BY id DESC
         LIMIT 1
        """,
        (trade_date, code),
    ).fetchone()
    if row is None:
        return {key: None for key in _ATTR_KEYS}
    return {key: row[key] for key in _ATTR_KEYS}


def _pos_row(conn, code):
    return conn.execute("SELECT * FROM positions WHERE code = ?", (code,)).fetchone()


def _upsert_pos(conn, code, name, qty, avg_cost, ts):
    conn.execute(
        """
        INSERT INTO positions (code, name, qty, avg_cost, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
          name = COALESCE(excluded.name, positions.name),
          qty = excluded.qty,
          avg_cost = excluded.avg_cost,
          updated_at = excluded.updated_at
        """,
        (code, name, qty, avg_cost, ts),
    )


def _insert_trade(conn, fields, side, client_req_id):
    attr = _attr_forward(conn, fields["trade_date"], fields["code"])
    cur = conn.execute(
        """
        INSERT INTO decision_log (
          trade_date, code, name, source_kind, tier, asof, rank, score, score_adj,
          side, price, qty, ts, note, watched,
          r1_a, r3_a, r1_b, r3_b,
          attr_tags, suggested_tags, suggested_at, suggested_asof,
          confirmed_tags, confirmed_at, tag_source, attr_evidence,
          client_req_id
        ) VALUES (
          :trade_date, :code, :name, :source_kind, :tier, :asof, :rank, :score, :score_adj,
          :side, :price, :qty, :ts, :note, :watched,
          'pending', 'pending', 'pending', 'pending',
          :attr_tags, :suggested_tags, :suggested_at, :suggested_asof,
          :confirmed_tags, :confirmed_at, :tag_source, :attr_evidence,
          :client_req_id
        )
        """,
        {**fields, **attr, "side": side, "client_req_id": client_req_id},
    )
    return conn.execute(
        "SELECT * FROM decision_log WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def _by_req(conn, client_req_id):
    return conn.execute(
        "SELECT * FROM decision_log WHERE client_req_id = ?", (client_req_id,)
    ).fetchone()


def _log_dict(row):
    data = {key: row[key] for key in row.keys()}
    for key in ("suggested_tags", "confirmed_tags", "attr_tags"):
        data[key] = _loads(data.get(key), None)
    data["attr_evidence"] = _loads(data.get("attr_evidence"), None) if data.get("attr_evidence") else None
    if data.get("qty") is not None:
        data["qty"] = int(data["qty"])
    data["qty_unit"] = "share" if data.get("side") in ("buy", "sell") else None
    return data


def _pos_dict(row):
    if row is None:
        return None
    return {
        "code": row["code"],
        "name": row["name"],
        "qty": int(row["qty"]),
        "avg_cost": row["avg_cost"],
        "updated_at": row["updated_at"],
        "qty_unit": "share",
    }


def _trade_result(conn, row, replay):
    pos = _pos_row(conn, row["code"])
    qty = int(pos["qty"]) if pos is not None else 0
    return {
        "ok": True,
        "status": "ok",
        "asof": row["ts"],
        "generated_at": now_text(),
        "disclaimer": DISCLAIMER,
        "idempotent_replay": bool(replay),
        "qty_unit": "share",
        "flat": qty == 0,
        "decision_log": _log_dict(row),
        "position": _pos_dict(pos),
    }


def _replay_or_none(conn, client_req_id, side):
    if not client_req_id:
        return None
    prev = _by_req(conn, client_req_id)
    if prev is None:
        return None
    if prev["side"] != side:
        raise StoreError(409, "idempotency_conflict", "Idempotency-Key 已用于另一笔方向不同的流水")
    return _trade_result(conn, prev, replay=True)


def _on_integrity(req, side):
    """并发下同一 Idempotency-Key 撞上 UNIQUE 时，回到已提交的那一笔，不重复记账。"""
    if not req:
        raise StoreError(409, "idempotency_conflict", "流水主键冲突")
    conn = connect()
    try:
        prev = _by_req(conn, req)
        if prev is None or prev["side"] != side:
            raise StoreError(409, "idempotency_conflict", "Idempotency-Key 已用于另一笔流水")
        return _trade_result(conn, prev, replay=True)
    finally:
        conn.close()


def buy(payload, client_req_id=None):
    fields = _trade_fields(payload)
    req = _clean_req_id(client_req_id)
    try:
        with _tx() as conn:
            replay = _replay_or_none(conn, req, "buy")
            if replay:
                return replay
            pos = _pos_row(conn, fields["code"])
            held = int(pos["qty"]) if pos is not None else 0
            old_avg = float(pos["avg_cost"] or 0) if pos is not None else 0.0
            if held > 0:
                new_qty = held + fields["qty"]
                new_avg = round((held * old_avg + fields["qty"] * fields["price"]) / new_qty, 4)
            else:
                new_qty = fields["qty"]
                new_avg = round(fields["price"], 4)
            row = _insert_trade(conn, fields, "buy", req)
            _upsert_pos(conn, fields["code"], fields["name"], new_qty, new_avg, fields["ts"])
            return _trade_result(conn, row, replay=False)
    except sqlite3.IntegrityError:
        return _on_integrity(req, "buy")


def sell(payload, client_req_id=None):
    fields = _trade_fields(payload)
    req = _clean_req_id(client_req_id)
    try:
        with _tx() as conn:
            replay = _replay_or_none(conn, req, "sell")
            if replay:
                return replay
            pos = _pos_row(conn, fields["code"])
            held = int(pos["qty"]) if pos is not None else 0
            if fields["qty"] > held:
                raise StoreError(
                    422,
                    "qty_exceeds_position",
                    f"卖出 {fields['qty']} 股超过当前持仓 {held} 股",
                )
            new_qty = held - fields["qty"]
            new_avg = round(float(pos["avg_cost"] or 0), 4) if pos is not None else 0.0
            row = _insert_trade(conn, fields, "sell", req)
            _upsert_pos(conn, fields["code"], fields["name"], new_qty, new_avg, fields["ts"])
            return _trade_result(conn, row, replay=False)
    except sqlite3.IntegrityError:
        return _on_integrity(req, "sell")


def list_positions():
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE qty > 0 ORDER BY updated_at DESC, code"
        ).fetchall()
        items = [_pos_dict(row) for row in rows]
        return {
            "ok": True,
            "status": "ok",
            "asof": items[0]["updated_at"] if items else None,
            "generated_at": now_text(),
            "disclaimer": DISCLAIMER,
            "qty_unit": "share",
            "empty": len(items) == 0,
            "positions": items,
        }
    finally:
        conn.close()


def list_decision_log(trade_date=None, code=None):
    trade_date = _optional_date(trade_date, "date")
    code = _require_code(code) if code else None
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM decision_log
             WHERE (? IS NULL OR trade_date = ?)
               AND (? IS NULL OR code = ?)
             ORDER BY ts DESC, id DESC
             LIMIT ?
            """,
            (trade_date, trade_date, code, code, _LIST_LIMIT + 1),
        ).fetchall()
        truncated = len(rows) > _LIST_LIMIT
        items = [_log_dict(row) for row in rows[:_LIST_LIMIT]]
        return {
            "ok": True,
            "status": "ok",
            "asof": items[0]["ts"] if items else None,
            "generated_at": now_text(),
            "disclaimer": DISCLAIMER,
            "truncated": truncated,
            "limit": _LIST_LIMIT,
            "rows": items,
        }
    finally:
        conn.close()


def _latest_log(conn, trade_date, code):
    return conn.execute(
        """
        SELECT * FROM decision_log
         WHERE trade_date = ? AND code = ?
         ORDER BY id DESC
         LIMIT 1
        """,
        (trade_date, code),
    ).fetchone()


def _suggest_body(row, trade_date, code):
    suggested = _loads(row["suggested_tags"], None) if row is not None else None
    suggested_at = row["suggested_at"] if row is not None else None
    confirmed = _loads(row["confirmed_tags"], None) if row is not None else None
    has_suggestion = suggested is not None or bool(suggested_at)
    return {
        "ok": True,
        "status": "ok" if has_suggestion else "no_data",
        "asof": (row["suggested_asof"] if row is not None else None),
        "generated_at": now_text(),
        "date": trade_date,
        "code": code,
        "name": row["name"] if row is not None else None,
        "suggested_tags": suggested if suggested is not None else [],
        "suggested_at": suggested_at,
        "suggested_asof": row["suggested_asof"] if row is not None else None,
        "evidence": _loads(row["attr_evidence"], None) if row is not None and row["attr_evidence"] else None,
        "confirmed_tags": confirmed,
        "confirmed_at": row["confirmed_at"] if row is not None else None,
        "tag_source": row["tag_source"] if row is not None else None,
        "note": "suggested 只用于预勾。未确认不构成正式归因。",
    }


def get_suggest(trade_date, code):
    trade_date = _require_date(trade_date, "date")
    code = _require_code(code)
    conn = connect()
    try:
        row = _latest_log(conn, trade_date, code)
        if row is None:
            return {
                "ok": True,
                "status": "no_data",
                "asof": None,
                "generated_at": now_text(),
                "date": trade_date,
                "code": code,
                "name": None,
                "suggested_tags": [],
                "suggested_at": None,
                "suggested_asof": None,
                "evidence": None,
                "confirmed_tags": None,
                "confirmed_at": None,
                "tag_source": None,
                "note": "尚无建议。未确认不构成正式归因。",
            }
        return _suggest_body(row, trade_date, code)
    finally:
        conn.close()


def refresh_suggested(trade_date, code, tags, evidence=None, asof=None, name=None):
    """批任务入口。只写 suggested_* / attr_evidence，不写 confirmed_*。"""
    if "confirmed" in SUGGEST_UPDATE_SQL.lower():
        raise RuntimeError("批任务 SQL 禁止出现 confirmed")
    trade_date = _require_date(trade_date, "date")
    code = _require_code(code)
    tags = tag_list(tags)
    if evidence is not None and not isinstance(evidence, (dict, list)):
        raise StoreError(400, "bad_request", "evidence 必须为 JSON 对象或数组")
    if name is not None and (not isinstance(name, str) or len(name.strip()) > 64):
        raise StoreError(400, "bad_request", "name 必须为不超过 64 字的字符串")
    suggested_at = now_text()
    suggested_asof = suggested_at if not asof else str(asof).strip()
    if len(suggested_asof) > 40:
        raise StoreError(400, "bad_request", "asof 最长 40")
    evidence_text = _dumps(evidence) if evidence is not None else None
    payload = (_dumps(tags), suggested_at, suggested_asof, evidence_text, trade_date, code)
    with _tx() as conn:
        exists = conn.execute(
            "SELECT COUNT(*) AS n FROM decision_log WHERE trade_date = ? AND code = ?",
            (trade_date, code),
        ).fetchone()["n"]
        if exists:
            conn.execute(SUGGEST_UPDATE_SQL, payload)
        else:
            conn.execute(
                """
                INSERT INTO decision_log (
                  trade_date, code, name, ts,
                  suggested_tags, suggested_at, suggested_asof, attr_evidence,
                  r1_a, r3_a, r1_b, r3_b
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending', 'pending', 'pending')
                """,
                (trade_date, code, (name or None), suggested_at,
                 _dumps(tags), suggested_at, suggested_asof, evidence_text),
            )
        row = _latest_log(conn, trade_date, code)
        return _suggest_body(row, trade_date, code)


def confirm(trade_date, code, confirmed_tags, name=None):
    """人工确认。唯一写 confirmed_tags / confirmed_at / tag_source 的入口。

    同日同码再次确认会覆盖确认集（显式人工操作）。不改 suggested_*。
    """
    trade_date = _require_date(trade_date, "date")
    code = _require_code(code)
    tags = tag_list(confirmed_tags)
    if name is not None and (not isinstance(name, str) or len(name.strip()) > 64):
        raise StoreError(400, "bad_request", "name 必须为不超过 64 字的字符串")
    clean_name = name.strip() if isinstance(name, str) and name.strip() else None
    confirmed_at = now_text()
    with _tx() as conn:
        rows = conn.execute(
            """
            SELECT suggested_tags FROM decision_log
             WHERE trade_date = ? AND code = ?
             ORDER BY id DESC
            """,
            (trade_date, code),
        ).fetchall()
        suggested = []
        for row in rows:
            parsed = _loads(row["suggested_tags"], None)
            if parsed is not None:
                suggested = parsed
                break
        source = tag_source(tags, suggested)
        dumped = _dumps(tags)
        if rows:
            conn.execute(
                """
                UPDATE decision_log
                   SET confirmed_tags = ?,
                       confirmed_at = ?,
                       tag_source = ?,
                       attr_tags = ?,
                       name = COALESCE(?, name)
                 WHERE trade_date = ? AND code = ?
                """,
                (dumped, confirmed_at, source, dumped, clean_name, trade_date, code),
            )
        else:
            conn.execute(
                """
                INSERT INTO decision_log (
                  trade_date, code, name, ts,
                  confirmed_tags, confirmed_at, tag_source, attr_tags,
                  r1_a, r3_a, r1_b, r3_b
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending', 'pending', 'pending')
                """,
                (trade_date, code, clean_name, confirmed_at, dumped, confirmed_at, source, dumped),
            )
        row = _latest_log(conn, trade_date, code)
        return {
            "ok": True,
            "status": "ok",
            "asof": confirmed_at,
            "generated_at": now_text(),
            "disclaimer": DISCLAIMER,
            "date": trade_date,
            "code": code,
            "confirmed_tags": tags,
            "confirmed_at": row["confirmed_at"],
            "tag_source": source,
            "attr_tags": tags,
            "suggested_tags": _loads(row["suggested_tags"], []) or [],
            "note": "正式归因已落库。仅记账，不代下单。",
        }


def upsert_auto_top1(trade_date, code, name, asof, score):
    """同日同 code 幂等 upsert。已移除的行可以再入；未移除的手动 source 保持手动。"""
    trade_date = _require_date(trade_date, "trade_date")
    if not code or not isinstance(code, str):
        raise StoreError(400, "bad_request", "watchlist code 不能为空")
    if len(code) > 32:
        raise StoreError(400, "bad_request", "watchlist code 过长")
    score_value = None if score is None else float(score)
    with _tx() as conn:
        conn.execute(
            """
            INSERT INTO watchlist (
              trade_date, code, name, source,
              first_top1_asof, last_top1_asof, last_score, removed_at
            ) VALUES (?, ?, ?, 'auto_top1', ?, ?, ?, NULL)
            ON CONFLICT(trade_date, code) DO UPDATE SET
              name = COALESCE(excluded.name, watchlist.name),
              last_top1_asof = excluded.last_top1_asof,
              last_score = excluded.last_score,
              first_top1_asof = COALESCE(watchlist.first_top1_asof, excluded.first_top1_asof),
              source = CASE
                WHEN watchlist.removed_at IS NOT NULL THEN 'auto_top1'
                WHEN watchlist.source IS NULL OR watchlist.source = '' THEN 'auto_top1'
                ELSE watchlist.source
              END,
              removed_at = NULL
            """,
            (trade_date, code, name, asof, asof, score_value),
        )
        row = conn.execute(
            "SELECT * FROM watchlist WHERE trade_date = ? AND code = ?",
            (trade_date, code),
        ).fetchone()
        return _watch_dict(row)


def _watch_dict(row):
    return {
        "trade_date": row["trade_date"],
        "code": row["code"],
        "name": row["name"],
        "source": row["source"],
        "first_top1_asof": row["first_top1_asof"],
        "last_top1_asof": row["last_top1_asof"],
        "last_score": row["last_score"],
        "removed_at": row["removed_at"],
    }


def list_watchlist(trade_date=None):
    trade_date = _optional_date(trade_date, "date")
    conn = connect()
    try:
        if trade_date:
            rows = conn.execute(
                """
                SELECT * FROM watchlist
                 WHERE trade_date = ? AND removed_at IS NULL
                 ORDER BY last_top1_asof DESC, code
                """,
                (trade_date,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM watchlist
                 WHERE removed_at IS NULL
                 ORDER BY trade_date DESC, last_top1_asof DESC, code
                """
            ).fetchall()
        items = [_watch_dict(row) for row in rows]
        return {
            "ok": True,
            "status": "ok",
            "asof": items[0]["last_top1_asof"] if items else None,
            "generated_at": now_text(),
            "disclaimer": "观察名单不是买入信号，不代下单",
            "date": trade_date,
            "empty": len(items) == 0,
            "items": items,
        }
    finally:
        conn.close()
