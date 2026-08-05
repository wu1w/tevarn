import 'package:flutter/material.dart';

import '../theme/pixel_theme.dart';

/// Shared pixel-console primitives (hard-shadow cards, toggles, section labels).
class PxCard extends StatelessWidget {
  const PxCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(14),
    this.dark = false,
    this.radius = 6,
  });
  final Widget child;
  final EdgeInsets padding;
  final bool dark;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: padding,
      decoration: BoxDecoration(
        color: dark ? Colors.white.withValues(alpha: 0.05) : PixelColors.elev,
        borderRadius: BorderRadius.circular(radius),
        border: Border.all(
          color: dark
              ? Colors.white.withValues(alpha: 0.1)
              : PixelColors.ink.withValues(alpha: 0.12),
        ),
        boxShadow: PixelTheme.hardShadow,
      ),
      child: child,
    );
  }
}

class PxSect extends StatelessWidget {
  const PxSect(this.label, {super.key});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8, top: 4),
      child: Text(label, style: PixelTheme.pixel.copyWith(fontSize: 9.5)),
    );
  }
}

class PxToggle extends StatelessWidget {
  const PxToggle({
    super.key,
    required this.value,
    required this.onChanged,
  });
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => onChanged(!value),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 160),
        width: 44,
        height: 26,
        padding: const EdgeInsets.all(3),
        decoration: BoxDecoration(
          color: value
              ? PixelColors.purple
              : PixelColors.ink.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(4),
          border: Border.all(
            color: value
                ? PixelColors.ink.withValues(alpha: 0.2)
                : PixelColors.ink.withValues(alpha: 0.16),
            width: 1.2,
          ),
          boxShadow: const [
            BoxShadow(
              color: Color(0x241D2330),
              offset: Offset(1, 1),
            ),
          ],
        ),
        alignment: value ? Alignment.centerRight : Alignment.centerLeft,
        child: Container(
          width: 18,
          height: 18,
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(2),
            boxShadow: const [
              BoxShadow(
                color: Color(0x241D2330),
                offset: Offset(1, 1),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class PxRow extends StatelessWidget {
  const PxRow({
    super.key,
    required this.title,
    this.sub,
    this.trailing,
    this.onTap,
    this.borderTop = true,
  });
  final String title;
  final String? sub;
  final Widget? trailing;
  final VoidCallback? onTap;
  final bool borderTop;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 12),
          decoration: BoxDecoration(
            border: borderTop
                ? Border(
                    top: BorderSide(
                      color: PixelColors.ink.withValues(alpha: 0.08),
                    ),
                  )
                : null,
          ),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        fontSize: 14.5,
                        fontWeight: FontWeight.w700,
                        color: PixelColors.ink,
                      ),
                    ),
                    if (sub != null) ...[
                      const SizedBox(height: 2),
                      Text(
                        sub!,
                        style: const TextStyle(
                          fontSize: 11.5,
                          color: PixelColors.ink3,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (trailing != null) trailing!,
            ],
          ),
        ),
      ),
    );
  }
}

class PxPrimaryBtn extends StatelessWidget {
  const PxPrimaryBtn({
    super.key,
    required this.label,
    required this.onTap,
    this.block = true,
    this.cyan = false,
  });
  final String label;
  final VoidCallback onTap;
  final bool block;
  final bool cyan;

  @override
  Widget build(BuildContext context) {
    final bg = cyan ? PixelColors.cyan : PixelColors.purple;
    final child = Material(
      color: bg,
      borderRadius: BorderRadius.circular(4),
      child: InkWell(
        borderRadius: BorderRadius.circular(4),
        onTap: onTap,
        child: Container(
          width: block ? double.infinity : null,
          height: 44,
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 16),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(4),
            border: Border.all(color: PixelColors.ink, width: 1.2),
            boxShadow: PixelTheme.hardShadowSm,
          ),
          child: Text(
            label,
            style: const TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.w700,
              fontSize: 14,
            ),
          ),
        ),
      ),
    );
    return child;
  }
}

class PxGhostBtn extends StatelessWidget {
  const PxGhostBtn({
    super.key,
    required this.label,
    required this.onTap,
    this.block = true,
    this.danger = false,
  });
  final String label;
  final VoidCallback onTap;
  final bool block;
  final bool danger;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: PixelColors.card,
      borderRadius: BorderRadius.circular(4),
      child: InkWell(
        borderRadius: BorderRadius.circular(4),
        onTap: onTap,
        child: Container(
          width: block ? double.infinity : null,
          height: 44,
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 16),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(4),
            border: Border.all(
              color: danger
                  ? PixelColors.red.withValues(alpha: 0.45)
                  : PixelColors.ink.withValues(alpha: 0.16),
              width: 1.2,
            ),
            boxShadow: PixelTheme.hardShadowSm,
          ),
          child: Text(
            label,
            style: TextStyle(
              color: danger ? PixelColors.red : PixelColors.ink,
              fontWeight: FontWeight.w700,
              fontSize: 14,
            ),
          ),
        ),
      ),
    );
  }
}

class PxField extends StatelessWidget {
  const PxField({
    super.key,
    required this.label,
    required this.controller,
    this.hint,
    this.obscure = false,
    this.onChanged,
  });
  final String label;
  final TextEditingController controller;
  final String? hint;
  final bool obscure;
  final ValueChanged<String>? onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(
              fontSize: 11.5,
              fontWeight: FontWeight.w600,
              color: PixelColors.ink3,
            ),
          ),
          const SizedBox(height: 5),
          TextField(
            controller: controller,
            obscureText: obscure,
            onChanged: onChanged,
            style: PixelTheme.mono.copyWith(
              fontSize: 13.5,
              color: PixelColors.ink,
            ),
            decoration: InputDecoration(
              hintText: hint,
              isDense: true,
              contentPadding:
                  const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
              filled: true,
              fillColor: PixelColors.ink.withValues(alpha: 0.045),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(4),
                borderSide: BorderSide(
                  color: PixelColors.ink.withValues(alpha: 0.16),
                ),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(4),
                borderSide: BorderSide(
                  color: PixelColors.ink.withValues(alpha: 0.12),
                ),
              ),
              focusedBorder: const OutlineInputBorder(
                borderRadius: BorderRadius.all(Radius.circular(4)),
                borderSide: BorderSide(color: PixelColors.purple, width: 1.4),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class PxAvatarT extends StatelessWidget {
  const PxAvatarT({super.key, this.size = 44});
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: PixelColors.purple,
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: PixelColors.ink, width: 1.2),
        boxShadow: PixelTheme.hardShadowSm,
      ),
      child: Text(
        'T',
        style: PixelTheme.pixel.copyWith(
          fontSize: size * 0.36,
          color: Colors.white,
          height: 1,
        ),
      ),
    );
  }
}
