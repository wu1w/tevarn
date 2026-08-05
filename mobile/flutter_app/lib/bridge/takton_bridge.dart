import 'dart:convert';

/// Abstract surface over the Rust mobile engine.
abstract class TaktonBridge {
  String get hostBase;

  /// 'http-web' | 'ffi' | 'http-fallback'
  String get bridgeKind => 'http';

  Future<Map<String, dynamic>> call(String method, [Map<String, dynamic>? args]);

  Future<Map<String, dynamic>> health() => call('health');
  Future<Map<String, dynamic>> state() => call('state');
  Future<Map<String, dynamic>> mode(String surface) =>
      call('mode', {'surface': surface});

  /// One-shot mode + messages + session (Rust does heavy work).
  Future<Map<String, dynamic>> switchSurface(
    String surface, {
    String? sessionId,
    bool ensureSession = true,
  }) =>
      call('switch_surface', {
        'surface': surface,
        if (sessionId != null && sessionId.isNotEmpty) 'session_id': sessionId,
        'ensure_session': ensureSession,
      });

  Future<Map<String, dynamic>> connect({
    required String baseUrl,
    String? email,
    String? password,
  }) =>
      call('connect', {
        'base_url': baseUrl,
        if (email != null) 'email': email,
        if (password != null) 'password': password,
      });
  Future<Map<String, dynamic>> disconnect() => call('disconnect');
  Future<Map<String, dynamic>> autoLogin() => call('auto_login');
  Future<Map<String, dynamic>> sessions() => call('sessions');
  Future<Map<String, dynamic>> sessionCreate() => call('session_create');
  Future<Map<String, dynamic>> sessionOpen(String id) =>
      call('session_open', {'id': id});
  Future<Map<String, dynamic>> sessionPin(String id, bool pinned) =>
      call('session_pin', {'id': id, 'pinned': pinned});
  Future<Map<String, dynamic>> sessionRename(String id, String title) =>
      call('session_rename', {'id': id, 'title': title});
  Future<Map<String, dynamic>> sessionDelete(String id) =>
      call('session_delete', {'id': id});
  /// Cancel in-flight remote (or local) generation for a session.
  Future<Map<String, dynamic>> sessionStop(String id) =>
      call('session_stop', {'id': id});
  Future<Map<String, dynamic>> messages(String id) =>
      call('messages', {'id': id});

  Future<Map<String, dynamic>> localHistory() => call('local_history');
  Future<Map<String, dynamic>> localHistoryClear() => call('local_history_clear');
  Future<Map<String, dynamic>> localConfigGet() => call('local_config_get');
  Future<Map<String, dynamic>> localConfigSet(Map<String, dynamic> cfg) =>
      call('local_config_set', cfg);
  Future<Map<String, dynamic>> localTest([Map<String, dynamic>? body]) =>
      call('local_test', body ?? {});
  Future<Map<String, dynamic>> localStop() => call('local_stop');
  Future<Map<String, dynamic>> approvals() => call('approvals');
  /// Single-shot: escalations + evolution + processes + badge pending count.
  Future<Map<String, dynamic>> approvalsSummary() => call('approvals_summary');
  Future<Map<String, dynamic>> decide(
    String id, {
    required bool approved,
    String kind = 'escalation',
    String? scope,
  }) =>
      call('decide', {
        'id': id,
        'approved': approved,
        'kind': kind,
        if (scope != null) 'scope': scope,
      });
  Future<Map<String, dynamic>> devices() => call('devices');
  Future<Map<String, dynamic>> processes() => call('processes');
  Future<Map<String, dynamic>> processStop(String id) =>
      call('process_stop', {'id': id});
  Future<Map<String, dynamic>> processResume(String id) =>
      call('process_resume', {'id': id});
  Future<Map<String, dynamic>> motion() => call('motion');
  Future<Map<String, dynamic>> kernel() => call('kernel');

  // Real PC catalog / presets (no mock). Filter/search done in Rust.
  Future<Map<String, dynamic>> catalog({
    bool refresh = false,
    String? q,
    String? providerId,
  }) =>
      call('catalog', {
        'refresh': refresh,
        if (q != null && q.isNotEmpty) 'q': q,
        if (providerId != null && providerId.isNotEmpty) 'provider_id': providerId,
      });
  Future<Map<String, dynamic>> presets() => call('presets');
  Future<Map<String, dynamic>> catalogSelect({
    required String providerId,
    required String model,
    String? sessionId,
  }) =>
      call('catalog_select', {
        'provider_id': providerId,
        'model': model,
        if (sessionId != null && sessionId.isNotEmpty) 'session_id': sessionId,
      });
  Future<Map<String, dynamic>> catalogRegister(Map<String, dynamic> body) =>
      call('catalog_register', body);
  Future<Map<String, dynamic>> setCredentials(Map<String, dynamic> body) =>
      call('set_credentials', body);
  Future<Map<String, dynamic>> testLlm(Map<String, dynamic> body) =>
      call('test_llm', body);

  // OAuth — ChatGPT (PKCE) + Grok (device code)
  Future<Map<String, dynamic>> oauthOpenaiStart() => call('oauth_openai_start');
  Future<Map<String, dynamic>> oauthOpenaiPoll({String? state}) =>
      call('oauth_openai_poll', {
        if (state != null && state.isNotEmpty) 'state': state,
      });
  Future<Map<String, dynamic>> oauthOpenaiComplete({
    String? callbackUrl,
    String? state,
  }) =>
      call('oauth_openai_complete', {
        if (callbackUrl != null && callbackUrl.isNotEmpty)
          'callback_url': callbackUrl,
        if (state != null && state.isNotEmpty) 'state': state,
      });
  Future<Map<String, dynamic>> oauthXaiStart() => call('oauth_xai_start');
  Future<Map<String, dynamic>> oauthXaiPoll({required String deviceCode}) =>
      call('oauth_xai_poll', {'device_code': deviceCode});

  /// Multipart upload to PC via host `/api/mobile/upload`.
  Future<Map<String, dynamic>> uploadFile({
    required String name,
    required List<int> bytes,
    String? contentType,
  });

  Stream<String> streamLocalChat(String content);
  Stream<String> streamRemoteChat(
    String sessionId,
    String content, {
    List<Map<String, dynamic>>? attachments,
  });

  void dispose() {}
}

Map<String, dynamic> decodeMap(String raw) {
  final v = jsonDecode(raw);
  if (v is Map<String, dynamic>) return v;
  if (v is Map) return Map<String, dynamic>.from(v);
  return {'ok': false, 'error': 'invalid json', 'raw': raw};
}

bool isOk(Map<String, dynamic> m) => m['ok'] == true;

