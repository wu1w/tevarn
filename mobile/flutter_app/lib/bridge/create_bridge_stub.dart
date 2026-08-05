import 'http_bridge.dart';
import 'takton_bridge.dart';

Future<TaktonBridge> createTaktonBridge() async => HttpTaktonBridge();
