import 'package:flutter/material.dart';

import '../model/attr_models.dart';
import '../theme/liupan_theme.dart';

/// 当时分数拆解。只读。
/// 本切片没有 /detail，康希诺样例按帧展示；其它代码标「字段缺失」。
/// 基准 A/B 固定 pending，不出现 0%。数字不会进入 confirm。
class ScoreBlock extends StatelessWidget {
  const ScoreBlock({super.key, required this.score});

  final ScoreSnapshot score;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          '当时分数拆解',
          style: TextStyle(color: LiupanColors.muted, fontSize: 13),
        ),
        const SizedBox(height: 8),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: LiupanColors.card,
            border: Border.all(color: LiupanColors.border),
            borderRadius: LiupanRadii.surface,
          ),
          child: Column(
            children: [
              for (final row in score.rows) _ScoreLine(row: row),
            ],
          ),
        ),
        const SizedBox(height: 8),
        const Row(
          children: [
            Expanded(child: _BenchmarkCard(
              title: '基准 A',
              subtitle: 'T 收益',
              footnote: '禁止显示 0%',
            )),
            SizedBox(width: 8),
            Expanded(child: _BenchmarkCard(
              title: '基准 B',
              subtitle: 'T+1 开盘',
              footnote: '待次日开盘验证',
            )),
          ],
        ),
      ],
    );
  }
}

class _ScoreLine extends StatelessWidget {
  const _ScoreLine({required this.row});

  final ScoreRow row;

  @override
  Widget build(BuildContext context) {
    final color = switch (row.tone) {
      ScoreTone.red => LiupanColors.red,
      ScoreTone.amber => LiupanColors.amber,
      ScoreTone.missing => LiupanColors.dim,
      ScoreTone.primary => LiupanColors.text,
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Expanded(
            child: Text(row.label, style: const TextStyle(color: LiupanColors.muted, fontSize: 14)),
          ),
          Text(
            row.value,
            style: TextStyle(color: color, fontSize: 15, fontWeight: FontWeight.w600),
          ),
        ],
      ),
    );
  }
}

class _BenchmarkCard extends StatelessWidget {
  const _BenchmarkCard({
    required this.title,
    required this.subtitle,
    required this.footnote,
  });

  final String title;
  final String subtitle;
  final String footnote;

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: const BoxConstraints(minHeight: 96),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: LiupanColors.card,
        border: Border.all(color: LiupanColors.border),
        borderRadius: LiupanRadii.surface,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(color: LiupanColors.text, fontSize: 14, fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Text(subtitle, style: const TextStyle(color: LiupanColors.muted, fontSize: 12)),
          const SizedBox(height: 6),
          Text(benchmarkValue(null), style: const TextStyle(color: LiupanColors.text, fontSize: 16)),
          const SizedBox(height: 4),
          Text(footnote, style: const TextStyle(color: LiupanColors.dim, fontSize: 11)),
        ],
      ),
    );
  }
}
