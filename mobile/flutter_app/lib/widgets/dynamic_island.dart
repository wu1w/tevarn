import 'dart:async';
import 'dart:math' as math;
import 'dart:ui' show DisplayFeatureType;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';

/// Industry-style camera island for center punch-hole Androids.
///
/// Visual language (小米超级岛 / OPPO 流体云 / vivo 原子岛 / 华为实况窗 / Apple DI):
/// - **小岛**: solid black **stadium capsule** in the status-bar band, sized from
///   the real [DisplayCutout] (native) or Flutter [DisplayFeatureType.cutout].
/// - **活跃**: same height, width expands left+right of the hole for a short label.
/// - **大岛/卡片**: content card hangs **below** the capsule (Fluid Cloud), never
///   a giant in-bar blob.
///
/// Idle is nearly tight to the camera; we never force a fixed 118×28 bar.
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

class _TaktonDynamicIslandState extends State<TaktonDynamicIsland> {
  static const _channel = MethodChannel('takton/display_cutout');

  /// Native cutout rects in logical px (empty until first probe).
  List<Rect> _nativeCutouts = const [];
  bool _probed = false;

  @override
  void initState() {
    super.initState();
    unawaited(_probeCutouts());
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    // Re-probe after first frame / rotation.
    if (_probed) unawaited(_probeCutouts());
  }

  Future<void> _probeCutouts() async {
    if (kIsWeb) {
      _probed = true;
      return;
    }
    try {
      final raw = await _channel.invokeMethod<List<dynamic>>('getCutouts');
      final list = <Rect>[];
      if (raw != null) {
        for (final e in raw) {
          if (e is! Map) continue;
          final m = Map<String, dynamic>.from(e);
          final l = (m['left'] as num?)?.toDouble();
          final t = (m['top'] as num?)?.toDouble();
          final r = (m['right'] as num?)?.toDouble();
          final b = (m['bottom'] as num?)?.toDouble();
          if (l == null || t == null || r == null || b == null) continue;
          if (r <= l || b <= t) continue;
          list.add(Rect.fromLTRB(l, t, r, b));
        }
      }
      if (!mounted) return;
      setState(() {
        _nativeCutouts = list;
        _probed = true;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() => _probed = true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final mq = MediaQuery.of(context);
    final shellW = widget.shellWidth ?? mq.size.width;
    final geom = _IslandGeom.resolve(
      mq: mq,
      shellW: shellW,
      nativeCutouts: _nativeCutouts,
    );

    final live = c.islandLive || c.streaming || c.islandExpanded;
    final showCard = c.islandExpanded && live;
    final kind = c.islandKind;
    final accent = kind == 'stream'
        ? PixelColors.cyan
        : (kind == 'local' ? PixelColors.green : PixelColors.purple);

    // --- OEM capsule geometry ---
    // Height follows the larger of: cutout height, ~status-bar content.
    // Stadium always: radius = h/2 (胶囊, not circle blob, not full-width bar).
    final d = geom.cameraDiameter;
    final hIdle = (d + 6).clamp(26.0, 36.0);
    // Idle width ≈ camera + small side cheeks (tight wrap of 中置孔).
    // Cheek scales with d so big holes get slightly wider islands.
    final cheek = (d * 0.28).clamp(4.0, 12.0);
    final wIdle = (d + cheek * 2).clamp(hIdle, hIdle * 1.55);

    // Active: grow horizontally only (Apple/Xiaomi compact activity).
    // Extra wing scales with camera — still a pill, not a notification banner.
    final wing = (d * 2.4).clamp(48.0, 88.0);
    final wLive = math
        .min(wIdle + wing * 2, math.min(shellW * 0.58, 220.0))
        .toDouble();
    final hLive = showCard ? hIdle : hIdle;

    final islandW = live ? wLive : wIdle;
    final islandH = hLive;

    // Pin vertically to camera center inside status band.
    final top = (geom.center.dy - islandH / 2)
        .clamp(0.0, math.max(0.0, mq.viewPadding.top - 2))
        .toDouble();
    final left = (geom.center.dx - islandW / 2)
        .clamp(4.0, shellW - islandW - 4)
        .toDouble();

    final label = _compactLabel(c);

    return Stack(
      children: [
        // ---- 小岛 capsule ----
        Positioned(
          top: top,
          left: left,
          width: islandW,
          height: islandH,
          child: GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () {
              if (c.streaming) {
                c.pulseIsland(text: '生成中', kind: 'stream');
                return;
              }
              if (live) {
                c.toggleIslandExpanded();
              } else {
                c.pulseIsland(
                  text: c.pcConnected
                      ? '已连 · ${c.state['approvals_pending'] ?? c.approvals.length}'
                      : '本机',
                  kind: c.pcConnected ? 'conn' : 'local',
                );
              }
            },
            onLongPress: () {
              if (c.pcConnected) {
                c.setTab(AppTab.approve);
              } else {
                c.setTab(AppTab.me);
              }
            },
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 320),
              curve: Curves.easeOutCubic,
              width: islandW,
              height: islandH,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: Colors.black,
                // Perfect stadium / 胶囊
                borderRadius: BorderRadius.circular(islandH / 2),
                border: Border.all(
                  color: live
                      ? accent.withValues(alpha: 0.42)
                      : Colors.white.withValues(alpha: 0.04),
                  width: 0.6,
                ),
                boxShadow: live
                    ? [
                        BoxShadow(
                          color: Colors.black.withValues(alpha: 0.35),
                          blurRadius: 8,
                          offset: const Offset(0, 2),
                        ),
                      ]
                    : null,
              ),
              child: live
                  ? Padding(
                      padding: EdgeInsets.symmetric(
                        horizontal: (islandH * 0.35).clamp(8.0, 14.0),
                      ),
                      child: Row(
                        mainAxisAlignment: MainAxisAlignment.center,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Container(
                            width: 6,
                            height: 6,
                            margin: const EdgeInsets.only(right: 6),
                            decoration: BoxDecoration(
                              color: accent,
                              shape: BoxShape.circle,
                              boxShadow: [
                                BoxShadow(
                                  color: accent.withValues(alpha: 0.55),
                                  blurRadius: 3,
                                ),
                              ],
                            ),
                          ),
                          Flexible(
                            child: Text(
                              label,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: PixelTheme.mono.copyWith(
                                fontSize: (islandH * 0.34).clamp(9.5, 11.5),
                                fontWeight: FontWeight.w700,
                                color: Colors.white,
                                height: 1.0,
                                letterSpacing: -0.2,
                              ),
                            ),
                          ),
                        ],
                      ),
                    )
                  : null, // idle: pure black capsule over the hole
            ),
          ),
        ),

        // ---- 大岛 / Fluid Cloud card below camera ----
        if (showCard)
          Positioned(
            top: top + islandH + 6,
            left: (shellW - math.min(shellW - 32, 300)) / 2,
            width: math.min(shellW - 32, 300),
            child: Material(
              color: Colors.transparent,
              child: AnimatedOpacity(
                opacity: 1,
                duration: const Duration(milliseconds: 200),
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  decoration: BoxDecoration(
                    color: widget.dark
                        ? const Color(0xF2151A2E)
                        : Colors.white.withValues(alpha: 0.96),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(
                      color: accent.withValues(alpha: 0.28),
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: Colors.black.withValues(alpha: 0.18),
                        blurRadius: 16,
                        offset: const Offset(0, 6),
                      ),
                    ],
                  ),
                  child: Row(
                    children: [
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          color: accent,
                          shape: BoxShape.circle,
                        ),
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
                                color: widget.dark
                                    ? Colors.white
                                    : PixelColors.ink,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              c.islandText.isNotEmpty
                                  ? c.islandText
                                  : (c.streaming
                                      ? '流式输出 · 点红键可停止'
                                      : '轻点岛收回'),
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                fontSize: 11.5,
                                height: 1.3,
                                color: (widget.dark
                                        ? Colors.white
                                        : PixelColors.ink)
                                    .withValues(alpha: 0.55),
                              ),
                            ),
                          ],
                        ),
                      ),
                      GestureDetector(
                        onTap: () => c.toggleIslandExpanded(),
                        child: Icon(
                          Icons.close_rounded,
                          size: 18,
                          color: (widget.dark ? Colors.white : PixelColors.ink)
                              .withValues(alpha: 0.4),
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }

  static String _compactLabel(AppController c) {
    if (c.streaming) {
      final t = c.islandText.trim();
      if (t.isNotEmpty && t != '生成中') {
        return t.length > 10 ? '${t.substring(0, 10)}…' : t;
      }
      return '生成中';
    }
    final t = c.islandText.trim();
    if (t.isNotEmpty) return t.length > 12 ? '${t.substring(0, 12)}…' : t;
    return c.pcConnected ? '已连' : '本机';
  }
}

class _IslandGeom {
  const _IslandGeom({
    required this.center,
    required this.cameraDiameter,
    required this.fromSystem,
  });

  final Offset center;
  /// Logical diameter of the front camera hole (or pill short side).
  final double cameraDiameter;
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
    ].where((r) => r.width > 0 && r.height > 0 && r.top < band + 16).toList();

    if (candidates.isNotEmpty) {
      // Prefer 中置: closest to horizontal center.
      final mid = shellW / 2;
      candidates.sort((a, b) {
        final da = (a.center.dx - mid).abs();
        final db = (b.center.dx - mid).abs();
        return da.compareTo(db);
      });
      var r = candidates.first;

      // OEM sometimes reports full-status-bar notch width — collapse to hole by height.
      if (r.width > shellW * 0.42) {
        final d = r.height.clamp(20.0, 38.0);
        r = Rect.fromCenter(
          center: Offset(mid, r.center.dy),
          width: d,
          height: d,
        );
      }

      // Pill-shaped hardware (rare dual sensor): use short side as diameter,
      // keep horizontal center of the pill.
      final diameter = r.shortestSide.clamp(18.0, 42.0);
      return _IslandGeom(
        center: Offset(
          r.center.dx.clamp(24.0, shellW - 24),
          r.center.dy.clamp(diameter / 2, math.max(diameter / 2, band)),
        ),
        cameraDiameter: diameter,
        fromSystem: true,
      );
    }

    // No cutout API: synthesize center hole from status-bar height
    // (typical 中置 dig ≈ 55–70% of status bar band).
    final b = band > 0 ? band : 32.0;
    final d = (b * 0.58).clamp(22.0, 32.0);
    final cy = (b * 0.5).clamp(d / 2 + 1, b - 1);
    return _IslandGeom(
      center: Offset(shellW / 2, cy),
      cameraDiameter: d,
      fromSystem: false,
    );
  }
}
