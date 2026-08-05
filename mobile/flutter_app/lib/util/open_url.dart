import 'open_url_stub.dart'
    if (dart.library.html) 'open_url_web.dart' as impl;

/// Open an external URL (browser tab on web; returns false if unsupported).
Future<bool> openExternalUrl(String url) => impl.openExternalUrl(url);
