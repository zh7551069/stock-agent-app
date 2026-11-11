import 'package:flutter_test/flutter_test.dart';
import 'package:liupan_mobile/model/attr_models.dart';

void main() {
  test('基准待验证与 0% 都不显示成 0%', () {
    expect(benchmarkValue(null), '待验证');
    expect(benchmarkValue('pending'), '待验证');
    expect(benchmarkValue('PENDING'), '待验证');
    expect(benchmarkValue('待验证'), '待验证');
    expect(benchmarkValue('0'), '待验证');
    expect(benchmarkValue('0%'), '待验证');
    expect(benchmarkValue('0.0'), '待验证');
    expect(benchmarkValue('0.0%'), '待验证');
    expect(benchmarkValue('1.2%'), '1.2%');
    expect(benchmarkValue('-1.5%'), '-1.5%');
  });

  test('代码归一成后缀式，帧头只显示 6 位', () {
    expect(normalizeCode('688185'), '688185.SH');
    expect(normalizeCode('688185.sh'), '688185.SH');
    expect(normalizeCode('000001'), '000001.SZ');
    expect(normalizeCode('300750'), '300750.SZ');
    expect(normalizeCode('830000'), '830000.BJ');
    expect(normalizeCode('康希诺'), isNull);
    expect(displayCode('688185.SH'), '688185');
    expect(stockTitle('康希诺', '688185.SH'), '康希诺 688185');
  });

  test('样例分数只挂在康希诺，其它代码标字段缺失', () {
    final sample = ScoreSnapshot.forCode(kSampleCode);
    expect(sample.frameSample, isTrue);
    expect(sample.rows.map((row) => row.value), containsAll(['0.72', '38%']));
    final other = ScoreSnapshot.forCode('000001.SZ');
    expect(other.frameSample, isFalse);
    expect(other.rows.every((row) => row.value == '字段缺失'), isTrue);
    expect(other.rows.any((row) => row.value.contains('0%')), isFalse);
  });

  test('confirm 请求体没有分数和买卖字段', () {
    final body = buildConfirmPayload(
      date: '2026-11-03',
      code: '688185.SH',
      confirmedTags: const ['板块背离'],
      name: '康希诺',
    );
    expect(body.keys.toSet(), {'date', 'code', 'confirmed_tags', 'name'});
    final withoutName = buildConfirmPayload(
      date: '2026-11-03',
      code: '688185.SH',
      confirmedTags: const [],
    );
    expect(withoutName.containsKey('name'), isFalse);
  });

  test('路由 /attr 与 hash 都能解析', () {
    final path = LaunchTarget.parse(Uri.parse(
      'http://127.0.0.1:8080/attr?date=2026-11-03&code=688185&name=康希诺',
    ));
    expect(path!.date, '2026-11-03');
    expect(path.code, '688185');
    expect(path.name, '康希诺');

    final hashed = LaunchTarget.parse(Uri.parse(
      'http://127.0.0.1:8080/#/attr?date=2026-11-04&code=000001.SZ',
    ));
    expect(hashed!.date, '2026-11-04');
    expect(hashed.code, '000001.SZ');
    expect(hashed.name, isNull);
    expect(LaunchTarget.parse(Uri.parse('http://127.0.0.1:8080/review')), isNull);
  });

  test('no_data 的空建议不会被词表填上', () {
    final snapshot = SuggestSnapshot.fromJson(const {
      'status': 'no_data',
      'suggested_tags': <String>[],
      'confirmed_tags': null,
    });
    expect(snapshot.suggestedTags, isEmpty);
    expect(snapshot.confirmedTags, isNull);
    expect(snapshot.hasSuggestion, isFalse);
  });
}
