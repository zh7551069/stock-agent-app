import 'package:flutter/material.dart';

import '../api/decision_log_client.dart';
import '../api/http_decision_log_client.dart';
import '../config/bff_config.dart';
import '../model/attr_models.dart';
import '../pages/attr_surface.dart';
import '../state/attr_controller.dart';
import '../theme/liupan_theme.dart';

enum _Cover { none, review, attr, invalid }

class ShellPage extends StatefulWidget {
  const ShellPage({super.key, this.client, this.initialBase, this.launch});

  final DecisionLogClient? client;
  final String? initialBase;
  final LaunchTarget? launch;

  @override
  State<ShellPage> createState() => _ShellPageState();
}

class _ShellPageState extends State<ShellPage> {
  final LeaveBus _bus = LeaveBus();
  final Map<String, AttrController> _sessions = {};
  late final BffConfig _config;
  late final DecisionLogClient _client;

  int _index = 2;
  _Cover _cover = _Cover.none;
  AttrController? _active;
  String _invalidMessage = '';

  @override
  void initState() {
    super.initState();
    _config = BffConfig(widget.initialBase ?? initialBffBase());
    _client = widget.client ?? HttpDecisionLogClient(baseUrl: () => _config.base);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final launch = widget.launch ?? LaunchTarget.resolve();
      if (launch != null) _openTarget(launch, AttrChrome.fullPage);
    });
  }

  @override
  void dispose() {
    for (final session in _sessions.values) {
      session.dispose();
    }
    _config.dispose();
    super.dispose();
  }

  AttrController _session(String date, String code, String? name) {
    final key = '$date|$code';
    final existing = _sessions[key];
    if (existing != null) {
      if (name != null && name.isNotEmpty) existing.name = name;
      return existing;
    }
    final created = AttrController(client: _client, date: date, code: code, name: name);
    _sessions[key] = created;
    created.load();
    return created;
  }

  void _openTarget(LaunchTarget target, AttrChrome chrome) {
    final date = target.date.trim();
    final code = normalizeCode(target.code);
    if (!validTradeDate(date)) {
      setState(() {
        _cover = _Cover.invalid;
        _invalidMessage = 'date 需为 YYYY-MM-DD，未请求建议';
      });
      return;
    }
    if (code == null) {
      setState(() {
        _cover = _Cover.invalid;
        _invalidMessage = 'code 需为后缀式（如 688185.SH）或 6 位代码，未请求建议';
      });
      return;
    }
    setState(() {
      _active = _session(date, code, target.name);
      _cover = chrome == AttrChrome.reviewCard ? _Cover.review : _Cover.attr;
    });
  }

  Future<void> _selectTab(int index) async {
    if (_cover != _Cover.none) {
      final ok = await _bus.leave();
      if (!ok || !mounted) return;
      setState(() {
        _cover = _Cover.none;
        _index = index;
      });
      return;
    }
    setState(() => _index = index);
  }

  void _dismiss() {
    if (!mounted) return;
    setState(() => _cover = _Cover.none);
  }

  Future<void> _closeCover() async {
    final ok = await _bus.leave();
    if (!ok || !mounted) return;
    _dismiss();
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: _cover == _Cover.none,
      onPopInvokedWithResult: (didPop, _) {
        if (didPop) return;
        _closeCover();
      },
      child: Scaffold(
        backgroundColor: LiupanColors.bg,
        body: SafeArea(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                children: [
                  Expanded(child: _body()),
                  _TabBar(index: _index, onSelect: _selectTab),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _body() {
    return Stack(
        children: [
        if (_cover == _Cover.none)
        IndexedStack(
          index: _index,
          children: [
            const _ShellNote(
              title: '候选',
              body: '候选池不在本切片。本页不改先手分，也不代下单。',
            ),
            const _ShellNote(
              title: '先手榜',
              body: '先手榜不在本切片。先手分公式与权重不在这里改。',
            ),
            _ReviewList(onOpen: () {
              _openTarget(
                const LaunchTarget(date: kSampleDate, code: kSampleCode, name: kSampleName),
                AttrChrome.reviewCard,
              );
            }),
            const _ShellNote(
              title: '持仓',
              body: '持仓数量单位是股。本切片不做买卖记账，也不代下单。',
            ),
            _SettingsPane(config: _config),
          ],
        ),
        if (_cover == _Cover.review && _active != null)
          Positioned.fill(
            child: AttrSurface(
              controller: _active!,
              chrome: AttrChrome.reviewCard,
              bus: _bus,
              onBack: _dismiss,
            ),
          ),
        if (_cover == _Cover.attr && _active != null)
          Positioned.fill(
            child: AttrSurface(
              controller: _active!,
              chrome: AttrChrome.fullPage,
              bus: _bus,
              onBack: _dismiss,
            ),
          ),
        if (_cover == _Cover.invalid)
          Positioned.fill(
            child: _InvalidTarget(message: _invalidMessage, onBack: () => setState(() => _cover = _Cover.none)),
          ),
      ],
    );
  }
}

class _ReviewList extends StatelessWidget {
  const _ReviewList({required this.onOpen});

  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 16),
      children: [
        const Text('复盘', style: TextStyle(color: LiupanColors.text, fontSize: 20, fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        const Text(
          '本切片只交付归因入口。点开复盘卡预勾、改标或确认写入。',
          style: TextStyle(color: LiupanColors.muted, fontSize: 13, height: 1.4),
        ),
        const SizedBox(height: 16),
        Material(
          color: LiupanColors.card,
          child: InkWell(
            key: const ValueKey('open-review-sample'),
            onTap: onOpen,
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                border: Border.all(color: LiupanColors.border),
                borderRadius: const BorderRadius.all(Radius.circular(2)),
              ),
              child: const Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('康希诺 688185', style: TextStyle(color: LiupanColors.text, fontSize: 16, fontWeight: FontWeight.w600)),
                  SizedBox(height: 4),
                  Text('单票复盘 · 2026-11-03', style: TextStyle(color: LiupanColors.muted, fontSize: 12)),
                  SizedBox(height: 8),
                  Text('归因须确认后写入 · 不自动落库', style: TextStyle(color: LiupanColors.blue, fontSize: 13)),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _ShellNote extends StatelessWidget {
  const _ShellNote({required this.title, required this.body});

  final String title;
  final String body;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 16),
      children: [
        Text(title, style: const TextStyle(color: LiupanColors.text, fontSize: 20, fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        Text(body, style: const TextStyle(color: LiupanColors.muted, fontSize: 14, height: 1.45)),
      ],
    );
  }
}

class _SettingsPane extends StatefulWidget {
  const _SettingsPane({required this.config});

  final BffConfig config;

  @override
  State<_SettingsPane> createState() => _SettingsPaneState();
}

class _SettingsPaneState extends State<_SettingsPane> {
  late final TextEditingController _url = TextEditingController(text: widget.config.base);
  String? _saved;

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  void _save() {
    widget.config.update(_url.text);
    setState(() => _saved = widget.config.base);
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 20, 16, 16),
      children: [
        const Text('设置', style: TextStyle(color: LiupanColors.text, fontSize: 20, fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        const Text(
          '手机只走 HTTP，不打开 DuckDB。正式归因只有「确认写入」。改标不请求网络。',
          style: TextStyle(color: LiupanColors.muted, fontSize: 13, height: 1.45),
        ),
        const SizedBox(height: 16),
        const Text('BFF 地址', style: TextStyle(color: LiupanColors.muted, fontSize: 12)),
        const SizedBox(height: 6),
        TextField(
          key: const ValueKey('bff-base'),
          controller: _url,
          style: const TextStyle(color: LiupanColors.text, fontSize: 14),
          decoration: const InputDecoration(
            isDense: true,
            filled: true,
            fillColor: LiupanColors.card,
            contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 12),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.all(Radius.circular(2)),
              borderSide: BorderSide(color: LiupanColors.border),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.all(Radius.circular(2)),
              borderSide: BorderSide(color: LiupanColors.amber),
            ),
          ),
        ),
        const SizedBox(height: 12),
        LiupanButton(
          key: const ValueKey('save-base'),
          label: '保存',
          background: LiupanColors.amber,
          foreground: LiupanColors.ink,
          onPressed: _save,
        ),
        if (_saved != null) ...[
          const SizedBox(height: 10),
          Text('下次读取和确认写入使用 $_saved', style: const TextStyle(color: LiupanColors.muted, fontSize: 12)),
        ],
      ],
    );
  }
}

class _InvalidTarget extends StatelessWidget {
  const _InvalidTarget({required this.message, required this.onBack});

  final String message;
  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: LiupanColors.bg,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          IconButton(
            onPressed: onBack,
            icon: const Icon(Icons.chevron_left, color: LiupanColors.text),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Text(message, style: const TextStyle(color: LiupanColors.muted, fontSize: 14, height: 1.4)),
          ),
        ],
      ),
    );
  }
}

class _TabBar extends StatelessWidget {
  const _TabBar({required this.index, required this.onSelect});

  final int index;
  final ValueChanged<int> onSelect;

  static const _items = [
    (Icons.format_list_bulleted, '候选'),
    (Icons.emoji_events_outlined, '先手榜'),
    (Icons.calendar_today_outlined, '复盘'),
    (Icons.inventory_2_outlined, '持仓'),
    (Icons.settings_outlined, '设置'),
  ];

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        color: LiupanColors.bg,
        border: Border(top: BorderSide(color: LiupanColors.border)),
      ),
      child: SizedBox(
        height: 56,
        child: Row(
          children: [
            for (var i = 0; i < _items.length; i++)
              Expanded(
                child: InkWell(
                  key: ValueKey('tab-${_items[i].$2}'),
                  onTap: () => onSelect(i),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Icon(
                        _items[i].$1,
                        size: 22,
                        color: i == index ? LiupanColors.amber : LiupanColors.dim,
                      ),
                      const SizedBox(height: 2),
                      Text(
                        _items[i].$2,
                        style: TextStyle(
                          fontSize: 11,
                          color: i == index ? LiupanColors.amber : LiupanColors.dim,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
