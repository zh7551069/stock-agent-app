# 蓄势池 · 归因建议切片

Flutter 工程只做归因建议。手机走 HTTP 到 BFF，不打开 DuckDB，也不打开本机 SQLite。

跑通步骤、对照帧和硬约束见 [docs/delivery/slice-attr-ui.md](../../docs/delivery/slice-attr-ui.md)。

```bash
cd apps/mobile
flutter pub get
flutter analyze
flutter test
flutter run --dart-define=BFF_BASE=http://127.0.0.1:8770
```

深链：`/attr?date=2026-11-03&code=688185.SH&name=康希诺`。
