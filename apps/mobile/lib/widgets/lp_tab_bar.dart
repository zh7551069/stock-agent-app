import 'package:flutter/material.dart';

import '../shell/lp_tab_selection.dart';
import '../theme/liupan_theme.dart';
import 'lp_tab_icon.dart';

/// 底栏五栏。高 52 加系统底 inset；inset 为 20 时总高 72。
/// 背景与内描边对齐稿面 `rgb(21,27,36)` / `rgb(36,48,65)`。
class LpTabBar extends StatelessWidget {
  const LpTabBar({super.key, required this.selected, required this.onSelect});

  final LpRootTab selected;
  final ValueChanged<LpRootTab> onSelect;

  @override
  Widget build(BuildContext context) {
    final inset = MediaQuery.paddingOf(context).bottom;
    return DecoratedBox(
      key: const ValueKey('lp-tab-bar'),
      decoration: const BoxDecoration(
        color: LiupanColors.card,
        border: Border.fromBorderSide(BorderSide(color: LiupanColors.border)),
      ),
      child: Padding(
        padding: EdgeInsets.fromLTRB(4, 8, 4, inset),
        child: SizedBox(
          height: 44,
          child: Row(
            children: [
              for (final tab in LpRootTab.values)
                Expanded(
                  child: _LpTabButton(
                    tab: tab,
                    selected: tab == selected,
                    onTap: () => onSelect(tab),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _LpTabButton extends StatelessWidget {
  const _LpTabButton({
    required this.tab,
    required this.selected,
    required this.onTap,
  });

  final LpRootTab tab;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = selected ? LiupanColors.amber : LiupanColors.muted;
    return Semantics(
      button: true,
      selected: selected,
      label: tab.label,
      child: InkWell(
        key: ValueKey('tab-${tab.label}'),
        onTap: onTap,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            LpTabIcon(tab: tab, color: color),
            const SizedBox(height: 4),
            Text(
              tab.label,
              key: selected ? ValueKey('tab-on-${tab.label}') : null,
              style: TextStyle(
                fontFamily: liupanFont,
                fontSize: 11,
                height: 1,
                fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                color: color,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
