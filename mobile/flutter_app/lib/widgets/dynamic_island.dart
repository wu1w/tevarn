import 'dart:async';
import 'dart:math' as math;
import 'dart:ui' show DisplayFeatureType;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';

/// Camera-aware Dynamic Island (中置挖孔).
///
/// Layout (OEM / Apple DI):
/// ```
///  [ left label ] [  camera well  ] [ right label ]
///  \__________ black stadium capsule __________/
/// ```
/// Text never enters the camera well. Expand/collapse is animated.
class TaktonDynamicIsland extends StatefulWidget {
  const TaktonDynamicIsland({
    super.key,
    required this.controller,
    required this.dark,
    this.shellWidth,
  });

  final AppController controller;
  final bool dark;
  final double? shellWidth;

  @override
  State<TaktonDynamicIsland> createState() => _TaktonDynamicIslandState();
}

class _TaktonDynamicIslandState extends State<TaktonDynamicIsland>
    with TickerProviderStateMixin {
  static const _channel = MethodChannel('takton/display_cutout');

  List<Rect> _nativeCutouts = const [];

  /// 0 = idle (tight on camera), 1 = live (wings open).
  late final AnimationController _liveCtrl;
  late final Animation<double> _liveT;

  /// 0 = card hidden, 1 = card shown below island.
  late final AnimationController _cardCtrl;
  late final Animation<double> _cardT;

  bool _prevLive = false;
  bool _prevCard = false;

  @override
  void initState() {
    super.initState();
    _liveCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 380),
      reverseDuration: const Duration(milliseconds: 300),
    );
    _liveT = CurvedAnimation(
      parent: _liveCtrl,
      curve: Curves.easeOutCubic,
      reverseCurve: Curves.easeInCubic,
    );

    _cardCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 320),
      reverseDuration: const Duration(milliseconds: 240),
    );
    _cardT = CurvedAnimation(
      parent: _cardCtrl,
      curve: Curves.easeOutBack,
      reverseCurve: Curves.easeInCubic,
    );

    unawaited(_probeCutouts());
    // Late probe — cutout sometimes only ready after first frame / edge-to-edge.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      unawaited(_probeCutouts());
    });
    Future<void>.delayed(const Duration(milliseconds: 400), () {
      if (mounted) unawaited(_probeCutouts());
    });
  }

  @override
  void dispose() {
    _liveCtrl.dispose();
    _cardCtrl.dispose();
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant TaktonDynamicIsland oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncAnims();
  }

  void _syncAnims() {
    final c = widget.controller;
    final live = c.islandLive || c.streaming;
    final card = c.islandExpanded && live;

    if (live != _prevLive) {
      _prevLive = live;
      if (live) {
        _liveCtrl.forward();
      } else {
        // Collapse card first if open, then wings.
        if (_cardCtrl.value > 0) {
          _cardCtrl.reverse().then((_) {
            if (mounted && !widget.controller.islandLive) {
              _liveCtrl.reverse();
            }
          });
        } else {
          _liveCtrl.reverse();
        }
      }
    }
    if (card != _prevCard) {
      _prevCard = card;
      if (card) {
        if (_liveCtrl.value < 1) {
          _liveCtrl.forward().then((_) {
            if (mounted && widget.controller.islandExpanded) {
              _cardCtrl.forward();
            }
          });
        } else {
          _cardCtrl.forward();
        }
      } else {
        _cardCtrl.reverse();
      }
    }
  }

  Future<void> _probeCutouts() async {
    if (kIsWeb) return;
    try {
      final raw = await _channel.invokeMethod<List<dynamic>>('getCutouts');
      final list = <Rect>[];
      if (raw != null) {
        for (final e in raw) {
          if (e is! Map) continue;
          final m = Map<String, dynamic>.from(e);
          final l = (m['left'] as num?)?.toDouble();
          final top = (m['top'] as num?)?.toDouble();
          final r = (m['right'] as num?)?.toDouble();
          final b = (m['bottom'] as num?)?.toDouble();
          if (l == null || top == null || r == null || b == null) continue;
          if (r <= l || b <= top) continue;
          list.add(Rect.fromLTRB(l, top, r, b));
        }
      }
      if (!mounted) return;
      setState(() => _nativeCutouts = list);
    } catch (_) {
      // Fall back to Flutter displayFeatures / synthetic center hole.
    }
  }

  @override
  Widget build(BuildContext context) {
    // Drive anim when parent rebuilds from ChangeNotifier.
    _syncAnims();

    final c = widget.controller;
    final mq = MediaQuery.of(context);
    final shellW = widget.shellWidth ?? mq.size.width;
    final geom = _IslandGeom.resolve(
      mq: mq,
      shellW: shellW,
      nativeCutouts: _nativeCutouts,
    );

    final kind = c.islandKind;
    final accent = kind == 'stream'
        ? PixelColors.cyan
        : (kind == 'local' ? PixelColors.green : PixelColors.purple);

    // Camera well = physical hole (never put text here).
    final well = geom.well; // Size
    final cx = geom.center.dx;
    final cy = geom.center.dy;

    // Capsule height hugs the hole (+ thin rim).
    final h = (well.height + 8).clamp(28.0, 38.0);
    // Idle cheeks: minimal black beside hole so capsule wraps it.
    final cheekIdle = (well.width * 0.22).clamp(5.0, 11.0);
    // Live wings: room for short labels, still proportional to hole.
    final wingLive = (well.width * 2.6).clamp(52.0, 96.0);

    final leftLabel = _leftLabel(c);
    final rightLabel = _rightLabel(c);

    return AnimatedBuilder(
      animation: Listenable.merge([_liveT, _cardT]),
      builder: (context, _) {
        final t = _liveT.value; // 0 idle → 1 live
        final wing = cheekIdle + (wingLive - cheekIdle) * t;
        final islandW = well.width + wing * 2;
        final islandH = h;
        // Soft scale on open for "pop"
        final pop = 0.92 + 0.08 * Curves.easeOutBack.transform(t.clamp(0.0, 1.0));

        final top = (cy - islandH / 2)
            .clamp(0.0, math.max(0.0, mq.viewPadding.top))
            .toDouble();
        final left = (cx - islandW / 2)
            .clamp(4.0, shellW - islandW - 4)
            .toDouble();

        final cardProgress = _cardT.value;
        final cardW = math.min(shellW - 28, 300.0);
        final cardLeft = (shellW - cardW) / 2;
        final cardTop = top + islandH + 4;

        return Stack(
          clipBehavior: Clip.none,
          children: [
            // ---- Capsule (小岛) ----
            Positioned(
              top: top,
              left: left,
              width: islandW,
              height: islandH,
              child: Transform.scale(
                scale: pop,
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => _onTap(c),
                  onLongPress: () {
                    if (c.pcConnected) {
                      c.setTab(AppTab.approve);
                    } else {
                      c.setTab(AppTab.me);
                    }
                  },
                  child: DecoratedBox(
                    decoration: BoxDecoration(
                      color: Colors.black,
                      borderRadius: BorderRadius.circular(islandH / 2),
                      border: Border.all(
                        color: Color.lerp(
                              Colors.white.withValues(alpha: 0.04),
                              accent.withValues(alpha: 0.5),
                              t,
                            ) ??
                            Colors.black,
                        width: 0.7,
                      ),
                      boxShadow: t > 0.05
                          ? [
                              BoxShadow(
                                color: Colors.black
                                    .withValues(alpha: 0.28 * t),
                                blurRadius: 10 * t,
                                offset: Offset(0, 2 * t),
                              ),
                            ]
                          : null,
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(islandH / 2),
                      child: Row(
                        children: [
                          // LEFT wing — text stays OUTSIDE camera well
                          Expanded(
                            child: Opacity(
                              opacity: t,
                              child: Align(
                                alignment: Alignment.centerRight,
                                child: Padding(
                                  padding: EdgeInsets.only(
                                    left: 6,
                                    right: math.max(3, well.width * 0.08),
                                  ),
                                  child: t > 0.15
                                      ? Text(
                                          leftLabel,
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          textAlign: TextAlign.right,
                                          style: _labelStyle(islandH, true),
                                        )
                                      : const SizedBox.shrink(),
                                ),
                              ),
                            ),
                          ),

                          // CAMERA WELL — exact hole size, no text, no widgets
                          // that draw over the lens. Physical cutout sits here.
                          // Camera well: reserved empty slot matching cutout.
                          // Labels only live in the Expanded wings — never here.
                          SizedBox(width: well.width, height: islandH),

                          // RIGHT wing
                          Expanded(
                            child: Opacity(
                              opacity: t,
                              child: Align(
                                alignment: Alignment.centerLeft,
                                child: Padding(
                                  padding: EdgeInsets.only(
                                    left: math.max(3, well.width * 0.08),
                                    right: 6,
                                  ),
                                  child: t > 0.15
                                      ? Text(
                                          rightLabel,
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          textAlign: TextAlign.left,
                                          style: _labelStyle(islandH, false),
                                        )
                                      : const SizedBox.shrink(),
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),

            // ---- 大岛 card (Fluid Cloud) — slide + fade ----
            if (cardProgress > 0.001)
              Positioned(
                top: cardTop + (1 - cardProgress) * -12,
                left: cardLeft,
                width: cardW,
                child: Opacity(
                  opacity: cardProgress.clamp(0.0, 1.0),
                  child: Transform.scale(
                    scale: 0.92 + 0.08 * cardProgress,
                    alignment: Alignment.topCenter,
                    child: _IslandCard(
                      dark: widget.dark,
                      accent: accent,
                      controller: c,
                      onClose: () {
                        if (c.islandExpanded) c.toggleIslandExpanded();
                      },
                    ),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }

  void _onTap(AppController c) {
    if (c.streaming) {
      // Expand card for streaming detail
      if (!c.islandExpanded) {
        c.islandLive = true;
        c.toggleIslandExpanded();
      } else {
        c.toggleIslandExpanded();
      }
      return;
    }
    if (c.islandLive) {
      c.toggleIslandExpanded();
    } else {
      c.pulseIsland(
        text: c.pcConnected
            ? '已连 · ${c.state['approvals_pending'] ?? c.approvals.length}'
            : '本机',
        kind: c.pcConnected ? 'conn' : 'local',
      );
    }
  }

  TextStyle _labelStyle(double islandH, bool bold) {
    return PixelTheme.mono.copyWith(
      fontSize: (islandH * 0.33).clamp(9.5, 11.5),
      fontWeight: bold ? FontWeight.w700 : FontWeight.w600,
      color: Colors.white.withValues(alpha: bold ? 1 : 0.9),
      height: 1.0,
      letterSpacing: -0.15,
    );
  }

  static String _leftLabel(AppController c) {
    if (c.streaming) return '生成';
    final t = c.islandText.trim();
    if (t.isEmpty) return c.pcConnected ? '已连' : '本机';
    if (t.contains('·')) return t.split('·').first.trim();
    if (t.length <= 4) return t;
    return t.substring(0, 4);
  }

  static String _rightLabel(AppController c) {
    if (c.streaming) {
      final t = c.islandText.trim();
      if (t.isNotEmpty && t != '生成中' && t != '生成') {
        return t.length > 6 ? '${t.substring(0, 6)}…' : t;
      }
      return '中…';
    }
    final t = c.islandText.trim();
    if (t.contains('·')) {
      final rest = t.split('·').skip(1).join('·').trim();
      if (rest.isNotEmpty) {
        return rest.length > 6 ? '${rest.substring(0, 6)}…' : rest;
      }
    }
    if (c.pcConnected) {
      final n = c.state['approvals_pending'] ?? c.approvals.length;
      return '待办$n';
    }
    return '就绪';
  }
}

class _IslandCard extends StatelessWidget {
  const _IslandCard({
    required this.dark,
    required this.accent,
    required this.controller,
    required this.onClose,
  });

  final bool dark;
  final Color accent;
  final AppController controller;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final ink = dark ? Colors.white : PixelColors.ink;
    return Material(
      color: Colors.transparent,
      child: Container(
        padding: const EdgeInsets.fromLTRB(14, 11, 10, 11),
        decoration: BoxDecoration(
          color: dark ? const Color(0xF2151A2E) : const Color(0xF5FFFFFF),
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: accent.withValues(alpha: 0.3)),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.2),
              blurRadius: 18,
              offset: const Offset(0, 8),
            ),
          ],
        ),
        child: Row(
          children: [
            Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(color: accent, shape: BoxShape.circle),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    c.streaming
                        ? '生成中'
                        : (c.pcConnected ? '已连 PC' : '本机模式'),
                    style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w800,
                      color: ink,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    c.islandText.isNotEmpty
                        ? c.islandText
                        : (c.streaming ? '流式输出 · 点红键停止' : '再点一次收起'),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 11.5,
                      height: 1.3,
                      color: ink.withValues(alpha: 0.55),
                    ),
                  ),
                ],
              ),
            ),
            IconButton(
              visualDensity: VisualDensity.compact,
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
              onPressed: onClose,
              icon: Icon(
                Icons.close_rounded,
                size: 18,
                color: ink.withValues(alpha: 0.4),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _IslandGeom {
  const _IslandGeom({
    required this.center,
    required this.well,
    required this.fromSystem,
  });

  final Offset center;
  /// Exact camera cutout size (logical px) — text must not enter this box.
  final Size well;
  final bool fromSystem;

  static _IslandGeom resolve({
    required MediaQueryData mq,
    required double shellW,
    required List<Rect> nativeCutouts,
  }) {
    final band = mq.viewPadding.top;
    final candidates = <Rect>[
      ...nativeCutouts,
      ...mq.displayFeatures
          .where((f) => f.type == DisplayFeatureType.cutout)
          .map((f) => f.bounds),
    ].where((r) => r.width > 0 && r.height > 0 && r.top < band + 20).toList();

    if (candidates.isNotEmpty) {
      final mid = shellW / 2;
      // 中置优先
      candidates.sort((a, b) {
        final da = (a.center.dx - mid).abs();
        final db = (b.center.dx - mid).abs();
        return da.compareTo(db);
      });
      var r = candidates.first;

      // Full-width notch band → collapse to height-based circle at center.
      if (r.width > shellW * 0.42) {
        final d = r.height.clamp(20.0, 36.0);
        r = Rect.fromCenter(
          center: Offset(mid, r.center.dy),
          width: d,
          height: d,
        );
      }

      // Keep true aspect (ellipse / pill hole) — don't force square.
      final well = Size(
        r.width.clamp(16.0, 48.0),
        r.height.clamp(16.0, 42.0),
      );
      return _IslandGeom(
        center: Offset(
          r.center.dx.clamp(well.width, shellW - well.width),
          r.center.dy.clamp(well.height / 2, math.max(well.height / 2, band)),
        ),
        well: well,
        fromSystem: true,
      );
    }

    final b = band > 0 ? band : 32.0;
    final d = (b * 0.56).clamp(22.0, 30.0);
    final cy = (b * 0.5).clamp(d / 2 + 1, b - 1);
    return _IslandGeom(
      center: Offset(shellW / 2, cy),
      well: Size(d, d),
      fromSystem: false,
    );
  }
}
