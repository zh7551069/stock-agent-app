import 'package:flutter/material.dart';

import '../theme/liupan_theme.dart';
import 'lp_nav_header.dart';

class StockHeader extends StatelessWidget {
  const StockHeader({
    super.key,
    required this.title,
    required this.subtitle,
    required this.onBack,
    required this.onReplay,
  });

  final String title;
  final String subtitle;
  final VoidCallback onBack;
  final VoidCallback onReplay;

  @override
  Widget build(BuildContext context) {
    return LpNavHeader.stack(
      title: title,
      subtitle: subtitle,
      onBack: onBack,
      actionLabel: '回放',
      onAction: onReplay,
      actionKey: const ValueKey('replay'),
    );
  }
}

/// 已确认帧上的浮条。盖在内容上，不把分数区往下推。
class SuccessBanner extends StatelessWidget {
  const SuccessBanner({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const ValueKey('success-banner'),
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: LiupanColors.toastGreen,
        border: Border.all(color: LiupanColors.green),
        borderRadius: LiupanRadii.surface,
      ),
      child: const Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '✓',
            style: TextStyle(
              fontFamily: liupanFont,
              color: LiupanColors.green,
              fontSize: 14,
              fontWeight: FontWeight.w700,
              height: 1.2,
            ),
          ),
          SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '归因已写入决策记录',
                  style: TextStyle(
                    fontFamily: liupanFont,
                    color: LiupanColors.text,
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                    height: 1.2,
                  ),
                ),
                SizedBox(height: 2),
                Text(
                  '人工确认完成 · 非自动落库',
                  style: TextStyle(
                    fontFamily: liupanFont,
                    color: LiupanColors.muted,
                    fontSize: 10,
                    fontWeight: FontWeight.w400,
                    height: 1.2,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
