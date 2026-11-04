import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:liupan_mobile/api/decision_log_client.dart';
import 'package:liupan_mobile/api/http_decision_log_client.dart';

http.Response jsonResponse(Object body, int status) {
  return http.Response.bytes(
    utf8.encode(jsonEncode(body)),
    status,
    headers: const {'content-type': 'application/json; charset=utf-8'},
  );
}

void main() {
  test('suggest / confirm 打到钉死的路径，confirm 只带约定字段', () async {
    final requests = <http.Request>[];
    final mock = MockClient((request) async {
      requests.add(request);
      if (request.method == 'GET') {
        return jsonResponse({
          'ok': true,
          'status': 'ok',
          'suggested_tags': ['选股对但择时错'],
          'confirmed_tags': null,
          'suggested_asof': '2026-11-03 15:00:00',
          'evidence': {'from': 'selftest'},
        }, 200);
      }
      return jsonResponse({
        'ok': true,
        'status': 'ok',
        'confirmed_tags': ['板块背离'],
        'suggested_tags': ['选股对但择时错'],
        'tag_source': 'mixed',
        'confirmed_at': '2026-11-03 15:05:00',
      }, 200);
    });
    final client = HttpDecisionLogClient(
      baseUrl: () => 'http://127.0.0.1:8770/',
      httpClient: mock,
    );

    final suggest = await client.fetchSuggest(date: '2026-11-03', code: '688185.SH');
    expect(suggest.suggestedTags, ['选股对但择时错']);
    expect(suggest.confirmedTags, isNull);
    expect(suggest.suggestedAsOf, '2026-11-03 15:00:00');
    expect(requests.single.method, 'GET');
    expect(requests.single.url.path, HttpDecisionLogClient.suggestPath);
    expect(requests.single.url.queryParameters['code'], '688185.SH');
    expect(requests.single.url.queryParameters['date'], '2026-11-03');

    final confirmed = await client.confirm(
      date: '2026-11-03',
      code: '688185.SH',
      confirmedTags: const ['板块背离'],
      name: '康希诺',
    );
    expect(confirmed.confirmedTags, ['板块背离']);
    expect(confirmed.tagSource, 'mixed');
    final post = requests[1];
    expect(post.method, 'POST');
    expect(post.url.path, HttpDecisionLogClient.confirmPath);
    final body = jsonDecode(post.body) as Map<String, dynamic>;
    expect(body.keys.toSet(), {'date', 'code', 'confirmed_tags', 'name'});
    expect(body['confirmed_tags'], ['板块背离']);
  });

  test('confirm 失败不把响应当成已写入', () async {
    final mock = MockClient((request) async {
      return jsonResponse({
        'ok': false,
        'status': 'error',
        'error': {'code': 'bad_request', 'message': 'code 必须为后缀式，如 000001.SZ'},
      }, 400);
    });
    final client = HttpDecisionLogClient(
      baseUrl: () => 'http://127.0.0.1:8770',
      httpClient: mock,
    );
    expect(
      () => client.confirm(
        date: '2026-11-03',
        code: '688185',
        confirmedTags: const ['其他'],
      ),
      throwsA(isA<DecisionLogException>().having((error) => error.code, 'code', 'bad_request')),
    );
  });
}
