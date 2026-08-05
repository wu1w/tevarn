import 'package:flutter/material.dart';

/// Pixel icons as 24×24 rect packs — matches Dioxus SVG `d` paths (crispEdges).
class PixelIcon extends StatelessWidget {
  const PixelIcon._(this.rects, {this.size = 22, this.color});
  final List<Rect> rects;
  final double size;
  final Color? color;

  factory PixelIcon.chat({double size = 22, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(3, 3, 18, 2),
          Rect.fromLTWH(3, 13, 18, 2),
          Rect.fromLTWH(3, 3, 2, 12),
          Rect.fromLTWH(19, 3, 2, 12),
          Rect.fromLTWH(6, 15, 3, 3),
          Rect.fromLTWH(5, 18, 2, 2),
          Rect.fromLTWH(7, 7, 2, 3),
          Rect.fromLTWH(11, 7, 2, 3),
          Rect.fromLTWH(15, 7, 2, 3),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.approve({double size = 22, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(4, 2, 14, 2),
          Rect.fromLTWH(4, 20, 14, 2),
          Rect.fromLTWH(4, 2, 2, 20),
          Rect.fromLTWH(16, 2, 2, 20),
          Rect.fromLTWH(8, 11, 2, 2),
          Rect.fromLTWH(10, 13, 2, 2),
          Rect.fromLTWH(12, 11, 2, 2),
          Rect.fromLTWH(14, 9, 2, 2),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.remote({double size = 22, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(9, 2, 6, 2),
          Rect.fromLTWH(9, 20, 6, 2),
          Rect.fromLTWH(7, 4, 2, 16),
          Rect.fromLTWH(15, 4, 2, 16),
          Rect.fromLTWH(10, 9, 4, 4),
          Rect.fromLTWH(3, 7, 2, 2),
          Rect.fromLTWH(1, 5, 2, 2),
          Rect.fromLTWH(19, 7, 2, 2),
          Rect.fromLTWH(21, 5, 2, 2),
          Rect.fromLTWH(3, 15, 2, 2),
          Rect.fromLTWH(1, 17, 2, 2),
          Rect.fromLTWH(19, 15, 2, 2),
          Rect.fromLTWH(21, 17, 2, 2),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.me({double size = 22, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(9, 3, 6, 6),
          Rect.fromLTWH(6, 11, 12, 3),
          Rect.fromLTWH(4, 14, 16, 7),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.menu({double size = 19, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(3, 5, 18, 3),
          Rect.fromLTWH(3, 11, 18, 3),
          Rect.fromLTWH(3, 17, 12, 3),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.plus({double size = 19, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(10, 4, 4, 6),
          Rect.fromLTWH(16, 10, 6, 4),
          Rect.fromLTWH(10, 14, 4, 6),
          Rect.fromLTWH(4, 10, 6, 4),
          Rect.fromLTWH(10, 10, 4, 4),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.attach({double size = 18, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(11, 5, 2, 6),
          Rect.fromLTWH(17, 11, 6, 2),
          Rect.fromLTWH(11, 13, 2, 6),
          Rect.fromLTWH(5, 11, 6, 2),
          Rect.fromLTWH(11, 11, 2, 2),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.send({double size = 16, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(3, 10, 11, 4),
          Rect.fromLTWH(13, 8, 3, 8),
          // chevron tip approximated
          Rect.fromLTWH(16, 10, 3, 2),
          Rect.fromLTWH(16, 12, 3, 2),
          Rect.fromLTWH(19, 11, 2, 2),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.stop({double size = 16, Color? color}) => PixelIcon._(
        const [Rect.fromLTWH(7, 7, 10, 10)],
        size: size,
        color: color,
      );

  factory PixelIcon.signal({double size = 15, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(0, 8, 3, 3),
          Rect.fromLTWH(4.5, 5, 3, 6),
          Rect.fromLTWH(9, 3, 3, 8),
          Rect.fromLTWH(13.5, 0, 3, 11),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.wifi({double size = 14, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(7, 8, 1, 3),
          Rect.fromLTWH(5, 6, 5, 2),
          Rect.fromLTWH(3, 4, 9, 2),
          Rect.fromLTWH(1, 2, 13, 2),
          Rect.fromLTWH(0, 0, 15, 1),
        ],
        size: size,
        color: color,
      );

  factory PixelIcon.battery({double size = 18, Color? color}) => PixelIcon._(
        const [
          Rect.fromLTWH(0.5, 0.5, 15, 10), // outer (stroke sim)
          Rect.fromLTWH(2, 2, 11, 7),
          Rect.fromLTWH(16, 3, 2, 5),
        ],
        size: size,
        color: color,
      );

  @override
  Widget build(BuildContext context) {
    final c = color ?? IconTheme.of(context).color ?? const Color(0xFF1D2330);
    return SizedBox(
      width: size,
      height: size,
      child: CustomPaint(painter: _RectsPainter(rects, c)),
    );
  }
}

class _RectsPainter extends CustomPainter {
  _RectsPainter(this.rects, this.color);
  final List<Rect> rects;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    final scale = size.width / 24.0;
    canvas.scale(scale, scale);
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.fill
      ..isAntiAlias = false;
    for (final r in rects) {
      canvas.drawRect(r, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _RectsPainter old) =>
      old.color != color || old.rects != rects;
}
