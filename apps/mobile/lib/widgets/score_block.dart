import 'package:flutter/material.dart';

import '../model/attr_models.dart';
import '../theme/liupan_theme.dart';

/// 当时分数拆解。只读。
/// 本切片没有 /detail，康希诺样例按复盘 v1 帧展示；其它代码标「字段缺失」。
/// 基准 A/B 固定「待验证」，不出现 0%。数字不会进入 confirm。
class ScoreBlock extends StatelessWidget {
  const ScoreBlock({super.key, required this.score});

  final ScoreSnapshot score;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: LiupanColors.card,
            border: Border.all(color: LiupanColors.border),
            borderRadius: LiupanRadii.surface,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('当时分数拆解', style: _title),
              for (final row in score.rows) ...[
                const SizedBox(height: 8),
                _ScoreLine(row: row),
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),
        const Row(
          children: [
            Expanded(
              child: _BenchmarkCard(
                title: '基准 A',
                subtitle: 'T 收盘',
                footnote: '禁止显示 0%',
              ),
            ),
            SizedBox(width: 8),
            Expanded(
              child: _BenchmarkCard(
                title: '基准 B',
                subtitle: 'T+1 开盘',
                footnote: '待次日开盘验证',
              ),
            ),
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
      ScoreTone.muted => LiupanColors.muted,
      ScoreTone.missing => LiupanColors.dim,
      ScoreTone.primary => LiupanColors.text,
    };
    return Row(
      children: [
        Expanded(child: Text(row.label, style: _label)),
        Text(
          row.value,
          style: TextStyle(
            fontFamily: liupanFont,
            color: color,
            fontSize: 12,
            fontWeight: FontWeight.w600,
            height: 1.2,
          ),
        ),
      ],
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
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: LiupanColors.card,
        border: Border.all(color: LiupanColors.border),
        borderRadius: LiupanRadii.surface,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: _benchTitle),
          const SizedBox(height: 6),
          Text(subtitle, style: _benchSub),
          const SizedBox(height: 6),
          Text(benchmarkValue(null), style: _benchValue),
          const SizedBox(height: 6),
          Text(footnote, style: _benchNote),
        ],
      ),
    );
  }
}

const _title = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.text,
  fontSize: 12,
  fontWeight: FontWeight.w700,
  height: 1.2,
);

const _label = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.muted,
  fontSize: 11,
  fontWeight: FontWeight.w400,
  height: 1.2,
);

const _benchTitle = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.muted,
  fontSize: 11,
  fontWeight: FontWeight.w600,
  height: 1.2,
);

const _benchSub = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.dim,
  fontSize: 10,
  fontWeight: FontWeight.w400,
  height: 1.2,
);

const _benchValue = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.muted,
  fontSize: 13,
  fontWeight: FontWeight.w700,
  height: 1.2,
);

const _benchNote = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.dim,
  fontSize: 9,
  fontWeight: FontWeight.w400,
  height: 1.2,
);
