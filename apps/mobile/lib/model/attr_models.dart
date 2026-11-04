/// 归因标签词表来自定稿帧，不是接口编造的建议。
/// 接口返回词表以外的标签会追加在后面，不丢。
const kAttrCatalog = <String>[
  '选股对但择时错',
  '先手分虚高(顶格)',
  '板块共振有效',
  '板块背离',
  '炸板惩罚正确',
  '漏掉强板块票',
  '池外黑马',
  '追高负期望',
  '数据过期',
  '其他',
];

/// 复盘卡入口使用的帧样例。分数不来自 suggest。
const kSampleDate = '2026-11-03';
const kSampleCode = '688185.SH';
const kSampleName = '康希诺';

final _suffixed = RegExp(r'^(\d{6})\.(SH|SZ|BJ)$');
final _digits = RegExp(r'^\d{6}$');
final _date = RegExp(r'^\d{4}-\d{2}-\d{2}$');

/// 对外 code 是后缀式。帧头只显示 6 位，请求仍用后缀。
/// 6/9 → SH（含 688），0/3 → SZ，4/8 → BJ。已带后缀的不改。
String? normalizeCode(String raw) {
  final text = raw.trim().toUpperCase();
  if (_suffixed.hasMatch(text)) return text;
  if (!_digits.hasMatch(text)) return null;
  if (text.startsWith('6') || text.startsWith('9')) return '$text.SH';
  if (text.startsWith('0') || text.startsWith('3')) return '$text.SZ';
  if (text.startsWith('4') || text.startsWith('8')) return '$text.BJ';
  return null;
}

String displayCode(String code) {
  final match = _suffixed.firstMatch(code.trim().toUpperCase());
  return match?.group(1) ?? code.trim();
}

bool validTradeDate(String raw) => _date.hasMatch(raw.trim());

String stockTitle(String? name, String code) {
  final shown = displayCode(code);
  final trimmed = name?.trim() ?? '';
  if (trimmed.isEmpty) return shown;
  return '$trimmed $shown';
}

/// 基准收益。pending 与 0 / 0% 都显示 pending，禁止显示 0%。
String benchmarkValue(String? raw) {
  if (raw == null) return 'pending';
  final text = raw.trim();
  if (text.isEmpty || text.toLowerCase() == 'pending') return 'pending';
  final compact = text.replaceAll('%', '').trim();
  final number = num.tryParse(compact);
  if (number != null && number == 0) return 'pending';
  return text;
}

bool sameTags(List<String> a, List<String> b) {
  if (a.length != b.length) return false;
  return Set<String>.from(a).containsAll(b);
}

List<String> dedupeTags(Iterable<String> raw) {
  final out = <String>[];
  final seen = <String>{};
  for (final item in raw) {
    final text = item.trim();
    if (text.isEmpty || !seen.add(text)) continue;
    out.add(text);
  }
  return out;
}

/// 选中项按词表顺序，词表外的标签保持传入顺序附在后面。
List<String> orderTags(List<String> selected, List<String> catalog) {
  final incoming = dedupeTags(selected);
  final want = incoming.toSet();
  final out = <String>[];
  for (final tag in catalog) {
    if (want.contains(tag)) out.add(tag);
  }
  for (final tag in incoming) {
    if (!catalog.contains(tag)) out.add(tag);
  }
  return out;
}

List<String> displayCatalog({
  required List<String> suggested,
  required List<String> local,
  required List<String>? confirmed,
}) {
  final out = <String>[...kAttrCatalog];
  final known = out.toSet();
  for (final tag in [...suggested, ...local, ...?confirmed]) {
    final text = tag.trim();
    if (text.isEmpty || !known.add(text)) continue;
    out.add(text);
  }
  return out;
}

/// confirm 请求体。只有这几个字段，不带分数、数量、方向。
Map<String, dynamic> buildConfirmPayload({
  required String date,
  required String code,
  required List<String> confirmedTags,
  String? name,
}) {
  final body = <String, dynamic>{
    'date': date,
    'code': code,
    'confirmed_tags': confirmedTags,
  };
  final trimmed = name?.trim() ?? '';
  if (trimmed.isNotEmpty) body['name'] = trimmed;
  return body;
}

List<String> stringList(Object? raw) {
  if (raw is! List) return const [];
  return dedupeTags(raw.whereType<String>());
}

List<String>? optionalStringList(Object? raw) {
  if (raw == null) return null;
  if (raw is! List) return null;
  return stringList(raw);
}

class SuggestSnapshot {
  const SuggestSnapshot({
    required this.status,
    required this.suggestedTags,
    required this.confirmedTags,
    this.name,
    this.suggestedAt,
    this.suggestedAsOf,
    this.evidence,
    this.note,
    this.tagSource,
    this.confirmedAt,
  });

  final String status;
  final List<String> suggestedTags;
  final List<String>? confirmedTags;
  final String? name;
  final String? suggestedAt;
  final String? suggestedAsOf;
  final Object? evidence;
  final String? note;
  final String? tagSource;
  final String? confirmedAt;

  bool get hasSuggestion => status != 'no_data' && suggestedTags.isNotEmpty;

  factory SuggestSnapshot.fromJson(Map<String, dynamic> json) {
    return SuggestSnapshot(
      status: (json['status'] ?? 'no_data').toString(),
      suggestedTags: stringList(json['suggested_tags']),
      confirmedTags: optionalStringList(json['confirmed_tags']),
      name: _optText(json['name']),
      suggestedAt: _optText(json['suggested_at']),
      suggestedAsOf: _optText(json['suggested_asof']),
      evidence: json['evidence'],
      note: _optText(json['note']),
      tagSource: _optText(json['tag_source']),
      confirmedAt: _optText(json['confirmed_at']),
    );
  }

  static const empty = SuggestSnapshot(
    status: 'no_data',
    suggestedTags: [],
    confirmedTags: null,
  );
}

class ConfirmSnapshot {
  const ConfirmSnapshot({
    required this.confirmedTags,
    required this.suggestedTags,
    this.tagSource,
    this.confirmedAt,
    this.note,
  });

  final List<String> confirmedTags;
  final List<String> suggestedTags;
  final String? tagSource;
  final String? confirmedAt;
  final String? note;

  factory ConfirmSnapshot.fromJson(Map<String, dynamic> json) {
    return ConfirmSnapshot(
      confirmedTags: stringList(json['confirmed_tags']),
      suggestedTags: stringList(json['suggested_tags']),
      tagSource: _optText(json['tag_source']),
      confirmedAt: _optText(json['confirmed_at']),
      note: _optText(json['note']),
    );
  }
}

String? _optText(Object? raw) {
  if (raw == null) return null;
  final text = raw.toString().trim();
  return text.isEmpty ? null : text;
}

String formatEvidence(Object? evidence) {
  if (evidence == null) return '无证据';
  if (evidence is Map) {
    if (evidence.isEmpty) return '无证据';
    return evidence.entries.map((entry) => '${entry.key}: ${entry.value}').join('\n');
  }
  if (evidence is List) {
    if (evidence.isEmpty) return '无证据';
    return evidence.map((item) => '$item').join('\n');
  }
  final text = evidence.toString().trim();
  return text.isEmpty ? '无证据' : text;
}

enum ScoreTone { primary, amber, red, missing }

class ScoreRow {
  const ScoreRow(this.label, this.value, this.tone);
  final String label;
  final String value;
  final ScoreTone tone;
}

class ScoreSnapshot {
  const ScoreSnapshot(this.rows, {required this.frameSample});

  final List<ScoreRow> rows;
  final bool frameSample;

  static const missing = ScoreSnapshot(
    [
      ScoreRow('先手分', '字段缺失', ScoreTone.missing),
      ScoreRow('顶格占比', '字段缺失', ScoreTone.missing),
      ScoreRow('动量 / 量比', '字段缺失', ScoreTone.missing),
      ScoreRow('封单质量', '字段缺失', ScoreTone.missing),
      ScoreRow('开板次数', '字段缺失', ScoreTone.missing),
    ],
    frameSample: false,
  );

  /// 只用于对照帧 8:99 / 35:2 的康希诺样例。不提交，不改先手分。
  static const kangxinuo = ScoreSnapshot(
    [
      ScoreRow('先手分', '0.72', ScoreTone.red),
      ScoreRow('顶格占比', '38%', ScoreTone.amber),
      ScoreRow('动量 / 量比', '3.2', ScoreTone.primary),
      ScoreRow('封单质量', '厚', ScoreTone.primary),
      ScoreRow('开板次数', '1', ScoreTone.primary),
    ],
    frameSample: true,
  );

  factory ScoreSnapshot.forCode(String code) {
    if (code == kSampleCode) return kangxinuo;
    return missing;
  }
}

class LaunchTarget {
  const LaunchTarget({required this.date, required this.code, this.name});

  final String date;
  final String code;
  final String? name;

  static LaunchTarget? parse(Uri uri) {
    final direct = _fromUri(uri);
    if (direct != null) return direct;
    final fragment = uri.fragment;
    if (fragment.isEmpty) return null;
    final hashed = fragment.startsWith('/') ? fragment : '/$fragment';
    return _fromUri(Uri.parse('http://local$hashed'));
  }

  static LaunchTarget? resolve([Uri? uri]) => parse(uri ?? Uri.base);

  static LaunchTarget? _fromUri(Uri uri) {
    final path = uri.path.endsWith('/') && uri.path.length > 1
        ? uri.path.substring(0, uri.path.length - 1)
        : uri.path;
    if (path != '/attr') return null;
    final date = uri.queryParameters['date']?.trim() ?? '';
    final code = uri.queryParameters['code']?.trim() ?? '';
    final name = uri.queryParameters['name']?.trim();
    return LaunchTarget(
      date: date,
      code: code,
      name: name == null || name.isEmpty ? null : name,
    );
  }
}
