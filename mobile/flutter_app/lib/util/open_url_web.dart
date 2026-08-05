// ignore: avoid_web_libraries_in_flutter, deprecated_member_use
import 'dart:html' as html;

Future<bool> openExternalUrl(String url) async {
  if (url.isEmpty) return false;
  try {
    html.window.open(url, '_blank');
    return true;
  } catch (_) {
    try {
      final a = html.AnchorElement(href: url)
        ..target = '_blank'
        ..rel = 'noopener noreferrer';
      html.document.body?.append(a);
      a.click();
      a.remove();
      return true;
    } catch (_) {
      return false;
    }
  }
}
