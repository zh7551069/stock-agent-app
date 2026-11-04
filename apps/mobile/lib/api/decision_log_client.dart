import '../model/attr_models.dart';

class DecisionLogException implements Exception {
  DecisionLogException(this.message, {this.code, this.statusCode});

  final String message;
  final String? code;
  final int? statusCode;

  @override
  String toString() => message;
}

abstract class DecisionLogClient {
  Future<SuggestSnapshot> fetchSuggest({
    required String date,
    required String code,
  });

  Future<ConfirmSnapshot> confirm({
    required String date,
    required String code,
    required List<String> confirmedTags,
    String? name,
  });
}
