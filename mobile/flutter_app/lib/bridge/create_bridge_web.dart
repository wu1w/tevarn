import 'http_bridge.dart';
import 'takton_bridge.dart';

/// Web / preview: same-origin HTTP to the Rust host.
Future<TaktonBridge> createTaktonBridge() async =>
    HttpTaktonBridge(kind: 'http-web');
