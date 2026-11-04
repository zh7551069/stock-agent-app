import 'package:flutter/widgets.dart';

import 'app.dart';
import 'model/attr_models.dart';
import 'routing/url_strategy_stub.dart' if (dart.library.ui_web) 'routing/url_strategy_web.dart';

void main() {
  final launch = LaunchTarget.resolve();
  WidgetsFlutterBinding.ensureInitialized();
  useAppUrlStrategy();
  runApp(LiupanApp(launch: launch));
}
