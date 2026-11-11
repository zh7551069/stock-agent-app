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
        margin: const EdgeInsets.symmetric(horizontal: 36),
        padding: const EdgeInsets.fromLTRB(16, 20, 16, 20),
        decoration: BoxDecoration(
          color: LiupanColors.card,
          border: Border.all(color: LiupanColors.amber),
          borderRadius: LiupanRadii.surface,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              '尚未确认写入',
              style: TextStyle(
                fontFamily: liupanFont,
                color: LiupanColors.amber,
                fontSize: 16,
                fontWeight: FontWeight.w700,
                height: 1.2,
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              '归因仍为建议预勾状态，未写入决策记录。离开将丢弃本次改动；系统不会自动落库。',
              style: TextStyle(
                fontFamily: liupanFont,
                color: LiupanColors.text,
                fontSize: 12,
                fontWeight: FontWeight.w400,
                height: 1.45,
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              '须确认后写入 · 不自动落库',
              style: TextStyle(
                fontFamily: liupanFont,
                color: LiupanColors.blue,
                fontSize: 11,
                fontWeight: FontWeight.w600,
                height: 1.2,
              ),
            ),
            const SizedBox(height: 12),
            LiupanButton(
              key: const ValueKey('stay-confirm'),
              label: '返回确认',
              background: LiupanColors.amber,
              foreground: LiupanColors.ink,
              fontSize: 14,
              fontWeight: FontWeight.w700,
              height: 44,
              onPressed: onStay,
            ),
            const SizedBox(height: 8),
            LiupanButton(
              key: const ValueKey('discard-leave'),
              label: '放弃并离开',
              background: LiupanColors.bg,
              foreground: LiupanColors.muted,
              borderColor: LiupanColors.border,
              fontSize: 14,
              height: 44,
              onPressed: onDiscard,
            ),
          ],
        ),
      ),
    );
  }
}
