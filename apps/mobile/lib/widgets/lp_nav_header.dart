import 'package:flutter/material.dart';

import '../theme/liupan_theme.dart';

/// 根页大标题 + 副标；二级页返回 + 标题 + 右侧文本按钮。
class LpNavHeader extends StatelessWidget {
  const LpNavHeader.root({
    super.key,
    required this.title,
    this.subtitle,
    this.trailing,
    this.titleMaxLines = 1,
  }) : onBack = null,
       backLabel = null,
       actionLabel = null,
       onAction = null,
       centeredTitle = false,
       backKey = null,
       actionKey = null;

  const LpNavHeader.stack({
    super.key,
    required this.title,
    this.subtitle,
    required this.onBack,
    this.backLabel,
    this.actionLabel,
    this.onAction,
    this.centeredTitle = false,
    this.titleMaxLines = 1,
    this.backKey = const ValueKey('header-back'),
    this.actionKey,
  }) : trailing = null;

  final String title;
  final String? subtitle;
  final Widget? trailing;
  final VoidCallback? onBack;
  final String? backLabel;
  final String? actionLabel;
  final VoidCallback? onAction;
  final bool centeredTitle;
  final int titleMaxLines;
  final Key? backKey;
  final Key? actionKey;

  @override
  Widget build(BuildContext context) {
    if (onBack == null) return _root();
    return _stack();
  }

  Widget _root() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  maxLines: titleMaxLines,
                  overflow: TextOverflow.ellipsis,
                  style: _rootTitle,
                ),
                if (subtitle != null) ...[
                  const SizedBox(height: 2),
                  Text(
                    subtitle!,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: _rootSubtitle,
                  ),
                ],
              ],
            ),
          ),
          if (trailing != null) trailing!,
        ],
      ),
    );
  }

  Widget _stack() {
    final backIsLink =
        centeredTitle || (backLabel != null && backLabel!.isNotEmpty);
    final backColor = backIsLink ? LiupanColors.blue : LiupanColors.text;
    final titleStyle = centeredTitle ? _centerTitle : _stackTitle;
    final actionColor = centeredTitle ? LiupanColors.muted : LiupanColors.blue;
    final actionSize = centeredTitle ? 14.0 : 13.0;
    final actionWeight = centeredTitle ? FontWeight.w500 : FontWeight.w600;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 10),
      child: Row(
        children: [
          TextButton(
            key: backKey,
            onPressed: onBack,
            style: TextButton.styleFrom(
              foregroundColor: backColor,
              padding: EdgeInsets.zero,
              minimumSize: const Size(32, 40),
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: Text(
              backLabel == null || backLabel!.isEmpty ? '‹' : '‹ $backLabel',
              style: TextStyle(
                fontFamily: liupanFont,
                fontSize: backIsLink ? 14 : 22,
                fontWeight: backIsLink ? FontWeight.w500 : FontWeight.w400,
                color: backColor,
                height: 1,
              ),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: centeredTitle
                  ? CrossAxisAlignment.center
                  : CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  maxLines: titleMaxLines,
                  overflow: TextOverflow.ellipsis,
                  textAlign: centeredTitle ? TextAlign.center : TextAlign.start,
                  style: titleStyle,
                ),
                if (subtitle != null) ...[
                  const SizedBox(height: 2),
                  Text(
                    subtitle!,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    textAlign: centeredTitle
                        ? TextAlign.center
                        : TextAlign.start,
                    style: _stackSubtitle,
                  ),
                ],
              ],
            ),
          ),
          if (actionLabel != null)
            TextButton(
              key: actionKey,
              onPressed: onAction,
              style: TextButton.styleFrom(
                foregroundColor: actionColor,
                minimumSize: const Size(48, 36),
                padding: const EdgeInsets.symmetric(horizontal: 12),
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
              child: Text(
                actionLabel!,
                style: TextStyle(
                  fontFamily: liupanFont,
                  fontSize: actionSize,
                  fontWeight: actionWeight,
                  color: actionColor,
                ),
              ),
            )
          else
            const SizedBox(width: 8),
        ],
      ),
    );
  }
}

const _rootTitle = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.text,
  fontSize: 22,
  fontWeight: FontWeight.w700,
  height: 1.2,
);

const _rootSubtitle = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.muted,
  fontSize: 12,
  fontWeight: FontWeight.w400,
  height: 1.2,
);

const _stackTitle = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.text,
  fontSize: 16,
  fontWeight: FontWeight.w700,
  height: 1.2,
);

const _centerTitle = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.muted,
  fontSize: 14,
  fontWeight: FontWeight.w600,
  height: 1.2,
);

const _stackSubtitle = TextStyle(
  fontFamily: liupanFont,
  color: LiupanColors.muted,
  fontSize: 11,
  fontWeight: FontWeight.w400,
  height: 1.2,
);
