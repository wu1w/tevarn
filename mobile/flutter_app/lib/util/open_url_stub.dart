/// Non-web: no reliable browser API without url_launcher; caller uses clipboard.
Future<bool> openExternalUrl(String url) async => false;
