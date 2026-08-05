import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

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
          'llm_base_url': '',
          'llm_model': '',
        },
        'models': <String>[],
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
          'llm_model': '',
        },
        'models': <String>[],
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
    _active = activeModel.isNotEmpty
        ? '$activePid · $activeModel'
        : (localCfg?['model']?.toString().isNotEmpty == true
            ? '本机 · ${localCfg!['model']}'
            : '—');

    if (activePid.isNotEmpty && _opts.any((o) => o.id == activePid)) {
      _providerId = activePid;
      _modelId = activeModel;
    } else if (localCfg != null &&
        (localCfg['base_url']?.toString().isNotEmpty == true)) {
      _providerId = '__custom__';
      _modelId = localCfg['model']?.toString() ?? '';
    } else if (_opts.isNotEmpty) {
      _providerId = _opts.first.id;
    }
    _hasKey = localCfg?['has_key'] == true;
    _keyMasked = localCfg?['api_key_masked']?.toString() ?? '';
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
    if (!c.pcConnected) {
      _hint = '未连 PC：仅本机直连。填 Base URL + API Key，点「测试连接」从供应商 /models 拉取最新列表。';
    } else if (_oauth) {
      _hint = 'OAuth 供应商：登录授权后从目录刷新模型列表，应用后自动切到远端对话。';
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

  Future<void> _fetchModels() async {
    if (_busy) return;
    final o = _cur;
    final base = _base.text.trim();
    final key = _key.text.trim();
    final model = _selectedModel;

    setState(() => _busy = true);
    try {
      if (_oauth) {
        if (!c.pcConnected) {
          c.showToast('OAuth 需先连接 PC');
          return;
        }
        await _reload(refresh: true);
        c.showToast(_models.isEmpty
            ? '目录已刷新 · 若仍无模型请先完成登录授权'
            : '已刷新 · ${_models.length} 个模型');
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
          if (key.isEmpty && !_hasKey) 'api_key': 'local',
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
        if (!c.pcConnected) {
          c.showToast('OAuth 供应商需先连接 PC');
          c.setTab(AppTab.remote);
          return;
        }
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
    if (!c.pcConnected) {
      c.showToast('OAuth 需先连接 PC');
      c.setTab(AppTab.remote);
      return;
    }
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
          _oauthStatus = '设备码 $code\n请打开 $url 授权';
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
        final url = r['authorization_url']?.toString() ??
            r['url']?.toString() ??
            r['auth_url']?.toString() ??
            '';
        setState(() {
          _oauthStatus = r['message']?.toString() ??
              (url.isNotEmpty
                  ? '请在浏览器完成授权\n若未自动完成，用下方备用粘贴回调地址'
                  : '已发起登录，请在浏览器完成授权');
        });
        if (url.isNotEmpty) {
          await _openAuthUrl(url);
        } else {
          c.showToast(r['message']?.toString() ?? '请按提示完成授权');
        }
        _startPollLoop();
        if (r['callback_listening'] != true) {
          c.showToast('请授权后，若未自动完成则粘贴回调 URL');
        }
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
            (isOk(r) && r['active_provider_id'] != null)) {
          _oauthPoll?.cancel();
          await _reload(refresh: true);
          await c.goRemoteChatAfterOauth(
              toastMsg: r['message']?.toString() ?? 'Grok 登录成功 · 已切远端');
          return true;
        }
        if (r['status']?.toString() == 'error' ||
            (r['ok'] == false && r['status']?.toString() != 'pending')) {
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
            (isOk(r) && r['active_provider_id'] != null)) {
          _oauthPoll?.cancel();
          await _reload(refresh: true);
          await c.goRemoteChatAfterOauth(
              toastMsg:
                  r['message']?.toString() ?? 'ChatGPT 登录成功 · 已切远端可发送');
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
            _oauthStatus = r['message']?.toString() ?? '等待 ChatGPT 授权…';
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
    if (!c.pcConnected) {
      c.showToast('请先连接 PC');
      return;
    }
    final url = _oauthCallback.text.trim();
    if (url.isEmpty) {
      c.showToast('请粘贴完整回调地址');
      return;
    }
    if (!url.contains('code=') && !RegExp(r'[?&]code[=%]').hasMatch(url)) {
      c.showToast('URL 里应包含 code= 参数，请复制地址栏完整链接');
      return;
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
      _oauthPoll?.cancel();
      _oauthCallback.clear();
      setState(() {
        _oauthStatus = '登录成功 · 已写入 PC';
      });
      await _reload(refresh: true);
      await c.goRemoteChatAfterOauth(
          toastMsg: r['message']?.toString() ?? 'ChatGPT 登录成功 · 已切远端');
    } catch (e) {
      setState(() => _oauthStatus = '失败: $e');
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
              ? '供应商与模型来自 PC 实时目录。OAuth 应用后自动切远端对话。'
              : '未连 PC：仅本机直连。填 Base URL + API Key，点「测试连接」从供应商拉取最新模型列表。',
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
                      ? 'Grok OAuth（设备码）'
                      : 'ChatGPT OAuth（PKCE）',
                  style: const TextStyle(
                    fontSize: 12.5,
                    fontWeight: FontWeight.w800,
                    color: PixelColors.purple,
                  ),
                ),
                const SizedBox(height: 8),
                PxPrimaryBtn(
                  label: _oauthKind == 'xai' ? 'Grok 登录' : 'ChatGPT 登录',
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
                    '备用：粘贴浏览器回调地址（含 code=）',
                    style: TextStyle(fontSize: 11, color: ink3),
                  ),
                  const SizedBox(height: 4),
                  _Inp(
                    controller: _oauthCallback,
                    hint: 'https://…?code=…&state=…',
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
                label: c.pcConnected ? '刷新目录 / 拉取模型' : '测试连接 / 拉取模型',
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
