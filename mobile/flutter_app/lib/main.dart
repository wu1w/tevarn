import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'bridge/create_bridge.dart';
import 'bridge/http_bridge.dart';
import 'services/app_controller.dart';
import 'theme/pixel_theme.dart';
import 'widgets/phone_shell.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.light,
    systemNavigationBarColor: Color(0xFF0C0F1A),
    systemNavigationBarIconBrightness: Brightness.light,
  ));

  // Show UI immediately — never block first frame on FFI/network (Xiaomi white-screen fix).
  runApp(const _BootstrapApp());
}

class _BootstrapApp extends StatefulWidget {
  const _BootstrapApp();

  @override
  State<_BootstrapApp> createState() => _BootstrapAppState();
}

class _BootstrapAppState extends State<_BootstrapApp>
    with SingleTickerProviderStateMixin {
  AppController? _ctrl;
  String _status = '启动中';
  String? _error;
  late final AnimationController _anim;
  late final Animation<double> _fade;
  late final Animation<double> _scale;
  late final Animation<double> _dot;

  @override
  void initState() {
    super.initState();
    _anim = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    );
    _fade = CurvedAnimation(parent: _anim, curve: Curves.easeOutCubic);
    _scale = Tween<double>(begin: 0.88, end: 1.0).animate(
      CurvedAnimation(parent: _anim, curve: Curves.easeOutBack),
    );
    _dot = Tween<double>(begin: 0.35, end: 1.0).animate(
      CurvedAnimation(
        parent: _anim,
        curve: const Interval(0.35, 1.0, curve: Curves.easeInOut),
      ),
    );
    _anim.forward();
    unawaited(_boot());
  }

  @override
  void dispose() {
    _anim.dispose();
    super.dispose();
  }

  Future<void> _boot() async {
    try {
      setState(() {
        _status = '准备引擎';
        _error = null;
      });

      // Hard timeout so a stuck native host cannot freeze the UI forever.
      final bridge = await createTaktonBridge().timeout(
        const Duration(seconds: 5),
        onTimeout: () {
          debugPrint('createTaktonBridge timeout → HTTP fallback');
          return HttpTaktonBridge(
            base: 'http://127.0.0.1:8765',
            kind: 'http-fallback-timeout',
          );
        },
      );

      if (!mounted) return;
      // P0: enter shell immediately — boot network work runs in background.
      final ctrl = AppController(bridge);
      setState(() {
        _ctrl = ctrl;
        _status = '就绪';
      });
      unawaited(ctrl.boot().timeout(
        const Duration(seconds: 12),
        onTimeout: () {
          debugPrint('AppController.boot timeout — UI already live');
        },
      ));
    } catch (e, st) {
      debugPrint('boot failed: $e\n$st');
      if (!mounted) return;
      try {
        final bridge = HttpTaktonBridge(kind: 'http-fallback-error');
        final ctrl = AppController(bridge);
        unawaited(ctrl.boot());
        setState(() {
          _ctrl = ctrl;
          _error = e.toString();
        });
      } catch (e2) {
        setState(() => _error = '$e / $e2');
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final ctrl = _ctrl;
    if (ctrl != null) {
      return ChangeNotifierProvider.value(
        value: ctrl,
        child: TaktonApp(controller: ctrl, bootNote: _error),
      );
    }
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF0C0F1A),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFF6D5DF6),
          surface: Color(0xFF0C0F1A),
        ),
      ),
      home: Scaffold(
        backgroundColor: const Color(0xFF0C0F1A),
        body: DecoratedBox(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [
                Color(0xFF12162A),
                Color(0xFF0C0F1A),
                Color(0xFF0A0C14),
              ],
            ),
          ),
          child: SafeArea(
            child: FadeTransition(
              opacity: _fade,
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 36),
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      ScaleTransition(
                        scale: _scale,
                        child: Container(
                          width: 64,
                          height: 64,
                          decoration: BoxDecoration(
                            borderRadius: BorderRadius.circular(16),
                            boxShadow: const [
                              BoxShadow(
                                color: Color(0x336D5DF6),
                                blurRadius: 24,
                                offset: Offset(0, 8),
                              ),
                            ],
                          ),
                          clipBehavior: Clip.antiAlias,
                          child: Image.asset(
                            'assets/takton_logo.png',
                            width: 64,
                            height: 64,
                            fit: BoxFit.cover,
                            errorBuilder: (_, __, ___) => Container(
                              alignment: Alignment.center,
                              color: const Color(0xFF1A2030),
                              child: const Text(
                                'T',
                                style: TextStyle(
                                  color: Color(0xFF00E676),
                                  fontSize: 28,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(height: 20),
                      const Text(
                        'Takton',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 18,
                          fontWeight: FontWeight.w700,
                          letterSpacing: -0.2,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        _status,
                        style: const TextStyle(
                          color: Color(0xFF8B9BB0),
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      const SizedBox(height: 28),
                      if (_error == null)
                        FadeTransition(
                          opacity: _dot,
                          child: const SizedBox(
                            width: 22,
                            height: 22,
                            child: CircularProgressIndicator(
                              strokeWidth: 2.2,
                              color: Color(0xFF6D5DF6),
                            ),
                          ),
                        )
                      else ...[
                        Text(
                          _error!,
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            color: Color(0xFFFF8A80),
                            fontSize: 12,
                            height: 1.4,
                          ),
                        ),
                        const SizedBox(height: 16),
                        FilledButton(
                          style: FilledButton.styleFrom(
                            backgroundColor: const Color(0xFF6D5DF6),
                            foregroundColor: Colors.white,
                            minimumSize: const Size(120, 44),
                          ),
                          onPressed: () {
                            setState(() {
                              _error = null;
                              _status = '重试中';
                            });
                            _anim.forward(from: 0);
                            unawaited(_boot());
                          },
                          child: const Text('重试'),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class TaktonApp extends StatefulWidget {
  const TaktonApp({super.key, required this.controller, this.bootNote});
  final AppController controller;
  final String? bootNote;

  @override
  State<TaktonApp> createState() => _TaktonAppState();
}

class _TaktonAppState extends State<TaktonApp> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    final note = widget.bootNote;
    if (note != null && note.isNotEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        widget.controller.showToast('启动警告: $note');
      });
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.resumed:
        widget.controller.onAppResumed();
      case AppLifecycleState.inactive:
      case AppLifecycleState.hidden:
      case AppLifecycleState.paused:
      case AppLifecycleState.detached:
        widget.controller.onAppPaused();
    }
  }

  @override
  Widget build(BuildContext context) {
    final dark = context.watch<AppController>().dark;
    // Keep system bars in sync with theme
    final overlay = SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: dark ? Brightness.light : Brightness.dark,
      statusBarBrightness: dark ? Brightness.dark : Brightness.light,
      systemNavigationBarColor: dark ? const Color(0xFF0C0F1A) : Colors.white,
      systemNavigationBarIconBrightness:
          dark ? Brightness.light : Brightness.dark,
    );
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: overlay,
      child: MaterialApp(
        title: 'Takton',
        debugShowCheckedModeBanner: false,
        theme: PixelTheme.light(),
        darkTheme: PixelTheme.dark(),
        themeMode: dark ? ThemeMode.dark : ThemeMode.light,
        home: const PhoneShell(),
      ),
    );
  }
}
