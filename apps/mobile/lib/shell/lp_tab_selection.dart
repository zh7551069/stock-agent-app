/// 底栏五栏。观察不是其中一栏。
enum LpRootTab {
  candidate,
  rank,
  review,
  holdings,
  settings;

  String get label {
    switch (this) {
      case LpRootTab.candidate:
        return '候选';
      case LpRootTab.rank:
        return '先手榜';
      case LpRootTab.review:
        return '复盘';
      case LpRootTab.holdings:
        return '持仓';
      case LpRootTab.settings:
        return '设置';
    }
  }
}

/// 栈上二级页。不进底 Tab，只决定底栏哪一栏保持选中。
enum LpStackPage {
  /// 单票详情。现稿从榜进入，底栏保持先手榜。
  detailFromRank,

  /// 复盘子页：日详情、周报、单票复盘、归因、空错态。底栏保持复盘。
  reviewChild,

  /// 观察。底栏保持进入时的来源栏（先手榜或复盘）。
  watch,
}

/// 根级 Tab 与栈页如何点亮底栏。根级切换只改 [root]，不另叠一层 Tab 历史。
class LpTabSelection {
  const LpTabSelection.root(this.root) : page = null, watchSource = null;

  const LpTabSelection.stack({
    required this.root,
    required this.page,
    this.watchSource,
  });

  final LpRootTab root;

  /// 非空表示当前盖着二级页。底栏仍在，选中栏由 [selected] 决定。
  final LpStackPage? page;

  /// 观察页的来源栏。只接受先手榜或复盘。
  final LpRootTab? watchSource;

  /// 底栏应点亮的栏。观察来源不是先手榜或复盘时，点亮先手榜，避免五栏全灭。
  LpRootTab get selected {
    switch (page) {
      case null:
        return root;
      case LpStackPage.detailFromRank:
        return LpRootTab.rank;
      case LpStackPage.reviewChild:
        return LpRootTab.review;
      case LpStackPage.watch:
        if (watchSource == LpRootTab.review) {
          return LpRootTab.review;
        }
        return LpRootTab.rank;
    }
  }
}
