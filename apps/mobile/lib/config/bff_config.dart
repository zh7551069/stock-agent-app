import 'package:flutter/foundation.dart';

/// 编译期 `--dart-define=BFF_BASE=` 或 `LIUPAN_BFF_BASE=`，否则本机 8770。
/// 设置页可以在本次运行里改地址，下一次 suggest / confirm 生效。
String initialBffBase() {
  const fromBff = String.fromEnvironment('BFF_BASE');
  const fromLiupan = String.fromEnvironment('LIUPAN_BFF_BASE');
  final raw = fromBff.isNotEmpty
      ? fromBff
      : (fromLiupan.isNotEmpty ? fromLiupan : 'http://127.0.0.1:8770');
  return normalizeBase(raw);
}

String normalizeBase(String raw) => raw.trim().replaceAll(RegExp(r'/+$'), '');

class BffConfig extends ChangeNotifier {
  BffConfig(this._base);

  String _base;
  String get base => _base;

  void update(String next) {
    final cleaned = normalizeBase(next);
    if (cleaned.isEmpty || cleaned == _base) return;
    _base = cleaned;
    notifyListeners();
  }
}
