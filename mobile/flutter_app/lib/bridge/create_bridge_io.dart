import 'ffi_bridge.dart';
import 'tevarn_bridge.dart';

/// Native: FFI → embedded Rust host.
/// Never silently points at a dead loopback port when the .so is missing.
Future<TevarnBridge> createTevarnBridge() async =>
    FfiTevarnBridge.createWithRetry(attempts: 2);
