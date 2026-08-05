import 'ffi_bridge.dart';
import 'takton_bridge.dart';

/// Native: FFI → embedded Rust host (falls back to HTTP if .so missing).
Future<TaktonBridge> createTaktonBridge() async => FfiTaktonBridge.create();
