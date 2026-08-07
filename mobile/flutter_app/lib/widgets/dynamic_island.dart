import 'dart:async';
import 'dart:math' as math;
import 'dart:ui' show DisplayFeatureType;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/physics.dart';
import 'package:flutter/services.dart';

import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';

/// Center punch-hole Dynamic Island.
///
/// Constraints that match real OEM / iPhone behaviour on Android:
/// 1. Capsule **hugs the camera cutout** (well = exact hole size).
/// 2. Horizontal expand stays inside a **center safe lane** so it never
///    covers system clock / signal / battery icons.
/// 3. Open/close uses **spring physics** (rubber-band / 弹弹), not linear ease.
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

  /// 0 idle → 1 live (wings). Driven by spring, not fixed duration.
  late final AnimationController _liveCtrl;

  /// 0 hidden → 1 card shown under status bar.
  late final AnimationController _cardCtrl;

  bool _prevLive = false;
  bool _prevCard = false;

  /// iPhone-like soft spring (slight overshoot = 弹弹).
  static final _springOpen = SpringDescription(
    mass: 0.85,
    stiffness: 220,
    damping: 14.5,
  );
  static final _springClose = SpringDescription(
    mass: 0.9,
    stiffness: 280,
    damping: 20,
  );
  static final _springCard = SpringDescription(
    mass: 0.75,
    stiffness: 190,
    damping: 13,
  );

  @override
  void initState() {
    super.initState();
    // Upper bound >1 so spring overshoot is visible, then settles to 1.
    _liveCtrl = AnimationController.unbounded(vsync: this);
    _cardCtrl = AnimationController.unbounded(vsync: this);

    unawaited(_probeCutouts());
    WidgetsBinding.instance.addPostFrameCallback((_) {
      unawaited(_probeCutouts());
    });
    Future<void>.delayed(const Duration(milliseconds: 350), () {
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

  void _springTo(AnimationController c, double target, SpringDescription spring) {
    final sim = SpringSimulation(spring, c.value, target, c.velocity);
    c.animateWith(sim);
  }

  void _syncAnims() {
    final c = widget.controller;
    final live = c.islandLive || c.streaming;
    final card = c.islandExpanded && live;

    if (live != _prevLive) {
      _prevLive = live;
      if (live) {
        _springTo(_liveCtrl, 1.0, _springOpen);
      } else {
        if (_cardCtrl.value > 0.05) {
          _springTo(_cardCtrl, 0.0, _springClose);
          // Collapse wings after card mostly gone.
          Future<void>.delayed(const Duration(milliseconds: 90), () {
            if (mounted &&
                !widget.controller.islandLive &&
                !widget.controller.streaming) {
              _springTo(_liveCtrl, 0.0, _springClose);
            }
          });
        } else {
          _springTo(_liveCtrl, 0.0, _springClose);
        }
      }
    }
    if (card != _prevCard) {
      _prevCard = card;
      if (card) {
        if (_liveCtrl.value < 0.85) {
          _springTo(_liveCtrl, 1.0, _springOpen);
          Future<void>.delayed(const Duration(milliseconds: 120), () {
            if (mounted && widget.controller.islandExpanded) {
              _springTo(_cardCtrl, 1.0, _springCard);
            }
          });
        } else {
          _springTo(_cardCtrl, 1.0, _springCard);
        }
      } else {
        _springTo(_cardCtrl, 0.0, _springClose);
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
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
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

    final well = geom.well;
    final cx = geom.center.dx;
    final cy = geom.center.dy;

    // ---- Safe lane: never cover system status icons ----
    // Left: clock / notification icons. Right: signal / battery / wifi.
    // Most CN ROMs keep ~72–100 logical px per side for status chrome.
    final leftReserve = (shellW * 0.20).clamp(68.0, 104.0);
    final rightReserve = (shellW * 0.20).clamp(68.0, 104.0);
    final maxIslandW =
        (shellW - leftReserve - rightReserve).clamp(well.width + 16, shellW * 0.55);

    // Capsule height: match hole as tightly as possible (shape 贴合).
    final h = well.height.clamp(24.0, 36.0);
    // Idle width: hole + thin cheeks only (almost a rounded rect on the hole).
    final cheekIdle = (well.width * 0.14).clamp(3.0, 7.0);
    final wIdle = (well.width + cheekIdle * 2).clamp(h, maxIslandW);
    // Live: open wings but hard-capped by safe lane (protect system icons).
    final wingWant = (well.width * 2.1).clamp(40.0, 72.0);
    final wLive = math.min(wIdle + wingWant * 2, maxIslandW);

    final leftLabel = _leftLabel(c);
    final rightLabel = _rightLabel(c);

    return AnimatedBuilder(
      animation: Listenable.merge([_liveCtrl, _cardCtrl]),
      builder: (context, _) {
        // Spring can overshoot slightly past 1 — clamp for layout sizes.
        final rawT = _liveCtrl.value;
        final tLayout = rawT.clamp(0.0, 1.0);

        final islandW = wIdle + (wLive - wIdle) * tLayout;
        final islandH = h;

        // Vertical: center on camera. Prefer sitting fully in status band.
        final top = (cy - islandH / 2)
            .clamp(0.0, math.max(0.0, mq.viewPadding.top - islandH * 0.15))
            .toDouble();
        // Horizontal: lock center to camera (中置), then clamp into safe lane.
        var left = cx - islandW / 2;
        left = left.clamp(leftReserve * 0.15, shellW - islandW - rightReserve * 0.15);

        // Rubber-band scale from spring overshoot (QQ / iPhone DI feel).
        final scale = 1.0 + (rawT - tLayout) * 0.06;
        // Extra squash on open start
        final squash = 1.0 + (1.0 - (rawT - 0.5).abs() * 2).clamp(0.0, 1.0) *
            (rawT < 1 ? 0.02 : 0);

        final cardRaw = _cardCtrl.value;
        final cardT = cardRaw.clamp(0.0, 1.0);
        final cardScale = 0.88 + 0.12 * cardT + (cardRaw - cardT) * 0.04;
        final cardW = math.min(shellW - leftReserve * 0.5 - rightReserve * 0.5, 280.0);
        final cardLeft = ((shellW - cardW) / 2)
            .clamp(leftReserve * 0.35, shellW - cardW - rightReserve * 0.35);
        final cardTop = mq.viewPadding.top + 4 + (1 - cardT) * -10;

        return Stack(
          clipBehavior: Clip.none,
          children: [
            // ---- Capsule ----
            Positioned(
              top: top,
              left: left,
              width: islandW,
              height: islandH,
              child: Transform.scale(
                scale: scale + squash * 0.5,
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
                              Colors.white.withValues(alpha: 0.05),
                              accent.withValues(alpha: 0.55),
                              tLayout,
                            ) ??
                            Colors.black,
                        width: 0.65,
                      ),
                      boxShadow: tLayout > 0.08
                          ? [
                              BoxShadow(
                                color: accent.withValues(alpha: 0.12 * tLayout),
                                blurRadius: 12 * tLayout,
                                spreadRadius: 0,
                              ),
                              BoxShadow(
                                color: Colors.black.withValues(alpha: 0.25 * tLayout),
                                blurRadius: 8 * tLayout,
                                offset: Offset(0, 2 * tLayout),
                              ),
                            ]
                          : null,
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(islandH / 2),
                      child: Row(
                        children: [
                          // Left wing — only as wide as spring allows
                          Expanded(
                            child: IgnorePointer(
                              child: Opacity(
                                opacity: (tLayout * 1.4).clamp(0.0, 1.0),
                                child: Align(
                                  alignment: Alignment.centerRight,
                                  child: Padding(
                                    padding: EdgeInsets.only(
                                      left: 4,
                                      right: math.max(2, well.width * 0.06),
                                    ),
                                    child: tLayout > 0.12
                                        ? Text(
                                            leftLabel,
                                            maxLines: 1,
                                            overflow: TextOverflow.clip,
                                            softWrap: false,
                                            textAlign: TextAlign.right,
                                            style: _labelStyle(islandH, true),
                                          )
                                        : const SizedBox.shrink(),
                                  ),
                                ),
                              ),
                            ),
                          ),

                          // Exact camera well — never draw text here
                          SizedBox(
                            width: well.width,
                            height: islandH,
                          ),

                          Expanded(
                            child: IgnorePointer(
                              child: Opacity(
                                opacity: (tLayout * 1.4).clamp(0.0, 1.0),
                                child: Align(
                                  alignment: Alignment.centerLeft,
                                  child: Padding(
                                    padding: EdgeInsets.only(
                                      left: math.max(2, well.width * 0.06),
                                      right: 4,
                                    ),
                                    child: tLayout > 0.12
                                        ? Text(
                                            rightLabel,
                                            maxLines: 1,
                                            overflow: TextOverflow.clip,
                                            softWrap: false,
                                            textAlign: TextAlign.left,
                                            style: _labelStyle(islandH, false),
                                          )
                                        : const SizedBox.shrink(),
                                  ),
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

            // ---- Drop card BELOW status bar (never in icon lanes) ----
            if (cardT > 0.01)
              Positioned(
                top: cardTop,
                left: cardLeft,
                width: cardW,
                child: Opacity(
                  opacity: cardT,
                  child: Transform.scale(
                    scale: cardScale.clamp(0.85, 1.08),
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
      fontSize: (islandH * 0.30).clamp(9.0, 10.5),
      fontWeight: bold ? FontWeight.w700 : FontWeight.w600,
      color: Colors.white.withValues(alpha: bold ? 1 : 0.88),
      height: 1.0,
      letterSpacing: -0.2,
    );
  }

  static String _leftLabel(AppController c) {
    if (c.streaming) return '生成';
    final t = c.islandText.trim();
    if (t.isEmpty) return c.pcConnected ? '已连' : '本机';
    if (t.contains('·')) return t.split('·').first.trim();
    if (t.length <= 3) return t;
    return t.substring(0, 3);
  }

  static String _rightLabel(AppController c) {
    if (c.streaming) {
      final t = c.islandText.trim();
      if (t.isNotEmpty && t != '生成中' && t != '生成') {
        return t.length > 4 ? '${t.substring(0, 4)}…' : t;
      }
      return '中';
    }
    final t = c.islandText.trim();
    if (t.contains('·')) {
      final rest = t.split('·').skip(1).join('·').trim();
      if (rest.isNotEmpty) {
        return rest.length > 4 ? '${rest.substring(0, 4)}…' : rest;
      }
    }
    if (c.pcConnected) {
      final n = c.state['approvals_pending'] ?? c.approvals.length;
      return '$n';
    }
    return 'OK';
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
      elevation: 0,
      child: Container(
        padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
        decoration: BoxDecoration(
          color: dark ? const Color(0xF2141828) : const Color(0xF7FFFFFF),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: accent.withValues(alpha: 0.28)),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.22),
              blurRadius: 20,
              offset: const Offset(0, 10),
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
                      fontSize: 13.5,
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
                      fontSize: 12,
                      height: 1.3,
                      color: ink.withValues(alpha: 0.55),
                    ),
                  ),
                ],
              ),
            ),
            IconButton(
              visualDensity: VisualDensity.compact,
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
  });

  final Offset center;
  final Size well;

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
    ].where((r) => r.width > 0 && r.height > 0 && r.top < band + 24).toList();

    if (candidates.isNotEmpty) {
      final mid = shellW / 2;
      candidates.sort((a, b) =>
          (a.center.dx - mid).abs().compareTo((b.center.dx - mid).abs()));
      var r = candidates.first;

      // Status-bar-wide fake cutout → real hole ≈ height at center.
      if (r.width > shellW * 0.38) {
        final d = r.height.clamp(18.0, 34.0);
        r = Rect.fromCenter(
          center: Offset(mid, r.center.dy),
          width: d,
          height: d,
        );
      }

      // Prefer near-circular well for punch-hole 贴合.
      final side = math
          .min(r.width, r.height)
          .clamp(18.0, 36.0);
      // If system reports ellipse, keep aspect but cap difference.
      final wellW = r.width <= r.height * 1.25
          ? r.width.clamp(18.0, 40.0)
          : side;
      final wellH = r.height.clamp(18.0, 36.0);

      return _IslandGeom(
        center: Offset(
          r.center.dx.clamp(side, shellW - side),
          r.center.dy.clamp(wellH / 2 + 0.5, math.max(wellH / 2 + 0.5, band - 1)),
        ),
        well: Size(wellW, wellH),
      );
    }

    // Synthetic center hole from status-bar band.
    final b = band > 0 ? band : 30.0;
    final d = (b * 0.52).clamp(20.0, 28.0);
    final cy = (b * 0.48).clamp(d / 2 + 1, b - 1);
    return _IslandGeom(
      center: Offset(shellW / 2, cy),
      well: Size(d, d),
    );
  }
}
