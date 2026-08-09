import 'ffi_bridge.dart';
import 'tevarn_bridge.dart';

/// Native: FFI → embedded Rust host (falls back to HTTP if .so missing).
Future<TevarnBridge> createTevarnBridge() async => FfiTevarnBridge.create();
