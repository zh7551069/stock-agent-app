import 'dart:async';

import 'package:flutter/material.dart';

import '../model/attr_models.dart';
import '../state/attr_controller.dart';
import '../widgets/attr_section.dart';
import '../widgets/leave_dialog.dart';
import '../widgets/retag_sheet.dart';
import '../widgets/score_block.dart';
import '../widgets/stock_header.dart';
import '../theme/liupan_theme.dart';

enum AttrChrome { fullPage, reviewCard }

/// 全页（35:2 / 35:98 / 35:194 / 35:290）和复盘卡（8:99）共用。
class AttrSurface extends StatefulWidget {
  const AttrSurface({
    super.key,
    required this.controller,
    required this.chrome,
    required this.bus,
    required this.onBack,
    this.onOverlay,
  });

  final AttrController controller;
  final AttrChrome chrome;
  final LeaveBus bus;
  final VoidCallback onBack;

  /// 改标 Sheet、离开拦截盖住页面时为 true。壳用来收起底栏。
  final ValueChanged<bool>? onOverlay;

  @override
  State<AttrSurface> createState() => _AttrSurfaceState();
}

class LeaveBus {
  Future<bool> Function()? ask;

  Future<bool> leave() {
    final current = ask;
    if (current == null) return Future<bool>.value(true);
    return current();
  }
}

class _AttrSurfaceState extends State<AttrSurface> {
  bool _sheet = false;
  bool _leave = false;
  _LeaveIntent _intent = _LeaveIntent.pop;
  Completer<bool>? _wait;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onChange);
    widget.bus.ask = _askLeave;
  }

  @override
  void dispose() {
    if (widget.bus.ask == _askLeave) widget.bus.ask = null;
    widget.controller.removeListener(_onChange);
    if (_wait != null && !_wait!.isCompleted) _wait!.complete(false);
    super.dispose();
  }

  void _onChange() {
    if (mounted) setState(() {});
  }

  void _mutate(VoidCallback change) {
    setState(change);
    widget.onOverlay?.call(_sheet || _leave);
  }

  Future<bool> _askLeave() {
    if (_sheet) {
      _mutate(() => _sheet = false);
      return Future.value(false);
    }
    if (widget.controller.confirming) return Future.value(false);
    if (!widget.controller.needsLeaveGuard) return Future.value(true);
    return _openLeave(_LeaveIntent.pop);
  }

  Future<bool> _openLeave(_LeaveIntent intent) {
    if (_wait != null && !_wait!.isCompleted) return _wait!.future;
    _intent = intent;
    _wait = Completer<bool>();
    _mutate(() => _leave = true);
    return _wait!.future;
  }

  void _stay() {
    _mutate(() => _leave = false);
    final wait = _wait;
    _wait = null;
    if (wait != null && !wait.isCompleted) wait.complete(false);
  }

  void _discard() {
    widget.controller.discardLocal();
    _mutate(() => _leave = false);
    final wait = _wait;
    _wait = null;
    final intent = _intent;
    if (wait != null && !wait.isCompleted) wait.complete(true);
    if (intent == _LeaveIntent.replay) {
      widget.controller.load();
    }
  }

  Future<void> _back() async {
    final ok = await _askLeave();
    if (ok) widget.onBack();
  }

  Future<void> _replay() async {
    if (widget.controller.confirming) return;
    if (!widget.controller.needsLeaveGuard) {
      await widget.controller.load();
      return;
    }
    await _openLeave(_LeaveIntent.replay);
  }

  void _apply(List<String> tags) {
    widget.controller.applyRetag(tags);
    _mutate(() => _sheet = false);
  }

  String _subtitle() {
    if (widget.chrome == AttrChrome.reviewCard) {
      return '单票复盘 · ${widget.controller.date}';
    }
    if (_leave) return '归因建议 · 离开拦截';
    if (_sheet) return '归因建议 · 改标中';
    if (widget.controller.showConfirmed) return '归因建议 · 已确认';
    return '归因建议 · 须确认写入';
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final catalog = displayCatalog(
      suggested: controller.suggestedTags,
      local: controller.localTags,
      confirmed: controller.confirmedTags,
    );
    return Material(
      color: LiupanColors.bg,
      child: Stack(
        children: [
          Column(
            children: [
              StockHeader(
                title: stockTitle(controller.name, controller.code),
                subtitle: _subtitle(),
                onBack: _back,
                onReplay: _replay,
              ),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(12, 4, 12, 20),
                  children: [
                    if (controller.showConfirmed) const SuccessBanner(),
                    ScoreBlock(score: ScoreSnapshot.forCode(controller.code)),
                    const SizedBox(height: 16),
                    AttrSection(
                      controller: controller,
                      reviewCard: widget.chrome == AttrChrome.reviewCard,
                      onRetag: () => _mutate(() => _sheet = true),
                      onConfirm: controller.confirmWrite,
                      onRetry: controller.load,
                    ),
                  ],
                ),
              ),
            ],
          ),
          if (_sheet) ...[
            Positioned.fill(
              child: GestureDetector(
                onTap: () => _mutate(() => _sheet = false),
                child: const ColoredBox(color: LiupanColors.scrim),
              ),
            ),
            Align(
              alignment: Alignment.bottomCenter,
              child: RetagSheet(
                initial: controller.localTags,
                catalog: catalog,
                onCancel: () => _mutate(() => _sheet = false),
                onApply: _apply,
              ),
            ),
          ],
          if (_leave) ...[
            const Positioned.fill(child: ColoredBox(color: LiupanColors.scrim)),
            LeaveDialog(onStay: _stay, onDiscard: _discard),
          ],
        ],
      ),
    );
  }
}

enum _LeaveIntent { pop, replay }
