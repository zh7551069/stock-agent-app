import 'package:flutter/material.dart';

import 'api/decision_log_client.dart';
import 'model/attr_models.dart';
import 'shell/app_shell.dart';
import 'theme/liupan_theme.dart';

class LiupanApp extends StatelessWidget {
  const LiupanApp({super.key, this.client, this.initialBase, this.launch});

  final DecisionLogClient? client;
  final String? initialBase;
  final LaunchTarget? launch;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '蓄势池',
      debugShowCheckedModeBanner: false,
      theme: buildLiupanTheme(),
      scrollBehavior: const _NoGlow(),
      home: AppShell(client: client, initialBase: initialBase, launch: launch),
    );
  }
}

class _NoGlow extends ScrollBehavior {
  const _NoGlow();

  @override
  Widget buildOverscrollIndicator(
    BuildContext context,
    Widget child,
    ScrollableDetails details,
  ) {
    return child;
  }
}
