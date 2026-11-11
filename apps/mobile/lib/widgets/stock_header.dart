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

class SuccessBanner extends StatelessWidget {
  const SuccessBanner({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
      decoration: const BoxDecoration(
        color: LiupanColors.green,
        borderRadius: LiupanRadii.surface,
      ),
      child: const Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.check, color: Colors.white, size: 18),
          SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '归因已写入决策记录',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 15,
                    fontWeight: FontWeight.w700,
                    height: 1.3,
                  ),
                ),
                SizedBox(height: 2),
                Text(
                  '人工确认完成 · 非自动落库',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 12,
                    height: 1.3,
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
