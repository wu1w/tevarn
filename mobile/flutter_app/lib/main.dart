import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'bridge/create_bridge.dart';
import 'services/app_controller.dart';
import 'theme/pixel_theme.dart';
import 'widgets/phone_shell.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.dark,
  ));

  // Web → HttpTaktonBridge; native → FfiTaktonBridge (conditional import)
  final bridge = await createTaktonBridge();
  final ctrl = AppController(bridge);
  await ctrl.boot();

  runApp(
    ChangeNotifierProvider.value(
      value: ctrl,
      child: const TaktonApp(),
    ),
  );
}

class TaktonApp extends StatelessWidget {
  const TaktonApp({super.key});

  @override
  Widget build(BuildContext context) {
    final dark = context.watch<AppController>().dark;
    return MaterialApp(
      title: 'Takton 手机端 · Pixel Console',
      debugShowCheckedModeBanner: false,
      theme: PixelTheme.light(),
      darkTheme: PixelTheme.dark(),
      themeMode: dark ? ThemeMode.dark : ThemeMode.light,
      home: const PhoneShell(),
    );
  }
}
