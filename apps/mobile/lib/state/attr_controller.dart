import 'package:flutter/foundation.dart';

import '../api/decision_log_client.dart';
import '../model/attr_models.dart';

/// 归因页状态。
/// 改标只改 [localTags]。正式落库只有 [confirmWrite] 会 POST confirm，且同一时刻只发一次。
class AttrController extends ChangeNotifier {
  AttrController({
    required DecisionLogClient client,
    required this.date,
    required this.code,
    this.name,
  }) : _client = client;

  final DecisionLogClient _client;
  final String date;
  final String code;
  String? name;

  bool loading = true;
  String? loadError;
  String? confirmError;
  bool confirming = false;

  List<String> suggestedTags = const [];
  List<String>? confirmedTags;
  List<String> localTags = const [];
  String? suggestedAt;
  String? suggestedAsOf;
  Object? evidence;
  String? note;
  String? tagSource;
  String? confirmedAt;
  bool retagApplied = false;

  bool get showConfirmed => confirmedTags != null && sameTags(localTags, confirmedTags!);

  /// 未确认的预勾，或已确认后又改过本地勾选，离开前要拦截。
  /// 没有建议、没有本地勾选、也没改过标时不拦。
  bool get needsLeaveGuard {
    if (loading || confirming) return false;
    if (showConfirmed) return false;
    if (confirmedTags == null && localTags.isEmpty && suggestedTags.isEmpty && !retagApplied) {
      return false;
    }
    return true;
  }

  Future<void> load() async {
    loading = true;
    loadError = null;
    confirmError = null;
    notifyListeners();
    try {
      final snapshot = await _client.fetchSuggest(date: date, code: code);
      _applySuggest(snapshot);
      loadError = null;
    } on DecisionLogException catch (error) {
      loadError = error.message;
      suggestedTags = const [];
      confirmedTags = null;
      localTags = const [];
    } catch (_) {
      loadError = '建议读取失败，未编造标签';
      suggestedTags = const [];
      confirmedTags = null;
      localTags = const [];
    } finally {
      loading = false;
      retagApplied = false;
      notifyListeners();
    }
  }

  void _applySuggest(SuggestSnapshot snapshot) {
    suggestedTags = List<String>.unmodifiable(snapshot.suggestedTags);
    confirmedTags = snapshot.confirmedTags == null
        ? null
        : List<String>.unmodifiable(snapshot.confirmedTags!);
    suggestedAt = snapshot.suggestedAt;
    suggestedAsOf = snapshot.suggestedAsOf;
    evidence = snapshot.evidence;
    note = snapshot.note;
    tagSource = snapshot.tagSource;
    confirmedAt = snapshot.confirmedAt;
    final incoming = snapshot.name;
    if (incoming != null && incoming.isNotEmpty) name = incoming;
    if (confirmedTags != null) {
      localTags = List<String>.from(confirmedTags!);
    } else {
      localTags = List<String>.from(suggestedTags);
    }
  }

  /// 只更新本地预勾。不调用 confirm。
  void applyRetag(List<String> next) {
    final ordered = orderTags(next, displayCatalog(
      suggested: suggestedTags,
      local: next,
      confirmed: confirmedTags,
    ));
    final changed = !sameTags(localTags, ordered);
    localTags = List<String>.unmodifiable(ordered);
    if (changed) retagApplied = true;
    confirmError = null;
    notifyListeners();
  }

  void discardLocal() {
    if (confirmedTags != null) {
      localTags = List<String>.from(confirmedTags!);
    } else {
      localTags = List<String>.from(suggestedTags);
    }
    retagApplied = false;
    confirmError = null;
    notifyListeners();
  }

  /// 页底「确认写入」。唯一会 POST /decision_log/confirm 的入口。
  Future<void> confirmWrite() async {
    if (confirming || showConfirmed || loading || loadError != null) return;
    confirming = true;
    confirmError = null;
    notifyListeners();
    try {
      final result = await _client.confirm(
        date: date,
        code: code,
        confirmedTags: List<String>.from(localTags),
        name: name,
      );
      confirmedTags = List<String>.unmodifiable(result.confirmedTags);
      localTags = List<String>.from(result.confirmedTags);
      suggestedTags = List<String>.unmodifiable(result.suggestedTags);
      tagSource = result.tagSource;
      confirmedAt = result.confirmedAt;
      retagApplied = false;
      confirmError = null;
    } on DecisionLogException catch (error) {
      confirmError = error.message;
    } catch (_) {
      confirmError = '确认失败，未写入决策记录';
    } finally {
      confirming = false;
      notifyListeners();
    }
  }
}
