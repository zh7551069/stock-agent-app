import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:liupan_mobile/app.dart';
import 'package:liupan_mobile/model/attr_models.dart';
import 'package:liupan_mobile/shell/lp_tab_selection.dart';
import 'package:liupan_mobile/theme/liupan_theme.dart';
import 'package:liupan_mobile/widgets/lp_nav_header.dart';
import 'package:liupan_mobile/widgets/lp_tab_icon.dart';

import 'support/fake_client.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void usePhone(WidgetTester tester) {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    tester.view.padding = const FakeViewPadding(top: 44, bottom: 20);
    tester.view.viewPadding = const FakeViewPadding(top: 44, bottom: 20);
    addTearDown(tester.view.reset);
  }

  test('底栏选中规则：详情保持先手榜，复盘子页保持复盘，观察保持来源栏', () {
    expect(
      const LpTabSelection.root(LpRootTab.candidate).selected,
      LpRootTab.candidate,
    );
    expect(
      const LpTabSelection.stack(
        root: LpRootTab.candidate,
        page: LpStackPage.detailFromRank,
      ).selected,
      LpRootTab.rank,
    );
    expect(
      const LpTabSelection.stack(
        root: LpRootTab.candidate,
        page: LpStackPage.reviewChild,
      ).selected,
      LpRootTab.review,
    );
    expect(
      const LpTabSelection.stack(
        root: LpRootTab.holdings,
        page: LpStackPage.watch,
        watchSource: LpRootTab.rank,
      ).selected,
      LpRootTab.rank,
    );
    expect(
      const LpTabSelection.stack(
        root: LpRootTab.candidate,
        page: LpStackPage.watch,
        watchSource: LpRootTab.review,
      ).selected,
      LpRootTab.review,
    );
    expect(LpRootTab.values.map((tab) => tab.label).toList(), [
      '候选',
      '先手榜',
      '复盘',
      '持仓',
      '设置',
    ]);
    expect(LiupanRadii.surface, BorderRadius.zero);
    expect(
      LiupanRadii.switchTrack,
      const BorderRadius.all(Radius.circular(999)),
    );
    expect(LiupanColors.scrim, const Color.fromRGBO(0, 0, 0, 0.67));
  });

  test('底栏线框 path 与本批 Bridge svg 一致', () {
    final repo = _repoRoot();
    final mainHtml = File(
      '${repo.path}/design/bridge-export/stock/design/v1 主界面/index.html',
    ).readAsStringSync();
    final reviewHtml = File(
      '${repo.path}/design/bridge-export/stock/design/复盘 v1/index.html',
    ).readAsStringSync();
    final svgDir = Directory(
      '${repo.path}/design/bridge-export/stock/design/v1 主界面/svgs',
    );

    for (final tab in LpRootTab.values) {
      for (final fragment in lpTabIconFragments(tab)) {
        final svg = File('${svgDir.path}/${fragment.file}').readAsStringSync();
        expect(svg, contains('d="${fragment.d}"'), reason: fragment.file);
        expect(
          svg,
          contains('viewBox="${fragment.viewBox}"'),
          reason: fragment.file,
        );
        expect(
          svg,
          contains('stroke-width="${fragment.strokeWidth}"'),
          reason: fragment.file,
        );
        expect(svg, contains('stroke="#F59E0B"'), reason: fragment.file);
        final placed = RegExp(
          'left:${RegExp.escape(fragment.left)}px;top:${RegExp.escape(fragment.top)}px;'
          'width:${RegExp.escape(fragment.width)}px;height:${RegExp.escape(fragment.height)}px;'
          '[\\s\\S]{0,240}?svgs/${RegExp.escape(fragment.file)}',
        );
        expect(
          placed.hasMatch(mainHtml),
          isTrue,
          reason: '${tab.label} ${fragment.file}',
        );
        final path = parseSvgPath(fragment.d);
        expect(path.computeMetrics().isNotEmpty, isTrue, reason: fragment.file);
      }
    }

    final start = reviewHtml.indexOf('class="frame tabbar');
    expect(start, greaterThanOrEqualTo(0));
    final nextFrame = reviewHtml.indexOf('class="frame absolute', start + 10);
    final chunk = reviewHtml.substring(
      start,
      nextFrame > start ? nextFrame : start + 20000,
    );
    expect(chunk, contains('h-[72px]'));
    expect(chunk, contains('background:rgb(21,27,36)'));
    expect(chunk, contains('rgb(36,48,65)'));
    final labels = ['候选', '先手榜', '复盘', '持仓', '设置'];
    final positions = labels.map(chunk.indexOf).toList();
    for (var i = 0; i < positions.length; i++) {
      expect(positions[i], greaterThanOrEqualTo(0), reason: labels[i]);
      if (i > 0) {
        expect(positions[i], greaterThan(positions[i - 1]));
      }
    }
    expect(chunk, isNot(contains('>观察<')));
  });

  testWidgets('冷启动在候选，五栏顺序与顶栏对稿，根级切换不叠返回', (tester) async {
    usePhone(tester);
    await tester.pumpWidget(
      LiupanApp(client: FakeDecisionLogClient(sampleSuggest())),
    );
    await tester.pump();

    expect(find.text('蓄势池'), findsOneWidget);
    expect(find.text('首板前蓄势 · 非已连板'), findsOneWidget);
    expect(find.byKey(const ValueKey('tab-on-候选')), findsOneWidget);
    expect(find.text('观察'), findsNothing);
    expect(find.byIcon(Icons.settings_outlined), findsNothing);

    final title = tester.widget<Text>(find.text('蓄势池'));
    expect(title.style?.fontFamily, liupanFont);
    expect(title.style?.fontSize, 22);

    final bar = tester.getSize(find.byKey(const ValueKey('lp-tab-bar')));
    expect(bar.height, 72);
    final decoration =
        tester
                .widget<DecoratedBox>(find.byKey(const ValueKey('lp-tab-bar')))
                .decoration
            as BoxDecoration;
    final border = decoration.border! as Border;
    expect(decoration.color, LiupanColors.card);
    expect(border.top.color, LiupanColors.border);

    var previous = -1.0;
    for (final label in ['候选', '先手榜', '复盘', '持仓', '设置']) {
      final dx = tester.getTopLeft(find.byKey(ValueKey('tab-$label'))).dx;
      expect(dx, greaterThan(previous));
      previous = dx;
    }

    await tester.tap(find.byKey(const ValueKey('tab-先手榜')));
    await tester.pump();
    expect(find.text('蓄势先手榜'), findsOneWidget);
    expect(find.text('先手分排序'), findsOneWidget);
    expect(find.byKey(const ValueKey('tab-on-先手榜')), findsOneWidget);
    expect(find.text('‹'), findsNothing);

    await tester.tap(find.byKey(const ValueKey('tab-候选')));
    await tester.pump();
    expect(find.text('蓄势池'), findsOneWidget);
    expect(find.byKey(const ValueKey('tab-on-候选')), findsOneWidget);
  });

  testWidgets('复盘卡保持复盘选中；改标与离开拦截不带底栏', (tester) async {
    usePhone(tester);
    final client = FakeDecisionLogClient(sampleSuggest());
    await tester.pumpWidget(LiupanApp(client: client));
    await tester.pump();

    await tester.tap(find.byKey(const ValueKey('tab-复盘')));
    await tester.pump();
    expect(find.text('复盘'), findsWidgets);
    expect(find.byKey(const ValueKey('tab-on-复盘')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('open-review-sample')));
    await tester.pump();
    await tester.pump();

    expect(find.text('单票复盘 · 2026-11-03'), findsOneWidget);
    expect(find.byKey(const ValueKey('tab-on-复盘')), findsOneWidget);
    expect(find.byKey(const ValueKey('lp-tab-bar')), findsOneWidget);
    expect(client.suggestCalls, 1);
    expect(client.confirmCalls, 0);

    final retag = find.byKey(const ValueKey('open-retag'));
    await tester.ensureVisible(retag);
    await tester.pump();
    await tester.tap(retag);
    await tester.pump();
    expect(find.text('改标 · 重选归因'), findsOneWidget);
    expect(find.byKey(const ValueKey('lp-tab-bar')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('close-retag')));
    await tester.pump();
    expect(find.byKey(const ValueKey('lp-tab-bar')), findsOneWidget);
    expect(find.byKey(const ValueKey('tab-on-复盘')), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('tab-持仓')));
    await tester.pump();
    expect(find.text('尚未确认写入'), findsOneWidget);
    expect(find.byKey(const ValueKey('lp-tab-bar')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('discard-leave')));
    await tester.pump();
    expect(find.text('本地记账仓位 · 不接券商'), findsOneWidget);
    expect(find.byKey(const ValueKey('tab-on-持仓')), findsOneWidget);
    expect(client.confirmCalls, 0);
  });

  testWidgets('深链归因是复盘子页，底栏保持复盘', (tester) async {
    usePhone(tester);
    final client = FakeDecisionLogClient(sampleSuggest());
    await tester.pumpWidget(
      LiupanApp(
        client: client,
        launch: const LaunchTarget(
          date: kSampleDate,
          code: kSampleCode,
          name: kSampleName,
        ),
      ),
    );
    await tester.pump();
    await tester.pump();

    expect(find.text('归因建议 · 须确认写入'), findsOneWidget);
    expect(find.byKey(const ValueKey('tab-on-复盘')), findsOneWidget);
    expect(find.text('蓄势池'), findsNothing);
    expect(client.confirmCalls, 0);
  });

  testWidgets('二级顶栏可以是返回加居中标题加右侧文本', (tester) async {
    usePhone(tester);
    await tester.pumpWidget(
      MaterialApp(
        theme: buildLiupanTheme(),
        home: Scaffold(
          body: LpNavHeader.stack(
            title: '单票详情',
            onBack: () {},
            backLabel: '返回榜单',
            actionLabel: '分享',
            onAction: () {},
            centeredTitle: true,
            actionKey: const ValueKey('header-share'),
          ),
        ),
      ),
    );
    expect(find.text('‹ 返回榜单'), findsOneWidget);
    expect(find.text('单票详情'), findsOneWidget);
    expect(find.text('分享'), findsOneWidget);
    final back = tester.widget<Text>(find.text('‹ 返回榜单'));
    expect(back.style?.fontFamily, liupanFont);
    expect(back.style?.color, LiupanColors.blue);
  });
}

Directory _repoRoot() {
  var dir = Directory.current;
  for (var i = 0; i < 8; i++) {
    if (Directory('${dir.path}/design/bridge-export/stock/design').existsSync()) {
      return dir;
    }
    final parent = dir.parent;
    if (parent.path == dir.path) break;
    dir = parent;
  }
  throw StateError('找不到 design/bridge-export，cwd=${Directory.current.path}');
}
