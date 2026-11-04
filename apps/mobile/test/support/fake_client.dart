import 'package:liupan_mobile/api/decision_log_client.dart';
import 'package:liupan_mobile/model/attr_models.dart';

class FakeDecisionLogClient implements DecisionLogClient {
  FakeDecisionLogClient(this.suggest);

  SuggestSnapshot suggest;
  int suggestCalls = 0;
  int confirmCalls = 0;
  final List<List<String>> posted = [];
  final List<Map<String, dynamic>> bodies = [];
  DecisionLogException? confirmError;
  Future<void> Function()? confirmGate;

  @override
  Future<SuggestSnapshot> fetchSuggest({
    required String date,
    required String code,
  }) async {
    suggestCalls += 1;
    return suggest;
  }

  @override
  Future<ConfirmSnapshot> confirm({
    required String date,
    required String code,
    required List<String> confirmedTags,
    String? name,
  }) async {
    confirmCalls += 1;
    posted.add(List<String>.from(confirmedTags));
    bodies.add(buildConfirmPayload(
      date: date,
      code: code,
      confirmedTags: confirmedTags,
      name: name,
    ));
    if (confirmGate != null) await confirmGate!();
    if (confirmError != null) throw confirmError!;
    return ConfirmSnapshot(
      confirmedTags: List<String>.from(confirmedTags),
      suggestedTags: suggest.suggestedTags,
      tagSource: 'suggested_accepted',
      confirmedAt: '2026-11-03 10:00:00',
    );
  }
}

SuggestSnapshot sampleSuggest() {
  return const SuggestSnapshot(
    status: 'ok',
    suggestedTags: ['选股对但择时错', '先手分虚高(顶格)'],
    confirmedTags: null,
    suggestedAsOf: '2026-11-03 15:00:00',
    suggestedAt: '2026-11-03 15:00:01',
    evidence: {'from': 'selftest'},
    note: 'suggested 只用于预勾。未确认不构成正式归因。',
  );
}
