import 'http_bridge.dart';
import 'tevarn_bridge.dart';

Future<TevarnBridge> createTevarnBridge() async => HttpTevarnBridge();
