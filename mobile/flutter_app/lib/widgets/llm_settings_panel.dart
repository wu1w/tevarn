import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../bridge/takton_bridge.dart';
import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';
import '../util/open_url.dart';
import 'pixel_widgets.dart';

enum _Src { catalog, preset, local }

class _Opt {
  _Opt({
    required this.id,
    required this.name,
    required this.source,
    required this.raw,
  });
  final String id;
  final String name;
  final _Src source;
  final Map<String, dynamic> raw;
}

/// PC-aligned LLM settings:
/// - providers/models from real catalog + presets (no mock lists)
/// - models refreshed via provider `/models` or catalog?refresh=true
/// - ChatGPT OAuth (PKCE) + Grok OAuth (device code) + manual callback backup
class LlmSettingsPanel extends StatefulWidget {
  const LlmSettingsPanel({super.key});

  @override
  State<LlmSettingsPanel> createState() => _LlmSettingsPanelState();
}

class _LlmSettingsPanelState extends State<LlmSettingsPanel> {
  final _base = TextEditingController();
  final _key = TextEditingController();
  final _modelCustom = TextEditingController();
  final _oauthCallback = TextEditingController();
  final _search = TextEditingController();

  List<_Opt> _opts = [];
  String _providerId = '__custom__';
  List<String> _models = [];
  String _modelId = '';
  bool _showCustomModel = false;
  bool _loading = true;
  bool _busy = false;
  bool _hasKey = false;
  String _keyMasked = '';
  String _hint = '加载中…';
  String _active = '—';
  String _oauthStatus = '';
  String _oauthState = '';
  String _oauthDevice = '';
  String _oauthKind = ''; // openai | xai | ''
  /// True after a successful phone/PC OAuth this session (or restored from local config).
  bool _oauthDone = false;
  Map<String, dynamic>? _catalog;

  List<Map<String, dynamic>> _presets = [];
  Timer? _oauthPoll;
  Timer? _searchDebounce;
  int _searchGen = 0;
  bool? _lastPc;
  bool _loaded = false;
  /// Flat models returned by Rust filter (when q/provider applied).
  List<String> _serverModels = [];

  AppController get c => context.read<AppController>();

  @override
  void initState() {
    super.initState();
    // Seed providers synchronously — never show an empty dropdown while loading.
    _presets = _offlinePresets();
    _buildOptions(null);
    _providerId = _opts.isNotEmpty ? _opts.first.id : '__custom__';
    _loading = false;
    _loaded = true; // keep panel visible during network refresh
    WidgetsBinding.instance.addPostFrameCallback((_) => _reload(refresh: true));
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final pc = context.watch<AppController>().pcConnected;
    if (_loaded) {
      _lastPc ??= pc;
      if (_lastPc != pc) {
        _lastPc = pc;
        _reload(refresh: true);
      }
    }
  }

  @override
  void dispose() {
    _oauthPoll?.cancel();
    _searchDebounce?.cancel();
    _base.dispose();
    _key.dispose();
    _modelCustom.dispose();
    _oauthCallback.dispose();
    _search.dispose();
    super.dispose();
  }

  Future<void> _openAuthUrl(String url, {String? copyExtra}) async {
    if (url.isEmpty) return;
    await Clipboard.setData(ClipboardData(text: copyExtra ?? url));
    final opened = await openExternalUrl(url);
    if (opened) {
      c.showToast('已打开授权页 · 链接已复制备用');
    } else {
      c.showToast('授权链接已复制，请在浏览器打开');
    }
  }

  Future<void> _reload({bool refresh = false, String? q}) async {
    final gen = ++_searchGen;
    // Only show full-panel loading on first paint; keep dropdown visible on refresh
    if (!_loaded && mounted) {
      setState(() => _loading = true);
    }
    Map<String, dynamic>? localCfg;
    try {
      final lr = await c.bridge.localConfigGet();
      if (isOk(lr) && lr['config'] is Map) {
        localCfg = Map<String, dynamic>.from(lr['config'] as Map);
      }
    } catch (_) {}

    // Discard stale work if a newer search started while we awaited config.
    if (!mounted || gen != _searchGen) return;

    Map<String, dynamic>? catalog;
    List<Map<String, dynamic>> presets = [];
    List<String> serverModels = [];
    final query = (q ?? _search.text).trim();
    if (c.pcConnected) {
      try {
        final results = await Future.wait([
          c.bridge.catalog(
            refresh: refresh,
            q: query.isEmpty ? null : query,
          ),
          c.bridge.presets(),
        ]);
        if (!mounted || gen != _searchGen) return;
        final catR = results[0];
        final preR = results[1];
        if (isOk(catR) && catR['catalog'] is Map) {
          catalog = Map<String, dynamic>.from(catR['catalog'] as Map);
        }
        final models = (catR['models'] as List?) ??
            (catalog?['models'] as List?) ??
            [];
        serverModels = models
            .map((e) => e.toString())
            .where((s) => s.isNotEmpty)
            .toList();
        presets = _extractPresets(preR);
          if (query.isNotEmpty && presets.isNotEmpty) {
            final ql = query.toLowerCase();
            presets = presets.where((p) {
              final id =
                  (p['id'] ?? p['preset_id'] ?? '').toString().toLowerCase();
              final name = (p['name'] ?? '').toString().toLowerCase();
              return id.contains(ql) || name.contains(ql);
            }).toList();
          }
      } catch (e) {
        if (gen == _searchGen) {
          _hint = '目录加载失败: $e';
        }
      }
    }

    if (!mounted || gen != _searchGen) return;

    // Always keep a usable provider list
    if (presets.isEmpty) {
      presets = _offlinePresets();
    }

    if (!mounted || gen != _searchGen) return;
    setState(() {
      _catalog = catalog;
      _presets = presets;
      _serverModels = serverModels;
      _buildOptions(localCfg);
      _selectInitial(localCfg);
      // ensure providerId always valid
      if (_opts.isEmpty) {
        _opts = [
          _Opt(
            id: '__custom__',
            name: '自定义 / 本机直连',
            source: _Src.local,
            raw: {},
          ),
        ];
      }
      if (!_opts.any((o) => o.id == _providerId)) {
        _providerId = _opts.first.id;
      }
      _loaded = true;
      _loading = false;
    });
    // fill base/model fields after opts are committed
    _applyProviderUi(autofillBase: true);
    _updateHint();
  }

  /// Accept multiple host/PC shapes: {presets:[...]}, {result:[...]}, raw list, nested data.
  List<Map<String, dynamic>> _extractPresets(Map<String, dynamic> preR) {
    dynamic raw = preR['presets'] ??
        preR['result'] ??
        preR['data'] ??
        preR['items'];
    if (raw is Map && raw['presets'] is List) {
      raw = raw['presets'];
    }
    // Some proxies wrap the whole PC array under ok envelope already unwrapped
    if (raw is! List) {
      // PC may return a top-level list that got stored under a numeric-string key — skip
      return [];
    }
    final out = <Map<String, dynamic>>[];
    for (final e in raw) {
      if (e is Map) {
        final m = Map<String, dynamic>.from(e);
        if ((m['id'] ?? m['preset_id'] ?? '').toString().isNotEmpty) {
          out.add(m);
        }
      }
    }
    return out;
  }

  void _onSearchChanged(String _) {
    _searchDebounce?.cancel();
    _searchDebounce = Timer(const Duration(milliseconds: 280), () {
      if (!mounted) return;
      // Server-side filter (Rust filter_catalog)
      _reload(refresh: false, q: _search.text);
    });
  }

  List<Map<String, dynamic>> _offlinePresets() {
    return [
      {
        'id': 'openai',
        'name': 'OpenAI',
        'llm': {
          'llm_provider': 'openai',
          'llm_base_url': 'https://api.openai.com/v1',
          'llm_model': '',
        },
        'models': <String>[],
      },
      {
        'id': 'openai-chatgpt-oauth',
        'name': 'ChatGPT 会员 (OAuth)',
        'auth_mode': 'oauth_pkce',
        'oauth_provider': 'openai',
        'llm': {
          'llm_provider': 'openai-compatible',
          'llm_base_url': 'codex-oauth://chatgpt',
          'llm_model': 'gpt-5.6-luna',
        },
        'models': <String>[
          'gpt-5.6-luna',
          'gpt-5.6-terra',
          'gpt-5.6-sol',
          'gpt-5.4',
          'gpt-5.3-codex',
          'gpt-5.2-codex',
          'gpt-5.1-codex',
          'gpt-4.1',
          'gpt-4o',
          'o3',
          'o4-mini',
        ],
      },
      {
        'id': 'xai',
        'name': 'xAI Grok',
        'llm': {
          'llm_provider': 'openai-compatible',
          'llm_base_url': 'https://api.x.ai/v1',
          'llm_model': '',
        },
        'models': <String>[],
      },
      {
        'id': 'xai-oauth',
        'name': 'Grok (OAuth)',
        'auth_mode': 'oauth_device_code',
        'oauth_provider': 'xai',
        'llm': {
          'llm_provider': 'openai-compatible',
          'llm_base_url': 'https://api.x.ai/v1',
          'llm_model': 'grok-3',
        },
        'models': <String>[
          'grok-4',
          'grok-3',
          'grok-3-mini',
          'grok-3-fast',
          'grok-2',
          'grok-2-vision-1212',
        ],
      },
      {
        'id': 'deepseek',
        'name': 'DeepSeek',
        'llm': {
          'llm_provider': 'openai-compatible',
          'llm_base_url': 'https://api.deepseek.com',
          'llm_model': '',
        },
        'models': <String>[],
      },
      {
        'id': 'ollama',
        'name': 'Ollama 本地',
        'llm': {
          'llm_provider': 'ollama',
          'llm_base_url': 'http://127.0.0.1:11434',
          'llm_model': '',
        },
        'models': <String>['llama3.2', 'qwen2.5', 'deepseek-r1'],
      },
    ];
  }

  void _buildOptions(Map<String, dynamic>? localCfg) {
    final list = <_Opt>[];
    final seen = <String>{};
    final providers = (_catalog?['providers'] as List?) ?? [];

    for (final p in providers) {
      if (p is! Map) continue;
      final m = Map<String, dynamic>.from(p);
      if (m['enabled'] == false) continue;
      final id = m['id']?.toString() ?? '';
      if (id.isEmpty || seen.contains(id)) continue;
      seen.add(id);
      list.add(_Opt(
        id: id,
        name: m['name']?.toString() ?? id,
        source: _Src.catalog,
        raw: m,
      ));
    }
    for (final p in _presets) {
      final id = p['id']?.toString() ?? p['preset_id']?.toString() ?? '';
      if (id.isEmpty || seen.contains(id)) continue;
      seen.add(id);
      list.add(_Opt(
        id: id,
        name: p['name']?.toString() ?? id,
        source: _Src.preset,
        raw: p,
      ));
    }
    list.add(_Opt(
      id: '__custom__',
      name: '自定义 / 本机直连',
      source: _Src.local,
      raw: {
        if (localCfg != null) ...localCfg,
        'llm_base_url': localCfg?['base_url'],
        'llm_model': localCfg?['model'],
      },
    ));
    _opts = list;
  }

  void _selectInitial(Map<String, dynamic>? localCfg) {
    final activePid = _catalog?['active_provider_id']?.toString() ?? '';
    final activeModel = _catalog?['active_model']?.toString() ?? '';
    final label = localCfg?['provider_label']?.toString() ?? '';
    final hasKey = localCfg?['has_key'] == true;
    _hasKey = hasKey;
    _keyMasked = localCfg?['api_key_masked']?.toString() ?? '';

    _active = activeModel.isNotEmpty
        ? '$activePid · $activeModel'
        : (localCfg?['model']?.toString().isNotEmpty == true
            ? '本机 · ${localCfg!['model']}${hasKey ? ' · 已授权' : ''}'
            : '—');

    // Prefer restoring OAuth provider when local profile is from OAuth
    final oauthId = _oauthProviderIdFromLabel(label);
    if (oauthId != null && _opts.any((o) => o.id == oauthId)) {
      _providerId = oauthId;
      final m = localCfg?['model']?.toString() ?? '';
      if (m.isNotEmpty) {
        _modelId = m;
        _modelCustom.text = m;
      }
      if (hasKey) {
        _oauthDone = true;
        _oauthStatus = '已登录 · 令牌已保存在本机（$label）\n可直接选模型并「应用模型」';
      }
      return;
    }

    if (activePid.isNotEmpty && _opts.any((o) => o.id == activePid)) {
      _providerId = activePid;
      _modelId = activeModel;
    } else if (localCfg != null &&
        (localCfg['base_url']?.toString().isNotEmpty == true)) {
      // Keep current OAuth selection if user mid-flow
      if (_oauthDone && _opts.any((o) => o.id == _providerId && _isOauth(o))) {
        // keep
      } else {
        _providerId = '__custom__';
        _modelId = localCfg['model']?.toString() ?? '';
      }
    } else if (_opts.isNotEmpty && !_opts.any((o) => o.id == _providerId)) {
      _providerId = _opts.first.id;
    }
  }

  String? _oauthProviderIdFromLabel(String label) {
    final l = label.toLowerCase();
    if (l.contains('chatgpt') || l.contains('openai')) {
      return 'openai-chatgpt-oauth';
    }
    if (l.contains('grok') || l.contains('xai')) return 'xai-oauth';
    if (l.contains('oauth')) {
      // generic
      return null;
    }
    return null;
  }

  /// Persist OAuth token to local config and refresh UI **without leaving** this page.
  Future<void> _finishOauthLocal(
    Map<String, dynamic> r, {
    required String kind,
  }) async {
    _oauthPoll?.cancel();
    final base = r['base_url']?.toString().isNotEmpty == true
        ? r['base_url'].toString()
        : (kind == 'xai' ? 'https://api.x.ai/v1' : 'codex-oauth://chatgpt');
    final defaultModel = kind == 'xai' ? 'grok-3' : 'gpt-5.6-luna';
    final model = _selectedModel.isNotEmpty ? _selectedModel : defaultModel;
    final token = r['access_token']?.toString() ?? '';
    final label = r['provider_label']?.toString() ??
        (kind == 'xai' ? 'Grok OAuth' : 'ChatGPT OAuth');
    final pid = r['provider_id']?.toString() ??
        (kind == 'xai' ? 'xai-oauth' : 'openai-chatgpt-oauth');

    final body = <String, dynamic>{
      'base_url': base,
      'model': model,
      'provider_label': label,
    };
    if (token.isNotEmpty) body['api_key'] = token;
    if (r['account_id'] != null && r['account_id'].toString().isNotEmpty) {
      body['account_id'] = r['account_id'].toString();
    }

    final setR = await c.bridge.localConfigSet(body);
    if (!isOk(setR)) {
      throw Exception(setR['error']?.toString() ?? '本机保存失败');
    }

    Map<String, dynamic>? localCfg;
    try {
      final lr = await c.bridge.localConfigGet();
      if (isOk(lr) && lr['config'] is Map) {
        localCfg = Map<String, dynamic>.from(lr['config'] as Map);
      }
    } catch (_) {}

    // Seed model list immediately (don't wait for pull)
    final seed = List<String>.from(_modelsOf(_opts.firstWhere(
      (o) => o.id == pid,
      orElse: () => _cur,
    )));
    if (seed.isEmpty) {
      seed.addAll(kind == 'xai'
          ? const ['grok-4', 'grok-3', 'grok-3-mini', 'grok-2']
          : const [
              'gpt-5.6-luna',
              'gpt-5.6-terra',
              'gpt-5.6-sol',
              'gpt-5.4',
              'gpt-5.3-codex',
              'gpt-4o',
              'o3',
              'o4-mini',
            ]);
    }

    if (!mounted) return;
    setState(() {
      _oauthDone = true;
      _providerId = _opts.any((o) => o.id == pid) ? pid : _providerId;
      _base.text = base;
      _models = seed;
      if (model.isNotEmpty) {
        if (_models.contains(model)) {
          _modelId = model;
          _showCustomModel = false;
        } else {
          _modelId = '__custom_model__';
          _showCustomModel = true;
          _modelCustom.text = model;
        }
      } else if (_models.isNotEmpty) {
        _modelId = _models.first;
        _showCustomModel = false;
      }
      _hasKey = localCfg?['has_key'] == true || token.isNotEmpty;
      _keyMasked = localCfg?['api_key_masked']?.toString() ??
          (token.length > 8
              ? '${token.substring(0, 4)}…${token.substring(token.length - 4)}'
              : '••••');
      _active = '本机 · ${_selectedModel.isNotEmpty ? _selectedModel : model} · 已授权';
      _oauthStatus =
          '✅ 登录成功 · 令牌已写入本机\n可直接「拉取模型」或选模型后「应用模型」';
      _oauthCallback.clear();
    });

    unawaited(c.refreshAll());
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove('takton-oauth-state');
      await prefs.remove('takton-oauth-kind');
    } catch (_) {}

    // Auto-pull models with stored token (no PC required)
    unawaited(_fetchModelsAfterOauth());
    c.showToast(r['message']?.toString() ?? 'OAuth 成功 · 正在拉取模型');
  }

  Future<void> _fetchModelsAfterOauth() async {
    if (!mounted) return;
    try {
      await _fetchModels(forceLocalOauth: true);
    } catch (_) {}
  }

  _Opt get _cur {
    return _opts.firstWhere(
      (o) => o.id == _providerId,
      orElse: () => _opts.isNotEmpty
          ? _opts.last
          : _Opt(id: '__custom__', name: '自定义', source: _Src.local, raw: {}),
    );
  }

  String get _selectedModel {
    if (_showCustomModel || _modelId == '__custom_model__') {
      return _modelCustom.text.trim();
    }
    return _modelId;
  }

  List<String> _modelsOf(_Opt o) {
    // Prefer server-filtered flat list when a search is active and models exist
    if (_search.text.trim().isNotEmpty &&
        _serverModels.isNotEmpty &&
        o.source == _Src.catalog) {
      // Intersection with this provider's known models when possible
      final local = _modelsOfRaw(o);
      if (local.isEmpty) return List<String>.from(_serverModels);
      return _serverModels.where(local.contains).toList();
    }
    return _modelsOfRaw(o);
  }

  List<String> _modelsOfRaw(_Opt o) {
    final raw = o.raw;
    final fromList = <String>[];
    // Only explicit model lists — never inject preset template llm_model
    // (preset template defaults) as a fake one-item catalog.
    for (final key in ['models', 'cached_models', 'available_models']) {
      final v = raw[key];
      if (v is List) {
        for (final e in v) {
          final s = e is Map
              ? (e['id'] ?? e['model'] ?? e['name'])?.toString()
              : e.toString();
          if (s != null && s.isNotEmpty && !fromList.contains(s)) {
            fromList.add(s);
          }
        }
      }
    }
    final llm = raw['llm'];
    if (llm is Map) {
      final ms = llm['models'];
      if (ms is List) {
        for (final e in ms) {
          final s = e is Map
              ? (e['id'] ?? e['model'] ?? e['name'])?.toString()
              : e.toString();
          if (s != null && s.isNotEmpty && !fromList.contains(s)) {
            fromList.add(s);
          }
        }
      }
    }
    // Local saved config: surface the saved model only for __custom__ / local
    if (o.source == _Src.local) {
      final saved = raw['llm_model']?.toString() ??
          raw['model']?.toString() ??
          '';
      if (saved.isNotEmpty && !fromList.contains(saved)) fromList.add(saved);
    }
    return fromList;
  }

  String _baseOf(_Opt o) {
    final r = o.raw;
    final llm = r['llm'];
    if (llm is Map && llm['llm_base_url'] != null) {
      return llm['llm_base_url'].toString();
    }
    return r['llm_base_url']?.toString() ??
        r['base_url']?.toString() ??
        r['api_base']?.toString() ??
        '';
  }

  bool _isOauth(_Opt o) {
    final p = o.raw;
    final id = o.id.toLowerCase();
    if (id.contains('oauth') ||
        id == 'openai-chatgpt-oauth' ||
        id == 'xai-oauth') {
      return true;
    }
    final auth = p['auth_mode']?.toString() ?? p['auth']?.toString() ?? '';
    if (auth == 'oauth_pkce' || auth == 'oauth_device_code') return true;
    if (p['oauth_provider'] != null &&
        p['oauth_provider'].toString().isNotEmpty &&
        p['requires_api_key'] == false) {
      return true;
    }
    final creds = p['credentials'];
    if (creds is List && creds.isNotEmpty) {
      final c0 = creds.first;
      if (c0 is Map &&
          (c0['auth_mode']?.toString().contains('oauth') == true)) {
        return true;
      }
    }
    return false;
  }

  String _oauthKindOf(_Opt o) {
    final p = o.raw;
    final id = o.id.toLowerCase();
    if (id.contains('xai') ||
        id.contains('grok') ||
        p['oauth_provider']?.toString() == 'xai' ||
        p['auth_mode']?.toString() == 'oauth_device_code') {
      return 'xai';
    }
    return 'openai';
  }

  bool get _oauth => _isOauth(_cur);

  String _oauthErr(Map<String, dynamic> r, [String fallback = '启动失败']) {
    for (final k in ['message', 'error', 'detail', 'hint']) {
      final v = r[k]?.toString().trim() ?? '';
      if (v.isNotEmpty && v != 'null') return v;
    }
    return fallback;
  }


  void _applyProviderUi({bool autofillBase = false}) {
    final o = _cur;
    final models = _modelsOf(o);
    setState(() {
      _models = models;
      _oauthKind = _oauth ? _oauthKindOf(o) : '';
      if (autofillBase) {
        final b = _baseOf(o);
        if (b.isNotEmpty) _base.text = b;
        if (o.source == _Src.local) {
          final lb = o.raw['base_url']?.toString() ?? c.llmBase;
          if (lb.isNotEmpty) _base.text = lb;
        }
      }
      if (_modelId.isEmpty ||
          (!_models.contains(_modelId) && _modelId != '__custom_model__')) {
        if (_models.isNotEmpty) {
          _modelId = _models.first;
          _showCustomModel = false;
        } else {
          _showCustomModel = true;
          _modelId = '__custom_model__';
          if (_modelCustom.text.isEmpty && c.llmModel.isNotEmpty) {
            _modelCustom.text = c.llmModel;
          }
        }
      }
      if (o.source == _Src.local &&
          _base.text.isEmpty &&
          c.llmBase.isNotEmpty &&
          !_oauth) {
        _base.text = c.llmBase;
      }
    });
  }

  void _updateHint() {
    if (_oauth && !c.pcConnected) {
      _hint =
          'OAuth 本机可用：点登录授权后即可本机对话，无需先连 PC。连上 PC 时也会同步到远端目录。';
    } else if (!c.pcConnected) {
      _hint = '未连 PC：仅本机直连。填 Base URL + API Key，点「测试连接」从供应商 /models 拉取最新列表。';
    } else if (_oauth) {
      _hint = 'OAuth 供应商：登录授权后从目录刷新模型列表；未连 PC 时写入本机配置。';
    } else {
      _hint = '已连 PC：供应商/模型来自真实目录。可刷新目录或测试连接拉取 /models。';
    }
  }

  Future<void> _onProviderChanged(String? id) async {
    if (id == null) return;
    setState(() {
      _providerId = id;
      _modelId = '';
      _showCustomModel = false;
      _oauthStatus = '';
    });
    _applyProviderUi(autofillBase: true);
    _updateHint();
  }

  Future<void> _fetchModels({bool forceLocalOauth = false}) async {
    if (_busy && !forceLocalOauth) return;
    final o = _cur;
    final base = _base.text.trim();
    final key = _key.text.trim();
    final model = _selectedModel;

    setState(() => _busy = true);
    try {
      // ── OAuth path: always use local stored token (PC catalog optional) ──
      if (_oauth || forceLocalOauth) {
        if (!_oauthDone && !_hasKey && !forceLocalOauth) {
          c.showToast('请先完成 OAuth 登录（点上方登录按钮）');
          return;
        }
        final oauthBase = base.isNotEmpty
            ? base
            : (_oauthKind == 'xai' || o.id == 'xai-oauth'
                ? 'https://api.x.ai/v1'
                : 'codex-oauth://chatgpt');
        // Ensure base is persisted for local_test (token already in profile)
        await c.bridge.localConfigSet({
          'base_url': oauthBase,
          if (model.isNotEmpty) 'model': model,
          'provider_label': o.name,
        });
        final r = await c.bridge.localTest({
          'base_url': oauthBase,
          if (model.isNotEmpty) 'model': model,
        });
        if (!isOk(r)) {
          // Still seed curated list so user can apply
          final seed = _modelsOf(o);
          if (seed.isNotEmpty) {
            setState(() {
              _models = seed;
              _showCustomModel = false;
              if (!_models.contains(_modelId) && _models.isNotEmpty) {
                _modelId = _models.first;
              }
              _base.text = oauthBase;
              _oauthDone = true;
            });
            c.showToast(
                '${r['error'] ?? '拉取失败'} · 已提供常用模型列表，可直接应用');
          } else {
            c.showToast(r['error']?.toString() ?? '拉取失败 · 请重新 OAuth 登录');
          }
          return;
        }
        final result = r['result'] is Map
            ? Map<String, dynamic>.from(r['result'] as Map)
            : r;
        var models = ((result['models'] as List?) ?? [])
            .map((e) => e.toString())
            .where((s) => s.isNotEmpty)
            .toList();
        if (models.isEmpty) {
          models = List<String>.from(_modelsOf(o));
        }
        if (models.isEmpty) {
          c.showToast(result['message']?.toString() ??
              '已授权，但未返回模型 · 可手写模型名后应用');
          setState(() {
            _showCustomModel = true;
            _oauthDone = true;
            _base.text = oauthBase;
          });
        } else {
          setState(() {
            _models = models;
            _showCustomModel = false;
            _modelId = models.contains(_modelId) ? _modelId : models.first;
            _oauthDone = true;
            _hasKey = true;
            _base.text = oauthBase;
          });
          c.showToast(result['message']?.toString() ??
              '已拉取 ${models.length} 个模型（OAuth 本机）');
        }
        await c.refreshAll();
        return;
      }

      if (c.pcConnected && o.source != _Src.local) {
        final body = <String, dynamic>{
          if (o.source == _Src.catalog) 'provider_id': o.id,
          'llm_provider': o.raw['llm_provider'] ??
              (o.raw['llm'] is Map
                  ? (o.raw['llm'] as Map)['llm_provider']
                  : null) ??
              'openai-compatible',
          'llm_base_url': base.isNotEmpty ? base : _baseOf(o),
          if (model.isNotEmpty) 'llm_model': model,
        };
        if (key.isNotEmpty) body['llm_api_key'] = key;
        final r = await c.bridge.testLlm(body);
        if (!isOk(r)) {
          c.showToast(r['error']?.toString() ?? '拉取失败');
          return;
        }
        final result = r['result'] is Map
            ? Map<String, dynamic>.from(r['result'] as Map)
            : r;
        final models = ((result['models'] as List?) ??
                (result['available'] as List?) ??
                [])
            .map((e) => e.toString())
            .where((s) => s.isNotEmpty)
            .toList();
        if (models.isEmpty) {
          c.showToast(result['message']?.toString() ?? '连接成功，但未返回模型列表');
        } else {
          setState(() {
            _models = models;
            _showCustomModel = false;
            if (!_models.contains(_modelId)) _modelId = _models.first;
          });
          c.showToast('已拉取 ${models.length} 个模型');
        }
      } else {
        if (base.isEmpty) {
          c.showToast('请填写 Base URL');
          return;
        }
        await c.bridge.localConfigSet({
          'base_url': base,
          if (model.isNotEmpty) 'model': model,
          if (key.isNotEmpty) 'api_key': key,
          'provider_label': o.name,
        });
        final r = await c.bridge.localTest({
          'base_url': base,
          if (model.isNotEmpty) 'model': model,
          if (key.isNotEmpty) 'api_key': key,
        });
        if (!isOk(r)) {
          c.showToast(r['error']?.toString() ?? '拉取失败');
          return;
        }
        final result = r['result'] is Map
            ? Map<String, dynamic>.from(r['result'] as Map)
            : r;
        final models = ((result['models'] as List?) ?? [])
            .map((e) => e.toString())
            .where((s) => s.isNotEmpty)
            .toList();
        if (models.isEmpty) {
          c.showToast(result['message']?.toString() ??
              '连接成功，但未返回模型列表 · 可手写模型名');
          setState(() => _showCustomModel = true);
        } else {
          setState(() {
            _models = models;
            _showCustomModel = false;
            _modelId = models.contains(_modelId) ? _modelId : models.first;
          });
          c.showToast('已从供应商拉取 ${models.length} 个模型');
        }
        await c.refreshAll();
      }
    } catch (e) {
      c.showToast('拉取失败: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _apply() async {
    if (_busy) return;
    final model = _selectedModel;
    if (model.isEmpty) {
      c.showToast('请选择或填写模型（可先拉取模型列表）');
      return;
    }
    final o = _cur;
    final base = _base.text.trim();
    final key = _key.text.trim();

    setState(() => _busy = true);
    try {
      if (_oauth) {
        // Prefer phone-local OAuth profile whenever we have a local token.
        if (_oauthDone || _hasKey || !c.pcConnected) {
          final oauthBase = base.isNotEmpty
              ? base
              : (_oauthKind == 'xai' || o.id == 'xai-oauth'
                  ? 'https://api.x.ai/v1'
                  : 'codex-oauth://chatgpt');
          // Do NOT pass empty key — keep server-side token
          await c.bridge.localConfigSet({
            'base_url': oauthBase,
            'model': model,
            'provider_label': o.name,
            if (key.isNotEmpty) 'api_key': key,
          });
          await c.refreshAll();
          if (!c.llmHasKey && !_hasKey) {
            c.showToast('本机未保存 OAuth 令牌，请重新登录');
            return;
          }
          await c.goLocalChatAfterOauth(
              toastMsg: '已应用 OAuth · $model · 本机对话可用');
          return;
        }
        // PC catalog path: provider must already be activated by PC OAuth
        if (o.source == _Src.preset) {
          c.showToast('请先点「登录授权」完成 OAuth，再应用模型');
          return;
        }
        final r = await c.bridge.catalogSelect(
          providerId: o.id,
          model: model,
          sessionId: c.activeSessionId,
        );
        if (!isOk(r)) {
          c.showToast(r['error']?.toString() ?? '切换失败');
          return;
        }
        await _reload(refresh: true);
        await c.goRemoteChatAfterOauth(
            toastMsg: '已应用 OAuth 模型 · $model · 已切远端');
        return;
      }

      if (c.pcConnected && o.source == _Src.catalog) {
        if (key.isNotEmpty) {
          final cr = await c.bridge.setCredentials({
            'provider_id': o.id,
            'credential_id': null,
            'label': 'Mobile',
            'api_key': key,
            'set_active': true,
          });
          if (!isOk(cr)) {
            c.showToast(cr['error']?.toString() ?? '凭证更新失败');
            return;
          }
        }
        if (base.isNotEmpty) {
          await c.bridge.catalogRegister({
            'id': o.id,
            'name': o.raw['name'] ?? o.id,
            'llm_provider': o.raw['llm_provider'] ?? 'openai-compatible',
            'llm_base_url': base,
            'llm_model': model,
            'set_active': true,
            if (o.raw['preset_id'] != null) 'preset_id': o.raw['preset_id'],
          });
        }
        final r = await c.bridge.catalogSelect(
          providerId: o.id,
          model: model,
          sessionId: c.activeSessionId,
        );
        if (!isOk(r)) {
          c.showToast(r['error']?.toString() ?? '切换失败');
          return;
        }
        await _syncLocal(o, model, base, key);
        await c.goRemoteChatAfterOauth(toastMsg: '已应用 · $model · 已切远端对话');
        return;
      } else if (c.pcConnected && o.source == _Src.preset) {
        final llm = o.raw['llm'] is Map
            ? Map<String, dynamic>.from(o.raw['llm'] as Map)
            : <String, dynamic>{};
        final regBase =
            base.isNotEmpty ? base : (llm['llm_base_url']?.toString() ?? '');
        if (regBase.isEmpty) {
          c.showToast('请填写 Base URL');
          return;
        }
        final reg = await c.bridge.catalogRegister({
          'id': o.id,
          'name': o.raw['name'] ?? o.id,
          'llm_provider': llm['llm_provider'] ?? 'openai-compatible',
          'llm_base_url': regBase,
          if (key.isNotEmpty) 'llm_api_key': key,
          'llm_model': model,
          'set_active': true,
          'preset_id': o.id,
        });
        if (!isOk(reg)) {
          c.showToast(reg['error']?.toString() ?? '登记失败');
          return;
        }
        final r = await c.bridge.catalogSelect(
          providerId: o.id,
          model: model,
          sessionId: c.activeSessionId,
        );
        if (!isOk(r)) {
          c.showToast(r['error']?.toString() ?? '切换失败');
          return;
        }
        await _syncLocal(o, model, regBase, key);
        await c.goRemoteChatAfterOauth(toastMsg: '已激活预设 · $model · 已切远端');
        return;
      } else {
        if (base.isEmpty) {
          c.showToast('请填写 Base URL');
          return;
        }
        if (key.isEmpty && !_hasKey) {
          c.showToast('请填写 API Key');
          return;
        }
        await _syncLocal(o, model, base, key);
        c.showToast('本机模型已就绪 · $model');
        await c.setSurface('local');
        c.setTab(AppTab.chat);
      }

      _key.clear();
      await c.refreshAll();
      await _reload(refresh: false);
    } catch (e) {
      c.showToast('应用失败: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _syncLocal(
    _Opt o,
    String model,
    String base,
    String key,
  ) async {
    if (base.isEmpty) return;
    final body = <String, dynamic>{
      'base_url': base,
      'model': model,
      'provider_label': o.name,
    };
    if (key.isNotEmpty) body['api_key'] = key;
    final r = await c.bridge.localConfigSet(body);
    if (!isOk(r)) {
      throw Exception(r['error']?.toString() ?? '本机配置保存失败');
    }
    if (r['config'] is Map) {
      final cfg = Map<String, dynamic>.from(r['config'] as Map);
      c.llmBase = cfg['base_url']?.toString() ?? base;
      c.llmModel = cfg['model']?.toString() ?? model;
      _hasKey = cfg['has_key'] == true;
      _keyMasked = cfg['api_key_masked']?.toString() ?? '';
    }
  }

  Future<void> _startOauth() async {
    if (_busy) return;
    if (!_oauth) {
      c.showToast('当前供应商不是 OAuth');
      return;
    }
    setState(() {
      _busy = true;
      _oauthStatus = '发起中…';
    });
    try {
      if (_oauthKind == 'xai') {
        final r = await c.bridge.oauthXaiStart();
        if (!isOk(r)) {
          final err = _oauthErr(r);
          c.showToast(err);
          setState(() => _oauthStatus = err);
          return;
        }
        _oauthDevice = r['device_code']?.toString() ?? '';
        final url = r['verification_uri']?.toString() ??
            r['verification_url']?.toString() ??
            'https://accounts.x.ai/device';
        final code = r['user_code']?.toString() ?? '';
        setState(() {
          _oauthStatus =
              '设备码 $code\n请打开 $url 授权${r['local'] == true ? '\n（本机 OAuth · 无需 PC）' : ''}';
        });
        if (code.isNotEmpty) {
          await Clipboard.setData(ClipboardData(text: code));
        }
        await _openAuthUrl(url, copyExtra: code.isEmpty ? url : '$code | $url');
        if (code.isNotEmpty) {
          c.showToast('设备码 $code · 已尝试打开授权页');
        }
        _startPollLoop();
      } else {
        final r = await c.bridge.oauthOpenaiStart();
        if (!isOk(r)) {
          final err = _oauthErr(r);
          c.showToast(err);
          setState(() => _oauthStatus = err);
          return;
        }
        _oauthState = r['state']?.toString() ?? '';
        // Persist state so paste-callback works after returning from browser
        try {
          final prefs = await SharedPreferences.getInstance();
          await prefs.setString('takton-oauth-state', _oauthState);
          await prefs.setString('takton-oauth-kind', 'openai');
        } catch (_) {}
        final url = r['authorization_url']?.toString() ??
            r['url']?.toString() ??
            r['auth_url']?.toString() ??
            '';
        setState(() {
          _oauthDone = false;
          _oauthStatus = r['message']?.toString() ??
              '① 打开浏览器登录 ChatGPT\n'
                  '② 跳转到 localhost 失败 = 正常\n'
                  '③ 复制地址栏完整 URL\n'
                  '④ 回到本页粘贴并点「完成登录」\n'
                  '⑤ 成功后点「应用模型」（不跳转）';
        });
        if (url.isNotEmpty) {
          await _openAuthUrl(url);
        } else {
          c.showToast(r['message']?.toString() ?? '请按提示完成授权');
        }
        _startPollLoop();
        c.showToast('授权链接已复制 · 浏览器 localhost 打不开时请复制地址栏 URL');
      }
    } catch (e) {
      c.showToast('登录失败: $e');
      setState(() => _oauthStatus = '失败: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _startPollLoop() {
    _oauthPoll?.cancel();
    final deadline = DateTime.now().add(const Duration(minutes: 10));
    Future<void> tick() async {
      if (!mounted) return;
      if (DateTime.now().isAfter(deadline)) {
        c.showToast('等待授权超时');
        return;
      }
      final ok = await _pollOauth(silent: true);
      if (ok) return;
      _oauthPoll = Timer(const Duration(milliseconds: 2500), tick);
    }

    tick();
  }

  Future<bool> _pollOauth({bool silent = false}) async {
    try {
      if (_oauthKind == 'xai') {
        if (_oauthDevice.isEmpty) {
          if (!silent) c.showToast('请先点 Grok 登录');
          return false;
        }
        final r = await c.bridge.oauthXaiPoll(deviceCode: _oauthDevice);
        if (r['status']?.toString() == 'authorized' ||
            (isOk(r) && r['access_token'] != null)) {
          await _finishOauthLocal(r, kind: 'xai');
          return true;
        }
        if (r['status']?.toString() == 'error' ||
            (r['ok'] == false &&
                r['status']?.toString() != null &&
                r['status']?.toString() != 'pending')) {
          if (!silent) {
            c.showToast(
                r['message']?.toString() ?? r['error']?.toString() ?? '登录失败');
          }
          return false;
        }
        if (mounted) {
          setState(() {
            _oauthStatus = r['message']?.toString() ?? '等待 Grok 授权…';
          });
        }
        return false;
      } else {
        final r = await c.bridge.oauthOpenaiPoll(state: _oauthState);
        if (r['status']?.toString() == 'authorized' ||
            (isOk(r) && r['access_token'] != null)) {
          await _finishOauthLocal(r, kind: 'openai');
          return true;
        }
        if (r['status']?.toString() == 'error' ||
            (r['ok'] == false &&
                r['status'] != null &&
                r['status'] != 'pending')) {
          if (!silent) {
            c.showToast(
                r['message']?.toString() ?? r['error']?.toString() ?? '登录失败');
          }
          return false;
        }
        if (mounted) {
          setState(() {
            _oauthStatus = r['message']?.toString() ??
                '等待授权…浏览器 localhost 失败请复制地址栏 URL 粘贴到下方';
          });
        }
        return false;
      }
    } catch (e) {
      if (!silent) c.showToast(e.toString());
      return false;
    }
  }

  Future<void> _completeOauth() async {
    if (_busy) return;
    final url = _oauthCallback.text.trim();
    if (url.isEmpty) {
      c.showToast('请粘贴完整回调地址（浏览器地址栏，含 code=）');
      return;
    }
    if (!url.contains('code=') && !RegExp(r'[?&]code[=%]').hasMatch(url)) {
      c.showToast('URL 里应包含 code= 参数，请复制地址栏完整链接');
      return;
    }
    // Restore state from prefs if UI was rebuilt while user was in browser
    if (_oauthState.isEmpty) {
      try {
        final prefs = await SharedPreferences.getInstance();
        _oauthState = prefs.getString('takton-oauth-state') ?? '';
      } catch (_) {}
    }
    setState(() {
      _busy = true;
      _oauthStatus = '正在用回调地址换取令牌…';
    });
    try {
      final r = await c.bridge.oauthOpenaiComplete(
        callbackUrl: url,
        state: _oauthState.isEmpty ? null : _oauthState,
      );
      if (!isOk(r)) {
        throw Exception(
            r['message']?.toString() ?? r['error']?.toString() ?? '失败');
      }
      // PC path: also stay on page if local flag or access_token present
      if (r['local'] == true || r['access_token'] != null || !c.pcConnected) {
        await _finishOauthLocal(r, kind: 'openai');
      } else {
        // PC catalog oauth — stay on page, soft reload, no forced chat jump
        _oauthPoll?.cancel();
        await _reload(refresh: true);
        if (mounted) {
          setState(() {
            _oauthDone = true;
            _oauthStatus =
                '✅ 登录成功 · 已写入 PC\n请选模型后点「应用模型」（不自动跳转）';
          });
        }
        c.showToast(r['message']?.toString() ?? 'ChatGPT 登录成功');
      }
    } catch (e) {
      if (mounted) {
        setState(() => _oauthStatus = '失败: $e\n可重新点登录再试，或检查 URL 是否含 code=');
      }
      c.showToast('完成失败: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final dark = c.dark;
    final ink3 = dark ? PixelColors.dInk3 : PixelColors.ink3;
    final o = _cur;
    final oauth = _oauth;

    if (_loading) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Text('加载 LLM 配置…', style: TextStyle(color: ink3, fontSize: 13)),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          c.pcConnected
              ? '供应商与模型来自 PC 实时目录。OAuth 也可本机独立完成。'
              : '本机模式：API Key 或 OAuth 均可，无需先连 PC。',
          style: TextStyle(fontSize: 11.5, color: ink3, height: 1.45),
        ),
        const SizedBox(height: 10),
        Text('当前活动', style: TextStyle(fontSize: 11, color: ink3)),
        const SizedBox(height: 4),
        Text(
          _active,
          style: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w700,
            fontFamily: 'JetBrains Mono',
            color: dark ? PixelColors.dInk : PixelColors.ink,
          ),
        ),
        const SizedBox(height: 12),
        if (c.pcConnected) ...[
          _FieldLabel('搜索供应商 / 模型', dark),
          const SizedBox(height: 4),
          TextField(
            controller: _search,
            onChanged: _onSearchChanged,
            style: TextStyle(
              fontSize: 13.5,
              color: dark ? PixelColors.dInk : PixelColors.ink,
            ),
            decoration: InputDecoration(
              isDense: true,
              hintText: '服务端过滤 · 如 gpt / grok / openai',
              hintStyle: TextStyle(color: ink3, fontSize: 12.5),
              filled: true,
              fillColor: dark ? PixelColors.dCard : PixelColors.card2,

              contentPadding:
                  const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(4),
                borderSide: BorderSide(
                  color: (dark ? PixelColors.dInk : PixelColors.ink)
                      .withValues(alpha: 0.12),
                ),
              ),
              enabledBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(4),
                borderSide: BorderSide(
                  color: (dark ? PixelColors.dInk : PixelColors.ink)
                      .withValues(alpha: 0.12),
                ),
              ),
              suffixIcon: _search.text.isEmpty
                  ? null
                  : IconButton(
                      icon: Icon(Icons.clear, size: 16, color: ink3),
                      onPressed: () {
                        _search.clear();
                        _onSearchChanged('');
                      },
                    ),
            ),
          ),
          const SizedBox(height: 10),
        ],
        _FieldLabel('供应商', dark),
        const SizedBox(height: 4),
        _Dropdown(
          value: _opts.isEmpty
              ? null
              : (_opts.any((e) => e.id == _providerId)
                  ? _providerId
                  : _opts.first.id),
          items: [
            if (_opts.isEmpty)
              const DropdownMenuItem(
                value: '__custom__',
                child: Text('自定义 / 本机直连'),
              ),
            for (final opt in _opts)
              DropdownMenuItem(
                value: opt.id,
                child: Text(
                  _isOauth(opt) ? '${opt.name} · OAuth' : opt.name,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
          ],
          onChanged: (id) {
            if (id == null) return;
            _onProviderChanged(id);
          },
          dark: dark,
        ),
        const SizedBox(height: 10),
        _FieldLabel('模型', dark),
        const SizedBox(height: 4),
        if (_models.isNotEmpty && !_showCustomModel)
          _Dropdown(
            value: _models.contains(_modelId) ? _modelId : _models.first,
            items: [
              for (final m in _models)
                DropdownMenuItem(
                    value: m, child: Text(m, overflow: TextOverflow.ellipsis)),
              const DropdownMenuItem(
                value: '__custom_model__',
                child: Text('手写模型名…'),
              ),
            ],
            onChanged: (v) {
              if (v == null) return;
              setState(() {
                if (v == '__custom_model__') {
                  _showCustomModel = true;
                  _modelId = '__custom_model__';
                } else {
                  _showCustomModel = false;
                  _modelId = v;
                }
              });
            },
            dark: dark,
          )
        else
          _Inp(
              controller: _modelCustom,
              hint: '模型 ID（从供应商拉取或手写）',
              dark: dark),
        if (_models.isNotEmpty && _showCustomModel) ...[
          const SizedBox(height: 6),
          _Inp(controller: _modelCustom, hint: '手写模型名', dark: dark),
        ],
        if (!oauth) ...[
          const SizedBox(height: 10),
          _FieldLabel('Base URL', dark),
          const SizedBox(height: 4),
          _Inp(
              controller: _base,
              hint: 'https://api.openai.com/v1',
              dark: dark),
          const SizedBox(height: 10),
          _FieldLabel(_hasKey ? 'API Key（已保存可留空）' : 'API Key', dark),
          const SizedBox(height: 4),
          _Inp(
            controller: _key,
            hint: _hasKey
                ? (_keyMasked.isNotEmpty ? _keyMasked : '••••••••')
                : 'sk-…',
            dark: dark,
            obscure: true,
          ),
        ],
        if (oauth) ...[
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: PixelColors.purple.withValues(alpha: 0.08),
              borderRadius: BorderRadius.circular(6),
              border: Border.all(
                  color: PixelColors.purple.withValues(alpha: 0.25)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  _oauthKind == 'xai'
                      ? 'Grok OAuth（设备码 · 本机）'
                      : 'ChatGPT OAuth（PKCE · 本机）',
                  style: const TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w800,
                    color: PixelColors.purple,
                  ),
                ),
                if (_oauthDone) ...[
                  const SizedBox(height: 6),
                  Text(
                    '状态：已登录 · ${_keyMasked.isNotEmpty ? _keyMasked : "令牌已保存"}',
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                      color: PixelColors.green,
                    ),
                  ),
                ],
                const SizedBox(height: 8),
                PxPrimaryBtn(
                  label: _oauthDone
                      ? (_oauthKind == 'xai' ? '重新 Grok 登录' : '重新 ChatGPT 登录')
                      : (_oauthKind == 'xai' ? 'Grok 登录' : 'ChatGPT 登录'),
                  onTap: _startOauth,
                ),
                if (_oauthStatus.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Text(
                    _oauthStatus,
                    style: TextStyle(fontSize: 11.5, color: ink3, height: 1.4),
                  ),
                ],
                if (_oauthKind != 'xai') ...[
                  const SizedBox(height: 10),
                  Text(
                    '浏览器跳到 localhost 失败时：复制地址栏完整 URL，粘贴到下方（不要重新开始）',
                    style: TextStyle(fontSize: 11, color: ink3, height: 1.35),
                  ),
                  const SizedBox(height: 4),
                  _Inp(
                    controller: _oauthCallback,
                    hint: 'http://localhost:1455/auth/callback?code=…&state=…',
                    dark: dark,
                  ),
                  const SizedBox(height: 6),
                  PxGhostBtn(
                    label: '用回调地址完成登录',
                    onTap: _completeOauth,
                  ),
                ],
              ],
            ),
          ),
        ],
        const SizedBox(height: 12),
        Text(_hint, style: TextStyle(fontSize: 11, color: ink3, height: 1.4)),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: PxGhostBtn(
                label: _oauth
                    ? (_oauthDone || _hasKey
                        ? '拉取 OAuth 模型'
                        : '请先登录 OAuth')
                    : (c.pcConnected ? '刷新目录 / 拉取模型' : '测试连接 / 拉取模型'),
                onTap: _fetchModels,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: PxPrimaryBtn(
                label: '应用模型',
                onTap: _apply,
              ),
            ),
          ],
        ),
        if (!oauth) ...[
          const SizedBox(height: 8),
          PxGhostBtn(
            label: '仅保存到本机配置',
            onTap: () async {
              if (_busy) return;
              final model = _selectedModel;
              if (model.isEmpty || _base.text.trim().isEmpty) {
                c.showToast('请填写 Base URL 与模型');
                return;
              }
              setState(() => _busy = true);
              try {
                await _syncLocal(
                    o, model, _base.text.trim(), _key.text.trim());
                await c.refreshAll();
                c.showToast('本机配置已保存');
              } catch (e) {
                c.showToast('$e');
              } finally {
                if (mounted) setState(() => _busy = false);
              }
            },
          ),
          const SizedBox(height: 8),
          PxGhostBtn(
            label: '清除本机 LLM 配置',
            onTap: () async {
              if (_busy) return;
              setState(() => _busy = true);
              try {
                await c.clearLocalLlm();
                _base.clear();
                _key.clear();
                _modelCustom.clear();
                setState(() {
                  _modelId = '';
                  _models = [];
                  _hasKey = false;
                  _keyMasked = '';
                  _showCustomModel = true;
                });
                await _reload(refresh: false);
              } finally {
                if (mounted) setState(() => _busy = false);
              }
            },
          ),
        ],
      ],
    );
  }
}

class _FieldLabel extends StatelessWidget {
  const _FieldLabel(this.text, this.dark);
  final String text;
  final bool dark;
  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: TextStyle(
        fontSize: 11.5,
        fontWeight: FontWeight.w600,
        color: dark ? PixelColors.dInk3 : PixelColors.ink3,
      ),
    );
  }
}

class _Inp extends StatelessWidget {
  const _Inp({
    required this.controller,
    required this.hint,
    required this.dark,
    this.obscure = false,
  });
  final TextEditingController controller;
  final String hint;
  final bool dark;
  final bool obscure;

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: controller,
      obscureText: obscure,
      style: TextStyle(
        fontSize: 13.5,
        fontFamily: 'JetBrains Mono',
        color: dark ? PixelColors.dInk : PixelColors.ink,
      ),
      decoration: InputDecoration(
        hintText: hint,
        hintStyle: TextStyle(
          fontSize: 12.5,
          color: dark ? PixelColors.dInk3 : PixelColors.ink3,
        ),
        isDense: true,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
        filled: true,
        fillColor: dark
            ? Colors.white.withValues(alpha: 0.05)
            : PixelColors.ink.withValues(alpha: 0.04),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(5),
          borderSide: BorderSide(
            color: dark
                ? Colors.white.withValues(alpha: 0.12)
                : PixelColors.ink.withValues(alpha: 0.12),
          ),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(5),
          borderSide: BorderSide(
            color: dark
                ? Colors.white.withValues(alpha: 0.12)
                : PixelColors.ink.withValues(alpha: 0.12),
          ),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(5),
          borderSide: const BorderSide(color: PixelColors.purple, width: 1.2),
        ),
      ),
    );
  }
}

class _Dropdown extends StatelessWidget {
  const _Dropdown({
    required this.value,
    required this.items,
    required this.onChanged,
    required this.dark,
  });
  final String? value;
  final List<DropdownMenuItem<String>> items;
  final ValueChanged<String?> onChanged;
  final bool dark;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      decoration: BoxDecoration(
        color: dark
            ? Colors.white.withValues(alpha: 0.05)
            : PixelColors.ink.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(5),
        border: Border.all(
          color: dark
              ? Colors.white.withValues(alpha: 0.12)
              : PixelColors.ink.withValues(alpha: 0.12),
        ),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          value: value,
          isExpanded: true,
          items: items,
          onChanged: onChanged,
          style: TextStyle(
            fontSize: 13,
            color: dark ? PixelColors.dInk : PixelColors.ink,
          ),
          dropdownColor: dark ? const Color(0xFF151A2E) : PixelColors.elev,
        ),
      ),
    );
  }
}
