import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../bridge/takton_bridge.dart';
import '../models/app_models.dart';
import '../models/status_card.dart';
import 'attach_utils.dart';
import 'local_agent.dart';
import 'mesh_runtime.dart';
import 'voice_service.dart';

class AppController extends ChangeNotifier {
  AppController(this.bridge);

  final TaktonBridge bridge;

  AppTab tab = AppTab.chat;
  bool dark = false;
  bool drawerOpen = false;
  bool streaming = false;
  String surface = 'local'; // local | remote
  ModeSnap mode = ModeSnap.empty();
  Map<String, dynamic> state = {};
  final List<ChatMsg> messages = [];
  /// Dual caches so local ↔ remote switch is instant (no empty flash).
  final List<ChatMsg> _localMsgCache = [];
  final List<ChatMsg> _remoteMsgCache = [];
  String? activeSessionId;
  final List<SessionItem> remoteSessions = [];
  SessionItem? localSession;
  String input = '';
  String toast = '';
  bool toastShow = false;
  final List<Map<String, dynamic>> approvals = [];
  final List<Map<String, dynamic>> evolutions = [];
  final List<Map<String, dynamic>> processes = [];
  final List<AttachFile> attachments = [];
  String islandText = '本机';
  bool islandLive = false;
  String islandKind = 'local';
  bool islandExpanded = false;
  final List<StatusCard> statusCards = [];
  String clock = '';
  bool voiceOn = true;
  /// Pair succeeded but PC login still required — Remote tab expands form.
  bool needsManualLogin = false;
  bool cameraOn = true;
  /// True while a surface switch network call is in flight (UI already swapped).
  bool surfaceSwitching = false;

  String formBase = 'http://127.0.0.1:8090';
  String formEmail = '';
  String formPass = '';
  String llmBase = '';
  String llmKey = '';
  String llmModel = '';
  String oauthCallback = '';
  bool llmHasKey = false;
  String llmKeyMasked = '';

  // M1/M2/M3 pairing + mesh
  Map<String, dynamic>? activePair; // host-side QR session
  List<Map<String, dynamic>> pairedDevices = [];
  Map<String, dynamic> mesh = {};
  Map<String, dynamic> pathProfile = {};
  String lastPairQr = '';
  bool pairBusy = false;
  bool pathBusy = false;
  Timer? _pairPoll;
  Timer? _meshPoll;
  Timer? _pathPoll;

  Timer? _clockTimer;
  Timer? _approvePoll;
  Timer? _streamNotifyTimer;
  Timer? _toastTimer;
  bool _booted = false;
  bool _notifyScheduled = false;
  int _switchGen = 0;
  int _streamGen = 0;
  /// Bumped when Me tab is opened so settings panels re-fetch.
  int mePanelGen = 0;
  bool _streamDirty = false;


  bool get pcConnected => state['authenticated'] == true;

  /// Bridge transport: http-web / ffi / http-fallback
  String get bridgeKind => bridge.bridgeKind;

  List<String> get attachNames => attachments.map((e) => e.name).toList();

  String get meMeta {
    if (pcConnected) {
      final email = state['user_email']?.toString() ??
          state['email']?.toString() ??
          '';
      if (email.isNotEmpty) return email;
      return '已连 PC · 远端就绪';
    }
    return '本机模式 · 未连 PC';
  }

  /// Coalesce multiple state changes into one frame rebuild.
  void _notify() {
    if (_notifyScheduled) return;
    _notifyScheduled = true;
    SchedulerBinding.instance.scheduleFrameCallback((_) {
      _notifyScheduled = false;
      if (hasListeners) notifyListeners();
    });
    SchedulerBinding.instance.ensureVisualUpdate();
  }

  void _saveSurfaceCache() {
    final snap = messages.map((m) => m.copyMeta()..streaming = false);
    if (surface == 'local') {
      _localMsgCache
        ..clear()
        ..addAll(snap);
    } else {
      _remoteMsgCache
        ..clear()
        ..addAll(snap);
    }
  }

  void _loadSurfaceCache(String s) {
    final src = s == 'local' ? _localMsgCache : _remoteMsgCache;
    messages
      ..clear()
      ..addAll(src.map((m) => m.copyMeta()..streaming = false));
  }

  List<ChatMsg> _parseUiMessages(dynamic list) {
    final out = <ChatMsg>[];
    if (list is! List) return out;
    for (final m in list) {
      if (m is! Map) continue;
      out.add(ChatMsg(
        id: '${m['id'] ?? out.length}',
        role: m['role']?.toString() ?? 'assistant',
        text: m['content']?.toString() ?? m['text']?.toString() ?? '',
        who: m['who']?.toString() ?? '',
        format: m['format']?.toString() == 'markdown' ? 'markdown' : 'plain',
      ));
    }
    return out;
  }

  void _applyMessages(List<ChatMsg> list, {required String forSurface}) {
    if (forSurface == 'local') {
      _localMsgCache
        ..clear()
        ..addAll(list.map((m) => m.copyMeta()));
    } else {
      _remoteMsgCache
        ..clear()
        ..addAll(list.map((m) => m.copyMeta()));
    }
    if (surface == forSurface) {
      messages
        ..clear()
        ..addAll(list.map((m) => m.copyMeta()));
    }
  }

  /// Stop in-flight generation and pin partial text into the current surface cache.
  Future<void> _abortStream({String? toastMsg}) async {
    if (!streaming) return;
    streaming = false;
    _streamGen++; // invalidate any in-flight await-for loops
    islandLive = false;
    islandKind = surface == 'remote' ? 'conn' : 'local';
    for (final m in messages) {
      if (m.streaming) m.streaming = false;
    }
    try {
      if (surface == 'local') {
        await bridge.localStop();
      } else {
        // Tell PC agent to stop (symmetric with localStop).
        final sid = activeSessionId;
        if (sid != null && sid.isNotEmpty && sid != '__local__') {
          await bridge.sessionStop(sid);
        }
      }
    } catch (_) {}
    _saveSurfaceCache();
    if (toastMsg != null && toastMsg.isNotEmpty) {
      toast = toastMsg;
      toastShow = true;
      _toastTimer?.cancel();
      _toastTimer = Timer(const Duration(milliseconds: 2200), () {
        if (!hasListeners) return;
        toastShow = false;
        _notify();
      });
    }
  }

  /// Explicit stop — do not overload [send].
  Future<void> stopGeneration({String toastMsg = '已停止生成'}) async {
    if (!streaming) return;
    await _abortStream(toastMsg: toastMsg);
    _notify();
  }

  Future<void> boot() async {
    if (_booted) return;
    _booted = true;
    final prefs = await SharedPreferences.getInstance();
    final preferredSurface = prefs.getString('takton-chat-mode') ?? 'local';
    surface = preferredSurface;
    dark = prefs.getString('takton-theme') == 'dark';
    voiceOn = prefs.getBool('takton-voice') ?? true;
    cameraOn = prefs.getBool('takton-camera') ?? true;
    formBase = prefs.getString('takton-form-base') ?? formBase;
    formEmail = prefs.getString('takton-form-email') ?? '';
    // Password intentionally not stored — auth_session / device_token in Rust.
    lastPairQr = prefs.getString('takton-last-pair-qr') ?? '';
    final tabName = prefs.getString('takton-tab');
    if (tabName != null) {
      for (final t in AppTab.values) {
        if (t.name == tabName) {
          tab = t;
          break;
        }
      }
    }
    _tickClock();
    _clockTimer =
        Timer.periodic(const Duration(seconds: 30), (_) => _tickClock());

    // M3/M4: bind mesh runtime + network-change failover (non-blocking)
    MeshRuntime.instance.bind(bridge);
    MeshRuntime.instance.onNetworkChanged = (_) {
      unawaited(onNetworkPathChanged());
    };
    unawaited(MeshRuntime.instance.up(hostname: 'takton-phone'));

    // Local UI first — never block first paint on PC reconnect / mesh.
    try {
      await refreshAll().timeout(const Duration(seconds: 4));
    } catch (_) {}
    unawaited(refreshPath());

    // Soft surface while offline: do NOT wipe preferred mode from prefs.
    // If preferred remote but not yet connected, stay local until reconnect.
    final wantRemote = preferredSurface == 'remote';
    if (!pcConnected && wantRemote) {
      surface = 'local';
    }

    try {
      await _applySwitchSurface(surface, ensureSession: false)
          .timeout(const Duration(seconds: 4));
    } catch (_) {}

    // Auto-reconnect in background; restore remote surface on success.
    if (!pcConnected) {
      unawaited(tryAutoReconnect().then((_) async {
        if (!pcConnected) return;
        // Restore preferred chat mode after successful reconnect
        final p = await SharedPreferences.getInstance();
        final mode = p.getString('takton-chat-mode') ?? preferredSurface;
        if (mode == 'remote' && surface != 'remote') {
          await setSurface('remote');
        } else {
          await refreshAll();
          _notify();
        }
      }));
    }

    unawaited(refreshMesh());
    unawaited(refreshPairedDevices());
    // Background path health while remote may be used
    _pathPoll?.cancel();
    _pathPoll = Timer.periodic(const Duration(seconds: 45), (_) {
      if (!pcConnected || surface == 'remote') {
        unawaited(pathHealthTick());
      }
    });
    if (bridgeKind == 'http-fallback' ||
        bridgeKind.contains('fallback') ||
        bridgeKind.contains('timeout')) {
      showToast('引擎加载中或已降级 · $bridgeKind');
    }
    _notify();
  }

  void _tickClock() {
    final n = DateTime.now();
    final next =
        '${n.hour.toString().padLeft(2, '0')}:${n.minute.toString().padLeft(2, '0')}';
    if (next == clock) return;
    clock = next;
    _notify();
  }

  void openDrawer() {
    drawerOpen = true;
    _notify();
  }

  void closeDrawer() {
    drawerOpen = false;
    _notify();
  }

  /// Input stays local to the composer when possible — only sync for send/enable.
  void setInput(String v, {bool notify = false}) {
    input = v;
    if (notify) _notify();
  }

  void pulseIsland({String? text, String kind = 'local'}) {
    islandLive = true;
    if (text != null) islandText = text;
    islandKind = kind;
    _notify();
    Future.delayed(const Duration(seconds: 2), () {
      islandLive = false;
      _notify();
    });
  }

  void removeAttach(int i) {
    if (i < 0 || i >= attachments.length) return;
    attachments.removeAt(i);
    _notify();
  }

  @Deprecated('Name-only attach is forbidden — use addAttach with bytes/path')
  void addAttachName(String name) {
    showToast('无法只附加文件名，请重新选择文件');
  }

  void addAttach(AttachFile file) {
    if (file.name.isEmpty) return;
    attachments.add(file);
    _notify();
  }

  Future<void> setVoice(bool v) async {
    voiceOn = v;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('takton-voice', v);
    _notify();
  }

  Future<void> setCamera(bool v) async {
    cameraOn = v;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('takton-camera', v);
    _notify();
  }

  void _applyLocalLlmFromState() {
    final llm = state['local_llm'];
    if (llm is! Map) return;
    llmBase = llm['base_url']?.toString() ?? llmBase;
    llmModel = llm['model']?.toString() ?? llmModel;
    llmHasKey = llm['has_key'] == true;
    llmKeyMasked = llm['api_key_masked']?.toString() ?? '';
  }

  Future<void> refreshAll() async {
    final st = await bridge.state();
    if (isOk(st)) {
      state = st;
      formBase = st['base_url']?.toString() ?? formBase;
      activeSessionId = st['active_session_id']?.toString();
      if (activeSessionId != null && activeSessionId!.isEmpty) {
        activeSessionId = null;
      }
      if (activeSessionId == '__local__') activeSessionId = null;
      remoteSessions
        ..clear()
        ..addAll(((st['sessions'] as List?) ?? [])
            .whereType<Map>()
            .map((e) => SessionItem.fromJson(Map<String, dynamic>.from(e))));
      final ls = st['local_session'];
      if (ls is Map) {
        localSession =
            SessionItem.fromJson(Map<String, dynamic>.from(ls), isLocal: true);
      } else {
        localSession ??= SessionItem(
          id: '__local__',
          title: '本机对话',
          pinned: false,
          isLocal: true,
        );
      }
      _applyLocalLlmFromState();
    }
    try {
      final lr = await bridge.localConfigGet();
      if (isOk(lr) && lr['config'] is Map) {
        final cfg = Map<String, dynamic>.from(lr['config'] as Map);
        llmBase = cfg['base_url']?.toString() ?? llmBase;
        llmModel = cfg['model']?.toString() ?? llmModel;
        llmHasKey = cfg['has_key'] == true;
        llmKeyMasked = cfg['api_key_masked']?.toString() ?? '';
        state = {
          ...state,
          'local_llm': cfg,
          'local_llm_ready': cfg['ready'] == true,
        };
      }
    } catch (_) {}
    await refreshMode();
    await loadApprovals();
  }

  Future<void> refreshMode() async {
    final m = await bridge.mode(surface);
    final modeMap = m['mode'] is Map
        ? Map<String, dynamic>.from(m['mode'] as Map)
        : m;
    mode = ModeSnap.fromJson(modeMap);
    _notify();
  }

  Future<void> loadApprovals() async {
    // One Rust round-trip: escalations + evolution + processes + badge.
    final a = await bridge.approvalsSummary();
    if (!isOk(a)) {
      // Fallback to legacy parallel endpoints if summary unavailable.
      final results = await Future.wait([
        bridge.approvals(),
        bridge.processes(),
      ]);
      final legacy = results[0];
      final p = results[1];
      approvals
        ..clear()
        ..addAll(((legacy['escalations'] as List?) ?? [])
            .whereType<Map>()
            .map((e) => Map<String, dynamic>.from(e)));
      evolutions
        ..clear()
        ..addAll(((legacy['evolution'] as List?) ?? [])
            .whereType<Map>()
            .map((e) => Map<String, dynamic>.from(e)));
      processes
        ..clear()
        ..addAll(((p['processes'] as List?) ?? [])
            .whereType<Map>()
            .map((e) => Map<String, dynamic>.from(e)));
      _notify();
      return;
    }
    approvals
      ..clear()
      ..addAll(((a['escalations'] as List?) ?? [])
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e)));
    evolutions
      ..clear()
      ..addAll(((a['evolution'] as List?) ?? [])
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e)));
    processes
      ..clear()
      ..addAll(((a['processes'] as List?) ?? [])
          .whereType<Map>()
          .map((e) => Map<String, dynamic>.from(e)));
    // Keep shell badge in sync without a separate /state call
    final pending = (a['pending'] as num?)?.toInt() ??
        (a['badge'] as num?)?.toInt() ??
        (approvals.length + evolutions.length);
    state = {
      ...state,
      'approvals_pending': pending,
    };
    _notify();
  }

  Future<void> loadLocalMsgs() async {
    final h = await bridge.localHistory();
    final list = _parseUiMessages(
        (h['messages'] as List?) ?? (h['history'] as List?) ?? []);
    // Empty history stays empty — no seeded/mock welcome bubble.
    _applyMessages(list, forSurface: 'local');
    _notify();
  }

  Future<void> loadRemoteMsgs(String id) async {
    final h = await bridge.messages(id);
    final list = _parseUiMessages(
        (h['messages'] as List?) ?? (h['items'] as List?) ?? []);
    // Empty session stays empty — no seeded/mock bubble.
    _applyMessages(list, forSurface: 'remote');
    _notify();
  }

  Future<void> _applySwitchSurface(
    String s, {
    bool ensureSession = true,
    String? sessionId,
  }) async {
    final r = await bridge.switchSurface(
      s,
      sessionId: sessionId ?? (s == 'remote' ? activeSessionId : null),
      ensureSession: ensureSession,
    );
    if (!isOk(r) && s == 'remote') {
      // keep optimistic UI; only toast if hard fail
      final err = r['error']?.toString() ?? '';
      if (err.isNotEmpty) showToast(err);
      if (r['mode'] is Map) {
        mode = ModeSnap.fromJson(Map<String, dynamic>.from(r['mode'] as Map));
      }
      return;
    }
    if (r['mode'] is Map) {
      mode = ModeSnap.fromJson(Map<String, dynamic>.from(r['mode'] as Map));
    }
    final sid = r['session_id']?.toString();
    if (s == 'remote') {
      if (sid != null && sid.isNotEmpty && sid != '__local__') {
        activeSessionId = sid;
      }
    }
    final msgs = _parseUiMessages(r['messages']);
    if (msgs.isNotEmpty) {
      _applyMessages(msgs, forSurface: s);
    } else if (s == 'local' && messages.isEmpty) {
      await loadLocalMsgs();
    }
  }

  /// Optimistic surface switch: UI flips immediately from cache, Rust fills in.
  Future<void> setSurface(String s) async {
    if (s == surface && !surfaceSwitching) {
      unawaited(refreshMode());
      return;
    }
    if (s == 'remote' && !pcConnected) {
      setTab(AppTab.remote);
      showToast('远端 Agent 需先连接 PC');
      return;
    }

    // P1: abort in-flight stream before swapping threads (prevents cache corruption)
    if (streaming) {
      await _abortStream(toastMsg: '已停止生成 · 切换通道');
    }

    final gen = ++_switchGen;
    _saveSurfaceCache();

    surface = s;
    drawerOpen = false;
    surfaceSwitching = true;
    if (s == 'local') {
      islandText = '本机';
      islandKind = 'local';
      _loadSurfaceCache('local');
      // Keep empty list while network history loads — no mock loading bubble.
    } else {
      islandText = '已连 PC';
      islandKind = 'conn';
      _loadSurfaceCache('remote');
    }
    notifyListeners();

    unawaited(() async {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('takton-chat-mode', s);
    }());

    try {
      await _applySwitchSurface(s, ensureSession: s == 'remote');
    } finally {
      if (gen == _switchGen) {
        surfaceSwitching = false;
        _notify();
      }
    }
  }

  /// Ensure there is an active remote session id. Creates one if needed.
  Future<bool> ensureRemoteSession({bool silent = false}) async {
    if (!pcConnected) {
      if (!silent) {
        setTab(AppTab.remote);
        showToast('请先连接 PC');
      }
      return false;
    }
    if (activeSessionId != null &&
        activeSessionId!.isNotEmpty &&
        activeSessionId != '__local__') {
      return true;
    }
    // Prefer one-shot Rust path
    final r = await bridge.switchSurface(
      'remote',
      ensureSession: true,
    );
    if (isOk(r)) {
      final sid = r['session_id']?.toString();
      if (sid != null && sid.isNotEmpty && sid != '__local__') {
        activeSessionId = sid;
      }
      if (r['mode'] is Map) {
        mode = ModeSnap.fromJson(Map<String, dynamic>.from(r['mode'] as Map));
      }
      final msgs = _parseUiMessages(r['messages']);
      if (msgs.isNotEmpty) {
        _applyMessages(msgs, forSurface: 'remote');
      }
      _notify();
      return activeSessionId != null && activeSessionId!.isNotEmpty;
    }
    if (!silent) {
      showToast(r['error']?.toString() ?? '无法创建远端会话');
    }
    return false;
  }

  /// After OAuth login / catalog apply: switch to remote chat surface.
  /// After OAuth / model apply: refresh state; do NOT force-jump off Me.
  Future<void> goRemoteChatAfterOauth({String? toastMsg, bool jumpToChat = false}) async {
    await refreshAll();
    if (!pcConnected) {
      await goLocalChatAfterOauth(toastMsg: toastMsg, jumpToChat: jumpToChat);
      return;
    }
    if (jumpToChat) {
      await setSurface('remote');
      setTab(AppTab.chat);
    }
    if (toastMsg != null && toastMsg.isNotEmpty) showToast(toastMsg);
  }

  /// After phone-local OAuth / apply: keep user on current tab by default.
  Future<void> goLocalChatAfterOauth({String? toastMsg, bool jumpToChat = false}) async {
    await refreshAll();
    if (jumpToChat) {
      await setSurface('local');
      setTab(AppTab.chat);
    } else {
      // Ensure local surface for subsequent chat without leaving Me.
      if (surface != 'local') {
        await setSurface('local');
      }
    }
    if (toastMsg != null && toastMsg.isNotEmpty) showToast(toastMsg);
  }

  void setTab(AppTab t) {
    final wasMe = tab == AppTab.me;
    tab = t;
    drawerOpen = false;
    // Only reload settings when entering Me from another tab (not re-tap).
    if (t == AppTab.me && !wasMe) {
      mePanelGen++;
    }
    _syncApprovePoll();
    unawaited(() async {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('takton-tab', t.name);
    }());
    _notify();
  }

  void _syncApprovePoll() {
    _approvePoll?.cancel();
    _approvePoll = null;
    if (tab == AppTab.approve && pcConnected) {
      _approvePoll = Timer.periodic(const Duration(seconds: 4), (_) async {
        if (tab != AppTab.approve || !pcConnected) return;
        await loadApprovals();
      });
    }
  }

  void pushStatusCard({
    required String title,
    required String body,
    StatusCardKind kind = StatusCardKind.info,
    String? actionLabel,
    String? actionId,
    int ttlMs = 5200,
  }) {
    statusCards.removeWhere((c) => c.expired);
    final card = StatusCard(
      id: 'sc-${DateTime.now().microsecondsSinceEpoch}',
      title: title,
      body: body,
      kind: kind,
      actionLabel: actionLabel,
      actionId: actionId,
      ttlMs: ttlMs,
    );
    statusCards.insert(0, card);
    while (statusCards.length > 3) {
      statusCards.removeLast();
    }
    islandLive = true;
    islandText = title;
    islandKind = switch (kind) {
      StatusCardKind.stream => 'stream',
      StatusCardKind.conn => 'conn',
      StatusCardKind.agent => 'local',
      StatusCardKind.success => 'local',
      StatusCardKind.warn => 'conn',
      StatusCardKind.info => islandKind,
    };
    _notify();
    Future.delayed(Duration(milliseconds: ttlMs + 80), () {
      statusCards.removeWhere((c) => c.id == card.id || c.expired);
      _notify();
    });
  }

  void dismissStatusCard(String id) {
    statusCards.removeWhere((c) => c.id == id);
    _notify();
  }

  void handleStatusAction(String? actionId) {
    if (actionId == null) return;
    switch (actionId) {
      case 'reconnect':
        unawaited(forceReconnect());
        break;
      case 'open_approve':
        setTab(AppTab.approve);
        break;
      case 'open_remote':
        setTab(AppTab.remote);
        break;
      case 'open_me':
        setTab(AppTab.me);
        break;
      case 'open_chat':
        setTab(AppTab.chat);
        break;
    }
  }

  void toggleIslandExpanded() {
    islandExpanded = !islandExpanded;
    if (islandExpanded) {
      islandLive = true;
    }
    _notify();
  }

  void showToast(String m) {
    toast = m;
    toastShow = true;
    _notify();
    _toastTimer?.cancel();
    _toastTimer = Timer(const Duration(milliseconds: 2200), () {
      if (!hasListeners) return;
      toastShow = false;
      _notify();
    });
  }

  /// Throttle streaming UI updates. Host already coalesces ~40ms, so keep
  /// Flutter light (~1 frame) to avoid double ~80ms lag.
  void _notifyStream() {
    _streamDirty = true;
    if (_streamNotifyTimer?.isActive ?? false) return;
    _streamNotifyTimer = Timer(const Duration(milliseconds: 16), () {
      if (_streamDirty) {
        _streamDirty = false;
        notifyListeners();
      }
    });
  }

  /// Returns true if the message was accepted (or stop handled).
  /// Returns false when blocked (empty / cannot send) so UI keeps the draft.
  Future<bool> send([String? overrideText]) async {
    if (overrideText != null) input = overrideText;
    final text = input.trim();
    if (text.isEmpty && attachments.isEmpty) return false;
    // Local lightweight agent: works even when LLM not configured
    // Slash-only local shortcuts (e.g. /help) — bare Chinese never steals LLM turns.
    if (surface == 'local' && attachments.isEmpty && text.startsWith('/')) {
      final localReply = LocalAgent.tryHandle(
        text,
        pcConnected: pcConnected,
        llmReady: mode.localLlmReady || state['local_llm_ready'] == true,
        pathKind: pathProfile['last_kind']?.toString(),
        baseUrl: formBase,
      );
      if (localReply != null) {
        input = '';
        messages.add(ChatMsg(
          id: 'u-${DateTime.now().microsecondsSinceEpoch}',
          role: 'user',
          text: text,
        ));
        messages.add(ChatMsg(
          id: 'a-local-${DateTime.now().microsecondsSinceEpoch}',
          role: 'assistant',
          text: localReply,
          who: '本机 Agent',
          format: 'markdown',
        ));
        _saveSurfaceCache();
        pushStatusCard(
          title: '本机 Agent',
          body: '已处理指令，未请求云端模型',
          kind: StatusCardKind.agent,
        );
        pulseIsland(text: '本机指令', kind: 'local');
        _notify();
        return true;
      }
    }

    // Streaming uses dedicated stopGeneration() — never hijack send.
    if (streaming) {
      showToast('正在生成 · 请点红色停止按钮');
      return false;
    }
    if (!mode.canSend) {
      final msg = mode.fixHint.isEmpty
          ? mode.reason
          : '${mode.reason} · ${mode.fixHint}';
      showToast(msg.isEmpty ? '当前模式不可发送' : msg);
      if (mode.fixTab == 'me') setTab(AppTab.me);
      if (mode.fixTab == 'remote') setTab(AppTab.remote);
      return false;
    }

    if (surface == 'remote') {
      final ok = await ensureRemoteSession();
      if (!ok || activeSessionId == null || activeSessionId!.isEmpty) {
        return false;
      }
    }

    var userText = text.isEmpty ? '（见图片/附件）' : text;

    final pending = List<AttachFile>.from(attachments);
    attachments.clear();

    // Resolve path→bytes for every attachment before any upload/OCR
    final resolved = <AttachFile>[];
    for (final f in pending) {
      final bytes = await resolveAttachBytes(f);
      if (bytes == null || bytes.isEmpty) {
        showToast('无法读取 ${f.name} 的内容');
        continue;
      }
      resolved.add(f);
    }
    if (pending.isNotEmpty && resolved.isEmpty) {
      // Put failed attachments back so user can retry
      attachments.addAll(pending);
      showToast('附件内容全部无法读取，请重新选择');
      return false;
    }

    final previewImages = <Uint8List>[];
    final previewNames = <String>[];
    final nonImageNames = <String>[];
    for (final f in resolved) {
      if (f.isImage && f.bytes != null && f.bytes!.isNotEmpty) {
        previewImages.add(f.bytes!);
        previewNames.add(f.name);
      } else {
        nonImageNames.add(f.name);
      }
    }

    List<Map<String, dynamic>>? uploaded;
    final contentBlocks = <String>[];
    final failedUploads = <String>[];
    // Images sent to local vision models as OpenAI/Codex multimodal parts
    final multimodalImages = <Map<String, dynamic>>[];

    if (resolved.isNotEmpty) {
      if (surface == 'remote' && pcConnected) {
        uploaded = [];
        for (final f in resolved) {
          final bytes = f.bytes!;
          final up = await bridge.uploadFile(
            name: f.name,
            bytes: bytes,
            contentType: f.mime,
          );
          if (isOk(up)) {
            final result = up['result'] is Map
                ? Map<String, dynamic>.from(up['result'] as Map)
                : Map<String, dynamic>.from(up);
            // Also inject local text extract so PC agent has content even if URL fetch lags
            final textBlock = extractTextBlock(f, bytes);
            uploaded.add({
              'name': f.name,
              'filename': f.name,
              if (f.mime != null) 'content_type': f.mime,
              if (f.mime != null) 'type': f.mime,
              if (textBlock != null) 'text_content': textBlock,
              ...result,
            });
            if (textBlock != null) contentBlocks.add(textBlock);
          } else {
            failedUploads.add(f.name);
            showToast('上传 ${f.name} 失败: ${up['error'] ?? 'unknown'}');
          }
        }
        if (failedUploads.isNotEmpty && uploaded.isEmpty) {
          // restore all and abort
          attachments.addAll(resolved);
          showToast('附件上传全部失败，请检查网络后重试');
          return false;
        }
      } else if (surface == 'local') {
        final useVision = modelLikelyVision(
          llmModel,
          providerLabel: state['local_llm'] is Map
              ? ((state['local_llm'] as Map)['provider_label']?.toString() ?? '')
              : '',
          baseUrl: llmBase,
        );
        for (final f in resolved) {
          final bytes = f.bytes!;
          String? mediaPath;
          try {
            final saved = await bridge.saveMedia(
              name: f.name,
              bytes: bytes,
              contentType: f.mime,
              kind: f.isImage ? 'image' : 'file',
            );
            if (isOk(saved)) {
              final media = saved['media'] is Map
                  ? Map<String, dynamic>.from(saved['media'] as Map)
                  : saved;
              mediaPath = media['path']?.toString();
            } else {
              showToast('保存 ${f.name} 失败: ${saved['error'] ?? ''}');
            }
          } catch (e) {
            showToast('保存 ${f.name} 失败: $e');
          }

          if (f.isImage) {
            final b64 = imageToApiBase64(bytes);
            if (b64 != null) {
              multimodalImages.add({
                'mime': f.mime ?? 'image/jpeg',
                'data_b64': b64,
              });
            } else {
              showToast('${f.name} 过大，请压缩后重试');
            }
            if (useVision) {
              // Vision model receives real pixels via multimodalImages — no forced OCR.
              contentBlocks.add(
                  '### 图片 · ${f.name}\n(已作为多模态图片发送，请直接看图)');
            } else {
              // Blind models: built-in OCR tool (ocr.space free fallback / Azure if configured)
              try {
                islandText = 'OCR · ${f.name}';
                _notify();
                final args = <String, dynamic>{
                  if (mediaPath != null && mediaPath.isNotEmpty)
                    'media_path': mediaPath
                  else
                    'image_base64': base64Encode(bytes),
                  'hint': f.name,
                };
                final ocr = await bridge.runLocalTool('ocr_image', args);
                final result = ocr['result']?.toString() ?? '';
                if (result.isNotEmpty && !result.startsWith('[tool_error]')) {
                  contentBlocks.add('### OCR · ${f.name}\n$result');
                } else {
                  final err = ocr['error']?.toString() ?? result;
                  showToast('OCR ${f.name}: ${err.isEmpty ? "空结果" : err}');
                  if (mediaPath != null) {
                    contentBlocks.add(
                        '### 图片 · ${f.name}\n(OCR 失败) media_path=$mediaPath — 模型可再调 ocr_image');
                  }
                }
              } catch (e) {
                showToast('OCR ${f.name} 失败: $e');
              }
            }
          } else {
            final textBlock = extractTextBlock(f, bytes);
            if (textBlock != null) {
              contentBlocks.add(textBlock);
            } else {
              contentBlocks.add(
                  '### 附件 · ${f.name}\n(二进制文件，已保存${mediaPath != null ? "：$mediaPath" : ""}，无法直接解码文本)');
            }
          }
        }
        if (useVision && multimodalImages.isNotEmpty) {
          showToast('多模态看图 · ${multimodalImages.length} 张');
        }
      } else {
        // remote but not connected — still extract text for local-looking bubble
        for (final f in resolved) {
          final bytes = f.bytes!;
          final textBlock = extractTextBlock(f, bytes);
          if (textBlock != null) {
            contentBlocks.add(textBlock);
          } else if (f.isImage) {
            contentBlocks.add('### 图片 · ${f.name}\n(未连接 PC，图片仅本地预览)');
          } else {
            contentBlocks.add('### 附件 · ${f.name}\n(未连接 PC，无法上传)');
          }
        }
      }

      final names = resolved.map((e) => e.name).join('、');
      if (contentBlocks.isNotEmpty) {
        userText =
            '$userText\n\n[已附: $names]\n\n${contentBlocks.join('\n\n')}';
      } else if (previewImages.isNotEmpty) {
        userText = '$userText\n\n[已附图片: $names · 请结合图片理解用户意图]';
      } else {
        userText = '$userText\n\n[附件: $names]';
      }
    }

    // Bubble: short caption + previews; model gets full userText
    final displayText = text.isEmpty
        ? (previewImages.isNotEmpty
            ? '（图片）'
            : (resolved.isNotEmpty ? '（附件）' : '（见图片/附件）'))
        : text;

    input = '';
    final uid = 'u${DateTime.now().millisecondsSinceEpoch}';
    final aid = 'a${DateTime.now().millisecondsSinceEpoch}';
    final streamSurface = surface;
    final streamGen = ++_streamGen;
    messages.add(ChatMsg(
      id: uid,
      role: 'user',
      text: displayText,
      format: 'plain',
      images: previewImages,
      imageNames: previewNames,
      attachNames: nonImageNames,
      modelText: userText,
    ));
    messages.add(ChatMsg(
      id: aid,
      role: 'assistant',
      text: '',
      who: surface == 'remote' ? '远端 Agent · 流式' : '本机 · LLM',
      streaming: true,
      format: 'plain',
    ));
    streaming = true;
    islandLive = true;
    islandKind = 'stream';
    islandText = '生成中';
    pushStatusCard(
      title: surface == 'local' ? '本机生成中' : '远端生成中',
      body: userText.length > 48 ? '${userText.substring(0, 48)}…' : userText,
      kind: StatusCardKind.stream,
      ttlMs: 8000,
    );
    notifyListeners();

    try {
      final stream = streamSurface == 'local'
          ? bridge.streamLocalChat(
              userText,
              images: multimodalImages.isEmpty ? null : multimodalImages,
            )
          : bridge.streamRemoteChat(
              activeSessionId!,
              userText,
              attachments: uploaded,
            );
      var acc = '';
      var toolTrail = <String>[];
      await for (final chunk in stream) {
        // Aborted, switched surface, or a newer stream started
        if (!streaming ||
            streamGen != _streamGen ||
            surface != streamSurface) {
          break;
        }
        // Control frames from local agent SSE (status / tool)
        if (chunk.startsWith('\x01STATUS\x01')) {
          final detail = chunk.substring(8);
          if (detail.isNotEmpty) {
            islandLive = true;
            islandKind = 'stream';
            islandText = detail;
            _notifyStream();
          }
          continue;
        }
        if (chunk.startsWith('\x01TOOL\x01')) {
          final parts = chunk.substring(6).split('\x01');
          // phase | name | ok | preview
          final phase = parts.isNotEmpty ? parts[0] : '';
          final name = parts.length > 1 ? parts[1] : 'tool';
          final okStr = parts.length > 2 ? parts[2] : '';
          final preview = parts.length > 3 ? parts[3] : '';
          if (phase == 'start') {
            islandText = '工具 · $name';
            toolTrail.add('· `$name` …');
          } else if (phase == 'end') {
            final mark = okStr == '0' ? '✗' : '✓';
            final short = preview.length > 48
                ? '${preview.substring(0, 48)}…'
                : preview;
            final line = short.isEmpty
                ? '· `$name` $mark'
                : '· `$name` $mark $short';
            final idx = toolTrail.lastIndexWhere((e) => e.contains('`$name`'));
            if (idx >= 0) {
              toolTrail[idx] = line;
            } else {
              toolTrail.add(line);
            }
            islandText = '工具 · $name $mark';
            // Auto-play Microsoft TTS written by voice_speak tool
            if (voiceOn && name.contains('voice')) {
              final path = VoiceService.extractTtsPath(preview);
              if (path != null) {
                unawaited(VoiceService.instance.playFilePath(path).then((ok) {
                  if (ok) {
                    islandText = '正在朗读…';
                    _notifyStream();
                  }
                }));
              }
            }
          }
          final display = toolTrail.isEmpty
              ? acc
              : '${toolTrail.join('\n')}${acc.isEmpty ? '' : '\n\n$acc'}';
          final target =
              streamSurface == 'local' ? _localMsgCache : _remoteMsgCache;
          if (surface == streamSurface) {
            final i = messages.indexWhere((m) => m.id == aid);
            if (i >= 0) {
              messages[i].text = display;
              messages[i].format = 'markdown';
              messages[i].who = '本机 Agent';
              _notifyStream();
            }
          } else {
            final i = target.indexWhere((m) => m.id == aid);
            if (i >= 0) {
              target[i].text = display;
              target[i].format = 'markdown';
            }
          }
          continue;
        }
        if (chunk.startsWith('\x00')) {
          acc = chunk.substring(1);
        } else {
          acc += chunk;
        }
        final display = toolTrail.isEmpty
            ? acc
            : '${toolTrail.join('\n')}${acc.isEmpty ? '' : '\n\n$acc'}';
        // Apply to the surface cache that owns this stream
        final target =
            streamSurface == 'local' ? _localMsgCache : _remoteMsgCache;
        // Live list only if still on same surface
        if (surface == streamSurface) {
          final i = messages.indexWhere((m) => m.id == aid);
          if (i >= 0) {
            messages[i].text = display;
            // upgrade to markdown mid-stream if markers appear
            if (messages[i].format != 'markdown' &&
                (display.contains('```') ||
                    display.contains('**') ||
                    display.contains('](') ||
                    toolTrail.isNotEmpty)) {
              messages[i].format = 'markdown';
            }
            if (toolTrail.isNotEmpty) {
              messages[i].who = '本机 Agent';
            }
            _notifyStream();
          }
        } else {
          // still update cache so when user returns they see partial
          final i = target.indexWhere((m) => m.id == aid);
          if (i >= 0) {
            target[i].text = display;
            target[i].streaming = false;
          }
        }
      }
      // Keep tool trail above final answer if tools ran
      if (toolTrail.isNotEmpty && acc.isNotEmpty) {
        acc = '${toolTrail.join('\n')}\n\n$acc';
        if (surface == streamSurface) {
          final i = messages.indexWhere((m) => m.id == aid);
          if (i >= 0) {
            messages[i].text = acc;
            messages[i].format = 'markdown';
            messages[i].who = '本机 Agent';
          }
        }
      }
      // Finalize on the owning surface
      void finalize(List<ChatMsg> list) {
        final i = list.indexWhere((m) => m.id == aid);
        if (i < 0) return;
        list[i].streaming = false;
        if (list[i].text.isEmpty && streamGen == _streamGen) {
          list[i].text = streamSurface == 'local'
              ? '（无模型输出）请检查本机 LLM 配置或切换远端 Agent'
              : '（无模型输出）PC Agent 可能未就绪';
        }
      }

      if (surface == streamSurface) {
        finalize(messages);
        final i = messages.indexWhere((m) => m.id == aid);
        if (i >= 0 &&
            messages[i].text.startsWith('（无模型输出）') &&
            streamGen == _streamGen) {
          showToast(messages[i].text);
        }
      }
      finalize(streamSurface == 'local' ? _localMsgCache : _remoteMsgCache);
    } catch (e) {
      if (streamGen == _streamGen && surface == streamSurface) {
        final i = messages.indexWhere((m) => m.id == aid);
        if (i >= 0) {
          messages[i].streaming = false;
          messages[i].text = e.toString();
        }
        showToast(e.toString());
      }
    } finally {
      if (streamGen == _streamGen) {
        streaming = false;
        islandLive = false;
        islandKind = surface == 'remote' ? 'conn' : 'local';
        islandText = surface == 'remote' ? '已连 PC 就绪' : '本机 就绪';
        if (surface == streamSurface) {
          _saveSurfaceCache();
        }
        notifyListeners();
      }
    }
    return true;
  }

  /// Re-send the last user message (ChatGPT-style regenerate).
  Future<void> regenerateLast() async {
    if (streaming) {
      await _abortStream(toastMsg: '已停止');
    }
    ChatMsg? lastUser;
    for (var i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role == 'user') {
        lastUser = messages[i];
        break;
      }
    }
    final payload = (lastUser?.modelText ?? lastUser?.text ?? '').trim();
    if (lastUser == null || payload.isEmpty) {
      showToast('没有可重新生成的消息');
      return;
    }
    // Drop last user + trailing assistant so send() doesn't duplicate bubbles
    while (messages.isNotEmpty &&
        (messages.last.role == 'assistant' || messages.last.id == lastUser.id)) {
      messages.removeLast();
    }
    _saveSurfaceCache();
    // Resend full model payload (includes OCR / file text)
    await send(payload);
  }

  Future<void> copyMessageText(String text) async {
    // UI uses Clipboard; controller only toasts for consistency
    showToast('已复制');
  }


  Future<void> clearLocalLlm() async {
    final r = await bridge.localConfigClear();
    if (!isOk(r)) {
      showToast(r['error']?.toString() ?? '清除失败');
      return;
    }
    llmBase = '';
    llmKey = '';
    llmModel = '';
    llmHasKey = false;
    llmKeyMasked = '';
    pushStatusCard(
      title: '本机模型已清除',
      body: '不再显示直连模型名，请重新配置',
      kind: StatusCardKind.info,
    );
    showToast('已清除本机 LLM 配置');
    await refreshAll();
  }

  Future<void> newChat() async {
    if (streaming) {
      await _abortStream(toastMsg: '已停止生成');
    }
    if (surface == 'local') {
      await bridge.localHistoryClear();
      _localMsgCache.clear();
      messages.clear();
      await loadLocalMsgs();
      showToast('已新建本机对话');
    } else {
      if (!pcConnected) {
        setTab(AppTab.remote);
        showToast('请先连接 PC');
        return;
      }
      final r = await bridge.sessionCreate();
      if (isOk(r)) {
        final id = r['session'] is Map
            ? (r['session'] as Map)['id']?.toString()
            : r['id']?.toString();
        activeSessionId = id;
        _applyMessages([], forSurface: 'remote');
        // light refresh of session list only
        unawaited(refreshAll());
        activeSessionId ??= id;
        showToast('已新建远端会话');
      } else {
        showToast(r['error']?.toString() ?? '新建失败');
      }
    }
    drawerOpen = false;
    _notify();
  }

  Future<void> openSession(String id) async {
    activeSessionId = id;
    surface = 'remote';
    drawerOpen = false;
    notifyListeners();
    await _applySwitchSurface('remote',
        ensureSession: false, sessionId: id);
    _notify();
  }

  Future<void> pinSession(String id, bool pinned) async {
    final r = await bridge.sessionPin(id, pinned);
    if (isOk(r)) {
      showToast(pinned ? '已置顶' : '已取消置顶');
    } else {
      showToast(r['error']?.toString() ?? '置顶失败');
    }
    await refreshAll();
  }

  Future<void> renameSession(String id, String title) async {
    final r = await bridge.sessionRename(id, title);
    if (!isOk(r)) {
      showToast(r['error']?.toString() ?? '改名失败');
    } else {
      showToast(r['note']?.toString() ?? '已改名');
    }
    await refreshAll();
  }

  Future<void> deleteSession(String id) async {
    final r = await bridge.sessionDelete(id);
    if (!isOk(r)) {
      showToast(r['error']?.toString() ?? '删除失败');
      return;
    }
    if (id == '__local__' || localSession?.id == id) {
      await loadLocalMsgs();
      showToast('已清空本机历史');
    } else {
      if (activeSessionId == id) {
        activeSessionId = null;
        messages.clear();
        _remoteMsgCache.clear();
      }
      showToast('已删除远端会话');
      await refreshAll();
      if (surface == 'remote' && activeSessionId == null) {
        if (remoteSessions.isNotEmpty) {
          await openSession(remoteSessions.first.id);
        } else {
          _applyMessages([], forSurface: 'remote');
        }
      }
      _notify();
      return;
    }
    await refreshAll();
  }

  Future<void> connectPc() async {
    final cands = _candidateList(extra: formBase);
    final r = await bridge.connect(
      baseUrl: formBase,
      candidates: cands,
      email: formEmail.isEmpty ? null : formEmail,
      password: formPass.isEmpty ? null : formPass,
    );
    if (isOk(r)) {
      final base = r['base_url']?.toString();
      if (base != null && base.isNotEmpty) formBase = base;
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('takton-form-base', formBase);
      if (formEmail.isNotEmpty) {
        await prefs.setString('takton-form-email', formEmail);
      }
      await _persistPathFrom(r);
      final kind = r['path_kind']?.toString() ?? '';
      showToast(kind.isEmpty ? '已连接 PC' : '已连接 PC · $kind');
      await refreshAll();
      await refreshPath();
    } else {
      showToast(r['error']?.toString() ?? '连接失败');
    }
  }

  Future<void> disconnectPc() async {
    await bridge.disconnect();
    showToast('已断开');
    await refreshAll();
  }

  // ── M1/M2/M3 Pairing ────────────────────────────────────────────────────

  Future<void> refreshMesh() async {
    try {
      final r = await bridge.meshStatus();
      if (isOk(r)) {
        mesh = Map<String, dynamic>.from(r);
        _notify();
      }
    } catch (_) {}
  }

  Future<void> setMeshMode(String mode) async {
    final r = await bridge.meshSet(mode: mode);
    if (isOk(r)) {
      mesh = Map<String, dynamic>.from(r);
      showToast('远程访问: ${r['mode'] ?? mode}');
      _notify();
    } else {
      showToast(r['error']?.toString() ?? '设置失败');
    }
  }

  Future<void> refreshPairedDevices() async {
    try {
      final r = await bridge.pairDevices();
      if (isOk(r)) {
        pairedDevices = ((r['devices'] as List?) ?? [])
            .whereType<Map>()
            .map((e) => Map<String, dynamic>.from(e))
            .toList();
        _notify();
      }
    } catch (_) {}
  }


  /// PC one-time setup: store auth key & start embedded mesh (silent after this).
  Future<bool> enableRemoteOnce(String authKey) async {
    final key = authKey.trim();
    if (key.isEmpty) {
      showToast('请粘贴远程访问密钥');
      return false;
    }
    try {
      final r = await bridge.meshAuth(authKey: key);
      if (!isOk(r)) {
        showToast(r['error']?.toString() ?? '启用失败');
        return false;
      }
      await bridge.meshSet(mode: 'auto');
      await bridge.meshEmbedStart(role: 'pc');
      await refreshMesh();
      showToast(r['detail']?.toString() ?? '远程已启用 · 之后扫码即可');
      return true;
    } catch (e) {
      showToast('$e');
      return false;
    }
  }

  String _redactPairQr(String raw) {
    // Drop tsk=… so join keys never linger on disk.
    var s = raw.trim().replaceAll(RegExp(r'([?&])tsk=[^&]*'), '');
    s = s.replaceAll('?&', '?');
    if (s.endsWith('?') || s.endsWith('&')) {
      s = s.substring(0, s.length - 1);
    }
    return s;
  }

  /// PC host only: generate QR for the phone to scan.
  /// (Not shown in the phone app UI — kept for PC workbench / host shell.)
  /// Mesh defaults to auto; tsnet starts silently when a one-time remote key
  /// was configured (or env). QR may include phone join key.
  Future<void> startPairing({String? meshMode}) async {
    pairBusy = true;
    _notify();
    try {
      // Product default: dual path always (LAN prefer, remote fallback).
      final mode = meshMode ?? 'auto';
      if (mode != 'off') {
        try {
          await bridge.meshSet(mode: mode);
        } catch (_) {}
      }
      final r = await bridge.pairStart(
        mesh: mode,
        requireConfirm: mesh['require_pair_confirm'] == true,
      );
      if (!isOk(r)) {
        showToast(r['error']?.toString() ?? '无法生成配对码');
        return;
      }
      activePair = Map<String, dynamic>.from(r);
      lastPairQr = r['qr']?.toString() ?? '';
      final prefs = await SharedPreferences.getInstance();
      // Store redacted form if possible — full QR stays in memory for display only.
      await prefs.setString('takton-last-pair-qr', _redactPairQr(lastPairQr));
      await refreshMesh();
      _pairPoll?.cancel();
      final pairId = r['pair_id']?.toString() ?? '';
      _pairPoll = Timer.periodic(const Duration(seconds: 2), (_) async {
        if (pairId.isEmpty) return;
        final st = await bridge.pairStatus(pairId);
        if (!isOk(st)) return;
        final status = st['status'];
        if (status is Map) {
          activePair = {
            ...?activePair,
            'status': Map<String, dynamic>.from(status),
          };
          if (status['claimed'] == true) {
            _pairPoll?.cancel();
            showToast('手机已配对');
            await refreshPairedDevices();
            activePair = null;
          }
          _notify();
        }
      });
      // Auto-expire UI after TTL (default 300s)
      final ttl = (r['ttl_secs'] is num) ? (r['ttl_secs'] as num).toInt() : 300;
      Future.delayed(Duration(seconds: ttl + 5), () {
        if (activePair?['pair_id'] == pairId) {
          activePair = null;
          _pairPoll?.cancel();
          _notify();
        }
      });
      showToast(r['hint']?.toString() ?? '配对码已生成 · ${ttl}s 内有效');
    } finally {
      pairBusy = false;
      _notify();
    }
  }

  Future<void> confirmPair() async {
    final id = activePair?['pair_id']?.toString();
    if (id == null || id.isEmpty) return;
    final r = await bridge.pairConfirm(id);
    if (isOk(r)) {
      showToast('已允许此手机');
      activePair = {...?activePair, 'confirmed': true};
      _notify();
    } else {
      showToast(r['error']?.toString() ?? '确认失败');
    }
  }

  Future<void> cancelPair() async {
    final id = activePair?['pair_id']?.toString();
    if (id != null && id.isNotEmpty) {
      await bridge.pairCancel(id);
    }
    _pairPoll?.cancel();
    activePair = null;
    _notify();
  }

  /// Phone role: apply scanned / pasted QR (M1 + M3 claim+connect).
  Future<bool> applyPairQr(String raw) async {
    final qr = raw.trim();
    if (qr.isEmpty) {
      showToast('请粘贴或扫描配对码');
      return false;
    }
    pairBusy = true;
    _notify();
    try {
      // M3: best-effort mesh up before claim (no-op on web)
      try {
        // ignore: avoid_dynamic_calls
        await MeshRuntime.instance.up(hostname: 'takton-phone');
      } catch (_) {}

      final r = await bridge.pairApply(
        qr: qr,
        deviceName: 'Takton Phone',
        email: formEmail.isEmpty ? null : formEmail,
        password: formPass.isEmpty ? null : formPass,
      );
      if (!isOk(r)) {
        showToast(r['error']?.toString() ?? '配对失败');
        return false;
      }
      final base = r['base_url']?.toString();
      if (base != null && base.isNotEmpty) {
        formBase = base;
      }
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('takton-form-base', formBase);
      await prefs.setString('takton-last-pair-qr', qr);
      if (formEmail.isNotEmpty) {
        await prefs.setString('takton-form-email', formEmail);
      }
      if (r['device_token'] != null) {
        await prefs.setString(
            'takton-device-token', r['device_token'].toString());
      }
      // Never persist phone join key (tsk) to disk after apply.
      lastPairQr = _redactPairQr(qr);
      await prefs.setString('takton-last-pair-qr', lastPairQr);
      await _persistPathFrom(r);
      // Store multi endpoints for offline reconnect
      final eps = r['endpoints'];
      if (eps is List) {
        await prefs.setStringList(
          'takton-path-candidates',
          eps.map((e) => e.toString()).where((s) => s.isNotEmpty).toList(),
        );
      }
      await refreshAll();
      await refreshPairedDevices();
      await refreshPath();
      await refreshMesh();
      if (r['authenticated'] == true) {
        needsManualLogin = false;
        final kind = r['path_kind']?.toString() ?? '';
        final seamless = r['seamless'] == true;
        showToast(
          seamless
              ? (kind.isEmpty ? '已连接 · 外出自动切换' : '已连接 · $kind')
              : (kind.isEmpty ? '配对成功 · 已连接 PC' : '配对成功 · $kind'),
        );
        pushStatusCard(
          title: seamless ? '已连接 PC' : '配对成功',
          body: seamless
              ? (kind.isEmpty ? '外出网络自动切换已就绪' : '路径 $kind')
              : (kind.isEmpty ? '远端 Agent 可用' : '路径 $kind'),
          kind: StatusCardKind.success,
          actionLabel: '对话',
          actionId: 'open_chat',
          ttlMs: 6000,
        );
        surface = 'remote';
        await prefs.setString('takton-chat-mode', 'remote');
        await _applySwitchSurface('remote', ensureSession: true);
      } else if (r['deferred_claim'] == true) {
        needsManualLogin = false;
        showToast(r['hint']?.toString() ?? '已保存 · 网络可用后自动完成');
        pushStatusCard(
          title: '配对已保存',
          body: '网络可用后将自动完成连接',
          kind: StatusCardKind.info,
          actionLabel: '立即重试',
          actionId: 'reconnect',
          ttlMs: 10000,
        );
      } else {
        needsManualLogin = true;
        final hint = r['hint']?.toString() ??
            r['login_error']?.toString() ??
            '配对完成 · 请填写账号登录';
        showToast(hint);
        pushStatusCard(
          title: '需要登录',
          body: hint,
          kind: StatusCardKind.warn,
          actionLabel: '去登录',
          actionId: 'open_remote',
          ttlMs: 12000,
        );
        setTab(AppTab.remote);
      }
      return true;
    } finally {
      pairBusy = false;
      _notify();
    }
  }

  Future<void> revokePaired(String id) async {
    final r = await bridge.pairRevoke(id);
    if (isOk(r)) {
      showToast('已解除配对');
      await refreshPairedDevices();
    } else {
      showToast(r['error']?.toString() ?? '解绑失败');
    }
  }

  /// User-triggered reconnect (always toast).
  Future<void> forceReconnect() async {
    showToast('正在重试连接…');
    islandLive = true;
    islandText = '重连中';
    islandKind = 'conn';
    _notify();
    await tryAutoReconnect();
    if (!pcConnected) {
      showToast('仍未连上 · 可扫码或手动登录');
      needsManualLogin = formBase.isNotEmpty;
    } else {
      needsManualLogin = false;
      showToast('已重新连接 PC');
    }
    _notify();
  }

  /// M3/M4: reconnect with multi-endpoint probe (LAN → host → TS).
  Future<void> tryAutoReconnect() async {
    final prefs = await SharedPreferences.getInstance();
    final base = prefs.getString('takton-form-base');
    final stored = prefs.getStringList('takton-path-candidates') ?? const [];
    if ((base == null || base.isEmpty) && stored.isEmpty) {
      // Still try path profile from Rust store
    } else if (base != null && base.isNotEmpty) {
      formBase = base;
    }
    try {
      try {
        await MeshRuntime.instance.up(hostname: 'takton-phone');
      } catch (_) {}
      final cands = <String>[
        if (base != null && base.isNotEmpty) base,
        ...stored,
      ];
      final r = await bridge.pathReconnect(
        candidates: cands,
        email: formEmail.isEmpty ? null : formEmail,
        password: formPass.isEmpty ? null : formPass,
        claim: true,
      );
      if (isOk(r) && r['authenticated'] == true) {
        final b = r['base_url']?.toString();
        if (b != null && b.isNotEmpty) {
          formBase = b;
          await prefs.setString('takton-form-base', formBase);
        }
        await _persistPathFrom(r);
        final kind = r['path_kind']?.toString() ?? '';
        showToast(kind.isEmpty ? '已自动重连 PC' : '已重连 · $kind');
        await refreshAll();
        await refreshPath();
        return;
      }
      // Fallback: single base connect
      if (base != null && base.isNotEmpty) {
        final r2 = await bridge.connect(baseUrl: base, candidates: cands);
        if (isOk(r2)) {
          showToast('已自动重连 PC');
          await refreshAll();
          return;
        }
      }
      final auto = await bridge.autoLogin();
      if (isOk(auto)) {
        showToast('已自动重连 PC');
        await refreshAll();
      }
    } catch (_) {}
  }

  Future<void> refreshPath() async {
    try {
      final r = await bridge.pathStatus();
      if (isOk(r)) {
        pathProfile = Map<String, dynamic>.from(r);
        final active = r['active_url']?.toString();
        if (active != null && active.isNotEmpty) formBase = active;
        _notify();
      }
    } catch (_) {}
  }

  List<String> _candidateList({String? extra}) {
    final out = <String>[];
    void add(String? s) {
      final v = s?.trim() ?? '';
      if (v.isEmpty) return;
      if (!out.contains(v)) out.add(v);
    }
    add(extra);
    add(formBase);
    add(pathProfile['active_url']?.toString());
    final eps = pathProfile['endpoints'];
    if (eps is List) {
      for (final e in eps) {
        if (e is Map) add(e['url']?.toString());
        else add(e?.toString());
      }
    }
    return out;
  }

  Future<void> _persistPathFrom(Map<String, dynamic> r) async {
    final prefs = await SharedPreferences.getInstance();
    final path = r['path'];
    if (path is Map) {
      pathProfile = Map<String, dynamic>.from(path);
      final eps = path['endpoints'];
      if (eps is List) {
        final list = <String>[];
        for (final e in eps) {
          if (e is Map && e['url'] != null) list.add(e['url'].toString());
        }
        if (list.isNotEmpty) {
          await prefs.setStringList('takton-path-candidates', list);
        }
      }
    }
    final eps2 = r['endpoints'];
    if (eps2 is List) {
      await prefs.setStringList(
        'takton-path-candidates',
        eps2.map((e) => e.toString()).where((s) => s.isNotEmpty).toList(),
      );
    }
  }

  /// Wi‑Fi ↔ 5G / interface change → re-probe endpoints + deferred claim.
  Future<void> onNetworkPathChanged() async {
    if (pathBusy) return;
    pathBusy = true;
    try {
      await refreshMesh();
      final r = await bridge.pathReconnect(
        candidates: _candidateList(),
        email: formEmail.isEmpty ? null : formEmail,
        password: formPass.isEmpty ? null : formPass,
        claim: true,
      );
      if (isOk(r) && r['authenticated'] == true) {
        final b = r['base_url']?.toString();
        if (b != null && b.isNotEmpty) {
          formBase = b;
          final prefs = await SharedPreferences.getInstance();
          await prefs.setString('takton-form-base', formBase);
        }
        await _persistPathFrom(r);
        final kind = r['path_kind']?.toString() ?? 'path';
        if (!pcConnected) {
          showToast('网络已切换 · 已重连 ($kind)');
        }
        pushStatusCard(
          title: '网络已切换',
          body: '已重连 · $kind',
          kind: StatusCardKind.conn,
          actionLabel: '刷新',
          actionId: 'reconnect',
        );
        await refreshAll();
      }
      await refreshPath();
    } catch (_) {
    } finally {
      pathBusy = false;
      _notify();
    }
  }

  /// Periodic health: if remote surface but unauthenticated, try reconnect.
  Future<void> pathHealthTick() async {
    if (pathBusy) return;
    try {
      await MeshRuntime.instance.checkNow();
      if (!pcConnected) {
        await tryAutoReconnect();
        return;
      }
      // Soft probe — if best path differs, flip without full re-login noise
      final probe = await bridge.pathProbe(candidates: _candidateList());
      if (!isOk(probe)) return;
      final best = probe['best'];
      final bestUrl = best is Map ? best['url']?.toString() : null;
      if (bestUrl != null &&
          bestUrl.isNotEmpty &&
          bestUrl.replaceAll(RegExp(r'/+\$'), '') != formBase.replaceAll(RegExp(r'/+\$'), '')) {
        // Prefer better path (usually LAN when home)
        final r = await bridge.pathReconnect(
          candidates: _candidateList(extra: bestUrl),
          claim: false,
        );
        if (isOk(r) && r['authenticated'] == true) {
          final b = r['base_url']?.toString();
          if (b != null && b.isNotEmpty) formBase = b;
          await _persistPathFrom(r);
          await refreshAll();
        }
      }
    } catch (_) {}
  }

  /// App resume / tab focus hook.
  Future<void> onAppResumed() async {
    await MeshRuntime.instance.checkNow();
    if (!pcConnected) {
      await tryAutoReconnect();
    } else {
      await pathHealthTick();
    }
  }

  Future<void> saveLocalLlm() async {
    final r = await bridge.localConfigSet({
      'base_url': llmBase,
      'model': llmModel,
      if (llmKey.isNotEmpty) 'api_key': llmKey,
    });
    await refreshAll();
    if (isOk(r) && mode.canSend) {
      showToast('本机模型已就绪');
      llmKey = '';
    } else if (isOk(r)) {
      showToast('已保存 · 请补全 base_url / api_key / model');
    } else {
      showToast(r['error']?.toString() ?? '保存失败');
    }
  }

  Future<void> testLocalLlm() async {
    final r = await bridge.localTest({
      'base_url': llmBase,
      'model': llmModel,
      if (llmKey.isNotEmpty) 'api_key': llmKey,
    });
    if (isOk(r)) {
      final result = r['result'] is Map ? r['result'] as Map : r;
      showToast(result['message']?.toString() ?? '连通正常');
    } else {
      showToast(r['error']?.toString() ?? '测试失败');
    }
    await refreshMode();
  }

  Future<void> toggleTheme() async {
    dark = !dark;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('takton-theme', dark ? 'dark' : 'light');
    _notify();
  }

  Future<void> clearLocalUi() async {
    if (streaming) {
      await _abortStream(toastMsg: '已停止');
    }
    final r = await bridge.localHistoryClear();
    _localMsgCache.clear();
    if (surface == 'local') {
      messages.clear();
      await loadLocalMsgs();
    }
    if (isOk(r) || r['ok'] == null) {
      showToast('已清空本机会话区');
    } else {
      showToast(r['error']?.toString() ?? '清空失败');
    }
    _notify();
  }

  @override
  void dispose() {
    _clockTimer?.cancel();
    _approvePoll?.cancel();
    _streamNotifyTimer?.cancel();
    _toastTimer?.cancel();
    _pairPoll?.cancel();
    _meshPoll?.cancel();
    _pathPoll?.cancel();
    MeshRuntime.instance.dispose();
    bridge.dispose();
    super.dispose();
  }
}
