import 'package:flutter/material.dart';

import '../model/attr_models.dart';
import '../state/attr_controller.dart';
import '../theme/liupan_theme.dart';

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
    final idle = catalog.where((tag) => !selected.contains(tag)).toList();
    final noSuggestion = controller.suggestedTags.isEmpty && controller.localTags.isEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _TitleRow(
          title: confirmed
              ? '归因已确认'
              : (noSuggestion ? '归因建议（无建议）' : '归因建议（预勾 · 须确认）'),
          showUnconfirmed: reviewCard && !confirmed,
        ),
        const SizedBox(height: 8),
        _Callout(
          text: confirmed
              ? '已写入决策记录 · 人工确认完成 · 非自动落库'
              : (noSuggestion ? '尚无建议 · 不自动落库' : '须确认后写入 · 不自动落库'),
          confirmed: confirmed,
        ),
        if (!reviewCard) ...[
          const SizedBox(height: 8),
          _EvidencePanel(controller: controller),
        ],
        const SizedBox(height: 10),
        if (confirmed)
          _ConfirmedChips(tags: controller.localTags)
        else ...[
          if (selected.isNotEmpty)
            _SelectedWrap(
              tags: controller.localTags,
              suggested: suggested,
              showCheck: !reviewCard,
            ),
          if (selected.isNotEmpty && idle.isNotEmpty) const SizedBox(height: 8),
          if (idle.isNotEmpty) _IdleGrid(tags: idle),
        ],
        if (controller.confirmError != null) ...[
          const SizedBox(height: 10),
          _ConfirmError(message: controller.confirmError!),
        ],
        if (!reviewCard || confirmed) ...[
          const SizedBox(height: 12),
          _StatusRow(
            confirmed: confirmed,
            count: controller.localTags.length,
            noSuggestion: noSuggestion,
          ),
        ],
        const SizedBox(height: 12),
        _ActionRow(
          confirmed: confirmed,
          confirming: controller.confirming,
          onRetag: onRetag,
          onConfirm: onConfirm,
        ),
        const SizedBox(height: 8),
        const Text(
          '禁全自动落库 · 批任务不得写「已确认」',
          style: TextStyle(color: LiupanColors.dim, fontSize: 11),
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
            child: CircularProgressIndicator(strokeWidth: 2, color: LiupanColors.amber),
          ),
          SizedBox(width: 10),
          Text('正在读取建议…', style: TextStyle(color: LiupanColors.muted, fontSize: 14)),
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
        const Text('建议读取失败，未编造标签', style: TextStyle(color: LiupanColors.red, fontSize: 14)),
        const SizedBox(height: 6),
        Text(message, style: const TextStyle(color: LiupanColors.muted, fontSize: 13)),
        const SizedBox(height: 12),
        LiupanButton(
          label: '重试',
          background: LiupanColors.buttonDark,
          foreground: LiupanColors.text,
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
        Expanded(
          child: Text(title, style: const TextStyle(color: LiupanColors.muted, fontSize: 13)),
        ),
        if (showUnconfirmed)
          Container(
            key: const ValueKey('unconfirmed-badge'),
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
              border: Border.all(color: LiupanColors.amber),
              borderRadius: const BorderRadius.all(Radius.circular(2)),
            ),
            child: const Text('未确认', style: TextStyle(color: LiupanColors.amber, fontSize: 12)),
          ),
      ],
    );
  }
}

class _Callout extends StatelessWidget {
  const _Callout({required this.text, required this.confirmed});

  final String text;
  final bool confirmed;

  @override
  Widget build(BuildContext context) {
    final color = confirmed ? LiupanColors.green : LiupanColors.blue;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
      decoration: BoxDecoration(
        border: Border.all(color: color),
        borderRadius: const BorderRadius.all(Radius.circular(2)),
      ),
      child: Text(text, style: TextStyle(color: color, fontSize: 13)),
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
            constraints: const BoxConstraints(minHeight: 40),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
            decoration: BoxDecoration(
              color: LiupanColors.card,
              border: Border.all(color: LiupanColors.border),
              borderRadius: const BorderRadius.all(Radius.circular(2)),
            ),
            child: Text(
              _open ? '证据（辅） · 点击收起' : '证据（辅） · 点击展开',
              style: const TextStyle(color: LiupanColors.muted, fontSize: 13),
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
              borderRadius: const BorderRadius.all(Radius.circular(2)),
            ),
            child: Text(
              'suggested_asof  ${controller.suggestedAsOf ?? '—'}\n'
              'suggested_at  ${controller.suggestedAt ?? '—'}\n'
              'code  ${controller.code}\n'
              'date  ${controller.date}\n'
              '${formatEvidence(controller.evidence)}'
              '${controller.note == null ? '' : '\n${controller.note}'}',
              style: const TextStyle(color: LiupanColors.muted, fontSize: 12, height: 1.45),
            ),
          ),
      ],
    );
  }
}

class _SelectedWrap extends StatelessWidget {
  const _SelectedWrap({
    required this.tags,
    required this.suggested,
    required this.showCheck,
  });

  final List<String> tags;
  final Set<String> suggested;
  final bool showCheck;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final tag in tags)
          _SuggestChip(
            tag: tag,
            showCheck: showCheck,
            showBadge: suggested.contains(tag),
          ),
      ],
    );
  }
}

class _SuggestChip extends StatelessWidget {
  const _SuggestChip({
    required this.tag,
    required this.showCheck,
    required this.showBadge,
  });

  final String tag;
  final bool showCheck;
  final bool showBadge;

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: const BoxConstraints(minHeight: 36),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
      decoration: BoxDecoration(
        color: LiupanColors.suggestedFill,
        border: Border.all(color: LiupanColors.amber, width: 1.5),
        borderRadius: const BorderRadius.all(Radius.circular(2)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (showCheck) ...[
            const Icon(Icons.check, size: 16, color: LiupanColors.amber),
            const SizedBox(width: 4),
          ],
          Text(tag, style: const TextStyle(color: LiupanColors.amber, fontSize: 12)),
          if (showBadge) ...[
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
              decoration: const BoxDecoration(
                color: LiupanColors.amber,
                borderRadius: BorderRadius.all(Radius.circular(2)),
              ),
              child: const Text(
                '建议',
                style: TextStyle(color: LiupanColors.ink, fontSize: 10, fontWeight: FontWeight.w700),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _IdleGrid extends StatelessWidget {
  const _IdleGrid({required this.tags});

  final List<String> tags;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        const gap = 8.0;
        final width = ((constraints.maxWidth - 16) / 3).clamp(0.0, 400.0).toDouble();
        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: [
            for (final tag in tags)
              SizedBox(
                width: width,
                child: Container(
                  constraints: const BoxConstraints(minHeight: 36),
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 12),
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: LiupanColors.chip,
                    border: Border.all(color: LiupanColors.border),
                    borderRadius: const BorderRadius.all(Radius.circular(2)),
                  ),
                  child: Text(
                    tag,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: LiupanColors.muted, fontSize: 12),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }
}

class _ConfirmedChips extends StatelessWidget {
  const _ConfirmedChips({required this.tags});

  final List<String> tags;

  @override
  Widget build(BuildContext context) {
    if (tags.isEmpty) {
      return const Text('已确认，无标签', style: TextStyle(color: LiupanColors.muted, fontSize: 13));
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        const gap = 8.0;
        final half = ((constraints.maxWidth - gap) / 2).clamp(0.0, constraints.maxWidth).toDouble();
        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: [
            for (final tag in tags)
              SizedBox(
                width: tags.length == 1 ? null : half,
                child: Container(
                  constraints: const BoxConstraints(minHeight: 40),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
                  alignment: Alignment.center,
                  decoration: const BoxDecoration(
                    color: LiupanColors.green,
                    borderRadius: BorderRadius.all(Radius.circular(2)),
                  ),
                  child: Text(
                    tag,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                  ),
                ),
              ),
          ],
        );
      },
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
        borderRadius: const BorderRadius.all(Radius.circular(2)),
      ),
      child: Text(
        '确认失败，未写入决策记录\n$message',
        style: const TextStyle(color: LiupanColors.red, fontSize: 13, height: 1.4),
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
    return Row(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
          decoration: BoxDecoration(
            color: confirmed ? LiupanColors.green : Colors.transparent,
            border: Border.all(color: confirmed ? LiupanColors.green : LiupanColors.amber),
            borderRadius: const BorderRadius.all(Radius.circular(2)),
          ),
          child: Text(
            confirmed ? '已写入决策记录' : '未写入决策记录',
            style: TextStyle(
              color: confirmed ? Colors.white : LiupanColors.amber,
              fontSize: 12,
            ),
          ),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Text(hint, style: const TextStyle(color: LiupanColors.muted, fontSize: 12)),
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
          flex: 2,
          child: LiupanButton(
            key: const ValueKey('open-retag'),
            label: confirmed ? '修改归因' : '改标',
            background: LiupanColors.buttonDark,
            foreground: LiupanColors.text,
            onPressed: confirming ? null : onRetag,
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          flex: 3,
          child: confirmed
              ? const LiupanButton(
                  key: ValueKey('confirmed-cta'),
                  label: '已确认写入 ✓',
                  background: LiupanColors.green,
                  foreground: Colors.white,
                  onPressed: null,
                )
              : LiupanButton(
                  key: const ValueKey('confirm-write'),
                  label: confirming ? '写入中…' : '确认写入',
                  background: LiupanColors.amber,
                  foreground: LiupanColors.ink,
                  onPressed: confirming ? null : onConfirm,
                ),
        ),
      ],
    );
  }
}
