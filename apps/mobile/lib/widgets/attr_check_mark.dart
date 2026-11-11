import 'package:flutter/material.dart';

import '../theme/liupan_theme.dart';

/// 归因勾选方块。18×18，直角。选中铺琥珀，勾用页面底色。
class AttrCheckMark extends StatelessWidget {
  const AttrCheckMark({super.key, required this.checked, required this.fill});

  final bool checked;
  final Color fill;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 18,
      height: 18,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: checked ? LiupanColors.amber : fill,
        border: Border.all(
          color: checked ? LiupanColors.amber : LiupanColors.border,
        ),
        borderRadius: LiupanRadii.surface,
      ),
      child: checked
          ? const Text(
              '✓',
              style: TextStyle(
                fontFamily: liupanFont,
                color: LiupanColors.bg,
                fontSize: 12,
                fontWeight: FontWeight.w600,
                height: 1,
              ),
            )
          : null,
    );
  }
}
