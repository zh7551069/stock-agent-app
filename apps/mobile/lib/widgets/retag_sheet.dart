import 'package:flutter/material.dart';

import '../model/attr_models.dart';
import '../theme/liupan_theme.dart';

/// 改标只在本地勾选。按钮是「应用改标」，这里没有 confirm 客户端。
class RetagSheet extends StatefulWidget {
  const RetagSheet({
    super.key,
    required this.initial,
    required this.catalog,
    required this.onCancel,
    required this.onApply,
  });

  final List<String> initial;
  final List<String> catalog;
  final VoidCallback onCancel;
  final ValueChanged<List<String>> onApply;

  @override
  State<RetagSheet> createState() => _RetagSheetState();
}

class _RetagSheetState extends State<RetagSheet> {
  late final Set<String> _selected = widget.initial.toSet();

  void _toggle(String tag) {
    setState(() {
      if (!_selected.add(tag)) _selected.remove(tag);
    });
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final maxHeight = constraints.maxHeight.isFinite ? constraints.maxHeight * 0.78 : 520.0;
        return ConstrainedBox(
          constraints: BoxConstraints(maxHeight: maxHeight),
          child: Material(
      color: LiupanColors.card,
      child: DecoratedBox(
        decoration: const BoxDecoration(
          color: LiupanColors.card,
          border: Border(top: BorderSide(color: LiupanColors.border)),
        ),
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Expanded(
                    child: Text(
                      '改标 · 重选归因',
                      style: TextStyle(color: LiupanColors.text, fontSize: 16, fontWeight: FontWeight.w700),
                    ),
                  ),
                  TextButton(
                    key: const ValueKey('close-retag'),
                    onPressed: widget.onCancel,
                    style: TextButton.styleFrom(
                      foregroundColor: LiupanColors.muted,
                      minimumSize: const Size(48, 36),
                    ),
                    child: const Text('关闭'),
                  ),
                ],
              ),
              const Text(
                '仅改本地预勾 · 不写决策记录 · 写库只走页底「确认写入」（一次确认）',
                style: TextStyle(color: LiupanColors.muted, fontSize: 12, height: 1.4),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final tag in widget.catalog)
                    _SheetChip(
                      tag: tag,
                      checked: _selected.contains(tag),
                      onTap: () => _toggle(tag),
                    ),
                ],
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(
                    flex: 2,
                    child: LiupanButton(
                      key: const ValueKey('cancel-retag'),
                      label: '取消',
                      background: LiupanColors.buttonDark,
                      foreground: LiupanColors.text,
                      onPressed: widget.onCancel,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    flex: 3,
                    child: LiupanButton(
                      key: const ValueKey('apply-retag'),
                      label: '应用改标',
                      background: LiupanColors.amber,
                      foreground: LiupanColors.ink,
                      onPressed: () => widget.onApply(orderTags(_selected.toList(), widget.catalog)),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
          ),
        );
      },
    );
  }
}

class _SheetChip extends StatelessWidget {
  const _SheetChip({required this.tag, required this.checked, required this.onTap});

  final String tag;
  final bool checked;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = checked ? LiupanColors.amber : LiupanColors.muted;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        key: ValueKey('sheet-chip-$tag'),
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 36),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
          decoration: BoxDecoration(
            color: checked ? LiupanColors.suggestedFill : LiupanColors.chip,
            border: Border.all(color: checked ? LiupanColors.amber : LiupanColors.border, width: checked ? 1.5 : 1),
            borderRadius: LiupanRadii.surface,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 16,
                height: 16,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: checked ? LiupanColors.amber : Colors.transparent,
                  border: Border.all(color: checked ? LiupanColors.amber : LiupanColors.dim),
                  borderRadius: LiupanRadii.surface,
                ),
                child: checked ? const Icon(Icons.check, size: 12, color: LiupanColors.ink) : null,
              ),
              const SizedBox(width: 8),
              Text(tag, style: TextStyle(color: color, fontSize: 13)),
            ],
          ),
        ),
      ),
    );
  }
}
