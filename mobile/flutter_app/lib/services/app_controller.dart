import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../bridge/takton_bridge.dart';
import '../models/app_models.dart';

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
  String clock = '';
  bool voiceOn = true;
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

  Timer? _clockTimer;
  Timer? _approvePoll;
  Timer? _streamNotifyTimer;
  bool _booted = false;
  bool _notifyScheduled = false;
  int _switchGen = 0;
  int _streamGen = 0;
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
    final snap = messages.map((m) => ChatMsg(
          id: m.id,
          role: m.role,
          text: m.text,
          who: m.who,
          streaming: false,
          format: m.format,
        ));
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
      ..addAll(src.map((m) => ChatMsg(
            id: m.id,
            role: m.role,
            text: m.text,
            who: m.who,
            streaming: false,
            format: m.format,
          )));
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
        ..addAll(list);
    } else {
      _remoteMsgCache
        ..clear()
        ..addAll(list);
    }
    if (surface == forSurface) {
      messages
        ..clear()
        ..addAll(list.map((m) => ChatMsg(
              id: m.id,
              role: m.role,
              text: m.text,
              who: m.who,
              format: m.format,
            )));
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
      Future.delayed(const Duration(milliseconds: 2200), () {
        toastShow = false;
        _notify();
      });
    }
  }

  Future<void> boot() async {
    if (_booted) return;
    _booted = true;
    final prefs = await SharedPreferences.getInstance();
    surface = prefs.getString('takton-chat-mode') ?? 'local';
    dark = prefs.getString('takton-theme') == 'dark';
    voiceOn = prefs.getBool('takton-voice') ?? true;
    cameraOn = prefs.getBool('takton-camera') ?? true;
    _tickClock();
    _clockTimer =
        Timer.periodic(const Duration(seconds: 30), (_) => _tickClock());
    await refreshAll();
    if (!pcConnected && surface == 'remote') {
      surface = 'local';
      unawaited(prefs.setString('takton-chat-mode', 'local'));
    }
    // Single Rust round-trip for mode + history
    await _applySwitchSurface(surface, ensureSession: false);
    if (bridgeKind == 'http-fallback') {
      showToast('引擎 FFI 未加载 · 已回落 HTTP（$bridgeKind）');
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

  void addAttachName(String name) {
    addAttach(AttachFile(name: name));
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
    if (list.isEmpty) {
      list.add(ChatMsg(
        id: 'w-local',
        role: 'assistant',
        text:
            '你好。本机对话已就绪。在「我的 → LLM 设置」配置 API Key 后即可流式聊天；ChatGPT OAuth 请用顶栏「远端 Agent」。',
        who: '本机 · LLM',
      ));
    }
    _applyMessages(list, forSurface: 'local');
    _notify();
  }

  Future<void> loadRemoteMsgs(String id) async {
    final h = await bridge.messages(id);
    final list = _parseUiMessages(
        (h['messages'] as List?) ?? (h['items'] as List?) ?? []);
    if (list.isEmpty) {
      list.add(ChatMsg(
        id: 'w-remote',
        role: 'assistant',
        text: '远端会话已打开。消息将经 PC Agent 工具链处理。',
        who: '远端 Agent',
      ));
    }
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
      if (messages.isEmpty) {
        messages.add(ChatMsg(
          id: 'w-local',
          role: 'assistant',
          text: '加载本机对话…',
          who: '本机 · LLM',
        ));
      }
    } else {
      islandText = '已连 PC';
      islandKind = 'conn';
      _loadSurfaceCache('remote');
      if (messages.isEmpty) {
        messages.add(ChatMsg(
          id: 'w-remote',
          role: 'assistant',
          text: '加载远端会话…',
          who: '远端 Agent',
        ));
      }
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
  Future<void> goRemoteChatAfterOauth({String? toastMsg}) async {
    await refreshAll();
    if (!pcConnected) {
      setTab(AppTab.remote);
      showToast(toastMsg ?? '请先连接 PC');
      return;
    }
    await setSurface('remote');
    setTab(AppTab.chat);
    if (toastMsg != null && toastMsg.isNotEmpty) showToast(toastMsg);
  }

  void setTab(AppTab t) {
    tab = t;
    drawerOpen = false;
    _syncApprovePoll();
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

  void showToast(String m) {
    toast = m;
    toastShow = true;
    _notify();
    Future.delayed(const Duration(milliseconds: 2200), () {
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
    if (streaming) {
      await _abortStream();
      _notify();
      return true;
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

    var userText = text.isEmpty ? '（见附件）' : text;
    final pending = List<AttachFile>.from(attachments);
    attachments.clear();

    List<Map<String, dynamic>>? uploaded;
    if (pending.isNotEmpty) {
      if (surface == 'remote' && pcConnected) {
        uploaded = [];
        for (final f in pending) {
          Uint8List? bytes = f.bytes;
          if (bytes != null && bytes.isNotEmpty) {
            final up = await bridge.uploadFile(
              name: f.name,
              bytes: bytes,
              contentType: f.mime,
            );
            if (isOk(up)) {
              final result = up['result'] is Map
                  ? Map<String, dynamic>.from(up['result'] as Map)
                  : up;
              uploaded.add({
                'name': f.name,
                if (f.mime != null) 'content_type': f.mime,
                ...result,
              });
            } else {
              showToast('上传 ${f.name} 失败: ${up['error'] ?? 'unknown'}');
            }
          } else {
            uploaded.add({
              'name': f.name,
              if (f.mime != null) 'content_type': f.mime
            });
          }
        }
        if (uploaded.isNotEmpty) {
          userText =
              '$userText\n\n[附件: ${pending.map((e) => e.name).join('、')}]';
        }
      } else {
        userText =
            '$userText\n\n[附件: ${pending.map((e) => e.name).join('、')}]';
      }
    }

    input = '';
    final uid = 'u${DateTime.now().millisecondsSinceEpoch}';
    final aid = 'a${DateTime.now().millisecondsSinceEpoch}';
    final streamSurface = surface;
    final streamGen = ++_streamGen;
    messages.add(ChatMsg(id: uid, role: 'user', text: userText, format: 'plain'));
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
    notifyListeners();

    try {
      final stream = streamSurface == 'local'
          ? bridge.streamLocalChat(userText)
          : bridge.streamRemoteChat(
              activeSessionId!,
              userText,
              attachments: uploaded,
            );
      var acc = '';
      await for (final chunk in stream) {
        // Aborted, switched surface, or a newer stream started
        if (!streaming ||
            streamGen != _streamGen ||
            surface != streamSurface) {
          break;
        }
        if (chunk.startsWith('\x00')) {
          acc = chunk.substring(1);
        } else {
          acc += chunk;
        }
        // Apply to the surface cache that owns this stream
        final target =
            streamSurface == 'local' ? _localMsgCache : _remoteMsgCache;
        // Live list only if still on same surface
        if (surface == streamSurface) {
          final i = messages.indexWhere((m) => m.id == aid);
          if (i >= 0) {
            messages[i].text = acc;
            // upgrade to markdown mid-stream if markers appear
            if (messages[i].format != 'markdown' &&
                (acc.contains('```') ||
                    acc.contains('**') ||
                    acc.contains(']('))) {
              messages[i].format = 'markdown';
            }
            _notifyStream();
          }
        } else {
          // still update cache so when user returns they see partial
          final i = target.indexWhere((m) => m.id == aid);
          if (i >= 0) {
            target[i].text = acc;
            target[i].streaming = false;
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

  Future<void> newChat() async {
    if (surface == 'local') {
      await bridge.localHistoryClear();
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
        final welcome = ChatMsg(
          id: 'w',
          role: 'assistant',
          text: '新远端会话已创建。',
          who: '远端 Agent',
        );
        _applyMessages([welcome], forSurface: 'remote');
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
    await bridge.sessionPin(id, pinned);
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
          _applyMessages([
            ChatMsg(
              id: 'w-remote',
              role: 'assistant',
              text: '远端会话已清空。发送消息将自动新建。',
              who: '远端 Agent',
            )
          ], forSurface: 'remote');
        }
      }
      _notify();
      return;
    }
    await refreshAll();
  }

  Future<void> connectPc() async {
    final r = await bridge.connect(
      baseUrl: formBase,
      email: formEmail.isEmpty ? null : formEmail,
      password: formPass.isEmpty ? null : formPass,
    );
    if (isOk(r)) {
      showToast('已连接 PC');
      await refreshAll();
    } else {
      showToast(r['error']?.toString() ?? '连接失败');
    }
  }

  Future<void> disconnectPc() async {
    await bridge.disconnect();
    showToast('已断开');
    await refreshAll();
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
    await bridge.localHistoryClear();
    if (surface == 'local') await loadLocalMsgs();
    showToast('已清空本机会话区');
  }

  @override
  void dispose() {
    _clockTimer?.cancel();
    _approvePoll?.cancel();
    _streamNotifyTimer?.cancel();
    bridge.dispose();
    super.dispose();
  }
}
