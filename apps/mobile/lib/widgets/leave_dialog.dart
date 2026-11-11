import 'package:flutter/material.dart';

import '../theme/liupan_theme.dart';

/// 未 confirm 就离开。两个按钮都不写库。
class LeaveDialog extends StatelessWidget {
  const LeaveDialog({
    super.key,
    required this.onStay,
    required this.onDiscard,
  });

  final VoidCallback onStay;
  final VoidCallback onDiscard;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Container(
        margin: const EdgeInsets.symmetric(horizontal: 28),
        padding: const EdgeInsets.fromLTRB(16, 18, 16, 16),
        decoration: BoxDecoration(
          color: LiupanColors.card,
          border: Border.all(color: LiupanColors.amber, width: 1.5),
          borderRadius: LiupanRadii.surface,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              '尚未确认写入',
              style: TextStyle(color: LiupanColors.amber, fontSize: 18, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 10),
            const Text(
              '归因仍为建议预勾状态，未写入决策记录。离开将丢弃本次改动；系统不会自动落库。',
              style: TextStyle(color: LiupanColors.text, fontSize: 14, height: 1.45),
            ),
            const SizedBox(height: 12),
            const Text(
              '须确认后写入 · 不自动落库',
              style: TextStyle(color: LiupanColors.blue, fontSize: 13),
            ),
            const SizedBox(height: 16),
            LiupanButton(
              key: const ValueKey('stay-confirm'),
              label: '返回确认',
              background: LiupanColors.amber,
              foreground: LiupanColors.ink,
              onPressed: onStay,
            ),
            const SizedBox(height: 10),
            LiupanButton(
              key: const ValueKey('discard-leave'),
              label: '放弃并离开',
              background: LiupanColors.buttonDark,
              foreground: LiupanColors.text,
              onPressed: onDiscard,
            ),
          ],
        ),
      ),
    );
  }
}
