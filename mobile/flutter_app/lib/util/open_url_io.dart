import 'package:url_launcher/url_launcher.dart';

/// Native: open system browser (Android / iOS / desktop).
Future<bool> openExternalUrl(String url) async {
  if (url.isEmpty) return false;
  final uri = Uri.tryParse(url);
  if (uri == null) return false;
  try {
    if (await canLaunchUrl(uri)) {
      return launchUrl(uri, mode: LaunchMode.externalApplication);
    }
    // Some OEMs return false for canLaunchUrl even when launch works
    return launchUrl(uri, mode: LaunchMode.externalApplication);
  } catch (_) {
    try {
      return launchUrl(uri, mode: LaunchMode.platformDefault);
    } catch (_) {
      return false;
    }
  }
}
