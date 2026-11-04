import 'package:flutter/material.dart';

/// 帧面色板。直角 / 小圆角，不自拟大圆角。
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

/// 触控高度至少 36。稿面按钮约 48，圆角 2。
class LiupanButton extends StatelessWidget {
  const LiupanButton({
    super.key,
    required this.label,
    required this.background,
    required this.foreground,
    required this.onPressed,
  });

  final String label;
  final Color background;
  final Color foreground;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 48,
      child: TextButton(
        onPressed: onPressed,
        style: TextButton.styleFrom(
          backgroundColor: background,
          foregroundColor: foreground,
          disabledBackgroundColor: background,
          disabledForegroundColor: foreground,
          minimumSize: const Size(0, 48),
          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
          shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(2)),
          ),
          textStyle: const TextStyle(
            fontFamily: liupanFont,
            fontSize: 16,
            fontWeight: FontWeight.w600,
          ),
        ),
        child: Text(label),
      ),
    );
  }
}
