# stock-agent-app

蓄势池 / 连板先手的本机 Flask BFF。手机只走 HTTP，不打开 DuckDB，也不打开本机 SQLite。

这个仓库现在有两块：

1. 种子：原来的 `stock-advice-agent` 和 openclaw 的 `liupan_*.py`。
2. 第一切片：5 分钟池级扫描落库、持仓按**股**记账、归因 `suggest` / `confirm`。没有 Flutter。

契约是 `docs/contracts/arch-C-contracts-v0.2.0-C.md`（v0.2.0-C）。本切片怎么对齐、哪些是假设，写在 `docs/delivery/slice-1-bff.md`。

## 硬约束

- 不改先手分公式和权重（`scripts/liupan/` 与工程规格原样放入）。
- 不代下单，不接券商。买卖接口只记账。
- 手机不直连 DuckDB。因子和行情只由本机 BFF / 脚本读。
- 5 分钟服务端扫描不走手机点触的 `GET /api/liupan/momentum`。轨迹 `kind=scan_5min`。
- 批任务只刷新 `suggested_*`，不 `UPDATE confirmed_*`。正式归因只有 `POST /api/liupan/decision_log/confirm`。
- 天量、顶格弱、分时口径以契约为准；本切片还没做这些接口。
- 不在仓库里放密钥、`.auth`、`.secret`、真实行情或账本。

## 目录

| 路径 | 内容 |
| --- | --- |
| `apps/bff/` | Flask `server.py`、`config.py`、`core/`、`scripts/`、`ingest/`、`web/`、`tunnel/` |
| `scripts/liupan/` | `liupan_scan.py`、`liupan_live_scan.py`、`liupan_auction_rank.py`、`liupan_auction_replay.py` |
| `docs/contracts/` | C 契约 |
| `docs/delivery/` | 切片说明、launchd 示例 |
| `docs/连板股与先手分-工程规格-v1.md` | 种子里的工程规格 |

`apps/bff/web/` 仍是种子里的 PWA，不是本切片要做的归因界面。`*.bak*` 没有入库。

脚本找哪份 `liupan_*.py`：`LIUPAN_SCRIPTS`，否则 `OPENCLAW_WORKSPACE/scripts`，否则本机已有的 `~/.openclaw-autoclaw/workspace/scripts`，否则仓库 `scripts/liupan`。

## 需要你自己提供的配置

见 `.env.example`。空着也能测下面的记账三条；真扫盘面才要路径。

| 变量 | 干什么 | 不填会怎样 |
| --- | --- | --- |
| `STOCK_ADVICE_DB` | DuckDB 行情库 | 盘面 / 真 5min 扫描给出 `no_disk`，不写假轨迹 |
| `OPENCLAW_WORKSPACE` 或 `LIUPAN_SCRIPTS` | 扫描脚本及 `qq_snap` 等依赖 | 用上面的查找顺序 |
| `STOCK_ADVICE_AUTH` | 公网登录 `user:password` | 空则本机直连不登录；公网不要空着 |
| `STOCK_ADVICE_SECRET` | session 签名 | 空则写入 `apps/bff/.secret`（gitignore） |
| `STOCK_ADVICE_VAULT` | Obsidian 知识库 | 只影响原来的知识点推荐 |
| `STOCK_ADVICE_PACK_DIR` | 增量包目录 | 只影响导入台 |
| `DUCK_TMP` | DuckDB 溢写目录 | 用 `config.py` 里的默认外盘路径 |

## 本地启动

```bash
cd apps/bff
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

浏览器打开 `http://127.0.0.1:8770`。

没有 parquet 缓存时，进程仍会听 8770，日志里会写行情缓存未就绪。`/api/liupan/pool` 这类盘面接口此时不可用。持仓和归因只依赖 sqlite，第一次调用时在 `apps/bff/data/liupan.sqlite` 建表（该目录已 gitignore）。

自测（不读行情库）：

```bash
cd apps/bff
python3 scripts/selftest_liupan_slice.py
```

## 手工测三条持仓 API

本机直连不需要登录。单位是股。响应里有「仅记账，不代下单」。

```bash
# 买入 100 股
curl -sS -X POST http://127.0.0.1:8770/api/liupan/positions/buy \
  -H 'Content-Type: application/json' \
  -d '{"code":"000001.SZ","name":"平安银行","price":10.5,"qty":100}'

# 卖出超过持仓 → HTTP 422，error.code = qty_exceeds_position
curl -sS -D - -o /tmp/sell.json -X POST http://127.0.0.1:8770/api/liupan/positions/sell \
  -H 'Content-Type: application/json' \
  -d '{"code":"000001.SZ","price":11,"qty":100000}'
cat /tmp/sell.json

# 列表。没有 qty>0 的持仓时 empty=true
curl -sS http://127.0.0.1:8770/api/liupan/positions
```

可选幂等：加上请求头 `Idempotency-Key: <你的键>`，或在 JSON 里放 `client_req_id`。不传就不是幂等。

归因（预勾不是正式归因）：

```bash
cd apps/bff
python3 scripts/refresh_suggested.py --date 2026-11-04 --code 000001.SZ \
  --tags '["竞价高开"]' --evidence '{"note":"批任务示例"}'

curl -sS 'http://127.0.0.1:8770/api/liupan/decision_log/suggest?date=2026-11-04&code=000001.SZ'

curl -sS -X POST http://127.0.0.1:8770/api/liupan/decision_log/confirm \
  -H 'Content-Type: application/json' \
  -d '{"date":"2026-11-04","code":"000001.SZ","confirmed_tags":["竞价高开"]}'
```

5 分钟扫描的安装方式见 `docs/delivery/launchd-liupan-5min.md`。非交易时段直接跑会跳过，不会写一条假轨迹：

```bash
cd apps/bff
python3 scripts/liupan_5min_append.py
```
