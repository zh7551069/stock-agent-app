import 'package:flutter/material.dart';

import '../theme/liupan_theme.dart';

/// 近板、水位、可买 / 已涨停、自动第1 / 手动关注、未确认共用这一张变体表。
/// 表面半径 0。A1 只用 [LpChipVariant.unconfirmed]；其余变体按已导出帧锁样式，页面后做。
enum LpChipVariant {
  unconfirmed,
  nearBoard,
  tradable,
  limitUp,
  waterLevel,
  autoTop1,
  manualWatch,
}

class LpChip extends StatelessWidget {
  const LpChip({super.key, required this.variant, required this.label});

  final LpChipVariant variant;
  final String label;

  @override
  Widget build(BuildContext context) {
    final style = lpChipStyle(variant);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: style.background,
        borderRadius: LiupanRadii.surface,
        border: style.border == null ? null : Border.all(color: style.border!),
      ),
      child: Padding(
        padding: style.padding,
        child: Text(
          label,
          style: TextStyle(
            fontFamily: liupanFont,
            color: style.foreground,
            fontSize: style.fontSize,
            fontWeight: style.weight,
            height: 1.1,
          ),
        ),
      ),
    );
  }
}

class LpChipStyle {
  const LpChipStyle({
    required this.background,
    required this.foreground,
    required this.fontSize,
    required this.weight,
    required this.padding,
    this.border,
  });

  final Color background;
  final Color foreground;
  final Color? border;
  final double fontSize;
  final FontWeight weight;
  final EdgeInsets padding;
}

/// 变体表。数值来自 Bridge：复盘 v1 帧 3「未确认」；v1 主界面近板 / 筛选 / 观察来源标。
LpChipStyle lpChipStyle(LpChipVariant variant) {
  switch (variant) {
    case LpChipVariant.unconfirmed:
      return const LpChipStyle(
        background: LiupanColors.amberWash,
        foreground: LiupanColors.amber,
        border: LiupanColors.amber,
        fontSize: 11,
        weight: FontWeight.w600,
        padding: EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      );
    case LpChipVariant.nearBoard:
      return const LpChipStyle(
        background: LiupanColors.nearBoard,
        foreground: LiupanColors.amber,
        fontSize: 10,
        weight: FontWeight.w400,
        padding: EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      );
    case LpChipVariant.tradable:
    case LpChipVariant.limitUp:
    case LpChipVariant.waterLevel:
      return const LpChipStyle(
        background: LiupanColors.card,
        foreground: LiupanColors.muted,
        border: LiupanColors.border,
        fontSize: 11,
        weight: FontWeight.w500,
        padding: EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      );
    case LpChipVariant.autoTop1:
      return const LpChipStyle(
        background: LiupanColors.amberBadge,
        foreground: LiupanColors.amber,
        border: LiupanColors.amber,
        fontSize: 10,
        weight: FontWeight.w500,
        padding: EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      );
    case LpChipVariant.manualWatch:
      return const LpChipStyle(
        background: LiupanColors.blueBadge,
        foreground: LiupanColors.blue,
        border: LiupanColors.blue,
        fontSize: 10,
        weight: FontWeight.w500,
        padding: EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      );
  }
}
