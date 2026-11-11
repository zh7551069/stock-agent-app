import 'package:flutter/material.dart';

import '../api/decision_log_client.dart';
import '../api/http_decision_log_client.dart';
import '../config/bff_config.dart';
import '../model/attr_models.dart';
import '../pages/attr_surface.dart';
import '../state/attr_controller.dart';
import '../theme/liupan_theme.dart';
import '../widgets/lp_nav_header.dart';
import '../widgets/lp_tab_bar.dart';
import 'lp_tab_selection.dart';

enum _Cover { none, review, attr, invalid }

/// A0 壳：五栏、顶栏、现有业务页。不单开启动页、权限页、登录或首次挂载引导。
class AppShell extends StatefulWidget {
  const AppShell({super.key, this.client, this.initialBase, this.launch});

  final DecisionLogClient? client;
  final String? initialBase;
  final LaunchTarget? launch;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  final LeaveBus _bus = LeaveBus();
  final Map<String, AttrController> _sessions = {};
  late final BffConfig _config;
  late final DecisionLogClient _client;

  LpTabSelection _selection = const LpTabSelection.root(LpRootTab.candidate);
  _Cover _cover = _Cover.none;
  AttrController? _active;
  String _invalidMessage = '';
  bool _obscured = false;

  @override
  void initState() {
    super.initState();
    _config = BffConfig(widget.initialBase ?? initialBffBase());
    _client =
        widget.client ?? HttpDecisionLogClient(baseUrl: () => _config.base);
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
    final created = AttrController(
      client: _client,
      date: date,
      code: code,
      name: name,
    );
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
        _selection = LpTabSelection.stack(
          root: _selection.root,
          page: LpStackPage.reviewChild,
        );
        _obscured = false;
      });
      return;
    }
    if (code == null) {
      setState(() {
        _cover = _Cover.invalid;
        _invalidMessage = 'code 需为后缀式（如 688185.SH）或 6 位代码，未请求建议';
        _selection = LpTabSelection.stack(
          root: _selection.root,
          page: LpStackPage.reviewChild,
        );
        _obscured = false;
      });
      return;
    }
    setState(() {
      _active = _session(date, code, target.name);
      _cover = chrome == AttrChrome.reviewCard ? _Cover.review : _Cover.attr;
      _selection = LpTabSelection.stack(
        root: _selection.root,
        page: LpStackPage.reviewChild,
      );
      _obscured = false;
    });
  }

  Future<void> _selectTab(LpRootTab tab) async {
    if (_cover != _Cover.none) {
      final ok = await _bus.leave();
      if (!ok || !mounted) return;
      setState(() {
        _cover = _Cover.none;
        _selection = LpTabSelection.root(tab);
        _obscured = false;
      });
      return;
    }
    if (tab == _selection.root) return;
    setState(() => _selection = LpTabSelection.root(tab));
  }

  void _dismiss() {
    if (!mounted) return;
    setState(() {
      _cover = _Cover.none;
      _selection = LpTabSelection.root(_selection.root);
      _obscured = false;
    });
  }

  Future<void> _closeCover() async {
    final ok = await _bus.leave();
    if (!ok || !mounted) return;
    _dismiss();
  }

  void _onOverlay(bool obscured) {
    if (!mounted || _obscured == obscured) return;
    setState(() => _obscured = obscured);
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
        body: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: Column(
              children: [
                Expanded(
                  child: SafeArea(bottom: _obscured, child: _body()),
                ),
                if (!_obscured)
                  LpTabBar(selected: _selection.selected, onSelect: _selectTab),
              ],
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
            index: _selection.root.index,
            children: [
              const _RootPage(
                title: '蓄势池',
                subtitle: '首板前蓄势 · 非已连板',
                body: '候选池不在本切片。本页不改先手分，也不代下单。',
              ),
              const _RootPage(
                title: '蓄势先手榜',
                subtitle: '先手分排序',
                body: '先手榜不在本切片。先手分公式与权重不在这里改。',
              ),
              _ReviewList(
                onOpen: () {
                  _openTarget(
                    const LaunchTarget(
                      date: kSampleDate,
                      code: kSampleCode,
                      name: kSampleName,
                    ),
                    AttrChrome.reviewCard,
                  );
                },
              ),
              const _RootPage(
                title: '持仓',
                subtitle: '本地记账仓位 · 不接券商',
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
              onOverlay: _onOverlay,
            ),
          ),
        if (_cover == _Cover.attr && _active != null)
          Positioned.fill(
            child: AttrSurface(
              controller: _active!,
              chrome: AttrChrome.fullPage,
              bus: _bus,
              onBack: _dismiss,
              onOverlay: _onOverlay,
            ),
          ),
        if (_cover == _Cover.invalid)
          Positioned.fill(
            child: _InvalidTarget(message: _invalidMessage, onBack: _dismiss),
          ),
      ],
    );
  }
}

class _RootPage extends StatelessWidget {
  const _RootPage({
    required this.title,
    required this.subtitle,
    required this.body,
  });

  final String title;
  final String subtitle;
  final String body;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        LpNavHeader.root(title: title, subtitle: subtitle),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
            children: [
              Text(
                body,
                style: const TextStyle(
                  fontFamily: liupanFont,
                  color: LiupanColors.muted,
                  fontSize: 14,
                  height: 1.45,
                ),
              ),
            ],
          ),
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const LpNavHeader.root(title: '复盘', subtitle: '首板前蓄势 · 非已连板'),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
            children: [
              const Text(
                '本切片只交付归因入口。点开复盘卡预勾、改标或确认写入。',
                style: TextStyle(
                  fontFamily: liupanFont,
                  color: LiupanColors.muted,
                  fontSize: 13,
                  height: 1.4,
                ),
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
                    decoration: const BoxDecoration(
                      border: Border.fromBorderSide(
                        BorderSide(color: LiupanColors.border),
                      ),
                      borderRadius: LiupanRadii.surface,
                    ),
                    child: const Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '康希诺 688185',
                          style: TextStyle(
                            fontFamily: liupanFont,
                            color: LiupanColors.text,
                            fontSize: 16,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        SizedBox(height: 4),
                        Text(
                          '单票复盘 · 2026-11-03',
                          style: TextStyle(
                            fontFamily: liupanFont,
                            color: LiupanColors.muted,
                            fontSize: 12,
                          ),
                        ),
                        SizedBox(height: 8),
                        Text(
                          '归因须确认后写入 · 不自动落库',
                          style: TextStyle(
                            fontFamily: liupanFont,
                            color: LiupanColors.blue,
                            fontSize: 13,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
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
  late final TextEditingController _url = TextEditingController(
    text: widget.config.base,
  );
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const LpNavHeader.root(
          title: '设置 / 数据',
          subtitle: '蓄势池 · 数据日期 · 模式 · 免责',
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 16),
            children: [
              const Text(
                '手机只走 HTTP，不打开 DuckDB。正式归因只有「确认写入」。改标不请求网络。',
                style: TextStyle(
                  fontFamily: liupanFont,
                  color: LiupanColors.muted,
                  fontSize: 13,
                  height: 1.45,
                ),
              ),
              const SizedBox(height: 16),
              const Text(
                'BFF 地址',
                style: TextStyle(
                  fontFamily: liupanFont,
                  color: LiupanColors.muted,
                  fontSize: 12,
                ),
              ),
              const SizedBox(height: 6),
              TextField(
                key: const ValueKey('bff-base'),
                controller: _url,
                style: const TextStyle(
                  fontFamily: liupanFont,
                  color: LiupanColors.text,
                  fontSize: 14,
                ),
                decoration: const InputDecoration(
                  isDense: true,
                  filled: true,
                  fillColor: LiupanColors.card,
                  contentPadding: EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 12,
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: LiupanRadii.surface,
                    borderSide: BorderSide(color: LiupanColors.border),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderRadius: LiupanRadii.surface,
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
                Text(
                  '下次读取和确认写入使用 $_saved',
                  style: const TextStyle(
                    fontFamily: liupanFont,
                    color: LiupanColors.muted,
                    fontSize: 12,
                  ),
                ),
              ],
            ],
          ),
        ),
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
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          LpNavHeader.stack(
            title: message,
            onBack: onBack,
            titleMaxLines: 3,
            actionKey: null,
          ),
        ],
      ),
    );
  }
}
