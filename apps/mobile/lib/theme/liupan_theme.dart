import 'package:flutter/material.dart';

/// 帧面色板。沿用这一套，不另起色板。
class LiupanColors {
  const LiupanColors._();

  static const bg = Color(0xFF0B0F14);
  static const card = Color(0xFF151B24);
  static const border = Color(0xFF243041);
  static const text = Color(0xFFE8EEF7);
  static const muted = Color(0xFF8B9BB4);
  static const dim = Color(0xFF5C6B82);
  static const amber = Color(0xFFF59E0B);
  static const blue = Color(0xFF3B82F6);
  static const green = Color(0xFF22C55E);
  static const red = Color(0xFFEF4444);
  static const ink = Color(0xFF1A1203);
  static const chip = Color(0xFF10161E);
  static const suggestedFill = Color(0xFF1A160C);
  static const buttonDark = Color(0xFF1C2430);

  /// 遮罩。约 `rgba(0,0,0,0.67)`。
  static const scrim = Color.fromRGBO(0, 0, 0, 0.67);

  /// 复盘 v1 帧面上的浅色铺底。主按钮字色仍用 [ink]。
  static const amberWash = Color.fromRGBO(245, 158, 11, 0.13);
  static const amberStatus = Color.fromRGBO(245, 158, 11, 0.20);
  static const amberBadge = Color.fromRGBO(245, 158, 11, 0.18);
  static const greenWash = Color.fromRGBO(34, 197, 94, 0.13);
  static const blueWash = Color.fromRGBO(59, 130, 246, 0.13);
  static const blueBadge = Color.fromRGBO(59, 130, 246, 0.18);
  static const toastGreen = Color(0xFF14532D);
  static const nearBoard = Color(0xFF1E2A3A);
}

/// 表面、按钮、卡片直角。开关轨道 999；时间线 / 状态圆点用直径的 50%（[BoxShape.circle]）。
class LiupanRadii {
  const LiupanRadii._();

  static const surface = BorderRadius.all(Radius.zero);
  static const switchTrack = BorderRadius.all(Radius.circular(999));
}

const liupanFont = 'LiupanSans';

ThemeData buildLiupanTheme() {
  const scheme = ColorScheme.dark(
    surface: LiupanColors.bg,
    primary: LiupanColors.amber,
    onPrimary: LiupanColors.ink,
    secondary: LiupanColors.blue,
    error: LiupanColors.red,
  );
  final base = ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    colorScheme: scheme,
    scaffoldBackgroundColor: LiupanColors.bg,
    canvasColor: LiupanColors.bg,
    fontFamily: liupanFont,
    splashFactory: NoSplash.splashFactory,
    highlightColor: Colors.transparent,
    hoverColor: Colors.transparent,
  );
  return base.copyWith(
    textTheme: base.textTheme.apply(
      fontFamily: liupanFont,
      bodyColor: LiupanColors.text,
      displayColor: LiupanColors.text,
    ),
    dividerColor: LiupanColors.border,
  );
}

/// 触控高度至少 36。主按钮高度约 48，表面直角。
class LiupanButton extends StatelessWidget {
  const LiupanButton({
    super.key,
    required this.label,
    required this.background,
    required this.foreground,
    required this.onPressed,
    this.borderColor,
    this.height = 48,
    this.fontSize = 16,
    this.fontWeight = FontWeight.w600,
  });

  final String label;
  final Color background;
  final Color foreground;
  final VoidCallback? onPressed;
  final Color? borderColor;
  final double height;
  final double fontSize;
  final FontWeight fontWeight;

  @override
  Widget build(BuildContext context) {
    final side = borderColor == null
        ? BorderSide.none
        : BorderSide(color: borderColor!);
    return SizedBox(
      height: height,
      child: TextButton(
        onPressed: onPressed,
        style: ButtonStyle(
          backgroundColor: WidgetStateProperty.all(background),
          foregroundColor: WidgetStateProperty.all(foreground),
          overlayColor: WidgetStateProperty.all(Colors.transparent),
          elevation: WidgetStateProperty.all(0),
          minimumSize: WidgetStateProperty.all(Size(0, height)),
          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
          padding: WidgetStateProperty.all(EdgeInsets.zero),
          shape: WidgetStateProperty.all(
            RoundedRectangleBorder(
              borderRadius: LiupanRadii.surface,
              side: side,
            ),
          ),
          textStyle: WidgetStateProperty.all(
            TextStyle(
              fontFamily: liupanFont,
              fontSize: fontSize,
              fontWeight: fontWeight,
            ),
          ),
        ),
        child: Text(label),
      ),
    );
  }
}
