import 'package:flutter/material.dart';

import '../model/attr_models.dart';
import '../theme/liupan_theme.dart';
import 'attr_check_mark.dart';

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
        final maxHeight = constraints.maxHeight.isFinite
            ? constraints.maxHeight * 0.78
            : 520.0;
        return ConstrainedBox(
          constraints: BoxConstraints(maxHeight: maxHeight),
          child: Material(
            color: LiupanColors.card,
            child: DecoratedBox(
              decoration: const BoxDecoration(
                color: LiupanColors.card,
                border: Border.fromBorderSide(
                  BorderSide(color: LiupanColors.border),
                ),
                borderRadius: LiupanRadii.surface,
              ),
              child: SingleChildScrollView(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        const Expanded(
                          child: Text(
                            '改标 · 重选归因',
                            style: TextStyle(
                              fontFamily: liupanFont,
                              color: LiupanColors.text,
                              fontSize: 16,
                              fontWeight: FontWeight.w700,
                              height: 1.2,
                            ),
                          ),
                        ),
                        TextButton(
                          key: const ValueKey('close-retag'),
                          onPressed: widget.onCancel,
                          style: TextButton.styleFrom(
                            foregroundColor: LiupanColors.blue,
                            minimumSize: const Size(48, 36),
                            padding: const EdgeInsets.symmetric(horizontal: 8),
                            tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                          ),
                          child: const Text(
                            '关闭',
                            style: TextStyle(
                              fontFamily: liupanFont,
                              color: LiupanColors.blue,
                              fontSize: 13,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    const Text(
                      '仅改本地预勾 · 不写决策记录 · 写库只走页底「确认写入」（一次确认）',
                      style: TextStyle(
                        fontFamily: liupanFont,
                        color: LiupanColors.muted,
                        fontSize: 11,
                        fontWeight: FontWeight.w400,
                        height: 1.4,
                      ),
                    ),
                    const SizedBox(height: 12),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final tag in widget.catalog)
                          _SheetChip(
                            tag: tag,
                            checked: _selected.contains(tag),
                            onTap: () => _toggle(tag),
                          ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        Expanded(
                          child: LiupanButton(
                            key: const ValueKey('cancel-retag'),
                            label: '取消',
                            background: LiupanColors.bg,
                            foreground: LiupanColors.text,
                            borderColor: LiupanColors.border,
                            fontSize: 14,
                            onPressed: widget.onCancel,
                          ),
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: LiupanButton(
                            key: const ValueKey('apply-retag'),
                            label: '应用改标',
                            background: LiupanColors.amber,
                            foreground: LiupanColors.ink,
                            fontSize: 14,
                            onPressed: () => widget.onApply(
                              orderTags(_selected.toList(), widget.catalog),
                            ),
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
  const _SheetChip({
    required this.tag,
    required this.checked,
    required this.onTap,
  });

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
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 12),
          decoration: BoxDecoration(
            color: checked ? LiupanColors.amberWash : LiupanColors.bg,
            border: Border.all(
              color: checked ? LiupanColors.amber : LiupanColors.border,
            ),
            borderRadius: LiupanRadii.surface,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              AttrCheckMark(checked: checked, fill: LiupanColors.bg),
              const SizedBox(width: 6),
              Text(
                tag,
                style: TextStyle(
                  fontFamily: liupanFont,
                  color: color,
                  fontSize: 13,
                  fontWeight: checked ? FontWeight.w600 : FontWeight.w400,
                  height: 1.2,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
