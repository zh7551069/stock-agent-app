import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../model/attr_models.dart';
import 'decision_log_client.dart';

/// GET /api/liupan/decision_log/suggest
/// POST /api/liupan/decision_log/confirm
/// 正式归因只有 confirm。本类不提供别的写入口。
class HttpDecisionLogClient implements DecisionLogClient {
  HttpDecisionLogClient({
    required this.baseUrl,
    http.Client? httpClient,
    this.timeout = const Duration(seconds: 10),
  }) : _http = httpClient ?? http.Client();

  final String Function() baseUrl;
  final http.Client _http;
  final Duration timeout;

  static const suggestPath = '/api/liupan/decision_log/suggest';
  static const confirmPath = '/api/liupan/decision_log/confirm';

  @override
  Future<SuggestSnapshot> fetchSuggest({
    required String date,
    required String code,
  }) async {
    final uri = _uri(suggestPath).replace(queryParameters: {
      'date': date,
      'code': code,
    });
    final response = await _send(() => _http.get(uri, headers: _headers));
    final body = _decode(response);
    _throwIfError(response, body);
    return SuggestSnapshot.fromJson(body);
  }

  @override
  Future<ConfirmSnapshot> confirm({
    required String date,
    required String code,
    required List<String> confirmedTags,
    String? name,
  }) async {
    final response = await _send(
      () => _http.post(
        _uri(confirmPath),
        headers: _headers,
        body: jsonEncode(buildConfirmPayload(
          date: date,
          code: code,
          confirmedTags: confirmedTags,
          name: name,
        )),
      ),
    );
    final body = _decode(response);
    _throwIfError(response, body);
    return ConfirmSnapshot.fromJson(body);
  }

  Uri _uri(String path) {
    final root = baseUrl().trim().replaceAll(RegExp(r'/+$'), '');
    return Uri.parse('$root$path');
  }

  Future<http.Response> _send(Future<http.Response> Function() run) async {
    try {
      return await run().timeout(timeout);
    } on TimeoutException {
      throw DecisionLogException('归因接口超时，未写入决策记录');
    } on DecisionLogException {
      rethrow;
    } on http.ClientException catch (error) {
      throw DecisionLogException('连不上 BFF，未写入决策记录：${error.message}');
    } catch (error) {
      throw DecisionLogException('连不上 BFF，未写入决策记录（$error）');
    }
  }

  Map<String, dynamic> _decode(http.Response response) {
    try {
      final dynamic decoded = jsonDecode(utf8.decode(response.bodyBytes));
      if (decoded is Map<String, dynamic>) return decoded;
      if (decoded is Map) {
        return decoded.map((key, value) => MapEntry('$key', value));
      }
    } catch (_) {
      throw DecisionLogException('响应不是 JSON', statusCode: response.statusCode);
    }
    throw DecisionLogException('响应不是 JSON 对象', statusCode: response.statusCode);
  }

  void _throwIfError(http.Response response, Map<String, dynamic> body) {
    final failed = response.statusCode >= 400 || body['ok'] == false || body['status'] == 'error';
    if (!failed) return;
    final error = body['error'];
    var message = '请求失败，未写入决策记录';
    String? code;
    if (error is Map) {
      code = error['code']?.toString();
      final text = error['message']?.toString();
      if (text != null && text.trim().isNotEmpty) message = text.trim();
    } else if (error is String && error.trim().isNotEmpty) {
      message = error.trim();
    }
    throw DecisionLogException(message, code: code, statusCode: response.statusCode);
  }
}

const _headers = {
  'Accept': 'application/json',
  'Content-Type': 'application/json',
};
