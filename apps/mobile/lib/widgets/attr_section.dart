import 'package:flutter/material.dart';

import '../model/attr_models.dart';
import '../state/attr_controller.dart';
import '../theme/liupan_theme.dart';
import 'attr_check_mark.dart';
import 'lp_chip.dart';

class AttrSection extends StatelessWidget {
  const AttrSection({
    super.key,
    required this.controller,
    required this.reviewCard,
    required this.onRetag,
    required this.onConfirm,
    required this.onRetry,
  });

  final AttrController controller;
  final bool reviewCard;
  final VoidCallback onRetag;
  final VoidCallback onConfirm;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    if (controller.loading) {
      return const _LoadingBlock();
    }
    if (controller.loadError != null) {
      return _ErrorBlock(message: controller.loadError!, onRetry: onRetry);
    }

    final confirmed = controller.showConfirmed;
    final catalog = displayCatalog(
      suggested: controller.suggestedTags,
      local: controller.localTags,
      confirmed: controller.confirmedTags,
    );
    final selected = controller.localTags.toSet();
    final suggested = controller.suggestedTags.toSet();
    final noSuggestion =
        controller.suggestedTags.isEmpty && controller.localTags.isEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _TitleRow(
          title: confirmed
              ? '归因已确认'
              : (noSuggestion ? '归因建议（无建议）' : '归因建议（预勾 · 须确认）'),
          showUnconfirmed: reviewCard && !confirmed,
        ),
        const SizedBox(height: 10),
        _Callout(
          text: confirmed
              ? '已写入决策记录 · 人工确认完成 · 非自动落库'
              : (noSuggestion ? '尚无建议 · 不自动落库' : '须确认后写入 · 不自动落库'),
          confirmed: confirmed,
          compact: reviewCard,
        ),
        if (!reviewCard) ...[
          const SizedBox(height: 10),
          _EvidencePanel(controller: controller),
        ],
        const SizedBox(height: 10),
        if (confirmed)
          _ConfirmedChips(tags: controller.localTags)
        else
          _TagWrap(
            tags: catalog,
            selected: selected,
            suggested: suggested,
            showCheck: !reviewCard,
            idleFill: reviewCard ? LiupanColors.card : LiupanColors.bg,
          ),
        if (controller.confirmError != null) ...[
          const SizedBox(height: 10),
          _ConfirmError(message: controller.confirmError!),
        ],
        if (!reviewCard || confirmed) ...[
          const SizedBox(height: 10),
          _StatusRow(
            confirmed: confirmed,
            count: controller.localTags.length,
            noSuggestion: noSuggestion,
          ),
        ],
        const SizedBox(height: 10),
        _ActionRow(
          confirmed: confirmed,
          confirming: controller.confirming,
          onRetag: onRetag,
          onConfirm: onConfirm,
        ),
        const SizedBox(height: 10),
        Opacity(
          opacity: confirmed ? 0.7 : 1,
          child: const Text(
            '禁全自动落库 · 批任务不得写「已确认」',
            style: _footnote,
          ),
        ),
      ],
    );
  }
}

class _LoadingBlock extends StatelessWidget {
  const _LoadingBlock();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 24),
      child: Row(
        children: [
          SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: LiupanColors.amber,
            ),
          ),
          SizedBox(width: 10),
          Text('正在读取建议…', style: _muted13),
        ],
      ),
    );
  }
}

class _ErrorBlock extends StatelessWidget {
  const _ErrorBlock({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('建议读取失败，未编造标签', style: _errorTitle),
        const SizedBox(height: 6),
        Text(message, style: _muted13),
        const SizedBox(height: 12),
        LiupanButton(
          label: '重试',
          background: LiupanColors.card,
          foreground: LiupanColors.text,
          borderColor: LiupanColors.border,
          fontSize: 14,
          onPressed: onRetry,
        ),
      ],
    );
  }
}

class _TitleRow extends StatelessWidget {
  const _TitleRow({required this.title, required this.showUnconfirmed});

  final String title;
  final bool showUnconfirmed;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Flexible(
          child: Text(
            title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: _sectionTitle,
          ),
        ),
        if (showUnconfirmed) ...[
          const SizedBox(width: 8),
          const LpChip(
            key: ValueKey('unconfirmed-badge'),
            variant: LpChipVariant.unconfirmed,
            label: '未确认',
          ),
        ],
      ],
    );
  }
}

class _Callout extends StatelessWidget {
  const _Callout({
    required this.text,
    required this.confirmed,
    required this.compact,
  });

  final String text;
  final bool confirmed;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final color = confirmed ? LiupanColors.green : LiupanColors.blue;
    return Container(
      width: double.infinity,
      padding: EdgeInsets.symmetric(horizontal: 10, vertical: compact ? 6 : 8),
      decoration: BoxDecoration(
        color: confirmed ? LiupanColors.greenWash : LiupanColors.blueWash,
        border: Border.all(color: color),
        borderRadius: LiupanRadii.surface,
      ),
      child: Text(
        text,
        style: TextStyle(
          fontFamily: liupanFont,
          color: color,
          fontSize: 11,
          fontWeight: FontWeight.w600,
          height: 1.3,
        ),
      ),
    );
  }
}

class _EvidencePanel extends StatefulWidget {
  const _EvidencePanel({required this.controller});

  final AttrController controller;

  @override
  State<_EvidencePanel> createState() => _EvidencePanelState();
}

class _EvidencePanelState extends State<_EvidencePanel> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          key: const ValueKey('evidence-toggle'),
          onTap: () => setState(() => _open = !_open),
          child: Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              color: LiupanColors.card,
              border: Border.all(color: LiupanColors.border),
              borderRadius: LiupanRadii.surface,
            ),
            child: Text(
              _open ? '证据（辅）▴ · 点击收起' : '证据（辅）▾ · 点击展开',
              style: _evidenceLabel,
            ),
          ),
        ),
        if (_open)
          Container(
            width: double.infinity,
            margin: const EdgeInsets.only(top: 6),
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: LiupanColors.card,
              border: Border.all(color: LiupanColors.border),
              borderRadius: LiupanRadii.surface,
            ),
            child: Text(
              'suggested_asof  ${controller.suggestedAsOf ?? '—'}\n'
              'suggested_at  ${controller.suggestedAt ?? '—'}\n'
              'code  ${controller.code}\n'
              'date  ${controller.date}\n'
              '${formatEvidence(controller.evidence)}'
              '${controller.note == null ? '' : '\n${controller.note}'}',
              style: const TextStyle(
                fontFamily: liupanFont,
                color: LiupanColors.muted,
                fontSize: 12,
                height: 1.45,
              ),
            ),
          ),
      ],
    );
  }
}

class _TagWrap extends StatelessWidget {
  const _TagWrap({
    required this.tags,
    required this.selected,
    required this.suggested,
    required this.showCheck,
    required this.idleFill,
  });

  final List<String> tags;
  final Set<String> selected;
  final Set<String> suggested;
  final bool showCheck;
  final Color idleFill;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        for (final tag in tags)
          _TagChip(
            tag: tag,
            selected: selected.contains(tag),
            showCheck: showCheck,
            showBadge: selected.contains(tag) && suggested.contains(tag),
            idleFill: idleFill,
          ),
      ],
    );
  }
}

class _TagChip extends StatelessWidget {
  const _TagChip({
    required this.tag,
    required this.selected,
    required this.showCheck,
    required this.showBadge,
    required this.idleFill,
  });

  final String tag;
  final bool selected;
  final bool showCheck;
  final bool showBadge;
  final Color idleFill;

  @override
  Widget build(BuildContext context) {
    final fill = selected ? LiupanColors.amberWash : idleFill;
    final border = selected ? LiupanColors.amber : LiupanColors.border;
    final color = selected ? LiupanColors.amber : LiupanColors.muted;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
      decoration: BoxDecoration(
        color: fill,
        border: Border.all(color: border),
        borderRadius: LiupanRadii.surface,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (showCheck) ...[
            AttrCheckMark(checked: selected, fill: idleFill),
            const SizedBox(width: 6),
          ],
          Text(
            tag,
            style: TextStyle(
              fontFamily: liupanFont,
              color: color,
              fontSize: 13,
              fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
              height: 1.2,
            ),
          ),
          if (showBadge) ...[
            const SizedBox(width: 6),
            const _SuggestBadge(),
          ],
        ],
      ),
    );
  }
}

class _SuggestBadge extends StatelessWidget {
  const _SuggestBadge();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
      decoration: const BoxDecoration(
        color: LiupanColors.amber,
        borderRadius: LiupanRadii.surface,
      ),
      child: const Text(
        '建议',
        style: TextStyle(
          fontFamily: liupanFont,
          color: LiupanColors.bg,
          fontSize: 8,
          fontWeight: FontWeight.w700,
          height: 1.1,
        ),
      ),
    );
  }
}

class _ConfirmedChips extends StatelessWidget {
  const _ConfirmedChips({required this.tags});

  final List<String> tags;

  @override
  Widget build(BuildContext context) {
    if (tags.isEmpty) {
      return const Text('已确认，无标签', style: _muted13);
    }
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        for (final tag in tags)
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: LiupanColors.greenWash,
              border: Border.all(color: LiupanColors.green),
              borderRadius: LiupanRadii.surface,
            ),
            child: Text(
              tag,
              style: const TextStyle(
                fontFamily: liupanFont,
                color: LiupanColors.green,
                fontSize: 13,
                fontWeight: FontWeight.w600,
                height: 1.2,
              ),
            ),
          ),
      ],
    );
  }
}

class _ConfirmError extends StatelessWidget {
  const _ConfirmError({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
      decoration: BoxDecoration(
        border: Border.all(color: LiupanColors.red),
        borderRadius: LiupanRadii.surface,
      ),
      child: Text(
        '确认失败，未写入决策记录\n$message',
        style: const TextStyle(
          fontFamily: liupanFont,
          color: LiupanColors.red,
          fontSize: 13,
          height: 1.4,
        ),
      ),
    );
  }
}

class _StatusRow extends StatelessWidget {
  const _StatusRow({
    required this.confirmed,
    required this.count,
    required this.noSuggestion,
  });

  final bool confirmed;
  final int count;
  final bool noSuggestion;

  @override
  Widget build(BuildContext context) {
    final hint = confirmed
        ? '人工确认 · 刷新仍保留'
        : (noSuggestion ? '尚无建议 · 可改标后确认' : '预勾 $count 项 · 可改标后再确认');
    final tone = confirmed ? LiupanColors.green : LiupanColors.amber;
    return Row(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
          decoration: BoxDecoration(
            color: confirmed ? LiupanColors.greenWash : LiupanColors.amberStatus,
            borderRadius: LiupanRadii.surface,
          ),
          child: Text(
            confirmed ? '已写入决策记录' : '未写入决策记录',
            style: TextStyle(
              fontFamily: liupanFont,
              color: tone,
              fontSize: 10,
              fontWeight: FontWeight.w600,
              height: 1.2,
            ),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            hint,
            style: const TextStyle(
              fontFamily: liupanFont,
              color: LiupanColors.muted,
              fontSize: 10,
              fontWeight: FontWeight.w400,
              height: 1.2,
            ),
          ),
        ),
      ],
    );
  }
}

class _ActionRow extends StatelessWidget {
  const _ActionRow({
    required this.confirmed,
    required this.confirming,
    required this.onRetag,
    required this.onConfirm,
  });

  final bool confirmed;
  final bool confirming;
  final VoidCallback onRetag;
  final VoidCallback onConfirm;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: LiupanButton(
            key: const ValueKey('open-retag'),
            label: confirmed ? '修改归因' : '改标',
            background: LiupanColors.card,
            foreground: LiupanColors.text,
            borderColor: LiupanColors.border,
            fontSize: 14,
            onPressed: confirming ? null : onRetag,
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: confirmed
              ? const LiupanButton(
                  key: ValueKey('confirmed-cta'),
                  label: '已确认写入 ✓',
                  background: LiupanColors.greenWash,
                  foreground: LiupanColors.green,
                  borderColor: LiupanColors.green,
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                  onPressed: null,
                )
              : LiupanButton(
                  key: const ValueKey('confirm-write'),
                  label: confirming ? '写入中…' : '确认写入',
                  background: LiupanColors.amber,
                  foreground: LiupanColors.ink,
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                  onPressed: confirming ? null : onConfirm,
                ),
        ),
      ],
    );
  }
}

const _sectionTitle = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.text,
  fontSize: 12,
  fontWeight: FontWeight.w700,
  height: 1.2,
);

const _footnote = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.muted,
  fontSize: 10,
  fontWeight: FontWeight.w400,
  height: 1.3,
);

const _muted13 = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.muted,
  fontSize: 13,
);

const _errorTitle = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.red,
  fontSize: 14,
);

const _evidenceLabel = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.muted,
  fontSize: 11,
  fontWeight: FontWeight.w600,
  height: 1.2,
);
