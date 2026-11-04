import 'package:flutter/material.dart';

import '../theme/liupan_theme.dart';

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
    return SizedBox(
      height: 56,
      child: Row(
        children: [
          IconButton(
            key: const ValueKey('header-back'),
            onPressed: onBack,
            icon: const Icon(Icons.chevron_left, color: LiupanColors.text, size: 28),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 40, minHeight: 40),
          ),
          Expanded(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: LiupanColors.text,
                    fontSize: 17,
                    fontWeight: FontWeight.w600,
                    height: 1.2,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: LiupanColors.muted, fontSize: 12, height: 1.2),
                ),
              ],
            ),
          ),
          TextButton(
            key: const ValueKey('replay'),
            onPressed: onReplay,
            style: TextButton.styleFrom(
              foregroundColor: LiupanColors.text,
              minimumSize: const Size(48, 36),
              padding: const EdgeInsets.symmetric(horizontal: 12),
            ),
            child: const Text('回放', style: TextStyle(fontSize: 15)),
          ),
        ],
      ),
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
        borderRadius: BorderRadius.all(Radius.circular(2)),
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
                  style: TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w700, height: 1.3),
                ),
                SizedBox(height: 2),
                Text(
                  '人工确认完成 · 非自动落库',
                  style: TextStyle(color: Colors.white, fontSize: 12, height: 1.3),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
