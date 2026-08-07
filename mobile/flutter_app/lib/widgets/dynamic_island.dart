import 'dart:math' as math;
import 'dart:ui' show DisplayFeatureType;

import 'package:flutter/material.dart';

import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';

/// OEM-style camera island (小米超级岛 / OPPO 流体云 / vivo 原子岛 /
/// 华为 Live Window / 三星 Now Bar 思路的挖孔嵌入版).
///
/// Real product islands sit **around the front camera cutout** — black capsule
/// engulfs the hole with interactive content on the **left and right**, not a
/// floating bubble below the status bar.
class TaktonDynamicIsland extends StatelessWidget {
  const TaktonDynamicIsland({
    super.key,
    required this.controller,
    required this.dark,
    this.shellWidth,
  });

  final AppController controller;
  final bool dark;
  /// When non-null (desktop phone frame), use this width instead of full screen.
  final double? shellWidth;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final mq = MediaQuery.of(context);
    final size = mq.size;
    final w = shellWidth ?? size.width;
    final geom = _resolveCutout(mq, w);

    final live = c.islandLive || c.streaming || c.islandExpanded;
    final kind = c.islandKind;
    final accent = kind == 'stream'
        ? PixelColors.cyan
        : (kind == 'local' ? PixelColors.green : PixelColors.purple);

    // Idle: tight black ring around the camera (hardware-like).
    // Live: expand beside the hole (center DI style or corner→expand right).
    final hole = geom.hole;
    final idleW = geom.corner
        ? math.max(hole.width + 72, 128.0)
        : math.max(hole.width + 52, 118.0);
    final liveW = math.min(w * (geom.corner ? 0.62 : 0.78), geom.corner ? 220.0 : 268.0);
    final islandW = live ? liveW : idleW;
    final idleH = math.max(hole.height + 10, 32.0);
    final liveH = c.islandExpanded ? idleH + 14 : idleH + 4;
    final islandH = live ? liveH : idleH;

    // Vertically center on real camera center.
    final top = (geom.cameraCenter.dy - islandH / 2)
        .clamp(0.0, math.max(0.0, mq.viewPadding.top))
        .toDouble();
    // Center hole: island centered on camera.
    // Corner hole: island starts just left of camera, expands toward center.
    final double left;
    if (geom.corner) {
      left = (geom.cameraCenter.dx - hole.width / 2 - 10)
          .clamp(4.0, w - islandW - 4)
          .toDouble();
    } else {
      left = (geom.cameraCenter.dx - islandW / 2)
          .clamp(8.0, w - islandW - 8)
          .toDouble();
    }

    final leftLabel = _leftLabel(c, live);
    final rightLabel = _rightLabel(c, live);

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
          duration: const Duration(milliseconds: 280),
          curve: Curves.easeOutCubic,
          width: islandW,
          height: islandH,
          decoration: BoxDecoration(
            // Pure black so it visually merges with the physical camera hole
            // (same language as Apple DI / Xiaomi Super Island / OPPO Fluid Cloud).
            color: Colors.black,
            borderRadius: BorderRadius.circular(islandH / 2),
            border: Border.all(
              color: live
                  ? accent.withValues(alpha: 0.45)
                  : Colors.white.withValues(alpha: 0.06),
              width: 0.8,
            ),
            boxShadow: live
                ? [
                    BoxShadow(
                      color: accent.withValues(alpha: 0.18),
                      blurRadius: 12,
                      spreadRadius: 0,
                    ),
                  ]
                : null,
          ),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(islandH / 2),
            child: Stack(
              alignment: Alignment.center,
              children: [
                // Center hole: left | camera | right (Apple / center Super Island).
                // Corner hole: camera | content expanding right (Honor/many CN OEMs).
                Row(
                  children: [
                    if (!geom.corner)
                      Expanded(
                        child: Align(
                          alignment: Alignment.centerRight,
                          child: Padding(
                            padding: EdgeInsets.only(
                              left: 10,
                              right: math.max(4, hole.width * 0.12),
                            ),
                            child: live && leftLabel.isNotEmpty
                                ? Text(
                                    leftLabel,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    textAlign: TextAlign.right,
                                    style: PixelTheme.mono.copyWith(
                                      fontSize: 10.5,
                                      fontWeight: FontWeight.w700,
                                      color: Colors.white,
                                      height: 1.05,
                                    ),
                                  )
                                : const SizedBox.shrink(),
                          ),
                        ),
                      )
                    else
                      const SizedBox(width: 8),
                    // Camera window — physical punch-hole sits inside this disc.
                    SizedBox(
                      width: hole.width + 6,
                      height: hole.height + 6,
                      child: Center(
                        child: Container(
                          width: hole.width,
                          height: hole.height,
                          decoration: BoxDecoration(
                            color: const Color(0xFF050505),
                            shape: BoxShape.circle,
                            border: Border.all(
                              color: Colors.white.withValues(alpha: 0.08),
                              width: 0.6,
                            ),
                          ),
                          child: live
                              ? Center(
                                  child: Container(
                                    width: 5,
                                    height: 5,
                                    decoration: BoxDecoration(
                                      color: accent,
                                      shape: BoxShape.circle,
                                      boxShadow: [
                                        BoxShadow(
                                          color: accent.withValues(alpha: 0.7),
                                          blurRadius: 4,
                                        ),
                                      ],
                                    ),
                                  ),
                                )
                              : null,
                        ),
                      ),
                    ),
                    Expanded(
                      child: Align(
                        alignment: Alignment.centerLeft,
                        child: Padding(
                          padding: EdgeInsets.only(
                            left: math.max(6, hole.width * 0.12),
                            right: 10,
                          ),
                          child: live
                              ? Text(
                                  geom.corner
                                      ? (leftLabel.isNotEmpty
                                          ? (rightLabel.isNotEmpty
                                              ? '$leftLabel · $rightLabel'
                                              : leftLabel)
                                          : rightLabel)
                                      : rightLabel,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  textAlign: TextAlign.left,
                                  style: PixelTheme.mono.copyWith(
                                    fontSize: 10.5,
                                    fontWeight: FontWeight.w600,
                                    color: Colors.white.withValues(alpha: 0.9),
                                    height: 1.05,
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
                    left: 12,
                    right: 12,
                    bottom: 3,
                    child: Text(
                      c.streaming
                          ? '流式输出 · 点红键停止'
                          : (c.pcConnected ? '远端 Agent' : '本机模式'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        fontSize: 9,
                        color: Colors.white.withValues(alpha: 0.45),
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

  static String _leftLabel(AppController c, bool live) {
    if (!live) return '';
    if (c.streaming) return '生成';
    final t = c.islandText.trim();
    if (t.isEmpty) return c.pcConnected ? '已连' : '本机';
    // Prefer short head for left slot
    if (t.contains('·')) return t.split('·').first.trim();
    if (t.length <= 6) return t;
    return t.substring(0, 6);
  }

  static String _rightLabel(AppController c, bool live) {
    if (!live) return '';
    if (c.streaming) {
      final t = c.islandText.trim();
      if (t.isNotEmpty && t != '生成中' && t != '生成') {
        return t.length > 8 ? '${t.substring(0, 8)}…' : t;
      }
      return '…';
    }
    final t = c.islandText.trim();
    if (t.contains('·')) {
      final rest = t.split('·').skip(1).join('·').trim();
      if (rest.isNotEmpty) {
        return rest.length > 8 ? '${rest.substring(0, 8)}…' : rest;
      }
    }
    if (c.pcConnected) {
      final n = c.state['approvals_pending'] ?? c.approvals.length;
      return '待办 $n';
    }
    return '就绪';
  }

  /// Align island to Android [DisplayFeatureType.cutout] when available
  /// (punch-hole). Fallback: center-top hole inside status bar band — same
  /// place Xiaomi/OPPO/vivo/Huawei/Samsung draw their islands.
  static _CutoutGeom _resolveCutout(MediaQueryData mq, double shellW) {
    final features = mq.displayFeatures;
    final cutouts = features
        .where((f) => f.type == DisplayFeatureType.cutout)
        .map((f) => f.bounds)
        .where((r) => r.top < mq.viewPadding.top + 8)
        .toList()
      ..sort((a, b) => a.top.compareTo(b.top));

    if (cutouts.isNotEmpty) {
      final r = cutouts.first;
      // Prefer the top cutout; if multiple (rare dual-hole), pick the one
      // nearest horizontal center for island-style, else leftmost (corner hole).
      Rect chosen = r;
      if (cutouts.length > 1) {
        // Corner-hole OEMs (many mid-range): left-top. Island expands mostly right.
        chosen = cutouts.reduce(
          (a, b) => a.center.dx <= b.center.dx ? a : b,
        );
      } else {
        chosen = r;
      }
      // If hole is far left (< 22% width), treat as corner punch — island
      // still wraps it but biased so content expands toward center.
      final cx = chosen.center.dx;
      final isCorner = cx < shellW * 0.28;
      final holeSize = math.max(chosen.shortestSide, 22.0);
      return _CutoutGeom(
        cameraCenter: Offset(
          cx.clamp(12.0, shellW - 12),
          chosen.center.dy,
        ),
        hole: Size(holeSize, holeSize),
        corner: isCorner,
      );
    }

    // Synthetic cutout (no DisplayFeature): center of status-bar band —
    // matches center punch-hole island on Xiaomi / OPPO / vivo / Huawei demos.
    final topPad = mq.viewPadding.top;
    final band = topPad > 0 ? topPad : 36.0;
    final hole = 28.0;
    final cy = (band * 0.52).clamp(14.0, band > 2 ? band - 2 : 18.0);
    return _CutoutGeom(
      cameraCenter: Offset(shellW / 2, cy),
      hole: Size(hole, hole),
      corner: false,
    );
  }
}

class _CutoutGeom {
  const _CutoutGeom({
    required this.cameraCenter,
    required this.hole,
    required this.corner,
  });
  /// Physical cutout center (logical px).
  final Offset cameraCenter;
  final Size hole;
  final bool corner;
}
