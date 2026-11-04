# 第一切片交付说明（BFF，无 UI）

对照 `docs/contracts/arch-C-contracts-v0.2.0-C.md` §2.3、§2.7、§6.2。合码由架构师评审，本文件只申报落地选择和缺口。

## 路径钉死

| 用途 | 方法 | 路径 |
| --- | --- | --- |
| 买入记账 | POST | `/api/liupan/positions/buy` |
| 卖出记账 | POST | `/api/liupan/positions/sell` |
| 持仓列表 | GET | `/api/liupan/positions` |
| 读流水 | GET | `/api/liupan/decision_log` |
| 归因预勾 | GET | `/api/liupan/decision_log/suggest` |
| 正式归因 | POST | `/api/liupan/decision_log/confirm` |
| 观察只读 | GET | `/api/liupan/watchlist` |

没有采用 `PATCH /decision_log?action=confirm`。批任务不走 HTTP，入口是 `apps/bff/scripts/refresh_suggested.py`。它只执行 `SUGGEST_UPDATE_SQL`（`suggested_tags` / `suggested_at` / `suggested_asof` / `attr_evidence`）。

5 分钟扫描不打 `GET /api/liupan/momentum`。入口是 `apps/bff/scripts/liupan_5min_append.py`（`scripts/liupan/run_5min_append.py` 只做转发）。轨迹 `kind=scan_5min`，`source=server_5min`。

## 假设（契约写着 TODO 或没写死的）

1. 幂等键可选：请求头 `Idempotency-Key` 或 JSON `client_req_id`。有键则同一笔不重复记账；无键则非幂等。契约 §2.8 尚未强制。
2. 清仓保留 `positions.qty=0` 行。`GET /positions` 只返回 `qty>0`，没有持仓时 `empty=true`。
3. `qty` 必须是正整数股。`avg_cost` 四舍五入到 4 位小数。卖出不改成本，只减股数。持仓为 0 后再买，按新成交价重开成本，不和 0 股混合。
4. 收益字段 `r1_a` / `r3_a` / `r1_b` / `r3_b` 写入时固定为 `pending`，不填 `0`。本切片不算 T+N。
5. 归因标签词表和证据规则契约没有给。本切片不从行情编造 `suggested_tags`。批任务传入什么就存什么；没有记录时 GET 返回 `suggested_tags: []` 且 `status=no_data`。
6. `tag_source`：确认集与建议集相等 → `suggested_accepted`；有交集但不相等 → `mixed`；无交集、无建议、或确认集为空 → `manual`。比较忽略顺序。
7. `attr_tags` 与 `confirmed_tags` 写同一份，给展示用。
8. 人工再次 `confirm` 覆盖该日该代码的确认集，并刷新 `confirmed_at`。不改 `suggested_*`。
9. 同一 `trade_date+code` 的新买卖流水会带上已有的 suggested/confirmed，避免同日拆成两套归因。这不是批任务去 `UPDATE confirmed_*`。
10. `decision_log` 列表默认最多 200 行，多了带 `truncated=true`。分页形式未钉。
11. 5 分钟节点的 top1 = 扫描 JSON 里 `buy[0]`（脚本已按 `score_adj` 排序）。水位门未过时脚本文案是「不出首选」，这里仍把排名第一记入观察，观察不是买入。`buy` 为空则 `top1=null`，不写观察列表。
12. 观察去重键是扫描发生的上海日历日 + code，不是因子表 asof。`first_top1_asof` / `last_top1_asof` 用落库时间。扫描器原始 `asof` 留在轨迹记录里。
13. 已移除的观察行，同日再次成为 top1 时以 `source=auto_top1` 再入。还在列表上的 `manual` 不被改成 `auto_top1`，只更新最近一次 top1 时间与分数。
14. 交易时段对齐 `liupan_live_scan._in_trade_window`：周一到周五 09:30–11:30、13:00–15:00，时区 Asia/Shanghai。周六日跳过。法定休市日没有日历，不编造；那天若真扫到，失败状态不会落库。
15. 重叠运行用 `data/liupan_5min.lock` 非阻塞锁，拿不到就打印 `[skip] overlap` 并且不写节点。
16. `--input` 只用于回放一份已经产出的扫描 JSON，记录带 `replay=true`。`--now` 只能和 `--input` 一起用。launchd 示例两者都不用。
17. 脚本目录：`LIUPAN_SCRIPTS` → `OPENCLAW_WORKSPACE/scripts` → `~/.openclaw-autoclaw/workspace/scripts`（目录存在才用）→ 仓库 `scripts/liupan`。先手分公式和权重没有改。
18. 新接口的错误是 HTTP 状态 + `error.code`。种子里旧的 `/api/liupan/pool` 等仍是原来的 `{ok, error: 字符串}`。没有强行统一。
19. 行情缓存不在时，`server.py` 仍监听 `:8770`，方便空库测记账。盘面接口该失败还是失败。

## 本切片明确没做

- Flutter / 归因 UI
- 手动观察的 POST / PATCH / DELETE
- `POST/PATCH /decision_log` 手填复盘卡（避免有第二条路把 suggested 写成 confirmed）
- `GET /review/day` 的 5 分钟时间线接口（节点已经可以 append；现有 `GET /api/liupan/trace?kind=scan_5min` 能读到）
- `factors_daily`、点触分时 / 日 K、`/status` `/rank` `/detail`
- OpenAPI、分页游标、顶格占比字段名（`cap_ratio` 还是 `open_clip_share`）
- 券商、下单、改先手分

`scripts/liupan/liupan_scan.py --selftest`（11/11）和 `liupan_live_scan.py --selftest`（26/26）在本环境通过。`liupan_auction_rank.py --selftest` 跑完排序用例后会 `import qq_snap`，仓库里没有这个模块，干净检出上会在这里 `ModuleNotFoundError`。脚本正文没有改。
