# 蓄势池 App · C 完整交付材料（接口契约 + UI 约束 + UI 技术评审清单）

| 项 | 值 |
| --- | --- |
| 性质 | **C 契约已放行**（辉本人当面确认「确认进入完整交付」） |
| 放行 | **2026-11-04**（Asia/Shanghai）· 架构师对话当面确认 |
| 架构版本旁注 | **v0.2.0-C**（契约已放行；主方案硬约束不变） |
| 依据 | PRD [v1.4/v1.5](https://app.notion.com/p/40df544e9e7b81a0b42dd83b01319408) §9 + 架构 [Notion](https://app.notion.com/p/40df544e9e7b8103be0ad19b17a1b64e) |
| 已拍板并入 | ① 补归因建议 UI 帧（设计师进行中）② 可写业务码（**合码仅辉**）③ 持仓单位=**股** |
| 契约归属 | 架构师；全栈不得私改路径/错误码/分页语义不申报 |
| 业务对接 | stocker |

> **放行范围**：本文件为正式 C 契约。**后端无 UI 切片可开工**。归因建议 UI：定稿帧已评（见 `/workspace/liupan-ui-tech-review-attr-20261104.md` 与 Notion 文末附录）**有条件通过** → 该切片前端可开工（须落实必改 35:194/35:98）；其它已有定稿帧仍须按切片做三行留痕。合码仍仅辉授权。
>
> 注：放行瞬间曾保留「帧未齐则禁前端」闸门；同日评审完成后闸门按三行留痕结果更新为有条件通过。

---

## 1. 范围与硬约束

### 1.1 范围内（P0，对齐 PRD §9 + 架构 §4/§5/§8）

- Flask BFF `stock-advice:8770` 前缀 `/api/liupan/*`：池/榜/详情/状态、复盘日/周、decision_log、持仓买卖与列表、点触分时/日K 动量、观察列表、因子列表、归因 suggest/confirm。
- 同机 SQLite：`decision_log`、`positions`、`watchlist`（或关注表+source）、`factors_daily` 物化。
- launchd 盘中约每 **5** 分钟池级扫描 **append** `liupan_trace`；快照 top1 → `source=auto_top1` 入观察（去重）。
- Flutter 五底 Tab：蓄势池 / 先手榜 / 复盘 / 持仓 / 设置；观察为**二级页**；详情买卖弹框仅记账。

### 1.2 硬约束（不可改；否定项见 PRD §9.3）

| # | 约束 |
| --- | --- |
| H1 | **不改**先手分公式/权重；三维风过 E 前只旁路 |
| H2 | **不代下单**、不对接券商执行、不自动改仓、不因动量/天量/auto_top1 自动卖出 |
| H3 | 手机**不直连** DuckDB；只 HTTP→BFF；因子读每日物化 SQLite |
| H4 | 归因 = `suggested` 预勾 + `confirmed` 人工确认；**禁覆盖已确认**；批任务禁 `UPDATE confirmed_*`；正式归因须确认后落库 |
| H5 | 5min 服务端扫描与手机点触动量**勿混**；禁手机静默轮询冒充实盘 |
| H6 | 天量警示换手 **>20%**；**禁与 >14% 混用** |
| H7 | 顶格弱判定基准 = **B（T+1 开）**；pending 收益禁填 0% |
| H8 | 分时 v1 = S1 + 站均价 + 异动五条；日K = S2 摘要，文案标明 **≠先手分、不可比** |
| H9 | 缺数状态机显性（`no_disk`/`no_data`/`stale`/…）；禁静默空池 |
| H10 | 持仓数量单位 = **股**（本拍板；UI 标明「股」；BFF `qty` 存股） |

### 1.3 明确不做

短信/券商/自动下单、改脚本权重由 App 触发、RAG、三维风进主排序、伪造 trace/动量、持仓动量静默轮询。

---

## 2. 接口契约表（已放行）

约定：

- Base：`http(s)://{host}:8770`，路径均相对 `/api/liupan`。
- 统一响应外壳（只读策略类）：至少含 `status`、`asof`、`generated_at`；动量类另含 `cost_ms`。
- `code` 对外：**后缀式**（与架构一致）；内部 JOIN 跟脚本铁律（不在此编造四分细节以外的新规则）。
- 分页：列表类未在 PRD/架构钉死默认 page size 者标 **TODO**；建议草稿默认 `limit≤100`，超出 `400`。
- 幂等：注明；未决标 TODO。
- 错误码：HTTP + 可选 `error.code` 字符串；下列为已放行契约；落地时可映射现有 Flask 习惯但**不得无申报删改语义**。

### 2.1 只读 · 池 / 榜 / 详情 / 状态

| 方法 | 路径 | 入参 | 出参要点 | 错误码 | 分页 | 超时 | 幂等 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/status` | — | 盘挂载、最新池/榜 as-of、`stale_days` | `503`/`no_disk` 等按 status | 无 | ≤2s | 是 |
| GET | `/pool` | `tier?` `mcap=50-150?` | tiers、pool 摘要、score、tag、价位块；`status` | 缺盘透传 status，非假空列表 | TODO 若超长 | 池 ~3s 级 | 是 |
| GET | `/rank` | `mode=auction\|intraday` `tier?` `mcap?` | buy/locked、水位门、hot；**每条顶格占比**（缺失→`null`） | 同上 | TODO | 盘中可达数十秒；须 as-of | 是 |
| GET | `/detail` | `code` `kind?` `asof?` | 分数拆解、`limit_pct`、关键字段、来源 | `400` 缺 code；`404` 无票 | 无 | ≤5s 目标 | 是 |
| GET | `/advice_gate` | `code` | 只读闸门文案；**不代执行** | `400` | 无 | ≤3s | 是 |
| GET | `/momentum` | `tier?` `refresh?` | 池级先手重排（既有）；TTL≈45s | 超时 `504`/`status=error` | 无 | 上限 300s | 是（refresh 强制刷新语义既有） |

### 2.2 复盘 / 轨迹

| 方法 | 路径 | 入参 | 出参要点 | 错误码 | 分页 | 超时 | 幂等 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/review/day` | `date`（交易日） | 票列表；基准 A/B；pending；**时间线**每 ~5min 节点：`ts/asof` + `top1.{code,name,score_adj}`（PRD 验收至少 name+score；架构含 code/score_adj）；缺失节点空态、**禁伪造** | `400` 日期非法；无数据 `status=no_data` | 时间线按全日节点；票列表 TODO | ≤5s 目标（索引后） | 是 |
| GET | `/review/week` | `end?` | 近 5 交易日；归因分布（建议以 **confirmed** 统计）；建议议题只读 | 同上 | 无/固定 5 日 | ≤5s | 是 |
| GET | `/trace` | `date` | 只读轨迹摘要 | 同上 | TODO | ≤5s | 是 |

### 2.3 持仓记账（单位=股）

| 方法 | 路径 | 入参 | 出参要点 | 错误码 | 分页 | 超时 | 幂等 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| POST | `/positions/buy` | JSON：`code`（必）、`name?`、`price`>0、`qty`>0（**股**）、`ts?`、`note?`、可选 `trade_date/source_kind/asof/tier/...` 关联字段 | 写 `decision_log`（`side=buy`）+ 更新 `positions`（加权成本）；返回持仓快照行 | `400` 校验失败；`409` 冲突（若启用幂等键） | 无 | ≤2s | **建议** `Idempotency-Key` 或客户端 `client_req_id`（**TODO** 放行后钉死）；无键则非幂等 |
| POST | `/positions/sell` | 同上；`qty`≤当前持仓（股） | 写 `side=sell` + 减仓/清仓 | `400`；**超量 → `422`/`qty_exceeds_position`**（可测） | 无 | ≤2s | 同上 TODO |
| GET | `/positions` | — | 列表（默认 `qty>0`）；空 → `empty=true`（或等价）供显性空态 | — | 无（自用仓位数少）；若加分页 TODO | ≤1s | 是 |

边界旁注（响应/UI）：**仅记账，不代下单**。

### 2.4 点触动量（禁静默轮询）

| 方法 | 路径 | 入参 | 出参要点 | 错误码 | 分页 | 超时 | 幂等 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/momentum/intraday_path` | `code` 必填 | **v1**：S1 + 站均价 + 异动五条（可与七要素并存）+ `ok/asof/cost_ms`；可含天量旁路字段（换手>20%） | `400`；失败显性 `error`，**禁假数据** | 无 | 秒级～15s+；上限 TODO（建议 ≤60s） | 是；短缓存须标 as-of |
| GET | `/momentum/daily_k` | `code` 必填 | **S2 摘要** + `ok/asof/cost_ms`；文案侧强制 ≠先手分 | 未实现可 `501`/`not_implemented`（架构曾述）；禁假数据 | 无 | 本地截面目标 &lt;2s | 是 |

### 2.5 观察列表

| 方法 | 路径 | 入参 | 出参要点 | 错误码 | 分页 | 超时 | 幂等 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/watchlist` | `date?` | 列表项含 `code/name/source`（`auto_top1`\|手动等）、首次/最近 top1 as-of 与 score | — | TODO | ≤1s | 是 |
| POST | `/watchlist` | `code`；手动关注 | 写入/标记手动源 | `400` | 无 | ≤1s | 建议按 code upsert |
| PATCH/DELETE | `/watchlist`（或 `/watchlist/{code}`） | 移除 | 用户移除；同日再 rank=1 **可再入**（PRD 默认） | `404` | 无 | ≤1s | 移除自身幂等 |

服务端自动：`auto_top1` 由扫描落盘路径写入，**非**客户端调用下单；同日同 code **去重**优先幂等 upsert（对齐 PRD §9.15 / 架构旁注）。

### 2.6 因子列表

| 方法 | 路径 | 入参 | 出参要点 | 错误码 | 分页 | 超时 | 幂等 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/factors` | `code` 或 `codes`≤50；`date`；`group=tech\|valuation\|event\|all` | `status/asof/generated_at/cost_ms/factors[]/stale_days?/warnings`；读 **SQLite 物化**优先 | 回源失败 `stale`/`error`，不假数据 | codes 批量上限 50 | 物化 &lt;200ms 目标；DuckDB 回源 ≤5s | 是；TTL≈300s（date+code） |

### 2.7 decision_log 与归因

| 方法 | 路径 | 入参 | 出参要点 | 错误码 | 分页 | 超时 | 幂等 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/decision_log` | `date?` `code?` | CRUD 读；含 suggested/confirmed 字段（见 §3） | — | TODO | ≤2s | 是 |
| POST/PATCH | `/decision_log` | 复盘卡手填字段、note、watched 等 | **不得**借此静默把 suggested 写成 confirmed | `400` | 无 | ≤2s | TODO |
| GET | `/decision_log/suggest` 或详情内嵌 | `date` `code` | 返回 `suggested_tags` + evidence（预勾用）；批任务主写盘后 | — | 无 | ≤2s | 是 |
| POST | `/decision_log/confirm` | `date` `code`；`confirmed_tags[]`（可从 suggested 改标）；显式确认 | 写 `confirmed_tags`/`attr_tags`/`confirmed_at`/`tag_source`；**唯一**正式归因落库入口之一 | `400`；禁批任务调此逻辑覆盖 | 无 | ≤2s | 同日同码重复确认 = 覆盖确认集（人工显式）；**TODO** 是否要 version |

> 路径名 `suggest`/`confirm` 若工程更想嵌在 `PATCH /decision_log` 的 `action=confirm`，属等价，**放行后首切片内钉死一种并申报**；语义不得弱化「未确认不落正式归因」。

### 2.8 契约缺口（TODO · 不编造）

1. 精确 HTTP 错误码表与现有 Flask 是否统一 JSON envelope。  
2. `Idempotency-Key` 是否强制（买卖）。  
3. watchlist 与「标记关注」同源表 vs 分表。  
4. positions 清仓后删行 vs 保留 `qty=0`。  
5. 顶格占比响应字段名：`cap_ratio` vs `open_clip_share`（架构待工程确认）。  
6. S1 / 站均价 / 异动五条 / S2 的 JSON 字段级 schema（规则已拍，字段名跟脚本/`intraday_path` 输出对齐，**TODO** 挂 OpenAPI）。  
7. `/decision_log/suggest|confirm` 最终路径拼写。  
8. 列表分页默认 `limit/offset` 或 cursor。

---

## 3. Schema 增量要点（SQLite · 单位=股）

### 3.1 `decision_log`（复盘卡 + 买卖流水；可同表或分表，须按 `trade_date+code` 关联 trace）

| 字段 | 说明 |
| --- | --- |
| trade_date, code, name | 关联键核心 |
| source_kind, tier, asof, rank, score, score_adj | 可选钉快照 |
| side | `buy` \| `sell`（弹框流水） |
| price, qty, ts | qty=**股**；price>0；ts 提交时间 |
| note, watched | 手填 |
| r1_a/r3_a/…, r1_b/… | 复盘 A/B；未入库=`pending` |
| attr_tags | 展示用正式标签（由 confirmed 派生或等同） |
| suggested_tags, suggested_at, suggested_asof | 引擎/批任务可刷新 |
| confirmed_tags, confirmed_at | **仅人工确认 API** |
| tag_source | `manual` \| `suggested_accepted` \| `mixed` |
| attr_evidence | 建议证据 JSON |

批任务：**禁止** `UPDATE confirmed_*`。

### 3.2 `positions`

| 字段 | 说明 |
| --- | --- |
| code, name | 标的 |
| qty | 当前自记数量，单位 **股** |
| avg_cost | 自记成本价 |
| updated_at | 最近买卖 ts |

买卖与流水**同事务**（或等价顺序）更新。

### 3.3 `watchlist`（名可合并关注表）

| 字段 | 说明 |
| --- | --- |
| code, name | |
| source | `auto_top1` / 手动等 |
| first_top1_asof, last_top1_asof, last_score | 可追溯 |
| removed_at? | 用户移除；再 top1 可再入 |

同日同 code auto 入列：**幂等 upsert**（验收去重）。

### 3.4 `factors_daily`（每日物化）

按架构专章 B：日截面从 DuckDB 物化；BFF 优先读此表；含 `stale_days` 可推导字段；口径门禁（代码四分、tech 绝对价禁用等）进 selftest，不在此扩造。

---

## 4. UI 约束（对客户端）

| # | 约束 |
| --- | --- |
| U1 | 底 Tab **恰 5**：蓄势池/先手榜/复盘/持仓/设置；**lucide** 图标；**全站直角**（对齐 UI 探路） |
| U2 | **观察不进底 Tab**；二级页；旁注「≠买入信号 · 不代下单」 |
| U3 | 池/榜/复盘：`status` 显性；禁静默空池；`pending` 禁显示 0% |
| U4 | 顶格占比必露；缺失→「字段缺失」 |
| U5 | 买卖弹框：价格>0、数量>0（**股**）；旁注「仅记账 · 不代下单」；超量卖出展示拒绝 |
| U6 | 持仓空态显性（非空白闪烁）；列表单位文案「股」 |
| U7 | 分时/日K：**仅点触**请求；加载态 + **as-of**；**禁**后台静默轮询；失败显性错误 |
| U8 | 日K/动量文案：**动量分 ≠ 先手分、不可比排序**；动量结果 ≠ 卖出指令 |
| U9 | 天量：换手>20% 警示；勿出现 >14% 文案口径 |
| U10 | 复盘时间线：5min 节点 top1；缺失空态不伪造 |
| U11 | 归因：建议预勾可见；**确认前不展示为已落库正式归因**；确认/改标后才正式写入 |
| U12 | 免责含 Top5 短线追入回测负期望；条件单仅文案 |
| U13 | 设置可配 Base URL；`no_disk` 挂载指引 |

---

## 5. UI 技术评审 · 三行留痕模板 + 归因帧检查项

### 5.1 三行留痕模板（定稿 UI 每帧/每包必填；未通过 → **禁止前端开工该切片**）

```
【UI 技术评审】帧/包：{Figma node}｜评审人：架构｜日期：YYYY-MM-DD（Asia/Shanghai）
1) 结论：通过 / 有条件通过 / 不通过
2) 数据与接口：所需 API/字段/状态机是否已在契约内；缺口=…
3) 风险与约束：空态/加载/as-of/禁轮询/单位股/≠先手分/不代下单等是否可实现；阻塞=…
```

### 5.2 已有可评帧（持仓/买卖/观察/复盘主路径 · 设计已验收者）

可对下列做评审留痕（业务帧已存在，C 已总放行；各切片仍须三行留痕）：详情 `1:12`；买 `14:9`；卖 `14:38`；持仓 `14:67`/`14:192`；动量 `14:231`/`14:267`；复盘 `8:61`/`8:80`/`8:99`/`8:118`/`8:137`；观察 `17:103`/`18:9`；TabBar 探路 `14:313`+lucide。

### 5.3 归因建议 UI · 待设计补齐检查项（**帧未到 = 待帧**）

目标帧（设计师进行中）：高保真「建议预勾 + 确认/改标 + 证据行」（复盘卡 `8:99` 已有基底；独立增量帧待补）。

检查项：

1. 建议标签预勾态 vs 已确认态视觉区分是否足够（防误读为已落库）。  
2. 确认/改标 CTA 是否显式（ steals 不自动确认）。  
3. 证据行是否只读、是否暴露 `suggested_asof`。  
4. 未确认时 decision 正式归因区是否为空/占位（对齐「未确认不落库」）。  
5. 与顶格虚高、基准 B 文案是否冲突。  
6. 是否引入新 IA/新接口需求（若有 → 须架构再评）。

**归因建议前端**：定稿帧已 **有条件通过**（必改：35:194 已确认角标/标题；35:98 文案与 CTA 一致）→ **可开工该切片**（落实必改）。详见 `/workspace/liupan-ui-tech-review-attr-20261104.md`。  
**后端无 UI**：第一切片（见 §6.2）可立即开工。其它已定稿帧：前端须完成该切片三行留痕通过后再开工。

---

## 6. 开放问题 / 第一切片与实施顺序（已放行）

### 6.1 开放问题

| ID | 问题 | 备注 |
| --- | --- | --- |
| O1 | ~~辉是否确认本 C 草稿生效~~ | **已确认** 2026-11-04 当面「确认进入完整交付」 |
| O2 | 归因建议独立帧交付与评审日程 | **已评有条件通过**；前端可开工（落实必改） |
| O3 | PRD 页版本升 v1.5 正文是否已写入三拍板 | 以 PRD 页为准；拍板已并入本契约 |
| O4 | §2.8 契约 TODO 清单钉死 | 契约归架构；首切片内申报 |
| O5 | launchd 5min 与单次扫描耗时互斥 | 架构已建议互斥/跳过 |
| O6 | OpenAPI 是否本阶段产出 | 建议与 Flutter 模型生成绑定 |

### 6.2 第一切片边界（给全栈 · 原文）

**可立即开工（后端 / 无 UI 依赖）**：

1. **5min 扫描落库**：launchd 交易时段约每 5 分钟池级 live；结果 **append** `liupan_trace`/日志库；成功完成才写节点；建议互斥防重叠。  
2. **持仓单位=股**：`POST /positions/buy|sell`、`GET /positions`；`qty` 存**股**；买卖写 `decision_log` + 更新 `positions`；**不代下单**。  
3. **归因 suggest+confirm 落库**：批任务/引擎只写 `suggested_*`；`POST .../confirm`（或等价）人工确认写 `confirmed_*`；**禁覆盖已确认**；批任务禁 `UPDATE confirmed_*`。

**硬约束（本切片亦不可破）**：不改先手分公式/权重；不代下单；手机不直连 DuckDB；不自动改仓；不因动量/auto_top1 自动卖出。

**前端**：归因建议切片按 UI 技术评审**有条件通过**可开工（落实必改 35:194/35:98）。其它前端切片须各自三行留痕通过。

### 6.3 建议实施顺序（合码仅辉）

1. SQLite schema 迁移（decision_log 增量、positions qty=股、watchlist、factors_daily）+ 口径 selftest。  
2. launchd 5min 对齐 + append trace + auto_top1 upsert。  
3. BFF：positions buy/sell/list → review/day timeline → watchlist → momentum 点触 → factors → suggest/confirm。  
4. Flutter：已评审帧对齐（池/榜/持仓/观察）→ 点触动量纪律 → 复盘时间线。  
5. 归因建议前端按有条件通过实现确认交互（同步设计改 35:194/35:98）。  
6. §9 验收用例全绿；合码 PR 仅辉授权。

---

## 7. 文档状态

- 本地正文：本文件 `/workspace/liupan-arch-C-contracts-20261104.md`（正式放行版；草稿副本仍保留 `…-draft-…` 仅作历史）。  
- Notion：架构页附录「**C 契约已放行**」；版本旁注 **v0.2.0-C**。  
- 关联：`/workspace/liupan-arch-prd-v14-align-20261104.md`、`/workspace/liupan-arch-eval-factors-20261103.md`。
