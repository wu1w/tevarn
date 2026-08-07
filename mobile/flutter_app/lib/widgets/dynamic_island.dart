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

/// Camera-hugging Dynamic Island (expert cutout fix).
///
/// Rules:
/// 1. Prefer [MediaQuery.displayFeatures] (already logical px).
/// 2. Native channel returns **physical** px; convert with [devicePixelRatio].
/// 3. Idle capsule = cutout.bounds.inflate(rim) — no minWidth / cheek formulas.
/// 4. Live: lock height, expand width only; hard-cap to center safe lane.
/// 5. Detail card drops below status bar (Fluid Cloud), not into icon zones.
/// 6. Spring open/close for 弹弹 feel.
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

  /// Physical-pixel cutout rects from Android (l,t,r,b).
  List<Map<String, double>> _nativePhysical = const [];

  late final AnimationController _liveCtrl;
  late final AnimationController _cardCtrl;

  bool _prevLive = false;
  bool _prevCard = false;

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

  /// Thin rim around real cutout — only allowed idle inflation.
  static const double _rim = 2.0;

  @override
  void initState() {
    super.initState();
    _liveCtrl = AnimationController.unbounded(vsync: this);
    _cardCtrl = AnimationController.unbounded(vsync: this);

    unawaited(_probeCutouts());
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

  void _springTo(
    AnimationController c,
    double target,
    SpringDescription spring,
  ) {
    c.animateWith(SpringSimulation(spring, c.value, target, c.velocity));
  }

  void _syncAnims() {
    final c = widget.controller;
    final live = c.islandLive || c.streaming;
    final card = c.islandExpanded && live;

    if (live != _prevLive) {
      _prevLive = live;
      if (live) {
        _springTo(_liveCtrl, 1.0, _springOpen);
      } else if (_cardCtrl.value > 0.05) {
        _springTo(_cardCtrl, 0.0, _springClose);
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
      final raw = await _channel.invokeMethod<dynamic>('getCutouts');
      if (raw is! Map) return;
      final m = Map<String, dynamic>.from(raw);
      final rectsRaw = m['rects'];
      final list = <Map<String, double>>[];
      if (rectsRaw is List) {
        for (final e in rectsRaw) {
          if (e is! Map) continue;
          final r = Map<String, dynamic>.from(e);
          final l = (r['l'] as num?)?.toDouble();
          final t = (r['t'] as num?)?.toDouble();
          final rr = (r['r'] as num?)?.toDouble();
          final b = (r['b'] as num?)?.toDouble();
          if (l == null || t == null || rr == null || b == null) continue;
          if (rr <= l || b <= t) continue;
          list.add({'l': l, 't': t, 'r': rr, 'b': b});
        }
      }
      if (!mounted) return;
      setState(() => _nativePhysical = list);
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    _syncAnims();

    final c = widget.controller;
    final mq = MediaQuery.of(context);
    final shellW = widget.shellWidth ?? mq.size.width;
    final dpr = mq.devicePixelRatio;

    final cutout = _Cutout.resolve(
      mq: mq,
      shellW: shellW,
      dpr: dpr,
      nativePhysical: _nativePhysical,
    );

    final kind = c.islandKind;
    final accent = kind == 'stream'
        ? PixelColors.cyan
        : (kind == 'local' ? PixelColors.green : PixelColors.purple);

    // Idle = exact cutout + rim (expert: no cheek/minWidth rewrite).
    final idleRect = cutout.bounds.inflate(_rim);
    final idleW = idleRect.width;
    final idleH = idleRect.height;
    final wellW = cutout.bounds.width;
    final cx = cutout.bounds.center.dx;
    final cy = cutout.bounds.center.dy;

    // Safe lane for system status icons (clock left, battery right).
    final leftReserve = (shellW * 0.20).clamp(68.0, 104.0);
    final rightReserve = (shellW * 0.20).clamp(68.0, 104.0);
    final maxIslandW =
        (shellW - leftReserve - rightReserve).clamp(idleW, shellW * 0.55);

    // Live: height locked; wings only; cap by safe lane.
    final wing = (wellW * 2.0).clamp(36.0, 64.0);
    final liveW = math.min(idleW + wing * 2, maxIslandW);
    final liveH = idleH;

    final leftLabel = _leftLabel(c);
    final rightLabel = _rightLabel(c);

    return AnimatedBuilder(
      animation: Listenable.merge([_liveCtrl, _cardCtrl]),
      builder: (context, _) {
        final rawT = _liveCtrl.value;
        final t = rawT.clamp(0.0, 1.0);

        final islandW = idleW + (liveW - idleW) * t;
        final islandH = liveH;

        // Anchor to cutout center — do NOT horizontally yank idle island.
        final top = (cy - islandH / 2).clamp(0.0, double.infinity).toDouble();
        final left = (cx - islandW / 2)
            .clamp(4.0, shellW - islandW - 4)
            .toDouble();

        final scale = 1.0 + (rawT - t) * 0.06;

        final cardRaw = _cardCtrl.value;
        final cardT = cardRaw.clamp(0.0, 1.0);
        final cardScale = 0.88 + 0.12 * cardT + (cardRaw - cardT) * 0.04;
        final cardW =
            math.min(shellW - leftReserve * 0.5 - rightReserve * 0.5, 280.0);
        final cardLeft = ((shellW - cardW) / 2)
            .clamp(leftReserve * 0.35, shellW - cardW - rightReserve * 0.35);
        // Below status bar band (Fluid Cloud), never in icon lanes.
        final cardTop = mq.viewPadding.top + 6 + (1 - cardT) * -10;

        return Stack(
          clipBehavior: Clip.none,
          children: [
            Positioned(
              top: top,
              left: left,
              width: islandW,
              height: islandH,
              child: Transform.scale(
                scale: scale,
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
                              t,
                            ) ??
                            Colors.black,
                        width: 0.65,
                      ),
                      boxShadow: t > 0.08
                          ? [
                              BoxShadow(
                                color: accent.withValues(alpha: 0.12 * t),
                                blurRadius: 12 * t,
                              ),
                              BoxShadow(
                                color: Colors.black.withValues(alpha: 0.25 * t),
                                blurRadius: 8 * t,
                                offset: Offset(0, 2 * t),
                              ),
                            ]
                          : null,
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(islandH / 2),
                      child: Row(
                        children: [
                          Expanded(
                            child: IgnorePointer(
                              child: Opacity(
                                opacity: (t * 1.4).clamp(0.0, 1.0),
                                child: Align(
                                  alignment: Alignment.centerRight,
                                  child: Padding(
                                    padding: EdgeInsets.only(
                                      left: 4,
                                      right: math.max(2, wellW * 0.06),
                                    ),
                                    child: t > 0.12
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
                          // Camera well = exact cutout size; no text.
                          SizedBox(width: wellW, height: islandH),
                          Expanded(
                            child: IgnorePointer(
                              child: Opacity(
                                opacity: (t * 1.4).clamp(0.0, 1.0),
                                child: Align(
                                  alignment: Alignment.centerLeft,
                                  child: Padding(
                                    padding: EdgeInsets.only(
                                      left: math.max(2, wellW * 0.06),
                                      right: 4,
                                    ),
                                    child: t > 0.12
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

/// Resolved camera cutout in **logical** pixels.
class _Cutout {
  const _Cutout(this.bounds);
  final Rect bounds;

  /// 1) Flutter displayFeatures (logical) first.
  /// 2) Native physical rects / [dpr].
  /// 3) Synthetic center hole from status-bar band.
  static _Cutout resolve({
    required MediaQueryData mq,
    required double shellW,
    required double dpr,
    required List<Map<String, double>> nativePhysical,
  }) {
    final band = mq.viewPadding.top;
    final mid = shellW / 2;
    final safeDpr = dpr > 0.5 ? dpr : 1.0;

    // Prefer engine-converted cutouts (already logical px).
    final fromFlutter = mq.displayFeatures
        .where((f) => f.type == DisplayFeatureType.cutout)
        .map((f) => f.bounds)
        .where((r) => r.width > 0 && r.height > 0 && r.top < band + 24)
        .toList();

    // Native physical → logical via devicePixelRatio (not density).
    final fromNative = <Rect>[];
    for (final m in nativePhysical) {
      final l = m['l']! / safeDpr;
      final t = m['t']! / safeDpr;
      final r = m['r']! / safeDpr;
      final b = m['b']! / safeDpr;
      if (r > l && b > t && t < band + 24) {
        fromNative.add(Rect.fromLTRB(l, t, r, b));
      }
    }

    // Flutter first, then native — both logical after conversion.
    final candidates = <Rect>[...fromFlutter, ...fromNative];

    if (candidates.isNotEmpty) {
      // Center punch preferred (closest to horizontal midline).
      candidates.sort((a, b) =>
          (a.center.dx - mid).abs().compareTo((b.center.dx - mid).abs()));
      var chosen = candidates.first;

      // OEM full-width status-bar "cutout" → hole by height at center.
      if (chosen.width > shellW * 0.40) {
        final d = chosen.height.clamp(18.0, 34.0);
        chosen = Rect.fromCenter(
          center: Offset(mid, chosen.center.dy),
          width: d,
          height: d,
        );
      }

      // Keep reported aspect; only gentle clamps for pathological values.
      final w = chosen.width.clamp(14.0, 56.0).toDouble();
      final h = chosen.height.clamp(14.0, 42.0).toDouble();
      final cx = chosen.center.dx.clamp(w / 2, shellW - w / 2).toDouble();
      final cy = chosen.center.dy.clamp(h / 2, math.max(h / 2, band)).toDouble();

      return _Cutout(Rect.fromCenter(center: Offset(cx, cy), width: w, height: h));
    }

    // No system cutout: synthetic center hole tracks status-bar band.
    final b = band > 0 ? band : 30.0;
    final d = (b * 0.55).clamp(20.0, 28.0).toDouble();
    final cy = (b * 0.5).clamp(d / 2 + 1, b - 1).toDouble();
    return _Cutout(
      Rect.fromCenter(center: Offset(mid, cy), width: d, height: d),
    );
  }
}
