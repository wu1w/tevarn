import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import 'takton_bridge.dart';

class HttpTaktonBridge extends TaktonBridge {
  HttpTaktonBridge({String? base, this.kind = 'http-web'})
      : _base = _normalize(base ?? '');

  String _base;
  final String kind;
  final _client = http.Client();

  static String _normalize(String b) {
    if (b.isEmpty) return '';
    return b.replaceAll(RegExp(r'/$'), '');
  }

  @override
  String get hostBase => _base.isEmpty ? '' : _base;

  @override
  String get bridgeKind => kind;

  Uri _u(String path) {
    if (_base.isEmpty) return Uri.parse(path);
    return Uri.parse('$_base$path');
  }

  @override
  Future<Map<String, dynamic>> call(String method,
      [Map<String, dynamic>? args]) async {
    final a = args ?? {};
    try {
      switch (method) {
        case 'health':
          return _get('/api/mobile/health');
        case 'state':
          return _get('/api/mobile/state');
        case 'mode':
          return _post('/api/mobile/mode', a);
        case 'switch_surface':
          return _post('/api/mobile/switch_surface', a);

        case 'connect':
          return _post('/api/mobile/connect', a);
        case 'disconnect':
          return _post('/api/mobile/disconnect', {});
        case 'auto_login':
          return _post('/api/mobile/auto-login', {});
        case 'sessions':
          return _get('/api/mobile/sessions');
        case 'session_create':
          return _post('/api/mobile/sessions', {});
        case 'session_open':
          return _post('/api/mobile/sessions/${a['id']}/open', {});
        case 'session_pin':
          return _post('/api/mobile/sessions/${a['id']}/pin', a);
        case 'session_rename':
          return _post('/api/mobile/sessions/${a['id']}/rename', a);
        case 'session_delete':
          return _post('/api/mobile/sessions/${a['id']}/delete', {});
        case 'session_stop':
          return _post('/api/mobile/sessions/${a['id']}/stop', {});
        case 'messages':
          return _get('/api/mobile/sessions/${a['id']}/messages');
        case 'local_history':
          return _get('/api/mobile/local/history');
        case 'local_history_clear':
          return _post('/api/mobile/local/history', {});
        case 'local_config_get':
          return _get('/api/mobile/local/config');
        case 'local_config_set':
          return _post('/api/mobile/local/config', a);
        case 'local_test':
          return _post('/api/mobile/local/test', a);
        case 'local_chat':
          return _post('/api/mobile/local/chat', a);
        case 'local_stop':
          return _post('/api/mobile/local/stop', {});
        case 'approvals':
          return _get('/api/mobile/approvals');
        case 'approvals_summary':
          return _get('/api/mobile/approvals/summary');
        case 'decide':
          return _post('/api/mobile/approvals/${a['id']}/decide', a);
        case 'devices':
          return _get('/api/mobile/devices');
        case 'pair_start':
          return _post('/api/mobile/pair/start', a);
        case 'pair_status':
          return _get('/api/mobile/pair/status/${a['pair_id'] ?? a['id']}');
        case 'pair_confirm':
          return _post(
              '/api/mobile/pair/confirm/${a['pair_id'] ?? a['id']}', {});
        case 'pair_cancel':
          return _post(
              '/api/mobile/pair/cancel/${a['pair_id'] ?? a['id']}', {});
        case 'pair_claim':
          return _post('/api/mobile/pair/claim', a);
        case 'pair_apply':
          return _post('/api/mobile/pair/apply', a);
        case 'pair_devices':
          return _get('/api/mobile/pair/devices');
        case 'pair_pending':
          return _get('/api/mobile/pair/pending');
        case 'pair_revoke':
          return _post('/api/mobile/pair/revoke/${a['id']}', {});
        case 'mesh':
        case 'mesh_status':
          return _get('/api/mobile/mesh');
        case 'mesh_set':
          return _post('/api/mobile/mesh', a);
        case 'mesh_up':
          return _post('/api/mobile/mesh/up', a);
        case 'mesh_down':
          return _post('/api/mobile/mesh/down', {});
        case 'mesh_ifaces':
          return _post('/api/mobile/mesh/ifaces', a);
        case 'path':
        case 'path_status':
          return _get('/api/mobile/path');
        case 'path_probe':
          return _post('/api/mobile/path/probe', a);
        case 'path_reconnect':
          return _post('/api/mobile/path/reconnect', a);
        case 'path_refresh':
          return _post('/api/mobile/path/refresh', a);
        case 'processes':
          return _get('/api/mobile/processes');

        case 'process_stop':
          return _post('/api/mobile/processes/${a['id']}/stop', {});
        case 'process_resume':
          return _post('/api/mobile/processes/${a['id']}/resume', {});
        case 'motion':
          return _get('/api/mobile/motion');
        case 'kernel':
          return _get('/api/mobile/kernel');
        case 'catalog':
          final params = <String>[];
          if (a['refresh'] == true) params.add('refresh=true');
          final q = a['q']?.toString();
          if (q != null && q.isNotEmpty) {
            params.add('q=${Uri.encodeQueryComponent(q)}');
          }
          final pid = a['provider_id']?.toString();
          if (pid != null && pid.isNotEmpty) {
            params.add('provider_id=${Uri.encodeQueryComponent(pid)}');
          }
          final qs = params.isEmpty ? '' : '?${params.join('&')}';
          return _get('/api/mobile/catalog$qs');
        case 'presets':
          return _get('/api/mobile/presets');
        case 'catalog_select':
          return _post('/api/mobile/catalog/select', a);
        case 'catalog_register':
          return _post('/api/mobile/catalog/register', a);
        case 'set_credentials':
          return _post('/api/mobile/settings/credentials', a);
        case 'test_llm':
          return _post('/api/mobile/test-llm', a);
        case 'oauth_openai_start':
          return _post('/api/mobile/oauth/openai/start', {});
        case 'oauth_openai_poll':
          return _post('/api/mobile/oauth/openai/poll', a);
        case 'oauth_openai_complete':
          return _post('/api/mobile/oauth/openai/complete', a);
        case 'oauth_xai_start':
          return _post('/api/mobile/oauth/xai/start', {});
        case 'oauth_xai_poll':
          return _post('/api/mobile/oauth/xai/poll', a);
        case 'host_base':
          return {'ok': true, 'base': hostBase};
        default:
          return {'ok': false, 'error': 'unknown method: $method'};
      }
    } catch (e) {
      return {'ok': false, 'error': e.toString()};
    }
  }

  Future<Map<String, dynamic>> _get(String path) async {
    final r = await _client.get(_u(path)).timeout(const Duration(seconds: 30));
    return _parse(r);
  }

  Future<Map<String, dynamic>> _post(
      String path, Map<String, dynamic> body) async {
    final r = await _client
        .post(
          _u(path),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode(body),
        )
        .timeout(const Duration(seconds: 60));
    return _parse(r);
  }

  Map<String, dynamic> _parse(http.Response r) {
    Map<String, dynamic> m;
    try {
      m = decodeMap(r.body);
    } catch (_) {
      m = {'ok': false, 'error': 'HTTP ${r.statusCode}', 'raw': r.body};
    }
    if (r.statusCode >= 400 && m['ok'] != true) {
      m['ok'] = false;
      m['error'] ??= 'HTTP ${r.statusCode}';
    }
    return m;
  }

  @override
  Future<Map<String, dynamic>> uploadFile({
    required String name,
    required List<int> bytes,
    String? contentType,
  }) async {
    try {
      final req = http.MultipartRequest('POST', _u('/api/mobile/upload'));
      req.files.add(http.MultipartFile.fromBytes(
        'file',
        bytes,
        filename: name,
      ));
      if (contentType != null && contentType.isNotEmpty) {
        req.fields['content_type'] = contentType;
      }
      final streamed =
          await _client.send(req).timeout(const Duration(seconds: 90));
      final body = await streamed.stream.bytesToString();
      final fake = http.Response(body, streamed.statusCode);
      return _parse(fake);
    } catch (e) {
      return {'ok': false, 'error': e.toString()};
    }
  }

  @override
  Stream<String> streamLocalChat(String content) async* {
    final req = http.Request('POST', _u('/api/mobile/local/chat'));
    req.headers['Content-Type'] = 'application/json';
    req.body = jsonEncode({'content': content});
    final streamed =
        await _client.send(req).timeout(const Duration(seconds: 120));
    final lines =
        streamed.stream.transform(utf8.decoder).transform(const LineSplitter());
    var event = '';
    await for (final line in lines) {
      if (line.startsWith('event:')) {
        event = line.substring(6).trim();
        continue;
      }
      if (line.startsWith('data:')) {
        final data = line.substring(5).trim();
        if (data.isEmpty || data == '[DONE]') continue;
        try {
          final v = jsonDecode(data);
          if (v is Map) {
            if (event == 'error' || v['error'] != null) {
              throw Exception(v['error']?.toString() ?? 'local LLM error');
            }
            final t = v['text']?.toString();
            if (t != null && t.isNotEmpty) {
              if (event == 'done') {
                yield '\x00$t';
              } else {
                yield t;
              }
            }
          }
        } catch (e) {
          if (e is Exception) rethrow;
        }
      }
    }
  }

  @override
  Stream<String> streamRemoteChat(
    String sessionId,
    String content, {
    List<Map<String, dynamic>>? attachments,
  }) async* {
    if (sessionId.isEmpty) {
      throw Exception('session_id required · 请先新建远端会话');
    }
    await _post('/api/mobile/sessions/$sessionId/open', {});
    final base = _base.isEmpty ? Uri.base : Uri.parse(_base);
    final scheme = base.scheme == 'https' ? 'wss' : 'ws';
    final host = base.hasPort ? '${base.host}:${base.port}' : base.host;
    final wsUrl = Uri.parse('$scheme://$host/api/mobile/ws');
    final ch = WebSocketChannel.connect(wsUrl);
    final payload = <String, dynamic>{
      'type': 'chat',
      'session_id': sessionId,
      'content': content,
    };
    if (attachments != null && attachments.isNotEmpty) {
      payload['attachments'] = attachments;
    }
    ch.sink.add(jsonEncode(payload));
    try {
      await for (final msg in ch.stream.timeout(const Duration(seconds: 90))) {
        if (msg is! String) continue;
        final v = jsonDecode(msg);
        if (v is! Map) continue;
        final ty = (v['type']?.toString() ?? '').toLowerCase();
        if (ty == 'error') {
          throw Exception(
              v['error']?.toString() ?? v['message']?.toString() ?? 'remote error');
        }
        if (ty == 'done' ||
            ty == 'chat_done' ||
            ty == 'closed' ||
            ty == 'complete' ||
            ty == 'final') {
          break;
        }
        // Prefer explicit delta field (Host-coalesced tokens).
        final delta = v['delta']?.toString();
        if (delta != null && delta.isNotEmpty) {
          yield delta;
          continue;
        }
        // Incremental types may carry content/text; ignore full assistant/message.
        if (ty.contains('delta') ||
            ty.contains('chunk') ||
            ty == 'token' ||
            ty == 'stream') {
          final t = (v['content'] ?? v['text'])?.toString();
          if (t != null && t.isNotEmpty) yield t;
        }
      }
    } finally {
      // Best-effort cancel if consumer aborted mid-stream.
      try {
        ch.sink.add(jsonEncode({
          'type': 'stop',
          'session_id': sessionId,
        }));
      } catch (_) {}
      await ch.sink.close();
    }
  }

  @override
  void dispose() {
    _client.close();
  }
}
