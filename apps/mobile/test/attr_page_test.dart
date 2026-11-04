import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:liupan_mobile/app.dart';
import 'package:liupan_mobile/model/attr_models.dart';
import 'package:liupan_mobile/pages/attr_surface.dart';
import 'package:liupan_mobile/state/attr_controller.dart';
import 'package:liupan_mobile/theme/liupan_theme.dart';

import 'support/fake_client.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  void usePhone(WidgetTester tester) {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
  }

  Future<AttrController> loaded(FakeDecisionLogClient client) async {
    final controller = AttrController(
      client: client,
      date: kSampleDate,
      code: kSampleCode,
      name: kSampleName,
    );
    await controller.load();
    return controller;
  }

  Future<void> tapKey(WidgetTester tester, String name) async {
    final finder = find.byKey(ValueKey(name));
    await tester.ensureVisible(finder);
    await tester.pump();
    await tester.tap(finder);
    await tester.pump();
  }

  Future<void> pumpSurface(
    WidgetTester tester, {
    required AttrController controller,
    AttrChrome chrome = AttrChrome.fullPage,
  }) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: buildLiupanTheme(),
        home: AttrSurface(
          controller: controller,
          chrome: chrome,
          bus: LeaveBus(),
          onBack: () {},
        ),
      ),
    );
    await tester.pump();
  }

  testWidgets('改标应用后不发 confirm，确认写入才 POST 一次', (tester) async {
    usePhone(tester);
    final client = FakeDecisionLogClient(sampleSuggest());
    final controller = await loaded(client);
    await pumpSurface(tester, controller: controller);

    expect(find.text('归因建议 · 须确认写入'), findsOneWidget);
    expect(find.text('建议'), findsNWidgets(2));
    expect(find.text('未写入决策记录'), findsOneWidget);
    expect(find.text('0%'), findsNothing);
    expect(find.text('0.72'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('open-retag')));
    await tester.pump();
    expect(find.text('归因建议 · 改标中'), findsOneWidget);
    expect(find.textContaining('仅改本地预勾'), findsOneWidget);
    expect(find.text('应用改标'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('sheet-chip-板块背离')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('apply-retag')));
    await tester.pump();

    expect(client.confirmCalls, 0);
    expect(controller.localTags, contains('板块背离'));
    expect(find.text('确认写入'), findsOneWidget);
    expect(find.text('已确认写入 ✓'), findsNothing);

    await tapKey(tester, 'confirm-write');

    expect(client.confirmCalls, 1);
    expect(client.posted.single, containsAll(['选股对但择时错', '先手分虚高(顶格)', '板块背离']));
    expect(find.text('归因建议 · 已确认'), findsOneWidget);
    expect(find.text('归因已写入决策记录'), findsOneWidget);
    expect(find.text('已确认写入 ✓'), findsOneWidget);
    expect(find.text('建议'), findsNothing);
    expect(find.text('归因已确认'), findsOneWidget);

    await tapKey(tester, 'confirmed-cta');
    expect(client.confirmCalls, 1);
  });

  testWidgets('成功后再改标，要再点一次确认写入', (tester) async {
    usePhone(tester);
    final client = FakeDecisionLogClient(sampleSuggest());
    final controller = await loaded(client);
    await pumpSurface(tester, controller: controller);
    await tapKey(tester, 'confirm-write');
    expect(client.confirmCalls, 1);

    await tester.tap(find.byKey(const ValueKey('open-retag')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('sheet-chip-选股对但择时错')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('apply-retag')));
    await tester.pump();
    expect(client.confirmCalls, 1);
    expect(find.text('确认写入'), findsOneWidget);

    await tapKey(tester, 'confirm-write');
    expect(client.confirmCalls, 2);
    expect(client.posted.last, ['先手分虚高(顶格)']);
  });

  testWidgets('未确认离开会拦截，放弃不写库', (tester) async {
    usePhone(tester);
    final client = FakeDecisionLogClient(sampleSuggest());
    final controller = await loaded(client);
    var backs = 0;
    await tester.pumpWidget(
      MaterialApp(
        theme: buildLiupanTheme(),
        home: AttrSurface(
          controller: controller,
          chrome: AttrChrome.fullPage,
          bus: LeaveBus(),
          onBack: () => backs += 1,
        ),
      ),
    );
    await tester.pump();

    await tester.tap(find.byKey(const ValueKey('header-back')));
    await tester.pump();
    expect(find.text('尚未确认写入'), findsOneWidget);
    expect(find.text('归因建议 · 离开拦截'), findsOneWidget);
    expect(backs, 0);

    await tester.tap(find.byKey(const ValueKey('stay-confirm')));
    await tester.pump();
    expect(backs, 0);
    expect(client.confirmCalls, 0);

    await tester.tap(find.byKey(const ValueKey('header-back')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('discard-leave')));
    await tester.pump();
    expect(backs, 1);
    expect(client.confirmCalls, 0);
  });

  testWidgets('复盘卡是 8:99 的未确认入口，没有已写入状态行', (tester) async {
    usePhone(tester);
    final client = FakeDecisionLogClient(sampleSuggest());
    final controller = await loaded(client);
    await pumpSurface(tester, controller: controller, chrome: AttrChrome.reviewCard);

    expect(find.text('单票复盘 · 2026-11-03'), findsOneWidget);
    expect(find.text('未确认'), findsOneWidget);
    expect(find.text('须确认后写入 · 不自动落库'), findsOneWidget);
    expect(find.text('未写入决策记录'), findsNothing);
    expect(find.text('建议'), findsNWidgets(2));
    expect(client.confirmCalls, 0);
  });

  testWidgets('证据区露出 suggested_asof，且只读', (tester) async {
    usePhone(tester);
    final client = FakeDecisionLogClient(sampleSuggest());
    final controller = await loaded(client);
    await pumpSurface(tester, controller: controller);
    await tester.tap(find.byKey(const ValueKey('evidence-toggle')));
    await tester.pump();
    expect(find.textContaining('suggested_asof  2026-11-03 15:00:00'), findsOneWidget);
    expect(find.textContaining('from: selftest'), findsOneWidget);
    expect(client.confirmCalls, 0);
  });

  testWidgets('复盘列表点进复盘卡会拉 suggest，不拉 confirm', (tester) async {
    usePhone(tester);
    final client = FakeDecisionLogClient(sampleSuggest());
    await tester.pumpWidget(LiupanApp(client: client));
    await tester.pump();

    await tester.tap(find.byKey(const ValueKey('open-review-sample')));
    await tester.pump();
    await tester.pump();

    expect(find.text('单票复盘 · 2026-11-03'), findsOneWidget);
    expect(find.text('须确认后写入 · 不自动落库'), findsOneWidget);
    expect(client.suggestCalls, 1);
    expect(client.confirmCalls, 0);

    await tester.tap(find.byKey(const ValueKey('tab-设置')));
    await tester.pump();
    expect(find.text('尚未确认写入'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('discard-leave')));
    await tester.pump();
    expect(find.text('BFF 地址'), findsOneWidget);
    expect(client.confirmCalls, 0);
  });
}
