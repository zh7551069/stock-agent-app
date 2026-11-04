# 5 分钟池级扫描 · launchd 对接

本页只说明怎么接。仓库不执行 `launchctl load`，也不代下单。

## 行为

`apps/bff/scripts/liupan_5min_append.py`：

1. 周末或非交易时段直接退出 0，打印 `[skip] weekend` 或 `[skip] out_of_window`。时段是上海时区周一至周五 09:30–11:30、13:00–15:00，与 `liupan_live_scan._in_trade_window` 一致。
2. 拿 `apps/bff/data/liupan_5min.lock`。上一轮还在跑就打印 `[skip] overlap`，不写节点（避免两轮叠在一起）。
3. 跑 `liupan_auction_rank.py --tier 锐利 --live --asof now --json`。`--live` 会再调 `liupan_live_scan.py` 做池级扫描，然后按现有先手分排序。这里不请求手机用的 `GET /api/liupan/momentum`。
4. 只有 stdout 是 JSON 且 `status=ok` 才 append 一行到 `data/liupan_trace.jsonl`。`no_disk` / `no_data` / 超时 / 非 JSON 都不写。
5. `buy[0]` 有代码才 upsert 观察列表 `source=auto_top1`（同日同 code 去重）。没有可买榜就不编造 top1。

成功日志形如：

```text
[append] ts=2026-11-04 10:05:00 top1=600000.SH score_adj=0.61 replay=False
```

没有首选但扫描成功：

```text
[append] ts=2026-11-04 10:05:00 top1=null replay=False
```

轨迹可用现成接口看：

```bash
curl -sS 'http://127.0.0.1:8770/api/liupan/trace?kind=scan_5min&limit=20'
```

## 本机准备

真扫需要：

- `OPENCLAW_WORKSPACE` 指向含 `qq_snap` 等依赖的 openclaw 工作区，或把那些依赖和 `scripts/liupan` 放在脚本能 import 的位置。仓库里的 `scripts/liupan/liupan_*.py` 是副本，不含行情库。
- `STOCK_ADVICE_DB` 指向本机 DuckDB。手机不要连这个文件。
- 时区 `Asia/Shanghai`。

不在交易时段可以看跳过，不会写假节点：

```bash
cd apps/bff
python3 scripts/liupan_5min_append.py
```

盘中要强制跑一轮真实扫描（仍然只有 `status=ok` 才落库）：

```bash
python3 scripts/liupan_5min_append.py --ignore-window
```

`--input` 只用于把一份已经生成的扫描 JSON 再放进库，行上会有 `replay=true`。launchd 不要加这个参数。

## launchd

示例 plist：`docs/delivery/com.stock-agent.liupan-5min.plist.example`。

把里面的绝对路径改成自己的检出和 python，复制到 `~/Library/LaunchAgents/com.stock-agent.liupan-5min.plist` 后再 `launchctl bootstrap`。`StartInterval` 300 秒即可，时段和互斥由脚本负责。

卸掉：

```bash
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.stock-agent.liupan-5min.plist
```

旧规格里的实时池是每 10 分钟。本契约是约每 5 分钟，plist 按 300 秒写。先手分公式没有为了改频率而改动。
