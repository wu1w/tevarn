import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/tool_call_ui.dart';
import 'tevarn_bridge.dart';

class HttpTevarnBridge extends TevarnBridge {
  HttpTevarnBridge({String? base, this.kind = 'http-web'})
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
          {
            final id = a['id'];
            final lim = a['limit'];
            final before = a['before']?.toString();
            final q = <String>[];
            if (lim != null) q.add('limit=$lim');
            if (before != null && before.isNotEmpty) {
              q.add('before=${Uri.encodeQueryComponent(before)}');
            }
            final qs = q.isEmpty ? '' : '?${q.join('&')}';
            return _get('/api/mobile/sessions/$id/messages$qs');
          }
        case 'turn_status':
          {
            final id = a['id'];
            final user = a['user']?.toString() ?? '';
            final q = user.isEmpty
                ? ''
                : '?user=${Uri.encodeQueryComponent(user)}';
            return _get('/api/mobile/sessions/$id/turn_status$q');
          }
        case 'local_history':
          return _get('/api/mobile/local/history');
        case 'local_history_clear':
          return _post('/api/mobile/local/history', {});
        case 'local_config_get':
          return _get('/api/mobile/local/config');
        case 'local_config_set':
          return _post('/api/mobile/local/config', a);
        case 'local_config_clear':
          return _post('/api/mobile/local/config/clear', {});
        case 'local_test':
          return _post('/api/mobile/local/test', a);
        case 'local_chat':
          return _post('/api/mobile/local/chat', a);
        case 'local_stop':
          return _post('/api/mobile/local/stop', {});
        case 'local_agent_config_get':
          return _get('/api/mobile/local/agent_config');
        case 'local_agent_config_set':
          return _post('/api/mobile/local/agent_config', a);
        case 'local_mcp_get':
          return _get('/api/mobile/local/mcp');
        case 'local_mcp_set':
          return _post('/api/mobile/local/mcp', a);
        case 'local_skills':
          return _get('/api/mobile/local/skills');
        case 'local_skills_install':
          return _post('/api/mobile/local/skills', a);
        case 'local_skills_install_pack':
          return _post('/api/mobile/local/skills/pack', a);
        case 'local_skills_uninstall':
          return _post('/api/mobile/local/skills/uninstall', a);
        case 'local_tools':
          return _post('/api/mobile/local/tools', a);
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
          // Heavy claim+login+bootstrap; never retry (double claim / dual WS crash).
          return _post(
            '/api/mobile/pair/apply',
            a,
            timeout: const Duration(seconds: 45),
            maxAttempts: 1,
          );
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
        case 'mesh_auth':
          return _post('/api/mobile/mesh/auth', a);
        case 'mesh_embed_start':
          return _post('/api/mobile/mesh/embed/start', a);
        case 'mesh_embed_stop':
          return _post('/api/mobile/mesh/embed/stop', {});
        case 'mesh_embed':
        case 'mesh_embed_status':
          return _get('/api/mobile/mesh/embed');
        case 'path':
        case 'path_status':
          return _get('/api/mobile/path');
        case 'path_probe':
          return _post('/api/mobile/path/probe', a);
        case 'path_reconnect':
          return _post('/api/mobile/path/reconnect', a,
              timeout: const Duration(seconds: 12), maxAttempts: 1);
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
        case 'notify':
          return _post('/api/mobile/notify', a);
        case 'runtime':
          return _get('/api/mobile/runtime');
        case 'host_base':
          return {'ok': true, 'base': hostBase};
        default:
          return {'ok': false, 'error': 'unknown method: $method'};
      }
    } catch (e) {
      return {'ok': false, 'error': e.toString()};
    }
  }

  /// Critical reads: 1 retry on timeout/network (path blip).
  Future<Map<String, dynamic>> _get(String path) async {
    Object? lastErr;
    for (var attempt = 0; attempt < 2; attempt++) {
      try {
        final r =
            await _client.get(_u(path)).timeout(const Duration(seconds: 8));
        return _parse(r);
      } catch (e) {
        lastErr = e;
        if (attempt == 0) {
          await Future<void>.delayed(const Duration(milliseconds: 200));
          continue;
        }
      }
    }
    return {'ok': false, 'error': lastErr?.toString() ?? 'GET failed'};
  }

  /// Critical writes: default 1 retry on network/timeout (not HTTP 4xx).
  /// [maxAttempts]=1 for non-idempotent heavy ops (pair_apply).
  Future<Map<String, dynamic>> _post(
    String path,
    Map<String, dynamic> body, {
    Duration timeout = const Duration(seconds: 12),
    int maxAttempts = 2,
  }) async {
    Object? lastErr;
    final attempts = maxAttempts.clamp(1, 3);
    for (var attempt = 0; attempt < attempts; attempt++) {
      try {
        final r = await _client
            .post(
              _u(path),
              headers: {'Content-Type': 'application/json'},
              body: jsonEncode(body),
            )
            .timeout(timeout);
        return _parse(r);
      } catch (e) {
        lastErr = e;
        if (attempt + 1 < attempts) {
          await Future<void>.delayed(const Duration(milliseconds: 250));
          continue;
        }
      }
    }
    return {'ok': false, 'error': lastErr?.toString() ?? 'POST failed'};
  }

  /// Host ring-buffer gap fill (seq strictly greater than [afterSeq]).
  Future<List<Map>> _eventsAfter(int afterSeq, String sessionId) async {
    if (afterSeq < 0) return const [];
    try {
      final sid = Uri.encodeQueryComponent(sessionId);
      final r = await _get(
        '/api/mobile/events?after_seq=$afterSeq&session_id=$sid&limit=80',
      );
      if (r['ok'] != true) return const [];
      final raw = r['events'];
      if (raw is! List) return const [];
      final out = <Map>[];
      for (final e in raw) {
        if (e is Map) out.add(e);
      }
      return out;
    } catch (_) {
      return const [];
    }
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
      MediaType? mt;
      if (contentType != null && contentType.isNotEmpty) {
        try {
          mt = MediaType.parse(contentType);
        } catch (_) {}
      }
      req.files.add(http.MultipartFile.fromBytes(
        'file',
        bytes,
        filename: name,
        contentType: mt,
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
  Future<Map<String, dynamic>> saveMedia({
    required String name,
    required List<int> bytes,
    String? contentType,
    String kind = 'image',
  }) async {
    try {
      final req = http.MultipartRequest('POST', _u('/api/mobile/media'));
      req.fields['kind'] = kind;
      MediaType? mt;
      if (contentType != null && contentType.isNotEmpty) {
        try {
          mt = MediaType.parse(contentType);
        } catch (_) {}
      }
      req.files.add(http.MultipartFile.fromBytes(
        'file',
        bytes,
        filename: name,
        contentType: mt,
      ));
      if (contentType != null && contentType.isNotEmpty) {
        req.fields['content_type'] = contentType;
      }
      final streamed =
          await _client.send(req).timeout(const Duration(seconds: 90));
      final body = await streamed.stream.bytesToString();
      return _parse(http.Response(body, streamed.statusCode));
    } catch (e) {
      return {'ok': false, 'error': e.toString()};
    }
  }

  @override
  Future<Map<String, dynamic>> runLocalTool(
    String name,
    Map<String, dynamic> args,
  ) async {
    try {
      final r = await _client
          .post(
            _u('/api/mobile/local/tools'),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode({'name': name, 'args': args}),
          )
          .timeout(const Duration(seconds: 90));
      return _parse(r);
    } catch (e) {
      return {'ok': false, 'error': e.toString()};
    }
  }

  @override
  Stream<String> streamLocalChat(String content, {List<Map<String, dynamic>>? images}) async* {
    final req = http.Request('POST', _u('/api/mobile/local/chat'));
    req.headers['Content-Type'] = 'application/json';
    req.body = jsonEncode({
      'content': content,
      if (images != null && images.isNotEmpty) 'images': images,
    });
    final streamed =
        await _client.send(req).timeout(const Duration(seconds: 300));
    final lines =
        streamed.stream.transform(utf8.decoder).transform(const LineSplitter());
    var event = '';
    var acc = '';
    await for (final line in lines) {
      if (line.startsWith('event:')) {
        event = line.substring(6).trim();
        continue;
      }
      if (!line.startsWith('data:')) continue;
      final data = line.substring(5).trim();
      if (data.isEmpty || data == '[DONE]') continue;
      Map? v;
      try {
        final decoded = jsonDecode(data);
        if (decoded is Map) v = decoded;
      } catch (_) {
        // plain text delta fallback
        if (data.isNotEmpty) {
          acc += data;
          yield data;
        }
        continue;
      }
      if (v == null) continue;

      if (event == 'error' || v['error'] != null) {
        throw Exception(
            v['error']?.toString() ?? v['message']?.toString() ?? 'local LLM error');
      }

      // Agent loop status (pi-style turn progress)
      if (event == 'status') {
        final detail = v['detail']?.toString() ?? '';
        if (detail.isNotEmpty) {
          yield '\x01STATUS\x01$detail';
        }
        event = '';
        continue;
      }

      // Tool start/end from Rust local_agent
      if (event == 'tool') {
        final phase = v['phase']?.toString() ?? '';
        final name = v['name']?.toString() ?? 'tool';
        final preview = (v['preview']?.toString() ?? '').replaceAll('\n', ' ');
        final ok = v['ok'];
        final okStr = ok == null ? '' : (ok == true ? '1' : '0');
        yield '\x01TOOL\x01$phase\x01$name\x01$okStr\x01$preview';
        event = '';
        continue;
      }

      // Host coalesced path: {"delta": "..."}
      final delta = v['delta']?.toString();
      if (delta != null && delta.isNotEmpty) {
        acc += delta;
        yield delta;
        event = '';
        continue;
      }

      // done: {"content": full} or {"text": full}
      final full = (v['content'] ?? v['text'])?.toString() ?? '';
      if (event == 'done' || v['done'] == true) {
        if (full.isNotEmpty) {
          // Replace with authoritative full text if we missed deltas
          if (acc.isEmpty || full.length > acc.length) {
            yield '\x00$full';
            acc = full;
          }
        } else if (acc.isEmpty) {
          throw Exception('本机 LLM 无输出 · 请确认已 OAuth/配置模型并点「应用模型」');
        }
        break;
      }

      // generic text field mid-stream
      final t = v['text']?.toString();
      if (t != null && t.isNotEmpty) {
        acc += t;
        yield t;
      }
      event = '';
    }
  }

  /// Host-authoritative turn probe (tool-loop aware). Falls back to messages scan.
  Future<String?> _pollRemoteAssistantText(
    String sessionId, {
    required String userContent,
    required int minLen,
  }) async {
    // Prefer dedicated endpoint: knows tool_calls / tool rows that UI normalize hides.
    try {
      final q = Uri.encodeQueryComponent(userContent);
      final r = await _get(
        '/api/mobile/sessions/$sessionId/turn_status?user=$q',
      );
      if (r['ok'] == true) {
        final ready = r['ready'] == true;
        // Host may still include tags on older builds — strip for mobile body.
        final text = stripThinkingBlocks((r['text'] ?? '').toString());
        if (!ready) return null;
        if (text.trim().isEmpty) return null;
        if (text.length < minLen && minLen > 0) {
          if (text.trim().length < 2 && minLen > 8) return null;
        }
        return text;
      }
    } catch (_) {
      // fall through to messages scan
    }

    try {
      final r = await _get('/api/mobile/sessions/$sessionId/messages');
      final raw = r['messages'];
      if (raw is! List || raw.isEmpty) return null;
      int lastUser = -1;
      final needle = userContent.trim();
      for (var i = raw.length - 1; i >= 0; i--) {
        final m = raw[i];
        if (m is! Map) continue;
        if ((m['role']?.toString() ?? '') != 'user') continue;
        final t = (m['content'] ?? m['text'] ?? '').toString().trim();
        if (t.isEmpty) continue;
        final prefixLen = needle.length.clamp(0, 32);
        final prefix = prefixLen == 0 ? '' : needle.substring(0, prefixLen);
        if (needle.isEmpty ||
            t == needle ||
            t.endsWith(needle) ||
            needle.endsWith(t) ||
            (prefix.isNotEmpty && t.contains(prefix))) {
          lastUser = i;
          break;
        }
      }
      if (lastUser < 0 || lastUser + 1 >= raw.length) return null;

      final last = raw.last;
      if (last is! Map) return null;
      final lastRole = last['role']?.toString() ?? '';
      // Tool loop still open.
      if (lastRole == 'tool' || lastRole == 'function') return null;
      if (lastRole != 'assistant') return null;

      // Normalized UI marks tool protocol as who=工具 — not a final answer.
      final who = (last['who']?.toString() ?? '');
      if (who.contains('工具')) return null;

      // Intermediate assistant that only requested tools — wait for final.
      final tc = last['tool_calls'];
      final hasTools = (tc is List && tc.isNotEmpty) ||
          (tc is Map && tc.isNotEmpty);
      if (hasTools) return null;

      final contentRaw = (last['content'] ?? last['text'] ?? '').toString();
      // Never treat thinking-only or tool-protocol rows as final answer.
      final t = stripThinkingBlocks(contentRaw);
      final trimmed = t.trim();
      if (trimmed.isEmpty) return null;
      // Codex-style tool rows produced by host normalize_ui_messages.
      if (trimmed.startsWith('· 调用') ||
          RegExp(r'^·\s*`[^`]+`\s*[…✓✗]').hasMatch(trimmed)) {
        return null;
      }
      if (t.length < minLen && minLen > 0) {
        // Allow short finals ("好的") even if minLen was inflated by partial deltas.
        if (trimmed.length < 2 && minLen > 8) return null;
      }
      return t;
    } catch (_) {
      return null;
    }
  }

  static bool _isRemoteTerminalEvent(Map v, {required bool sawTurn}) {
    final ty = (v['type']?.toString() ?? '').toLowerCase();
    if (ty == 'done' ||
        ty == 'chat_done' ||
        ty == 'complete' ||
        ty == 'final') {
      return true;
    }
    // Transport `closed` is often path switch / half-dead socket — recoverable.
    // Only end the turn if host marks it non-recoverable and we already saw work.
    if (ty == 'closed') {
      final recoverable = v['recoverable'] == true ||
          (v['reason']?.toString() ?? '').toLowerCase().contains('path');
      if (recoverable) return false;
      return sawTurn;
    }
    // Do NOT treat status=idle alone as terminal: tool loops can emit idle-ish
    // statuses; Host HTTP watchdog + run.completed / done are authoritative.
    if (ty == 'run_event') {
      final topic = (v['topic']?.toString() ?? '').toLowerCase();
      if (topic.endsWith('completed') ||
          topic.endsWith('failed') ||
          topic.endsWith('cancelled') ||
          topic.endsWith('interrupted')) {
        return sawTurn;
      }
    }
    return false;
  }

  static bool _isRemoteTurnStart(Map v) {
    final ty = (v['type']?.toString() ?? '').toLowerCase();
    if (ty == 'user_message_ack' ||
        ty == 'stream_delta' ||
        ty.contains('delta') ||
        ty == 'token' ||
        ty == 'chunk' ||
        ty == 'stream') {
      return true;
    }
    if (ty == 'run_event') {
      final topic = (v['topic']?.toString() ?? '').toLowerCase();
      return topic.endsWith('created') || topic.contains('status_changed');
    }
    if (ty == 'status') {
      final st = (v['state']?.toString() ?? '').toLowerCase();
      return st == 'thinking' || st == 'running' || st == 'tool';
    }
    return false;
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
    try {
      await ch.ready.timeout(const Duration(seconds: 8));
    } catch (e) {
      try {
        await ch.sink.close();
      } catch (_) {}
      throw Exception('手机本地 WS 未就绪: $e');
    }

    final payload = <String, dynamic>{
      'type': 'chat',
      'session_id': sessionId,
      'content': content,
    };
    if (attachments != null && attachments.isNotEmpty) {
      payload['attachments'] = attachments;
    }
    ch.sink.add(jsonEncode(payload));

    var finishedCleanly = false;
    // Sentinel yielded once at end so controller knows clean vs unclean finish.
    const finishOkMark = '\x01FINISH\x01ok';
    const finishBadMark = '\x01FINISH\x01bad';
    var sawTurn = false;
    var accLen = 0;
    // Host stamps monotonic `seq` on every fan-out frame (ordered merge / drop stale).
    var lastSeq = 0;
    // HTTP silence reconcile must be stable across polls — a single snapshot can
    // catch intermediate assistant text before tools finish (was finishing early).
    var pollStable = 0;
    var lastPolled = '';
    final started = DateTime.now();
    var lastActivity = started;
    // Base cap 5 min (dead LLM / no tools). Active tools extend to 30 min.
    const baseLimit = Duration(minutes: 5);
    const toolChainLimit = Duration(minutes: 30);
    // No progress after tools for this long → stall out (still < toolChainLimit).
    const activityIdleLimit = Duration(minutes: 15);
    // No turn / no text after this → treat as PC LLM/path failure.
    const silentFailAfter = Duration(seconds: 90);
    // Silence → first ring gap-fill, then turn_status (tools can pause longer).
    const silencePoll = Duration(milliseconds: 1500);
    var sawToolOrText = false;

    /// Snapshot of whether this event should replace UI text (computed before accLen mutates).
    bool _eventWantsReplace(Map v) {
      final src = (v['source']?.toString() ?? '').toLowerCase();
      if (v['replace'] == true || src == 'http_watchdog') return true;
      final delta = v['delta']?.toString();
      final contentField = (v['content'] ?? v['text'])?.toString();
      if (delta != null &&
          delta.isNotEmpty &&
          contentField != null &&
          contentField == delta &&
          delta.length > accLen + 20) {
        return true;
      }
      final ty = (v['type']?.toString() ?? '').toLowerCase();
      final t = contentField;
      if (t != null &&
          t.isNotEmpty &&
          (ty.contains('delta') ||
              ty.contains('chunk') ||
              ty == 'token' ||
              ty == 'stream' ||
              src == 'http_watchdog') &&
          t.length > accLen + 20) {
        return true;
      }
      return false;
    }

    /// Apply one host event map (state only); returns true if stream should end.
    /// Caller must [yieldsFor] for UI. On seq gap, call [_eventsAfter] first.
    Future<bool> applyEvent(Map v, {bool forceApply = false}) async {
      final tyEarly = (v['type']?.toString() ?? '').toLowerCase();
      // Hello MUST soft-align before any seq bump (hello itself carries a seq).
      if (tyEarly == 'mobile_hello') {
        final latest = v['latest_seq'];
        final ls = latest is int
            ? latest
            : int.tryParse(latest?.toString() ?? '') ?? 0;
        if (ls > lastSeq) {
          lastSeq = ls;
        }
        return false;
      }
      final seqRaw = v['seq'];
      final seq = seqRaw is int
          ? seqRaw
          : int.tryParse(seqRaw?.toString() ?? '') ?? 0;
      final srcEarly = (v['source']?.toString() ?? '').toLowerCase();
      final replaceEarly =
          v['replace'] == true || srcEarly == 'http_watchdog';
      if (seq > 0 && !forceApply) {
        // Drop duplicates and reordered stale frames (except full replace snapshots).
        if (seq <= lastSeq && !replaceEarly) {
          return false;
        }
        if (seq > lastSeq) lastSeq = seq;
      } else if (seq > 0 && forceApply && seq > lastSeq) {
        lastSeq = seq;
      }
      final ty = tyEarly;
      if (ty == 'error') {
        final full = await _pollRemoteAssistantText(
          sessionId,
          userContent: content,
          minLen: 0, // allow shorter authoritative recovery
        );
        if (full != null && full.isNotEmpty) {
          accLen = full.length;
          lastPolled = full;
          v['_ui_replace'] = true;
          v['content'] = full;
          v['text'] = full;
          return true;
        }
        throw Exception(
            v['error']?.toString() ?? v['message']?.toString() ?? 'remote error');
      }

      if (_isRemoteTurnStart(v)) {
        sawTurn = true;
      }

      if (ty == 'mobile_status' || ty == 'status') {
        final detail = (v['detail'] ?? v['state'] ?? '').toString();
        if (detail.isNotEmpty) {
          sawTurn = true;
        }
      }
      if (ty == 'mobile_tool' || ty == 'tool_event') {
        sawTurn = true;
      }

      final src = (v['source']?.toString() ?? '').toLowerCase();
      final replace = v['replace'] == true || src == 'http_watchdog';
      final delta = v['delta']?.toString();
      final contentField = (v['content'] ?? v['text'])?.toString();
      // Store replace decision for yieldsFor (same event) before mutating accLen.
      v['_ui_replace'] = _eventWantsReplace(v) || replace;
      if (delta != null && delta.isNotEmpty) {
        if (v['_ui_replace'] == true) {
          final full =
              (contentField != null && contentField.length >= delta.length)
                  ? contentField
                  : delta;
          accLen = full.length;
          sawTurn = true;
          lastPolled = full;
          pollStable = 0;
        } else {
          accLen += delta.length;
          sawTurn = true;
        }
      } else if (ty.contains('delta') ||
          ty.contains('chunk') ||
          ty == 'token' ||
          ty == 'stream' ||
          replace) {
        final t = contentField;
        if (t != null && t.isNotEmpty) {
          if (v['_ui_replace'] == true || replace || t.length > accLen + 20) {
            accLen = t.length;
            sawTurn = true;
            lastPolled = t;
            pollStable = 0;
          } else {
            accLen += t.length;
            sawTurn = true;
          }
        }
      }

      if (_isRemoteTerminalEvent(v, sawTurn: sawTurn)) {
        final embedded = (v['content'] ?? v['text'])?.toString();
        // Authoritative terminal may be shorter than lossy stream — always accept.
        if (embedded != null && embedded.isNotEmpty) {
          accLen = embedded.length;
          lastPolled = embedded;
          v['_ui_replace'] = true;
        }
        return true;
      }
      return false;
    }

    /// Yield UI strings for a processed event (status/tool/text). Call after apply*.
    Iterable<String> yieldsFor(Map v) sync* {
      final ty = (v['type']?.toString() ?? '').toLowerCase();
      if (ty == 'mobile_status' || ty == 'status') {
        final detail = (v['detail'] ?? v['state'] ?? '').toString();
        final action = (v['action'] ?? '').toString();
        if (detail.isNotEmpty) {
          lastActivity = DateTime.now();
          yield action.isEmpty
              ? '\x01STATUS\x01$detail'
              : '\x01STATUS\x01$detail\x01$action';
        }
      }
      if (ty == 'mobile_approval_badge') {
        final n = v['pending']?.toString() ?? '0';
        yield '\x01BADGE\x01$n';
      }
      if (ty == 'mobile_confirm') {
        final id = (v['confirm_id'] ?? v['id'] ?? '').toString();
        // Escape SOH in detail so split stays stable
        final detail =
            (v['detail'] ?? '').toString().replaceAll('\x01', ' ');
        final raw = (v['raw_type'] ?? v['kind'] ?? '').toString().toLowerCase();
        final kind = (raw.contains('escalat') || raw.contains('approval'))
            ? 'escalation'
            : (raw.contains('confirm') || raw.contains('permission')
                ? 'confirm'
                : (raw.isEmpty ? 'escalation' : raw));
        yield '\x01CONFIRM\x01$id\x01$kind\x01$detail';
      }
      if (ty == 'mobile_tool' || ty == 'tool_event') {
        final phase = (v['phase']?.toString() ?? 'start').toLowerCase();
        final name = v['name']?.toString() ?? 'tool';
        // Prefer tool_call_id / call_id only (not event envelope id).
        final tid =
            (v['tool_call_id'] ?? v['call_id'] ?? '').toString();
        final status = v['status']?.toString() ?? '';
        final p = phase.contains('end') ||
                phase == 'completed' ||
                phase == 'result'
            ? 'end'
            : 'start';
        // End without ok/status → neutral (not success). Start → not marked failed.
        final String okStr;
        if (v.containsKey('ok')) {
          okStr = v['ok'] == true ? '1' : '0';
        } else if (status == 'failed' ||
            status == 'error' ||
            status == 'denied') {
          okStr = '0';
        } else if (p == 'end' && status.isEmpty) {
          okStr = ''; // unknown — controller treats non-'0' carefully
        } else {
          okStr = '1';
        }
        final preview = (v['preview'] ?? v['result'] ?? '').toString();
        sawToolOrText = true;
        lastActivity = DateTime.now();
        yield '\x01TOOL\x01$p\x01$name\x01$okStr\x01$preview\x01$tid';
      }
      final src = (v['source']?.toString() ?? '').toLowerCase();
      final replaceFlag = v['_ui_replace'] == true ||
          v['replace'] == true ||
          src == 'http_watchdog';
      final delta = v['delta']?.toString();
      final contentField = (v['content'] ?? v['text'])?.toString();
      if (delta != null && delta.isNotEmpty) {
        sawToolOrText = true;
        lastActivity = DateTime.now();
        final full =
            (contentField != null && contentField.length >= delta.length)
                ? contentField
                : delta;
        if (replaceFlag) {
          yield '\x00$full';
        } else {
          yield delta;
        }
      } else if (ty.contains('delta') ||
          ty.contains('chunk') ||
          ty == 'token' ||
          ty == 'stream' ||
          replaceFlag) {
        final t = contentField;
        if (t != null && t.isNotEmpty) {
          if (replaceFlag) {
            yield '\x00$t';
          } else {
            yield t;
          }
        }
      }
    }

    try {
      final iter = StreamIterator(ch.stream);
      Future<bool>? pendingMove;
      var silenceBackoffMs = 2000;

      while (true) {
        final now = DateTime.now();
        final elapsed = now.difference(started);
        final idle = now.difference(lastActivity);
        final cap = sawToolOrText ? toolChainLimit : baseLimit;
        if (elapsed >= cap) break;
        // Tools started but no activity for 6 min → stall (avoids infinite hang).
        if (sawToolOrText && idle >= activityIdleLimit) break;

        pendingMove ??= iter.moveNext();
        final silence = Duration(milliseconds: silenceBackoffMs);
        final winner = await Future.any<Object>([
          pendingMove.then<_Move>((has) => _Move(has)),
          Future<Object>.delayed(silence, () => const _Silence()),
        ]);

        if (winner is _Silence) {
          // Dead LLM / no events: don't spin until cap with zero feedback.
          if (!sawTurn && elapsed >= silentFailAfter) {
            break;
          }
          if (sawTurn &&
              !sawToolOrText &&
              elapsed >= silentFailAfter) {
            break;
          }
          // No WS frame — first fill seq gaps from host ring, then turn_status.
          if (!sawTurn) continue;
          {
            final missing = await _eventsAfter(lastSeq, sessionId);
            for (final m in missing) {
              final done = await applyEvent(m);
              for (final y in yieldsFor(m)) {
                yield y;
              }
              if (done) {
                finishedCleanly = true;
                break;
              }
            }
            if (finishedCleanly) break;
          }
          final full = await _pollRemoteAssistantText(
            sessionId,
            userContent: content,
            minLen: 0,
          );
          if (full != null && full.isNotEmpty) {
            sawToolOrText = true;
            lastActivity = DateTime.now();
            if (full != lastPolled) {
              lastPolled = full;
              pollStable = 0;
              // Authoritative poll may shorten stream text — always replace.
              accLen = full.length;
              yield '\x00$full';
              silenceBackoffMs = 1500;
            } else {
              pollStable += 1;
              // Adaptive backoff while tools/long runs are quiet.
              silenceBackoffMs = (silenceBackoffMs + 800).clamp(2000, 6000);
            }
            // Need 3 identical final polls so mid-tool snapshots don't end the turn.
            if (pollStable >= 2) {
              finishedCleanly = true;
              break;
            }
          } else {
            pollStable = 0;
            lastPolled = '';
            silenceBackoffMs = (silenceBackoffMs + 800).clamp(2000, 6000);
          }
          continue;
        }

        final has = (winner as _Move).has;
        pendingMove = null;
        silenceBackoffMs = 2000;
        if (!has) {
          // Host WS closed — ring gap-fill then HTTP stabilize.
          {
            final missing = await _eventsAfter(lastSeq, sessionId);
            for (final m in missing) {
              final done = await applyEvent(m);
              for (final y in yieldsFor(m)) {
                yield y;
              }
              if (done) {
                finishedCleanly = true;
                break;
              }
            }
          }
          if (!finishedCleanly) {
            var stableSame = 0;
            for (var i = 0; i < 5; i++) {
              final full = await _pollRemoteAssistantText(
                sessionId,
                userContent: content,
                minLen: 0,
              );
              if (full != null && full.isNotEmpty) {
                accLen = full.length;
                yield '\x00$full';
                if (full == lastPolled) {
                  stableSame += 1;
                  if (stableSame >= 1) {
                    finishedCleanly = true;
                    break;
                  }
                } else {
                  stableSame = 0;
                  lastPolled = full;
                }
              }
              await Future<void>.delayed(const Duration(milliseconds: 800));
            }
          }
          break;
        }

        final msg = iter.current;
        if (msg is! String) continue;
        Map? v;
        try {
          final decoded = jsonDecode(msg);
          if (decoded is Map) v = Map<String, dynamic>.from(decoded);
        } catch (_) {
          continue;
        }
        if (v == null) continue;
        // Unwrap non-object host frames: { type: wrapped, payload: ... }
        if ((v['type']?.toString() ?? '') == 'wrapped' && v['payload'] is Map) {
          final inner = Map<String, dynamic>.from(v['payload'] as Map);
          if (v['seq'] != null) inner['seq'] = v['seq'];
          if (v['session_id'] != null) {
            inner['session_id'] = v['session_id'];
          }
          v = inner;
        }

        // Seq gap mid-stream → pull ring for (lastSeq, seqJump) only, never past current.
        final seqRaw = v['seq'];
        final seqJump = seqRaw is int
            ? seqRaw
            : int.tryParse(seqRaw?.toString() ?? '') ?? 0;
        if (seqJump > lastSeq + 1 || (lastSeq == 0 && seqJump > 1)) {
          final missing = await _eventsAfter(lastSeq, sessionId);
          for (final m in missing) {
            final ms = m['seq'];
            final mseq =
                ms is int ? ms : int.tryParse(ms?.toString() ?? '') ?? 0;
            // Open interval: lastSeq < mseq < seqJump (when seqJump known).
            if (seqJump > 0 && mseq >= seqJump) continue;
            if (mseq > 0 && mseq <= lastSeq) continue;
            final d = await applyEvent(m);
            for (final y in yieldsFor(m)) {
              yield y;
            }
            if (d) {
              finishedCleanly = true;
              break;
            }
          }
          if (finishedCleanly) break;
        }

        final done = await applyEvent(v);
        for (final y in yieldsFor(v)) {
          yield y;
        }
        if (done) {
          // Terminal: HTTP reconcile — allow shorter authoritative final text.
          final embedded = (v['content'] ?? v['text'])?.toString();
          if (embedded != null && embedded.isNotEmpty) {
            accLen = embedded.length;
            yield '\x00$embedded';
            finishedCleanly = true;
            break;
          }
          var stableSame = 0;
          for (var i = 0; i < 5; i++) {
            final full = await _pollRemoteAssistantText(
              sessionId,
              userContent: content,
              minLen: 0,
            );
            if (full != null && full.isNotEmpty) {
              accLen = full.length;
              yield '\x00$full';
              if (full == lastPolled) {
                stableSame += 1;
                if (stableSame >= 1) {
                  finishedCleanly = true;
                  break;
                }
              } else {
                stableSame = 0;
                lastPolled = full;
              }
            }
            await Future<void>.delayed(const Duration(milliseconds: 600));
          }
          // Only mark clean if we actually have text or stable poll; otherwise leave
          // for overall fallback (avoids empty "clean" finish mid-tool).
          if (lastPolled.isNotEmpty || accLen > 0) {
            finishedCleanly = true;
          }
          break;
        }
      }

      if (!finishedCleanly) {
        {
          final missing = await _eventsAfter(lastSeq, sessionId);
          for (final m in missing) {
            await applyEvent(m);
            for (final y in yieldsFor(m)) {
              yield y;
            }
          }
        }
        final full = await _pollRemoteAssistantText(
          sessionId,
          userContent: content,
          minLen: 0,
        );
        if (full != null && full.isNotEmpty) {
          accLen = full.length;
          yield '\x00$full';
          finishedCleanly = true;
        } else if (accLen == 0) {
          // Visible error so UI ends streaming instead of silent crash/kill.
          yield '\x00⚠️ PC Agent 长时间无响应。请检查 PC 工作台 LLM/模型配置是否可用，然后重试。';
        }
      }
      // Terminal honesty for offline-queue / streamOk
      yield finishedCleanly ? finishOkMark : finishBadMark;
    } finally {
      // CRITICAL: never auto-send type=stop when the phone stream ends uncleanly.
      // That killed PC agent mid-tool (weather / long runs) while the user never
      // pressed Stop — UI showed「远端中断」. Only sessionStop / explicit user
      // abort may cancel the PC run (see AppController.stopGeneration).
      try {
        await ch.sink.close();
      } catch (_) {}
    }
  }

  @override
  void dispose() {
    _client.close();
  }
}

class _Silence {
  const _Silence();
}

class _Move {
  const _Move(this.has);
  final bool has;
}
