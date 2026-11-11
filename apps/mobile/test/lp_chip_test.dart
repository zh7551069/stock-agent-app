import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:liupan_mobile/theme/liupan_theme.dart';
import 'package:liupan_mobile/widgets/lp_chip.dart';

void main() {
  test('未确认与其它角标走同一张变体表', () {
    final unconfirmed = lpChipStyle(LpChipVariant.unconfirmed);
    expect(unconfirmed.background, LiupanColors.amberWash);
    expect(unconfirmed.foreground, LiupanColors.amber);
    expect(unconfirmed.border, LiupanColors.amber);
    expect(unconfirmed.fontSize, 11);
    expect(unconfirmed.weight, FontWeight.w600);
    expect(unconfirmed.padding, const EdgeInsets.symmetric(horizontal: 8, vertical: 3));

    final near = lpChipStyle(LpChipVariant.nearBoard);
    expect(near.background, LiupanColors.nearBoard);
    expect(near.foreground, LiupanColors.amber);
    expect(near.border, isNull);

    final tradable = lpChipStyle(LpChipVariant.tradable);
    final limitUp = lpChipStyle(LpChipVariant.limitUp);
    final water = lpChipStyle(LpChipVariant.waterLevel);
    expect(tradable.background, LiupanColors.card);
    expect(tradable.border, LiupanColors.border);
    expect(limitUp.foreground, tradable.foreground);
    expect(water.padding, tradable.padding);

    final autoTop = lpChipStyle(LpChipVariant.autoTop1);
    expect(autoTop.background, LiupanColors.amberBadge);
    expect(autoTop.border, LiupanColors.amber);
    expect(autoTop.fontSize, 10);

    final manual = lpChipStyle(LpChipVariant.manualWatch);
    expect(manual.background, LiupanColors.blueBadge);
    expect(manual.foreground, LiupanColors.blue);
    expect(manual.border, LiupanColors.blue);
  });

  testWidgets('LpChip 表面半径为 0，字体 LiupanSans', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: LpChip(
            key: ValueKey('chip'),
            variant: LpChipVariant.unconfirmed,
            label: '未确认',
          ),
        ),
      ),
    );
    final text = tester.widget<Text>(find.text('未确认'));
    expect(text.style?.fontFamily, liupanFont);
    final box = tester.widget<DecoratedBox>(
      find.descendant(
        of: find.byKey(const ValueKey('chip')),
        matching: find.byType(DecoratedBox),
      ),
    );
    final deco = box.decoration as BoxDecoration;
    expect(deco.borderRadius, BorderRadius.zero);
  });
}
