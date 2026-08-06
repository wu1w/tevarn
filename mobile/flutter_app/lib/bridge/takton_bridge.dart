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
    String? baseUrl,
    List<String>? candidates,
    String? email,
    String? password,
  }) =>
      call('connect', {
        if (baseUrl != null && baseUrl.isNotEmpty) 'base_url': baseUrl,
        if (candidates != null && candidates.isNotEmpty) 'candidates': candidates,
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
  
  Future<Map<String, dynamic>> agentConfigGet() => call('local_agent_config_get');
  Future<Map<String, dynamic>> agentConfigSet(Map<String, dynamic> cfg) =>
      call('local_agent_config_set', cfg);
  Future<Map<String, dynamic>> mcpConfigGet() => call('local_mcp_get');
  Future<Map<String, dynamic>> mcpConfigSet(Map<String, dynamic> config) =>
      call('local_mcp_set', {'config': config});
  Future<Map<String, dynamic>> localSkills() => call('local_skills');
  Future<Map<String, dynamic>> localConfigClear() =>
      call('local_config_clear');
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

  // M1 QR pairing
  Future<Map<String, dynamic>> pairStart({
    String? mesh,
    bool? requireConfirm,
    String? host,
    int? port,
    String? name,
  }) =>
      call('pair_start', {
        if (mesh != null) 'mesh': mesh,
        if (requireConfirm != null) 'require_confirm': requireConfirm,
        if (host != null) 'host': host,
        if (port != null) 'port': port,
        if (name != null) 'name': name,
      });
  Future<Map<String, dynamic>> pairStatus(String pairId) =>
      call('pair_status', {'pair_id': pairId});
  Future<Map<String, dynamic>> pairConfirm(String pairId) =>
      call('pair_confirm', {'pair_id': pairId});
  Future<Map<String, dynamic>> pairCancel(String pairId) =>
      call('pair_cancel', {'pair_id': pairId});
  Future<Map<String, dynamic>> pairClaim({
    required String pairId,
    required String code,
    String? deviceName,
  }) =>
      call('pair_claim', {
        'pair_id': pairId,
        'code': code,
        if (deviceName != null) 'device_name': deviceName,
      });
  Future<Map<String, dynamic>> pairApply({
    required String qr,
    String? deviceName,
    String? email,
    String? password,
    bool claim = true,
  }) =>
      call('pair_apply', {
        'qr': qr,
        if (deviceName != null) 'device_name': deviceName,
        if (email != null) 'email': email,
        if (password != null) 'password': password,
        'claim': claim,
      });
  Future<Map<String, dynamic>> pairDevices() => call('pair_devices');
  Future<Map<String, dynamic>> pairPending() => call('pair_pending');
  Future<Map<String, dynamic>> pairRevoke(String id) =>
      call('pair_revoke', {'id': id});

  // M2 mesh / remote access
  Future<Map<String, dynamic>> meshStatus() => call('mesh');
  Future<Map<String, dynamic>> meshSet({
    String? mode,
    String? hostname,
    bool? requirePairConfirm,
  }) =>
      call('mesh_set', {
        if (mode != null) 'mode': mode,
        if (hostname != null) 'hostname': hostname,
        if (requirePairConfirm != null)
          'require_pair_confirm': requirePairConfirm,
      });
  Future<Map<String, dynamic>> meshUp({
    String? hostname,
    List<String>? ifaces,
    bool? authKey,
  }) =>
      call('mesh_up', {
        if (hostname != null) 'hostname': hostname,
        if (ifaces != null) 'ifaces': ifaces,
        if (authKey != null) 'auth_key': authKey,
      });
  Future<Map<String, dynamic>> meshDown() => call('mesh_down');
  Future<Map<String, dynamic>> meshIfaces(List<String> ifaces) =>
      call('mesh_ifaces', {'ifaces': ifaces});
  Future<Map<String, dynamic>> meshAuth({String? authKey}) =>
      call('mesh_auth', {
        if (authKey != null) 'auth_key': authKey,
      });
  Future<Map<String, dynamic>> meshEmbedStart({
    String? role,
    String? hostname,
  }) =>
      call('mesh_embed_start', {
        if (role != null) 'role': role,
        if (hostname != null) 'hostname': hostname,
      });
  Future<Map<String, dynamic>> meshEmbedStop() => call('mesh_embed_stop');
  Future<Map<String, dynamic>> meshEmbedStatus() => call('mesh_embed');

  Future<Map<String, dynamic>> pathStatus() => call('path');
  Future<Map<String, dynamic>> pathProbe({List<String>? candidates}) =>
      call('path_probe', {
        if (candidates != null) 'candidates': candidates,
      });
  Future<Map<String, dynamic>> pathReconnect({
    List<String>? candidates,
    String? email,
    String? password,
    bool claim = true,
  }) =>
      call('path_reconnect', {
        if (candidates != null) 'candidates': candidates,
        if (email != null) 'email': email,
        if (password != null) 'password': password,
        'claim': claim,
      });
  Future<Map<String, dynamic>> pathRefresh({List<String>? candidates}) =>
      call('path_refresh', {
        if (candidates != null) 'candidates': candidates,
      });

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

  /// Save media into phone host store (`/api/mobile/media`).
  Future<Map<String, dynamic>> saveMedia({
    required String name,
    required List<int> bytes,
    String? contentType,
    String kind = 'image',
  });

  /// Invoke a local agent tool (OCR / search / …).
  Future<Map<String, dynamic>> runLocalTool(
    String name,
    Map<String, dynamic> args,
  );

  Stream<String> streamLocalChat(String content, {List<Map<String, dynamic>>? images});
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

