import 'http_bridge.dart';
import 'tevarn_bridge.dart';

/// Web / preview: same-origin HTTP to the Rust host.
Future<TevarnBridge> createTevarnBridge() async =>
    HttpTevarnBridge(kind: 'http-web');
