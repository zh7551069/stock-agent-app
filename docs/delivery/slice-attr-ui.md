# 归因建议 UI 切片（Flutter）

对照 `docs/contracts/arch-C-contracts-v0.2.0-C.md` §2.7、U11、H4，以及 `docs/delivery/slice-1-bff.md` 钉死的路径。本文件只覆盖 `apps/mobile`。`apps/bff/web` 仍是种子 PWA，不是这个切片的产品界面。

## 路径

| 用途 | 位置 |
| --- | --- |
| 工程 | `apps/mobile` |
| 入口 | `apps/mobile/lib/main.dart` |
| 五栏壳 | `apps/mobile/lib/pages/shell_page.dart` |
| 归因页 / 复盘卡 | `apps/mobile/lib/pages/attr_surface.dart` |
| 改标 sheet | `apps/mobile/lib/widgets/retag_sheet.dart` |
| 离开拦截 | `apps/mobile/lib/widgets/leave_dialog.dart` |
| 状态机 | `apps/mobile/lib/state/attr_controller.dart` |
| HTTP | `apps/mobile/lib/api/http_decision_log_client.dart` |

深链：`/attr?date=YYYY-MM-DD&code=688185.SH&name=康希诺`。`code` 也可以只给 6 位，客户端按后缀式补全后再请求（6/9→`.SH`，0/3→`.SZ`，4/8→`.BJ`）。页头按帧显示 6 位。

底栏五栏：候选 / 先手榜 / 复盘 / 持仓 / 设置。复盘是本切片的业务入口，其余是壳。设置里可以改本次运行的 BFF 地址。

## 接口

Base 默认 `http://127.0.0.1:8770`。编译期覆盖：

```bash
flutter run --dart-define=BFF_BASE=http://127.0.0.1:8770
# 或
flutter run --dart-define=LIUPAN_BFF_BASE=http://10.0.2.2:8770
```

| 动作 | 方法 | 路径 |
| --- | --- | --- |
| 读预勾 | GET | `/api/liupan/decision_log/suggest?date=&code=` |
| 正式落库 | POST | `/api/liupan/decision_log/confirm` |

POST JSON 只有 `date`、`code`、`confirmed_tags`，有名称时再加 `name`。不传分数、数量、买卖方向。

`status=no_data` 或 `suggested_tags: []` 时不编造预勾。`confirmed_tags == null` 视为还没确认；已经是数组（含空数组）则按已确认展示。

## 本地连上 BFF

先起第一切片的 BFF（归因不读行情库）：

```bash
cd apps/bff
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 server.py --no-watch
```

给康希诺写一条预勾（这一步是批任务，只写 `suggested_*`）：

```bash
cd apps/bff
python3 scripts/refresh_suggested.py --date 2026-11-03 --code 688185.SH \
  --name 康希诺 \
  --tags '["选股对但择时错","先手分虚高(顶格)"]' \
  --asof '2026-11-03 15:00:00' \
  --evidence '{"from":"frame"}'
```

再跑 App：

```bash
cd apps/mobile
flutter pub get
flutter run --dart-define=BFF_BASE=http://127.0.0.1:8770
```

Android 模拟器访问宿主机用 `http://10.0.2.2:8770`。清单里开了明文 HTTP，因为默认地址是 `http://`。iOS 同样放行了本地与任意 HTTP，方便指向局域网 BFF。

自测：

```bash
cd apps/mobile
flutter analyze
flutter test
```

`flutter analyze` 本次无 error、无 warning。`flutter test` 覆盖：改标不发 confirm、确认才 POST、连点只发一次、确认失败不改成已写入、`no_data` 不编造标签、成功后再改标必须再确认一次、离开拦截放弃不写库、请求体字段、基准不显示 `0%`。

## 对照帧

Figma 文件 `2aHDiixK3G20NfZDIjRxzx`。

| 帧 | 界面 | 实现 |
| --- | --- | --- |
| `35:2` | 主态 | 副标「归因建议 · 须确认写入」。建议标签带「建议」角标和勾。蓝框「须确认后写入 · 不自动落库」。状态「未写入决策记录」。CTA「确认写入」。 |
| `35:98` | 改标 | 副标「归因建议 · 改标中」。旁注「仅改本地预勾 · 不写决策记录 · 写库只走页底「确认写入」（一次确认）」。CTA「应用改标」，只改本地勾选。 |
| `35:194` | 写入成功 | 副标「归因建议 · 已确认」。绿条「归因已写入决策记录」。区块「归因已确认」。标签去掉「建议」角标。主 CTA「已确认写入 ✓」，再点不会重发。 |
| `35:290` | 未确认离开 | 副标「归因建议 · 离开拦截」。对话框「尚未确认写入」。返回确认留下；放弃并离开不 POST。 |
| `8:99` | 复盘卡增量 | 复盘列表点进「康希诺 688185」。副标「单票复盘 · 日期」。标题旁「未确认」角标。同一套改标 / 确认写入。未确认时不显示「未写入决策记录」那一行，和帧一致。 |

全页证据行可展开，只读，露出 `suggested_asof`。复盘卡按 `8:99` 不放这一行。

## 硬约束

1. 正式归因只有页底「确认写入」会 POST `/decision_log/confirm`。同一次点击只发一次。
2. 「应用改标」和「关闭 / 取消」不调 confirm。
3. 打开页面、回放、离开，都不会自动落库。
4. 成功态文案是「已确认」，不再挂「建议 / 预勾 · 须确认」。
5. 已经确认过的，再改勾选后必须再点一次「确认写入」。同日同码覆盖确认集，和 BFF 一致。
6. 不改先手分。分数区只读，不进 confirm 请求。
7. 不代下单。本切片没有买卖按钮。持仓壳写明单位是股。
8. 基准 A/B 固定显示 `pending`，脚注「禁止显示 0%」。不会把 `0` 或 `0%` 画出来。

## 假设（契约和帧没钉死的）

1. 词表（选股对但择时错、先手分虚高(顶格)、板块共振有效等）来自定稿帧，用来改标。预勾只来自 `suggested_tags`。接口多出来的标签会加在词表后面，不丢。
2. 「当时分数拆解」第一切片没有 `/detail`。只有康希诺 `688185.SH` 按帧 `8:99` / `35:2` 显示样例读数（先手分 0.72、顶格占比 38% 等），方便对照。其它代码显示「字段缺失」。这些数字不提交。
3. `回放` 重新 GET suggest。有未确认内容时先走离开拦截，放弃后才重新读取。不写库。
4. 底栏图标用 Material outlined（列表、奖杯、日历、箱子、齿轮），对应契约里的五栏，没有再引入图标包。
5. 中文用 Droid Sans Fallback（Apache-2.0，见 `apps/mobile/fonts/`）。
6. Web 使用 path URL。`/#/attr?...` 也能解析。

## 没做

五栏的池、榜、持仓、买卖、观察、动量轮询，以及改 BFF 契约。
