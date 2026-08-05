import 'dart:convert';
import 'dart:ffi';
import 'dart:io' show Platform;

import 'package:ffi/ffi.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'http_bridge.dart';
import 'takton_bridge.dart';

typedef _StartHostNative = Pointer<Utf8> Function(Int32);
typedef _StartHostDart = Pointer<Utf8> Function(int);
typedef _StartHost2Native = Pointer<Utf8> Function(Int32, Pointer<Utf8>);
typedef _StartHost2Dart = Pointer<Utf8> Function(int, Pointer<Utf8>);
typedef _CallNative = Pointer<Utf8> Function(Pointer<Utf8>, Pointer<Utf8>);
typedef _CallDart = Pointer<Utf8> Function(Pointer<Utf8>, Pointer<Utf8>);
typedef _FreeNative = Void Function(Pointer<Utf8>);
typedef _FreeDart = void Function(Pointer<Utf8>);

/// Native FFI bridge → `libtakton_mobile_ffi`.
/// Falls back to [HttpTaktonBridge] with **explicit** kind when the .so is missing.
class FfiTaktonBridge extends TaktonBridge {
  FfiTaktonBridge._(this._http, this._call, this._free, this._kind);

  final HttpTaktonBridge _http;
  final _CallDart? _call;
  final _FreeDart? _free;
  final String _kind;

  /// Last create note (for island / debug).
  static String lastCreateNote = '';

  static Future<TaktonBridge> create({int preferredPort = 8765}) async {
    if (kIsWeb) {
      lastCreateNote = 'web → HttpTaktonBridge';
      return HttpTaktonBridge(kind: 'http-web');
    }
    const envBase = String.fromEnvironment(
      'TAKTON_HOST',
      defaultValue: 'http://127.0.0.1:8765',
    );
    try {
      final lib = _openLib();
      final free = lib.lookupFunction<_FreeNative, _FreeDart>('takton_free');

      // Prefer application support dir so Android can write (dirs crate is empty there).
      String? dataDir;
      try {
        final dir = await getApplicationSupportDirectory();
        dataDir = dir.path;
      } catch (e) {
        debugPrint('path_provider failed: $e');
      }

      Pointer<Utf8> p;
      if (dataDir != null && dataDir.isNotEmpty) {
        try {
          final start2 = lib
              .lookupFunction<_StartHost2Native, _StartHost2Dart>('takton_start_host2');
          final dPtr = dataDir.toNativeUtf8();
          try {
            p = start2(preferredPort, dPtr);
          } finally {
            malloc.free(dPtr);
          }
        } catch (_) {
          // Older .so without takton_start_host2
          final start =
              lib.lookupFunction<_StartHostNative, _StartHostDart>('takton_start_host');
          p = start(preferredPort);
        }
      } else {
        final start =
            lib.lookupFunction<_StartHostNative, _StartHostDart>('takton_start_host');
        p = start(preferredPort);
      }

      final raw = p.toDartString();
      free(p);
      final m = decodeMap(raw);
      if (m['ok'] != true) {
        lastCreateNote =
            'FFI host start failed: ${m['error'] ?? raw} → HTTP fallback $envBase';
        debugPrint(lastCreateNote);
        return HttpTaktonBridge(base: envBase, kind: 'http-fallback');
      }
      final base = m['base']?.toString() ?? 'http://127.0.0.1:$preferredPort';
      lastCreateNote = 'FFI host ok · $base · data=${m['data_dir'] ?? dataDir}';
      debugPrint(lastCreateNote);

      final call = lib.lookupFunction<_CallNative, _CallDart>('takton_call');
      return FfiTaktonBridge._(
        HttpTaktonBridge(base: base, kind: 'ffi'),
        call,
        free,
        'ffi',
      );
    } catch (e, st) {
      lastCreateNote = 'FFI load failed: $e → HTTP fallback $envBase';
      debugPrint(lastCreateNote);
      debugPrint('$st');
      return HttpTaktonBridge(base: envBase, kind: 'http-fallback');
    }
  }

  static DynamicLibrary _openLib() {
    if (Platform.isAndroid) return DynamicLibrary.open('libtakton_mobile_ffi.so');
    if (Platform.isIOS) return DynamicLibrary.process();
    if (Platform.isLinux) return DynamicLibrary.open('libtakton_mobile_ffi.so');
    if (Platform.isMacOS) return DynamicLibrary.open('libtakton_mobile_ffi.dylib');
    throw UnsupportedError('platform');
  }

  @override
  String get hostBase => _http.hostBase;

  @override
  String get bridgeKind => _kind;

  @override
  Future<Map<String, dynamic>> call(String method,
      [Map<String, dynamic>? args]) async {
    if (_call == null || _free == null) return _http.call(method, args);
    final mPtr = method.toNativeUtf8();
    final aPtr = jsonEncode(args ?? {}).toNativeUtf8();
    try {
      final out = _call(mPtr, aPtr);
      final s = out.toDartString();
      _free(out);
      return decodeMap(s);
    } finally {
      malloc.free(mPtr);
      malloc.free(aPtr);
    }
  }

  @override
  Future<Map<String, dynamic>> uploadFile({
    required String name,
    required List<int> bytes,
    String? contentType,
  }) =>
      _http.uploadFile(name: name, bytes: bytes, contentType: contentType);

  @override
  Stream<String> streamLocalChat(String content) =>
      _http.streamLocalChat(content);

  @override
  Stream<String> streamRemoteChat(
    String sessionId,
    String content, {
    List<Map<String, dynamic>>? attachments,
  }) =>
      _http.streamRemoteChat(sessionId, content, attachments: attachments);

  @override
  void dispose() => _http.dispose();
}
