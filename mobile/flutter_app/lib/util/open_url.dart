import 'open_url_stub.dart'
    if (dart.library.html) 'open_url_web.dart'
    if (dart.library.io) 'open_url_io.dart' as impl;

/// Open an external URL in the system browser when possible.
Future<bool> openExternalUrl(String url) => impl.openExternalUrl(url);
