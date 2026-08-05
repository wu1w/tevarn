import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';

import '../bridge/takton_bridge.dart';
import 'mesh_ifaces_stub.dart'
    if (dart.library.io) 'mesh_ifaces_io.dart' as ifaces;

/// Phone/PC mesh runtime — UI-facing façade over Rust embed + path selection.
///
/// Happy path is fully silent:
/// 1. App boot → mesh up (iface watch)
/// 2. Scan QR with `tsk` → Rust auto-starts phone tsnet → claim PC
/// 3. Wi‑Fi ↔ 5G → fingerprint change → path_reconnect
///
/// Users never open system Tailscale or paste IPs when QR is v3 seamless.
class MeshRuntime with WidgetsBindingObserver {
  MeshRuntime._();
  static final MeshRuntime instance = MeshRuntime._();

  bool _up = false;
  bool _boundLifecycle = false;
  String? lastError;
  String fingerprint = '';
  String detail = '';
  String backend = '';
  bool embedRunning = false;
  String? tailscaleIp;
  Timer? _watch;
  void Function(bool changed)? onNetworkChanged;
  TaktonBridge? _bridge;

  bool get isUp => _up;

  void bind(TaktonBridge bridge) {
    _bridge = bridge;
    if (!_boundLifecycle) {
      WidgetsBinding.instance.addObserver(this);
      _boundLifecycle = true;
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      unawaited(checkNow());
    }
  }

  /// Bring up mesh intent + start iface watch. Auth key optional (QR supplies it).
  Future<bool> up({
    String? hostname,
    String? authKey,
    String? controlUrl,
  }) async {
    try {
      final list = await ifaces.listIpv4();
      // If we already have a key from a previous seamless pair, pass flag
      final hasKey = authKey != null && authKey.isNotEmpty;
      if (hasKey) {
        await _bridge?.meshAuth(authKey: authKey);
        await _bridge?.meshEmbedStart(role: 'phone', hostname: hostname);
      }
      final r = await _bridge?.meshUp(
            hostname: hostname ?? 'takton-phone',
            ifaces: list,
            authKey: hasKey,
          ) ??
          {'ok': true, 'up': true, 'backend': 'noop', 'detail': 'no bridge'};
      _up = r['ok'] == true || r['up'] == true;
      backend = r['backend']?.toString() ?? backend;
      detail = r['detail']?.toString() ?? detail;
      fingerprint = r['fingerprint']?.toString() ?? fingerprint;
      lastError = r['ok'] == false ? r['error']?.toString() : null;
      await _refreshEmbedStatus();
      _startWatch();
      return _up;
    } catch (e) {
      lastError = e.toString();
      _up = true;
      _startWatch();
      return true;
    }
  }

  Future<void> _refreshEmbedStatus() async {
    try {
      final e = await _bridge?.meshEmbedStatus();
      if (e == null) return;
      embedRunning = e['running'] == true;
      tailscaleIp = e['tailscale_ip']?.toString();
      if (e['detail'] != null) detail = e['detail'].toString();
      if (e['backend'] != null) backend = e['backend'].toString();
    } catch (_) {}
  }

  Future<void> down() async {
    _watch?.cancel();
    _watch = null;
    try {
      await _bridge?.meshEmbedStop();
      await _bridge?.meshDown();
    } catch (_) {}
    _up = false;
    embedRunning = false;
  }

  Future<String?> localIp() async {
    final list = await ifaces.listIpv4();
    return list.isEmpty ? null : list.first;
  }

  Future<List<String>> peers() async => const [];

  Future<List<String>> currentIfaces() => ifaces.listIpv4();

  void _startWatch() {
    _watch?.cancel();
    _watch = Timer.periodic(const Duration(seconds: 12), (_) {
      unawaited(_tick());
    });
  }

  Future<void> _tick() async {
    final bridge = _bridge;
    if (bridge == null) return;
    try {
      final list = await ifaces.listIpv4();
      final r = await bridge.meshIfaces(list);
      final changed = r['changed'] == true;
      if (r['fingerprint'] != null) {
        fingerprint = r['fingerprint'].toString();
      }
      if (r['detail'] != null) detail = r['detail'].toString();
      await _refreshEmbedStatus();
      if (changed) {
        onNetworkChanged?.call(true);
      }
    } catch (e) {
      if (kDebugMode) {
        debugPrint('mesh watch: $e');
      }
    }
  }

  Future<bool> checkNow() async {
    await _tick();
    return true;
  }

  void dispose() {
    _watch?.cancel();
    _watch = null;
    onNetworkChanged = null;
    if (_boundLifecycle) {
      WidgetsBinding.instance.removeObserver(this);
      _boundLifecycle = false;
    }
  }
}
