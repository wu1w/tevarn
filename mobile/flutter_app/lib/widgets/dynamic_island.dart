import 'dart:math' as math;
import 'dart:ui' show DisplayFeatureType;

import 'package:flutter/material.dart';

import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';

/// Camera-cutout Dynamic Island.
///
/// Width/height are derived from the **real Android cutout bounds**
/// (`MediaQuery.displayFeatures` → [DisplayFeatureType.cutout]), so a
/// center punch-hole drives a tight black capsule that grows with the hole —
/// same idea as Xiaomi Super Island / OPPO Fluid Cloud / vivo Atomic Island.
class TaktonDynamicIsland extends StatelessWidget {
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
  Widget build(BuildContext context) {
    final c = controller;
    final mq = MediaQuery.of(context);
    final shellW = shellWidth ?? mq.size.width;
    final geom = _CutoutGeom.resolve(mq, shellW);

    final live = c.islandLive || c.streaming || c.islandExpanded;
    final kind = c.islandKind;
    final accent = kind == 'stream'
        ? PixelColors.cyan
        : (kind == 'local' ? PixelColors.green : PixelColors.purple);

    // ---- Size strictly from cutout ----
    // cutoutW/H = physical camera region in logical px (device-reported).
    final cutoutW = geom.bounds.width;
    final cutoutH = geom.bounds.height;
    // Thin black rim around the hole (scales with camera size).
    final rimX = (cutoutW * 0.22).clamp(3.0, 10.0);
    final rimY = (cutoutH * 0.18).clamp(2.0, 8.0);

    // Idle: only wrap the camera (+ rim). NO fixed min 118/128.
    final idleW = cutoutW + rimX * 2;
    final idleH = cutoutH + rimY * 2;

    // Live: expand left/right wings proportional to camera, not full screen.
    // wing ≈ one camera-width of text each side (center); corner expands one side.
    final wing = (cutoutW * 1.35).clamp(28.0, 56.0);
    final liveWRaw = geom.corner
        ? cutoutW + rimX * 2 + wing * 2.2 // content mostly to the right
        : cutoutW + rimX * 2 + wing * 2;
    // Hard cap still relative to cutout & screen — never 78% screen.
    final liveW = math
        .min(liveWRaw, math.min(shellW * 0.52, cutoutW * 7.5))
        .toDouble();
    final liveH = c.islandExpanded
        ? idleH + (cutoutH * 0.45).clamp(8.0, 16.0)
        : idleH + (cutoutH * 0.12).clamp(1.0, 4.0);

    final islandW = live ? liveW : idleW;
    final islandH = live ? liveH : idleH;

    // Vertically center on cutout center; stay inside status-bar band when possible.
    final bandBottom = math.max(mq.viewPadding.top, geom.bounds.bottom + rimY);
    final top = (geom.bounds.center.dy - islandH / 2)
        .clamp(0.0, math.max(0.0, bandBottom - islandH * 0.2))
        .toDouble();

    // Horizontally: always track camera center for 中置; corner keeps hole left.
    final double left;
    if (geom.corner) {
      left = (geom.bounds.left - rimX).clamp(2.0, shellW - islandW - 2).toDouble();
    } else {
      left = (geom.bounds.center.dx - islandW / 2)
          .clamp(2.0, shellW - islandW - 2)
          .toDouble();
    }

    final leftLabel = _leftLabel(c, live);
    final rightLabel = _rightLabel(c, live);

    // Camera disc = exact cutout size (no +6 inflation that fights real hole).
    final discW = cutoutW;
    final discH = cutoutH;

    return Positioned(
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
          if (live && c.statusCards.isNotEmpty) {
            c.toggleIslandExpanded();
            return;
          }
          c.pulseIsland(
            text: c.pcConnected
                ? '已连 · ${c.state['approvals_pending'] ?? c.approvals.length}'
                : '本机',
            kind: c.pcConnected ? 'conn' : 'local',
          );
        },
        onLongPress: () {
          if (c.pcConnected) {
            c.setTab(AppTab.approve);
          } else {
            c.setTab(AppTab.me);
          }
        },
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 260),
          curve: Curves.easeOutCubic,
          width: islandW,
          height: islandH,
          decoration: BoxDecoration(
            color: Colors.black,
            borderRadius: BorderRadius.circular(islandH / 2),
            border: Border.all(
              color: live
                  ? accent.withValues(alpha: 0.4)
                  : Colors.white.withValues(alpha: 0.05),
              width: 0.7,
            ),
            boxShadow: live
                ? [
                    BoxShadow(
                      color: accent.withValues(alpha: 0.16),
                      blurRadius: 10,
                    ),
                  ]
                : null,
          ),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(islandH / 2),
            child: Stack(
              alignment: Alignment.center,
              children: [
                Row(
                  children: [
                    if (!geom.corner)
                      Expanded(
                        child: Align(
                          alignment: Alignment.centerRight,
                          child: Padding(
                            padding: EdgeInsets.only(
                              left: rimX * 0.6,
                              right: rimX * 0.35,
                            ),
                            child: live && leftLabel.isNotEmpty
                                ? Text(
                                    leftLabel,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    textAlign: TextAlign.right,
                                    style: PixelTheme.mono.copyWith(
                                      fontSize: _fontFor(cutoutH),
                                      fontWeight: FontWeight.w700,
                                      color: Colors.white,
                                      height: 1.0,
                                    ),
                                  )
                                : const SizedBox.shrink(),
                          ),
                        ),
                      )
                    else
                      SizedBox(width: rimX),
                    // Exact cutout window — physical 中置摄像头 sits here.
                    SizedBox(
                      width: discW,
                      height: discH,
                      child: DecoratedBox(
                        decoration: BoxDecoration(
                          color: const Color(0xFF050505),
                          borderRadius: BorderRadius.circular(
                            math.min(discW, discH) / 2,
                          ),
                          border: Border.all(
                            color: Colors.white.withValues(alpha: 0.06),
                            width: 0.5,
                          ),
                        ),
                        child: live
                            ? Center(
                                child: Container(
                                  width: (discW * 0.18).clamp(3.0, 6.0),
                                  height: (discH * 0.18).clamp(3.0, 6.0),
                                  decoration: BoxDecoration(
                                    color: accent,
                                    shape: BoxShape.circle,
                                    boxShadow: [
                                      BoxShadow(
                                        color: accent.withValues(alpha: 0.65),
                                        blurRadius: 3,
                                      ),
                                    ],
                                  ),
                                ),
                              )
                            : null,
                      ),
                    ),
                    Expanded(
                      child: Align(
                        alignment: Alignment.centerLeft,
                        child: Padding(
                          padding: EdgeInsets.only(
                            left: rimX * 0.35,
                            right: rimX * 0.6,
                          ),
                          child: live
                              ? Text(
                                  geom.corner
                                      ? _cornerLabel(leftLabel, rightLabel)
                                      : rightLabel,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  textAlign: TextAlign.left,
                                  style: PixelTheme.mono.copyWith(
                                    fontSize: _fontFor(cutoutH),
                                    fontWeight: FontWeight.w600,
                                    color: Colors.white.withValues(alpha: 0.9),
                                    height: 1.0,
                                  ),
                                )
                              : const SizedBox.shrink(),
                        ),
                      ),
                    ),
                  ],
                ),
                if (c.islandExpanded && live)
                  Positioned(
                    left: 8,
                    right: 8,
                    bottom: 2,
                    child: Text(
                      c.streaming
                          ? '流式 · 点红键停'
                          : (c.pcConnected ? '远端' : '本机'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        fontSize: 8.5,
                        color: Colors.white.withValues(alpha: 0.4),
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  static double _fontFor(double cutoutH) =>
      (cutoutH * 0.32).clamp(9.0, 11.5);

  static String _cornerLabel(String left, String right) {
    if (left.isEmpty) return right;
    if (right.isEmpty) return left;
    return '$left · $right';
  }

  static String _leftLabel(AppController c, bool live) {
    if (!live) return '';
    if (c.streaming) return '生成';
    final t = c.islandText.trim();
    if (t.isEmpty) return c.pcConnected ? '已连' : '本机';
    if (t.contains('·')) return t.split('·').first.trim();
    if (t.length <= 5) return t;
    return t.substring(0, 5);
  }

  static String _rightLabel(AppController c, bool live) {
    if (!live) return '';
    if (c.streaming) {
      final t = c.islandText.trim();
      if (t.isNotEmpty && t != '生成中' && t != '生成') {
        return t.length > 7 ? '${t.substring(0, 7)}…' : t;
      }
      return '…';
    }
    final t = c.islandText.trim();
    if (t.contains('·')) {
      final rest = t.split('·').skip(1).join('·').trim();
      if (rest.isNotEmpty) {
        return rest.length > 7 ? '${rest.substring(0, 7)}…' : rest;
      }
    }
    if (c.pcConnected) {
      final n = c.state['approvals_pending'] ?? c.approvals.length;
      return '待办 $n';
    }
    return '就绪';
  }
}

class _CutoutGeom {
  const _CutoutGeom({
    required this.bounds,
    required this.corner,
    required this.fromSystem,
  });

  /// Exact cutout bounding rect in logical pixels (from Android when possible).
  final Rect bounds;
  final bool corner;
  final bool fromSystem;

  /// Prefer **center** punch-hole when multiple; use raw bounds width/height
  /// (no force-square, no min 22) so island width tracks the camera.
  static _CutoutGeom resolve(MediaQueryData mq, double shellW) {
    final topBand = mq.viewPadding.top + 12;
    final cutouts = mq.displayFeatures
        .where((f) => f.type == DisplayFeatureType.cutout)
        .map((f) => f.bounds)
        .where((r) => r.width > 0 && r.height > 0 && r.top < topBand)
        .toList();

    if (cutouts.isNotEmpty) {
      // 中置优先：取水平中心最靠近屏幕中线的挖孔。
      final mid = shellW / 2;
      cutouts.sort((a, b) {
        final da = (a.center.dx - mid).abs();
        final db = (b.center.dx - mid).abs();
        return da.compareTo(db);
      });
      final chosen = cutouts.first;
      final isCorner = chosen.center.dx < shellW * 0.28 ||
          chosen.center.dx > shellW * 0.72;

      // Use system rect as-is. Only shrink absurd full-width "notch" bands
      // (some OEMs report the whole status bar as one cutout).
      var bounds = chosen;
      if (bounds.width > shellW * 0.45) {
        // Collapse to a center hole estimated from height (true camera size).
        final d = bounds.height.clamp(18.0, 40.0);
        bounds = Rect.fromCenter(
          center: Offset(isCorner ? chosen.center.dx : mid, chosen.center.dy),
          width: d,
          height: d,
        );
      } else if ((bounds.width - bounds.height).abs() > bounds.shortestSide * 0.55) {
        // Pill-shaped cutout (true Dynamic Island hardware): keep full width.
        // height stays; width is the island base.
      } else {
        // Near-circular punch: keep reported size (may be slightly elliptical).
        bounds = chosen;
      }

      return _CutoutGeom(
        bounds: bounds,
        corner: isCorner && bounds.center.dx < shellW * 0.4,
        fromSystem: true,
      );
    }

    // Fallback when Flutter gets no DisplayFeature (rare after edge-to-edge):
    // diameter tracks status-bar band — still dynamic, not a fixed 118-wide bar.
    final band = mq.viewPadding.top > 0 ? mq.viewPadding.top : 32.0;
    // Typical center hole ≈ 55–70% of status-bar height on modern Androids.
    final d = (band * 0.62).clamp(22.0, 34.0);
    final cy = (band * 0.50).clamp(d / 2 + 1, band - 1);
    return _CutoutGeom(
      bounds: Rect.fromCenter(
        center: Offset(shellW / 2, cy),
        width: d,
        height: d,
      ),
      corner: false,
      fromSystem: false,
    );
  }
}
