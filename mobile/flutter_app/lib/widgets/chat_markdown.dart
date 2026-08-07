import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../theme/pixel_theme.dart';
import '../util/open_url.dart';

/// Rich markdown aligned with PC chat (tables, code, lists, headings).
class ChatMarkdown extends StatelessWidget {
  const ChatMarkdown({
    super.key,
    required this.data,
    required this.dark,
    required this.ink,
    required this.card2,
    this.selectable = false,
  });

  final String data;
  final bool dark;
  final Color ink;
  final Color card2;
  final bool selectable;

  @override
  Widget build(BuildContext context) {
    final ink2 = dark ? PixelColors.dInk2 : PixelColors.ink2;
    final border = dark
        ? Colors.white.withValues(alpha: 0.12)
        : PixelColors.ink.withValues(alpha: 0.12);

    final sheet = MarkdownStyleSheet(
      p: TextStyle(fontSize: 14, height: 1.55, color: ink),
      h1: TextStyle(
        fontSize: 18,
        fontWeight: FontWeight.w800,
        height: 1.35,
        color: ink,
      ),
      h2: TextStyle(
        fontSize: 16.5,
        fontWeight: FontWeight.w800,
        height: 1.35,
        color: ink,
      ),
      h3: TextStyle(
        fontSize: 15,
        fontWeight: FontWeight.w700,
        height: 1.4,
        color: ink,
      ),
      h4: TextStyle(
        fontSize: 14.5,
        fontWeight: FontWeight.w700,
        color: ink,
      ),
      a: const TextStyle(
        fontSize: 14,
        height: 1.55,
        color: PixelColors.amber,
        decoration: TextDecoration.underline,
      ),
      em: TextStyle(fontSize: 14, height: 1.55, color: ink, fontStyle: FontStyle.italic),
      strong: TextStyle(
        fontSize: 14,
        height: 1.55,
        color: ink,
        fontWeight: FontWeight.w700,
      ),
      listBullet: TextStyle(fontSize: 14, height: 1.55, color: ink),
      listIndent: 20,
      blockquote: TextStyle(fontSize: 13.5, height: 1.5, color: ink2),
      blockquoteDecoration: BoxDecoration(
        border: Border(
          left: BorderSide(color: PixelColors.purple.withValues(alpha: 0.55), width: 3),
        ),
        color: card2,
      ),
      blockquotePadding: const EdgeInsets.fromLTRB(10, 6, 8, 6),
      code: TextStyle(
        fontFamily: 'JetBrains Mono',
        fontSize: 12.5,
        backgroundColor: card2,
        color: ink,
      ),
      codeblockDecoration: BoxDecoration(
        color: dark ? const Color(0xFF0E1220) : const Color(0xFFF4F6FA),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: border),
      ),
      codeblockPadding: const EdgeInsets.all(10),
      horizontalRuleDecoration: BoxDecoration(
        border: Border(top: BorderSide(color: border, width: 1)),
      ),
      tableHead: TextStyle(
        fontSize: 12.5,
        fontWeight: FontWeight.w700,
        color: ink,
      ),
      tableBody: TextStyle(fontSize: 12.5, height: 1.4, color: ink),
      tableBorder: TableBorder.all(color: border, width: 1),
      tableHeadAlign: TextAlign.left,
      tableCellsPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      tableColumnWidth: const IntrinsicColumnWidth(),
      checkbox: TextStyle(fontSize: 14, color: ink),
    );

    return MarkdownBody(
      data: data,
      selectable: selectable,
      shrinkWrap: true,
      softLineBreak: true,
      styleSheet: sheet,
      onTapLink: (text, href, title) {
        final u = (href ?? text).trim();
        if (u.isEmpty) return;
        openExternalUrl(u);
      },
    );
  }
}
