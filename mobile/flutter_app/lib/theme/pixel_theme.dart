import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Pixel Console tokens — matches PC/mobile pixel-console.css
class PixelColors {
  static const bg = Color(0xFFF4F6F8);
  static const card = Color(0xFFFBFCFF);
  static const card2 = Color(0xFFEEF1F6);
  static const elev = Color(0xFFFFFFFF);
  static const ink = Color(0xFF1D2330);
  static const ink2 = Color(0xFF4A5261);
  static const ink3 = Color(0xFF7A8291);
  static const purple = Color(0xFF6D5DF6);
  static const cyan = Color(0xFF00A8C0);
  static const pink = Color(0xFFF6489B);
  static const green = Color(0xFF16A34A);
  static const amber = Color(0xFFD97706);
  static const red = Color(0xFFDC4446);
  static const line = Color(0x171D2330);
  static const line2 = Color(0x291D2330);

  // dark
  static const dBg = Color(0xFF0C0F1A);
  static const dCard = Color(0x12151A2E);
  static const dInk = Color(0xFFE6E9F2);
  static const dInk2 = Color(0xFFA3ABC2);
  static const dInk3 = Color(0xFF6D7590);
  static const dPurple = Color(0xFF8B7CFF);
}

class PixelTheme {
  static ThemeData light() {
    final base = ThemeData(
      useMaterial3: true,
      brightness: Brightness.light,
      scaffoldBackgroundColor: PixelColors.bg,
      colorScheme: ColorScheme.light(
        primary: PixelColors.purple,
        secondary: PixelColors.cyan,
        error: PixelColors.red,
        surface: PixelColors.card,
        onPrimary: Colors.white,
        onSurface: PixelColors.ink,
      ),
    );
    return base.copyWith(
      textTheme: GoogleFonts.interTextTheme(base.textTheme).apply(
        bodyColor: PixelColors.ink,
        displayColor: PixelColors.ink,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: PixelColors.bg,
        foregroundColor: PixelColors.ink,
        elevation: 0,
        centerTitle: false,
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: PixelColors.purple,
          foregroundColor: Colors.white,
          elevation: 0,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(4),
            side: const BorderSide(color: PixelColors.ink, width: 1.2),
          ),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: PixelColors.ink.withValues(alpha: 0.045),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: PixelColors.ink.withValues(alpha: 0.16)),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: BorderSide(color: PixelColors.ink.withValues(alpha: 0.12)),
        ),
        focusedBorder: const OutlineInputBorder(
          borderRadius: BorderRadius.all(Radius.circular(6)),
          borderSide: BorderSide(color: PixelColors.purple, width: 1.4),
        ),
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        hintStyle: const TextStyle(color: PixelColors.ink3, fontSize: 14),
      ),
    );
  }

  static ThemeData dark() {
    final base = ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: PixelColors.dBg,
      colorScheme: ColorScheme.dark(
        primary: PixelColors.dPurple,
        secondary: PixelColors.cyan,
        error: PixelColors.red,
        surface: const Color(0xFF151A2E),
        onPrimary: Colors.white,
        onSurface: PixelColors.dInk,
      ),
    );
    return base.copyWith(
      textTheme: GoogleFonts.interTextTheme(base.textTheme).apply(
        bodyColor: PixelColors.dInk,
        displayColor: PixelColors.dInk,
      ),
    );
  }

  static TextStyle get pixel => GoogleFonts.silkscreen(
        fontSize: 11,
        color: PixelColors.purple,
        letterSpacing: 0.4,
      );

  static TextStyle get mono => GoogleFonts.jetBrainsMono(
        fontSize: 12,
        color: PixelColors.ink2,
      );

  /// Hard pixel shadow --hs: 3px 3px 0
  static List<BoxShadow> get hardShadow => const [
        BoxShadow(color: Color(0x241D2330), offset: Offset(3, 3), blurRadius: 0),
      ];

  /// Hard pixel shadow --hs2: 2px 2px 0
  static List<BoxShadow> get hardShadowSm => const [
        BoxShadow(color: Color(0x241D2330), offset: Offset(2, 2), blurRadius: 0),
      ];
}
