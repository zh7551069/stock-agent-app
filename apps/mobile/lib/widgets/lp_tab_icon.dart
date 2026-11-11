import 'package:flutter/material.dart';

import '../shell/lp_tab_selection.dart';
import 'lp_tab_icon_data.dart';

List<LpIconFragment> lpTabIconFragments(LpRootTab tab) {
  switch (tab) {
    case LpRootTab.candidate:
      return lpTabIconCandidate;
    case LpRootTab.rank:
      return lpTabIconRank;
    case LpRootTab.review:
      return lpTabIconReview;
    case LpRootTab.holdings:
      return lpTabIconHoldings;
    case LpRootTab.settings:
      return lpTabIconSettings;
  }
}

/// 解析本批 Bridge 线框 path。这些文件只用绝对命令 M/L/H/V/C/Z。
Path parseSvgPath(String source) {
  final path = Path();
  final tokens = RegExp(
    r'[MmLlHhVvCcZz]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?',
  ).allMatches(source).map((match) => match.group(0)!).toList(growable: false);
  var index = 0;
  String? command;
  var cx = 0.0;
  var cy = 0.0;

  while (index < tokens.length) {
    final token = tokens[index];
    if (RegExp(r'^[A-Za-z]$').hasMatch(token)) {
      command = token;
      index += 1;
      if (command == 'Z' || command == 'z') {
        path.close();
        command = null;
      }
      continue;
    }
    final current = command;
    if (current == null) {
      throw FormatException('SVG path 在命令之前出现数字');
    }
    double read() {
      if (index >= tokens.length) {
        throw FormatException('SVG path 参数不足: $current');
      }
      return double.parse(tokens[index++]);
    }

    switch (current) {
      case 'M':
      case 'm':
        final x = current == 'M' ? read() : cx + read();
        final y = current == 'M' ? read() : cy + read();
        path.moveTo(x, y);
        cx = x;
        cy = y;
        command = current == 'M' ? 'L' : 'l';
      case 'L':
        final x = read();
        final y = read();
        path.lineTo(x, y);
        cx = x;
        cy = y;
      case 'l':
        cx += read();
        cy += read();
        path.lineTo(cx, cy);
      case 'H':
        cx = read();
        path.lineTo(cx, cy);
      case 'h':
        cx += read();
        path.lineTo(cx, cy);
      case 'V':
        cy = read();
        path.lineTo(cx, cy);
      case 'v':
        cy += read();
        path.lineTo(cx, cy);
      case 'C':
        final x1 = read();
        final y1 = read();
        final x2 = read();
        final y2 = read();
        final x = read();
        final y = read();
        path.cubicTo(x1, y1, x2, y2, x, y);
        cx = x;
        cy = y;
      case 'c':
        final originX = cx;
        final originY = cy;
        final x1 = originX + read();
        final y1 = originY + read();
        final x2 = originX + read();
        final y2 = originY + read();
        final x = originX + read();
        final y = originY + read();
        path.cubicTo(x1, y1, x2, y2, x, y);
        cx = x;
        cy = y;
      default:
        throw FormatException('不支持的 SVG 命令: $current');
    }
  }
  return path;
}

final Map<String, Path> _pathCache = <String, Path>{};

Path _cachedPath(String d) => _pathCache.putIfAbsent(d, () => parseSvgPath(d));

/// 20×20 底栏线框。颜色由调用方传入，几何来自 Bridge svg。
class LpTabIcon extends StatelessWidget {
  const LpTabIcon({super.key, required this.tab, required this.color});

  final LpRootTab tab;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return CustomPaint(
      size: const Size(20, 20),
      painter: _LpTabIconPainter(lpTabIconFragments(tab), color),
    );
  }
}

class _LpTabIconPainter extends CustomPainter {
  const _LpTabIconPainter(this.fragments, this.color);

  final List<LpIconFragment> fragments;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round
      ..color = color
      ..isAntiAlias = true;
    for (final fragment in fragments) {
      final box = fragment.viewBoxPx;
      canvas.save();
      canvas.translate(fragment.leftPx, fragment.topPx);
      canvas.scale(fragment.widthPx / box[2], fragment.heightPx / box[3]);
      paint.strokeWidth = fragment.strokeWidthPx;
      canvas.drawPath(_cachedPath(fragment.d), paint);
      canvas.restore();
    }
  }

  @override
  bool shouldRepaint(covariant _LpTabIconPainter oldDelegate) {
    return oldDelegate.color != color || oldDelegate.fragments != fragments;
  }
}
