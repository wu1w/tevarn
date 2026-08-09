import 'dart:convert';
import 'dart:ffi';
import 'dart:io' show Platform;

import 'package:ffi/ffi.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import 'http_bridge.dart';
import 'tevarn_bridge.dart';

typedef _StartHostNative = Pointer<Utf8> Function(Int32);
typedef _StartHostDart = Pointer<Utf8> Function(int);
typedef _StartHost2Native = Pointer<Utf8> Function(Int32, Pointer<Utf8>);
typedef _StartHost2Dart = Pointer<Utf8> Function(int, Pointer<Utf8>);
typedef _CallNative = Pointer<Utf8> Function(Pointer<Utf8>, Pointer<Utf8>);
typedef _CallDart = Pointer<Utf8> Function(Pointer<Utf8>, Pointer<Utf8>);
typedef _FreeNative = Void Function(Pointer<Utf8>);
typedef _FreeDart = void Function(Pointer<Utf8>);

/// Native FFI bridge → `libtevarn_mobile_ffi` (also accepts legacy `libtakton_mobile_ffi`).
///
/// Robustness rules:
/// - Never silently point at a dead `http://127.0.0.1:8765` when the host is not up.
/// - Accept both Tevarn and legacy Takton library / symbol names (0.4.0 packaging drift).
/// - After start, probe `/api/mobile/health` so we fail closed if bind/start lied.
class FfiTevarnBridge extends TevarnBridge {
  FfiTevarnBridge._(this._http, this._call, this._free, this._kind);

  final HttpTevarnBridge _http;
  final _CallDart? _call;
  final _FreeDart? _free;
  final String _kind;

  /// Last create note (for island / debug / 我的页).
  static String lastCreateNote = '';

  /// Human-readable last failure (Chinese), empty when healthy.
  static String lastError = '';

  static Future<TevarnBridge> create({int preferredPort = 8765}) async {
    lastError = '';
    if (kIsWeb) {
      lastCreateNote = 'web → HttpTevarnBridge';
      return HttpTevarnBridge(kind: 'http-web');
    }

    DynamicLibrary? lib;
    String? libName;
    try {
      final opened = _openLib();
      lib = opened.$1;
      libName = opened.$2;
    } catch (e, st) {
      lastCreateNote = 'FFI load failed: $e';
      lastError = '本机引擎库未加载 · $e';
      debugPrint(lastCreateNote);
      debugPrint('$st');
      return EngineDeadBridge(reason: lastError);
    }

    try {
      final free = _lookupFree(lib);
      if (free == null) {
        lastCreateNote = 'FFI symbols missing (free) · lib=$libName';
        lastError = '本机引擎符号不匹配 · 请重装 $libName 对应版本';
        debugPrint(lastCreateNote);
        return EngineDeadBridge(reason: lastError);
      }

      // Prefer application support dir so Android can write (dirs crate is empty there).
      String? dataDir;
      try {
        final dir = await getApplicationSupportDirectory();
        dataDir = dir.path;
      } catch (e) {
        debugPrint('path_provider failed: $e');
      }

      Pointer<Utf8>? p;
      Object? startErr;
      try {
        if (dataDir != null && dataDir.isNotEmpty) {
          final start2 = _lookupStart2(lib);
          if (start2 != null) {
            final dPtr = dataDir.toNativeUtf8();
            try {
              p = start2(preferredPort, dPtr);
            } finally {
              malloc.free(dPtr);
            }
          } else {
            final start = _lookupStart(lib);
            if (start == null) {
              throw StateError('start_host symbol missing in $libName');
            }
            p = start(preferredPort);
          }
        } else {
          final start = _lookupStart(lib);
          if (start == null) {
            throw StateError('start_host symbol missing in $libName');
          }
          p = start(preferredPort);
        }
      } catch (e) {
        startErr = e;
      }

      if (p == null) {
        lastCreateNote = 'FFI host start threw: $startErr · lib=$libName';
        lastError = '本机引擎启动异常 · $startErr';
        debugPrint(lastCreateNote);
        return EngineDeadBridge(reason: lastError);
      }

      final raw = p.toDartString();
      free(p);
      final m = decodeMap(raw);
      if (m['ok'] != true) {
        final err = m['error']?.toString() ?? raw;
        lastCreateNote = 'FFI host start failed: $err · lib=$libName';
        lastError = '本机引擎启动失败 · $err';
        debugPrint(lastCreateNote);
        return EngineDeadBridge(reason: lastError);
      }

      final base = m['base']?.toString() ?? 'http://127.0.0.1:$preferredPort';
      lastCreateNote =
          'FFI host ok · $base · lib=$libName · data=${m['data_dir'] ?? dataDir}';
      debugPrint(lastCreateNote);

      final call = _lookupCall(lib);
      final http = HttpTevarnBridge(base: base, kind: 'ffi');

      // Fail closed: ensure loopback host really answers.
      final healthy = await _probeHealth(http);
      if (!healthy) {
        lastCreateNote =
            'FFI host started but health probe failed · $base · lib=$libName';
        lastError = '本机引擎未就绪 · 健康检查失败 ($base)';
        debugPrint(lastCreateNote);
        return EngineDeadBridge(reason: lastError, attemptedBase: base);
      }

      return FfiTevarnBridge._(http, call, free, 'ffi');
    } catch (e, st) {
      lastCreateNote = 'FFI init failed: $e · lib=$libName';
      lastError = '本机引擎初始化失败 · $e';
      debugPrint(lastCreateNote);
      debugPrint('$st');
      return EngineDeadBridge(reason: lastError);
    }
  }

  /// Retry helper used by UI / AppController.
  static Future<TevarnBridge> createWithRetry({
    int preferredPort = 8765,
    int attempts = 2,
  }) async {
    TevarnBridge? last;
    for (var i = 0; i < attempts; i++) {
      last = await create(preferredPort: preferredPort);
      if (last.bridgeKind == 'ffi' || last.bridgeKind == 'http-web') {
        return last;
      }
      if (last is EngineDeadBridge && last.bridgeKind == 'engine-dead') {
        // brief backoff before second native start
        await Future<void>.delayed(Duration(milliseconds: 350 * (i + 1)));
        continue;
      }
      break;
    }
    return last ?? EngineDeadBridge(reason: '本机引擎不可用');
  }

  static Future<bool> _probeHealth(HttpTevarnBridge http) async {
    try {
      final r = await http.health().timeout(const Duration(seconds: 3));
      return r['ok'] == true ||
          r['status']?.toString() == 'ok' ||
          r['service'] != null;
    } catch (e) {
      debugPrint('health probe failed: $e');
      return false;
    }
  }

  /// Open Tevarn so first, then legacy Takton so (0.4.0 APK shipped wrong name).
  static (DynamicLibrary, String) _openLib() {
    if (Platform.isAndroid) {
      const names = [
        'libtevarn_mobile_ffi.so',
        'libtakton_mobile_ffi.so',
      ];
      Object? last;
      for (final n in names) {
        try {
          return (DynamicLibrary.open(n), n);
        } catch (e) {
          last = e;
          debugPrint('DynamicLibrary.open($n) failed: $e');
        }
      }
      throw UnsupportedError(
        'native engine .so missing (tried ${names.join(", ")}): $last',
      );
    }
    if (Platform.isIOS) {
      return (DynamicLibrary.process(), 'process');
    }
    if (Platform.isLinux) {
      for (final n in ['libtevarn_mobile_ffi.so', 'libtakton_mobile_ffi.so']) {
        try {
          return (DynamicLibrary.open(n), n);
        } catch (_) {}
      }
      throw UnsupportedError('libtevarn/takton_mobile_ffi.so missing');
    }
    if (Platform.isMacOS) {
      for (final n in [
        'libtevarn_mobile_ffi.dylib',
        'libtakton_mobile_ffi.dylib',
      ]) {
        try {
          return (DynamicLibrary.open(n), n);
        } catch (_) {}
      }
      throw UnsupportedError('libtevarn/takton_mobile_ffi.dylib missing');
    }
    throw UnsupportedError('platform');
  }

  static _FreeDart? _lookupFree(DynamicLibrary lib) {
    for (final name in ['tevarn_free', 'takton_free']) {
      try {
        return lib.lookupFunction<_FreeNative, _FreeDart>(name);
      } catch (_) {}
    }
    return null;
  }

  static _StartHostDart? _lookupStart(DynamicLibrary lib) {
    for (final name in ['tevarn_start_host', 'takton_start_host']) {
      try {
        return lib.lookupFunction<_StartHostNative, _StartHostDart>(name);
      } catch (_) {}
    }
    return null;
  }

  static _StartHost2Dart? _lookupStart2(DynamicLibrary lib) {
    for (final name in ['tevarn_start_host2', 'takton_start_host2']) {
      try {
        return lib.lookupFunction<_StartHost2Native, _StartHost2Dart>(name);
      } catch (_) {}
    }
    return null;
  }

  static _CallDart? _lookupCall(DynamicLibrary lib) {
    for (final name in ['tevarn_call', 'takton_call']) {
      try {
        return lib.lookupFunction<_CallNative, _CallDart>(name);
      } catch (_) {}
    }
    return null;
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
      final map = decodeMap(s);
      // Safety net: if native whitelist lags host routes, fall back to HTTP.
      final err = map['error']?.toString() ?? '';
      if (map['ok'] == false &&
          (err.startsWith('unknown method') || err.contains('unknown method'))) {
        debugPrint('FFI unknown method "$method" → HTTP fallback');
        return _http.call(method, args);
      }
      return map;
    } catch (e) {
      debugPrint('FFI call "$method" failed: $e → HTTP fallback');
      return _http.call(method, args);
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
  Future<Map<String, dynamic>> saveMedia({
    required String name,
    required List<int> bytes,
    String? contentType,
    String kind = 'image',
  }) =>
      _http.saveMedia(
        name: name,
        bytes: bytes,
        contentType: contentType,
        kind: kind,
      );

  @override
  Future<Map<String, dynamic>> runLocalTool(
    String name,
    Map<String, dynamic> args,
  ) =>
      _http.runLocalTool(name, args);

  @override
  Stream<String> streamLocalChat(String content,
          {List<Map<String, dynamic>>? images}) =>
      _http.streamLocalChat(content, images: images);

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

/// Explicit dead engine — never pretends 127.0.0.1:8765 is alive.
class EngineDeadBridge extends TevarnBridge {
  EngineDeadBridge({
    required this.reason,
    this.attemptedBase = '',
  });

  final String reason;
  final String attemptedBase;

  @override
  String get hostBase => attemptedBase;

  @override
  String get bridgeKind => 'engine-dead';

  Map<String, dynamic> _dead([String? method]) => {
        'ok': false,
        'error': reason.isEmpty
            ? '本机引擎未启动 · 无法执行${method == null ? "操作" : method}'
            : reason,
        'engine_dead': true,
      };

  @override
  Future<Map<String, dynamic>> call(String method,
          [Map<String, dynamic>? args]) async =>
      _dead(method);

  @override
  Future<Map<String, dynamic>> uploadFile({
    required String name,
    required List<int> bytes,
    String? contentType,
  }) async =>
      _dead('upload');

  @override
  Future<Map<String, dynamic>> saveMedia({
    required String name,
    required List<int> bytes,
    String? contentType,
    String kind = 'image',
  }) async =>
      _dead('save_media');

  @override
  Future<Map<String, dynamic>> runLocalTool(
    String name,
    Map<String, dynamic> args,
  ) async =>
      _dead(name);

  @override
  Stream<String> streamLocalChat(String content,
      {List<Map<String, dynamic>>? images}) async* {
    yield jsonEncode(_dead('local_chat'));
  }

  @override
  Stream<String> streamRemoteChat(
    String sessionId,
    String content, {
    List<Map<String, dynamic>>? attachments,
  }) async* {
    yield jsonEncode(_dead('remote_chat'));
  }

  @override
  void dispose() {}
}
