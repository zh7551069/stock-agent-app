import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:liupan_mobile/api/decision_log_client.dart';
import 'package:liupan_mobile/model/attr_models.dart';
import 'package:liupan_mobile/state/attr_controller.dart';

import 'support/fake_client.dart';

void main() {
  test('改标只改本地预勾，不调用 confirm', () async {
    final client = FakeDecisionLogClient(sampleSuggest());
    final controller = AttrController(
      client: client,
      date: '2026-11-03',
      code: '688185.SH',
      name: '康希诺',
    );
    await controller.load();
    expect(controller.localTags, ['选股对但择时错', '先手分虚高(顶格)']);
    expect(controller.showConfirmed, isFalse);
    expect(client.confirmCalls, 0);

    controller.applyRetag(['先手分虚高(顶格)', '板块背离']);
    expect(controller.localTags, ['先手分虚高(顶格)', '板块背离']);
    expect(controller.showConfirmed, isFalse);
    expect(client.confirmCalls, 0);
    expect(client.suggestCalls, 1);
  });

  test('确认写入才 POST，且带上改标后的本地集合', () async {
    final client = FakeDecisionLogClient(sampleSuggest());
    final controller = AttrController(
      client: client,
      date: '2026-11-03',
      code: '688185.SH',
      name: '康希诺',
    );
    await controller.load();
    controller.applyRetag(['板块背离']);
    await controller.confirmWrite();

    expect(client.confirmCalls, 1);
    expect(client.posted.single, ['板块背离']);
    expect(client.bodies.single.keys.toSet(), {'date', 'code', 'confirmed_tags', 'name'});
    expect(client.bodies.single.containsKey('score'), isFalse);
    expect(client.bodies.single.containsKey('qty'), isFalse);
    expect(client.bodies.single.containsKey('side'), isFalse);
    expect(controller.showConfirmed, isTrue);
    expect(controller.confirmError, isNull);
  });

  test('连续点确认只发一次', () async {
    final client = FakeDecisionLogClient(sampleSuggest());
    final gate = Completer<void>();
    client.confirmGate = () => gate.future;
    final controller = AttrController(
      client: client,
      date: '2026-11-03',
      code: '688185.SH',
    );
    await controller.load();
    final first = controller.confirmWrite();
    final second = controller.confirmWrite();
    gate.complete();
    await Future.wait([first, second]);
    expect(client.confirmCalls, 1);
    expect(controller.showConfirmed, isTrue);
  });

  test('确认失败保持未写入，再次点击才重试', () async {
    final client = FakeDecisionLogClient(sampleSuggest());
    client.confirmError = DecisionLogException('sqlite busy');
    final controller = AttrController(
      client: client,
      date: '2026-11-03',
      code: '688185.SH',
    );
    await controller.load();
    await controller.confirmWrite();
    expect(controller.showConfirmed, isFalse);
    expect(controller.confirmError, 'sqlite busy');
    expect(controller.localTags, ['选股对但择时错', '先手分虚高(顶格)']);

    client.confirmError = null;
    await controller.confirmWrite();
    expect(client.confirmCalls, 2);
    expect(controller.showConfirmed, isTrue);
  });

  test('no_data 不编造预勾', () async {
    final client = FakeDecisionLogClient(SuggestSnapshot.empty);
    final controller = AttrController(
      client: client,
      date: '2026-11-03',
      code: '688185.SH',
    );
    await controller.load();
    expect(controller.suggestedTags, isEmpty);
    expect(controller.localTags, isEmpty);
    expect(controller.needsLeaveGuard, isFalse);
    expect(client.confirmCalls, 0);

    controller.applyRetag(const ['其他']);
    expect(client.confirmCalls, 0);
    expect(controller.needsLeaveGuard, isTrue);
    await controller.confirmWrite();
    expect(client.posted.single, ['其他']);
  });

  test('已确认的建议刷新后仍是已确认，改标后要再次 confirm', () async {
    final client = FakeDecisionLogClient(const SuggestSnapshot(
      status: 'ok',
      suggestedTags: ['选股对但择时错', '先手分虚高(顶格)'],
      confirmedTags: ['选股对但择时错', '先手分虚高(顶格)'],
    ));
    final controller = AttrController(
      client: client,
      date: '2026-11-03',
      code: '688185.SH',
    );
    await controller.load();
    expect(controller.showConfirmed, isTrue);
    expect(controller.needsLeaveGuard, isFalse);
    expect(client.confirmCalls, 0);

    controller.applyRetag(const ['板块背离']);
    expect(controller.showConfirmed, isFalse);
    expect(client.confirmCalls, 0);
    await controller.confirmWrite();
    expect(client.confirmCalls, 1);
    expect(client.posted.single, ['板块背离']);
    expect(controller.showConfirmed, isTrue);
  });

  test('放弃离开不写库', () async {
    final client = FakeDecisionLogClient(sampleSuggest());
    final controller = AttrController(
      client: client,
      date: '2026-11-03',
      code: '688185.SH',
    );
    await controller.load();
    controller.applyRetag(const ['池外黑马']);
    expect(controller.needsLeaveGuard, isTrue);
    controller.discardLocal();
    expect(controller.localTags, ['选股对但择时错', '先手分虚高(顶格)']);
    expect(client.confirmCalls, 0);
  });
}
