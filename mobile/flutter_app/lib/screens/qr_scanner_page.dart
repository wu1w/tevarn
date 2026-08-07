import 'dart:async';

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../theme/pixel_theme.dart';

/// Full-screen live QR scanner for PC pairing.
/// Returns the decoded string via [Navigator.pop], or null if cancelled.
class QrScannerPage extends StatefulWidget {
  const QrScannerPage({super.key});

  @override
  State<QrScannerPage> createState() => _QrScannerPageState();
}

class _QrScannerPageState extends State<QrScannerPage>
    with WidgetsBindingObserver {
  late final MobileScannerController _controller;
  bool _handled = false;
  bool _torchOn = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _controller = MobileScannerController(
      detectionSpeed: DetectionSpeed.normal,
      facing: CameraFacing.back,
      formats: const [BarcodeFormat.qrCode],
    );
  }

  Future<void> _releaseCamera() async {
    try {
      await _controller.stop();
    } catch (_) {}
    try {
      await _controller.dispose();
    } catch (_) {}
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    // Fire-and-forget: stop then dispose native camera (Android crash fix).
    unawaited(_releaseCamera());
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Permission dialogs and backgrounding can leave camera half-open.
    switch (state) {
      case AppLifecycleState.resumed:
        if (!_handled) {
          unawaited(_controller.start());
        }
      case AppLifecycleState.inactive:
      case AppLifecycleState.paused:
      case AppLifecycleState.hidden:
      case AppLifecycleState.detached:
        unawaited(_controller.stop());
    }
  }

  Future<void> _onDetect(BarcodeCapture capture) async {
    if (_handled || !mounted) return;
    for (final b in capture.barcodes) {
      final raw = (b.rawValue ?? b.displayValue ?? '').trim();
      if (raw.isEmpty) continue;
      _handled = true;
      try {
        await _controller.stop();
      } catch (_) {}
      // Brief settle so camera release does not race pair_apply network work.
      await Future<void>.delayed(const Duration(milliseconds: 150));
      if (!mounted) return;
      Navigator.of(context).pop(raw);
      return;
    }
  }

  Future<void> _toggleTorch() async {
    try {
      await _controller.toggleTorch();
      if (mounted) setState(() => _torchOn = !_torchOn);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    if (kIsWeb) {
      return Scaffold(
        appBar: AppBar(title: const Text('扫码')),
        body: const Center(
          child: Text('Web 预览不支持摄像头扫码，请粘贴配对码'),
        ),
      );
    }

    final cutSize = MediaQuery.sizeOf(context).width * 0.72;

    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        fit: StackFit.expand,
        children: [
          MobileScanner(
            controller: _controller,
            onDetect: _onDetect,
            errorBuilder: (context, error, child) {
              return Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Text(
                    '无法打开摄像头\n${error.errorCode.name}\n请检查相机权限后重试',
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      color: Colors.white70,
                      height: 1.45,
                      fontSize: 14,
                    ),
                  ),
                ),
              );
            },
          ),
          IgnorePointer(
            child: CustomPaint(
              painter: _ScanOverlayPainter(cutSize: cutSize),
              child: const SizedBox.expand(),
            ),
          ),
          SafeArea(
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(4, 4, 4, 0),
                  child: Row(
                    children: [
                      IconButton(
                        onPressed: () async {
                          try {
                            await _controller.stop();
                          } catch (_) {}
                          if (context.mounted) {
                            Navigator.of(context).pop();
                          }
                        },
                        icon: const Icon(Icons.close, color: Colors.white),
                      ),
                      const Expanded(
                        child: Text(
                          '扫描 PC 配对二维码',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            color: Colors.white,
                            fontSize: 16,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ),
                      IconButton(
                        onPressed: _toggleTorch,
                        icon: Icon(
                          _torchOn ? Icons.flash_on : Icons.flash_off,
                          color: Colors.white,
                        ),
                      ),
                      IconButton(
                        onPressed: () => _controller.switchCamera(),
                        icon: const Icon(
                          Icons.cameraswitch,
                          color: Colors.white,
                        ),
                      ),
                    ],
                  ),
                ),
                const Spacer(),
                Padding(
                  padding: const EdgeInsets.fromLTRB(24, 0, 24, 8),
                  child: Text(
                    _error ?? '对准工作台「匹配手机」二维码，自动识别',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color:
                          _error != null ? PixelColors.amber : Colors.white70,
                      fontSize: 13.5,
                      height: 1.35,
                    ),
                  ),
                ),
                TextButton(
                  onPressed: () async {
                    try {
                      await _controller.stop();
                    } catch (_) {}
                    if (context.mounted) {
                      Navigator.of(context).pop();
                    }
                  },
                  child: const Text(
                    '改用粘贴配对码',
                    style: TextStyle(color: Colors.white60),
                  ),
                ),
                const SizedBox(height: 16),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ScanOverlayPainter extends CustomPainter {
  _ScanOverlayPainter({required this.cutSize});
  final double cutSize;

  @override
  void paint(Canvas canvas, Size size) {
    final hole = Rect.fromCenter(
      center: Offset(size.width / 2, size.height * 0.42),
      width: cutSize,
      height: cutSize,
    );
    final overlay = Path()
      ..addRect(Rect.fromLTWH(0, 0, size.width, size.height));
    final cut = Path()
      ..addRRect(RRect.fromRectAndRadius(hole, const Radius.circular(16)));
    final path = Path.combine(PathOperation.difference, overlay, cut);
    canvas.drawPath(
      path,
      Paint()..color = const Color(0x8C000000),
    );
    final border = Paint()
      ..color = PixelColors.cyan
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;
    const L = 22.0;
    canvas.drawLine(hole.topLeft, hole.topLeft + const Offset(L, 0), border);
    canvas.drawLine(hole.topLeft, hole.topLeft + const Offset(0, L), border);
    canvas.drawLine(hole.topRight, hole.topRight + const Offset(-L, 0), border);
    canvas.drawLine(hole.topRight, hole.topRight + const Offset(0, L), border);
    canvas.drawLine(
        hole.bottomLeft, hole.bottomLeft + const Offset(L, 0), border);
    canvas.drawLine(
        hole.bottomLeft, hole.bottomLeft + const Offset(0, -L), border);
    canvas.drawLine(
        hole.bottomRight, hole.bottomRight + const Offset(-L, 0), border);
    canvas.drawLine(
        hole.bottomRight, hole.bottomRight + const Offset(0, -L), border);
  }

  @override
  bool shouldRepaint(covariant _ScanOverlayPainter oldDelegate) =>
      oldDelegate.cutSize != cutSize;
}
