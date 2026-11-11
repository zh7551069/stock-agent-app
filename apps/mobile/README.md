# 蓄势池 · 归因建议切片

Flutter 工程。手机走 HTTP 到 BFF，不打开 DuckDB，也不打开本机 SQLite。

A0 壳：冷启动在「候选」。底栏顺序是候选、先手榜、复盘、持仓、设置。候选根页标题「蓄势池」，副标「首板前蓄势 · 非已连板」。观察不是底栏。页面内容仍是现有归因切片，池、榜、持仓对稿留给后续切片。

跑通步骤、对照帧和硬约束见 [docs/delivery/slice-attr-ui.md](../../docs/delivery/slice-attr-ui.md)。

```bash
cd apps/mobile
flutter pub get
flutter analyze
flutter test
flutter run --dart-define=BFF_BASE=http://127.0.0.1:8770
```

深链：`/attr?date=2026-11-03&code=688185.SH&name=康希诺`。打开后底栏保持复盘选中；关掉后回到进入前的根栏。没有深链时冷启动在候选。

本机 Android 预览（需要已安装的 Flutter 与 Android SDK；模拟器访问宿主机 BFF 用 `10.0.2.2`）：

```bash
cd apps/mobile
flutter pub get
flutter build apk --debug --dart-define=BFF_BASE=http://10.0.2.2:8770
flutter install --dart-define=BFF_BASE=http://10.0.2.2:8770
# 或直接：
flutter run --dart-define=BFF_BASE=http://10.0.2.2:8770
```

调试包在 `build/app/outputs/flutter-apk/app-debug.apk`。Cloud Linux 环境没有 Android SDK 时，用上面的命令在本机出包。
