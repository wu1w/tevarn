import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';
import 'pixel_widgets.dart';

/// OCR / Voice / MCP / search keys for local agent tools.
class AgentSettingsPanel extends StatefulWidget {
  const AgentSettingsPanel({super.key});

  @override
  State<AgentSettingsPanel> createState() => _AgentSettingsPanelState();
}

class _AgentSettingsPanelState extends State<AgentSettingsPanel> {
  final _visionKey = TextEditingController();
  final _visionEp = TextEditingController();
  final _speechKey = TextEditingController();
  final _speechRegion = TextEditingController(text: 'eastasia');
  final _tavily = TextEditingController();
  final _mcpName = TextEditingController(text: 'default');
  final _mcpUrl = TextEditingController();
  final _ttsVoice = TextEditingController(text: 'zh-CN-XiaoxiaoNeural');
  bool _loaded = false;
  int _seenMeGen = -1;
  bool _busy = false;
  bool _enableMcp = true;
  bool _enableSkills = true;
  bool _hasVisionKey = false;
  bool _hasSpeechKey = false;
  bool _hasTavily = false;
  List<Map<String, dynamic>> _mcpServers = [];
  List<Map<String, dynamic>> _skills = [];
  String _status = '';

  static bool _isMasked(String? s) {
    if (s == null || s.isEmpty) return false;
    return s.contains('…') || s.contains('...') || s == '••••';
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final c = context.watch<AppController>();
    if (!_loaded) {
      _loaded = true;
      _seenMeGen = c.mePanelGen;
      _load();
      return;
    }
    if (c.mePanelGen != _seenMeGen) {
      _seenMeGen = c.mePanelGen;
      _load();
    }
  }

  @override
  void dispose() {
    _visionKey.dispose();
    _visionEp.dispose();
    _speechKey.dispose();
    _speechRegion.dispose();
    _tavily.dispose();
    _mcpName.dispose();
    _mcpUrl.dispose();
    _ttsVoice.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final c = context.read<AppController>();
    try {
      final r = await c.bridge.agentConfigGet();
      if (r['ok'] == false) {
        _status = r['error']?.toString() ?? '加载工具配置失败';
        if (mounted) setState(() {});
        return;
      }
      final cfg = r['config'] is Map
          ? Map<String, dynamic>.from(r['config'] as Map)
          : <String, dynamic>{};

      // Secrets: never put masked placeholders into the text field.
      // Empty field + has_* flag means "keep existing on save".
      final vk = cfg['azure_vision_key']?.toString() ?? '';
      final sk = cfg['azure_speech_key']?.toString() ?? '';
      final tk = cfg['tavily_api_key']?.toString() ?? '';
      _hasVisionKey = cfg['has_azure_vision_key'] == true ||
          (vk.isNotEmpty && !_isMasked(vk));
      _hasSpeechKey = cfg['has_azure_speech_key'] == true ||
          (sk.isNotEmpty && !_isMasked(sk));
      _hasTavily =
          cfg['has_tavily_api_key'] == true || (tk.isNotEmpty && !_isMasked(tk));
      // If server returned a real (unmasked) key, show it; else leave blank.
      _visionKey.text = _isMasked(vk) ? '' : vk;
      _speechKey.text = _isMasked(sk) ? '' : sk;
      _tavily.text = _isMasked(tk) ? '' : tk;

      _visionEp.text = cfg['azure_vision_endpoint']?.toString() ?? '';
      _speechRegion.text =
          (cfg['azure_speech_region']?.toString().isNotEmpty == true)
              ? cfg['azure_speech_region'].toString()
              : 'eastasia';
      _ttsVoice.text = (cfg['tts_voice']?.toString().isNotEmpty == true)
          ? cfg['tts_voice'].toString()
          : 'zh-CN-XiaoxiaoNeural';
      _enableMcp = cfg['enable_mcp'] != false;
      _enableSkills = cfg['enable_skills'] != false;

      try {
        final sk = await c.bridge.localSkills();
        if (sk['ok'] != false && sk['skills'] is List) {
          _skills = (sk['skills'] as List)
              .whereType<Map>()
              .map((e) => Map<String, dynamic>.from(e))
              .toList();
        }
      } catch (_) {}
      final m = await c.bridge.mcpConfigGet();
      if (m['ok'] == false) {
        _status = m['error']?.toString() ?? '加载 MCP 失败';
      } else {
        final mcfg = m['config'] is Map
            ? Map<String, dynamic>.from(m['config'] as Map)
            : <String, dynamic>{};
        final servers = mcfg['servers'];
        _mcpServers = [];
        if (servers is List) {
          for (final s in servers) {
            if (s is Map) {
              _mcpServers.add(Map<String, dynamic>.from(s));
            }
          }
        }
        _status = '';
      }
    } catch (e) {
      _status = '加载失败: $e';
    }
    if (mounted) setState(() {});
  }

  Future<void> _saveAgent() async {
    if (_busy) return;
    final c = context.read<AppController>();
    setState(() => _busy = true);
    try {
      final r = await c.bridge.agentConfigSet({
        'azure_vision_key': _visionKey.text.trim(),
        'azure_vision_endpoint': _visionEp.text.trim(),
        'azure_speech_key': _speechKey.text.trim(),
        'azure_speech_region': _speechRegion.text.trim(),
        'tavily_api_key': _tavily.text.trim(),
        'tts_voice': _ttsVoice.text.trim(),
        'enable_mcp': _enableMcp,
        'enable_skills': _enableSkills,
      });
      if (r['ok'] == true) {
        _status = 'Agent 工具配置已保存';
        c.showToast(_status);
        await _load();
      } else {
        _status = _friendlyErr(r['error']?.toString() ?? '保存失败');
        c.showToast(_status);
      }
    } catch (e) {
      _status = '$e';
      c.showToast(_status);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _saveMcp() async {
    if (_busy) return;
    final c = context.read<AppController>();
    final name = _mcpName.text.trim().isEmpty ? 'default' : _mcpName.text.trim();
    final url = _mcpUrl.text.trim();
    if (url.isEmpty) {
      c.showToast('请填写 MCP 服务器 URL');
      return;
    }
    setState(() {
      _busy = true;
      // upsert by name
      final i = _mcpServers.indexWhere((s) => s['name'] == name);
      final row = {
        'name': name,
        'url': url,
        'enabled': true,
        'headers': <String, String>{},
      };
      if (i >= 0) {
        _mcpServers[i] = row;
      } else {
        _mcpServers.add(row);
      }
    });
    try {
      final r = await c.bridge.mcpConfigSet({'servers': _mcpServers});
      if (r['ok'] == true) {
        _status = 'MCP 已保存 · ${_mcpServers.length} 个服务器';
        c.showToast(_status);
        _mcpUrl.clear();
        await _load();
      } else {
        _status = _friendlyErr(r['error']?.toString() ?? 'MCP 保存失败');
        c.showToast(_status);
        await _load();
      }
    } catch (e) {
      _status = '$e';
      c.showToast(_status);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _removeMcp(String name) async {
    if (_busy) return;
    final c = context.read<AppController>();
    final prev = List<Map<String, dynamic>>.from(_mcpServers);
    setState(() {
      _mcpServers.removeWhere((s) => s['name'] == name);
      _busy = true;
    });
    try {
      final r = await c.bridge.mcpConfigSet({'servers': _mcpServers});
      if (r['ok'] == true) {
        c.showToast('已移除 $name');
      } else {
        _mcpServers = prev;
        c.showToast(_friendlyErr(r['error']?.toString() ?? '移除失败'));
      }
    } catch (e) {
      _mcpServers = prev;
      c.showToast(_friendlyErr('$e'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _installMattPack() async {
    if (_busy) return;
    final c = context.read<AppController>();
    setState(() {
      _busy = true;
      _status = '正在安装 Matt 技能包…';
    });
    try {
      final r = await c.bridge.localSkillsInstallPack('mattpocock-mobile');
      if (r['ok'] == true) {
        final msg = r['message']?.toString() ?? '安装完成';
        _status = msg;
        c.showToast(msg);
        await _load();
      } else {
        _status = _friendlyErr(r['error']?.toString() ?? '安装失败');
        c.showToast(_status);
      }
    } catch (e) {
      _status = _friendlyErr('$e');
      c.showToast(_status);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _friendlyErr(String raw) {
    final e = raw.trim();
    if (e.contains('unknown method')) {
      return '引擎方法未注册 · 请更新到最新安装包后重试';
    }
    if (e.startsWith('Exception:')) {
      return e.replaceFirst('Exception:', '').trim();
    }
    return e.isEmpty ? '操作失败' : e;
  }

  String _secretHint(bool has, String emptyHint) {
    if (has) return '已配置 · 留空则保持原值 · 填新值覆盖';
    return emptyHint;
  }

  @override
  Widget build(BuildContext context) {
    final c = context.watch<AppController>();
    final dark = c.dark;
    final ink = dark ? PixelColors.dInk : PixelColors.ink;
    final ink3 = dark ? PixelColors.dInk3 : PixelColors.ink3;

    return PxCard(
      dark: dark,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            '本机工具 · OCR / 语音 / 搜索 / MCP',
            style: TextStyle(
              fontSize: 14.5,
              fontWeight: FontWeight.w800,
              color: ink,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'OCR 已内建（ocr.space 免费回退）；GPT/Grok 等多模态直接看图，不强制 OCR。DeepSeek/GLM 等盲模型发图时自动 OCR。TTS 默认微软 Edge。',
            style: TextStyle(fontSize: 11.5, height: 1.4, color: ink3),
          ),
          if (_hasVisionKey || _hasSpeechKey || _hasTavily) ...[
            const SizedBox(height: 8),
            Text(
              [
                if (_hasVisionKey) 'Vision✓',
                if (_hasSpeechKey) 'Speech✓',
                if (_hasTavily) 'Tavily✓',
              ].join(' · '),
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w700,
                color: PixelColors.green,
              ),
            ),
          ],
          const SizedBox(height: 12),
          PxField(
            label: 'Azure Vision Key（可选·增强 OCR）',
            controller: _visionKey,
            hint: _secretHint(_hasVisionKey, '可选 · 空则用免费 OCR 回退'),
            obscure: true,
          ),
          PxField(
            label: 'Azure Vision Endpoint',
            controller: _visionEp,
            hint: 'https://xxx.cognitiveservices.azure.com',
          ),
          PxField(
            label: 'Azure Speech Key（TTS）',
            controller: _speechKey,
            hint: _secretHint(_hasSpeechKey, '可选 · 空则用 Edge 免费 TTS'),
            obscure: true,
          ),
          PxField(
            label: 'Speech Region',
            controller: _speechRegion,
            hint: 'eastasia',
          ),
          PxField(
            label: 'TTS 音色',
            controller: _ttsVoice,
            hint: 'zh-CN-XiaoxiaoNeural',
          ),
          PxField(
            label: 'Tavily API Key（增强搜索）',
            controller: _tavily,
            hint: _secretHint(_hasTavily, '可选 · 空则用 Bing/Wiki 等免费源'),
            obscure: true,
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              Expanded(
                child: Text('启用 Skills', style: TextStyle(fontSize: 13, color: ink)),
              ),
              PxToggle(
                value: _enableSkills,
                onChanged: (v) => setState(() => _enableSkills = v),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            '已装 Skills · ${_skills.length} 个（含默认 codex-security）',
            style: TextStyle(fontSize: 11.5, color: ink3),
          ),
          if (_skills.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              _skills
                  .map((s) => s['name']?.toString() ?? s['id']?.toString() ?? '')
                  .where((s) => s.isNotEmpty)
                  .take(12)
                  .join(' · '),
              style: TextStyle(fontSize: 11, height: 1.35, color: ink),
            ),
          ],
          const SizedBox(height: 8),
          PxGhostBtn(
            label: _busy ? '…' : '安装 Matt 技能包（手机轻量）',
            onTap: _busy ? () {} : _installMattPack,
          ),
          const SizedBox(height: 4),
          Text(
            '从 GitHub 拉取 grill-me / handoff / tdd / code-review 等 SKILL.md，与 PC 同源。',
            style: TextStyle(fontSize: 11, height: 1.35, color: ink3),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: Text('启用 MCP', style: TextStyle(fontSize: 13, color: ink)),
              ),
              PxToggle(
                value: _enableMcp,
                onChanged: (v) => setState(() => _enableMcp = v),
              ),
            ],
          ),
          const SizedBox(height: 10),
          PxPrimaryBtn(
            label: _busy ? '保存中…' : '保存工具配置',
            cyan: true,
            onTap: _busy ? () {} : _saveAgent,
          ),
          const SizedBox(height: 8),
          PxGhostBtn(
            label: '重新加载配置',
            onTap: _busy ? () {} : _load,
          ),
          const SizedBox(height: 16),
          Text(
            'MCP 服务器',
            style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w800, color: ink),
          ),
          const SizedBox(height: 4),
          Text(
            '兼容社区 streamable HTTP MCP。保存后 agent 可通过 mcp_list / mcp_call 调用。\n'
            'Firecrawl：PC 端 MCP 商店一键装 stdio（npx firecrawl-mcp + FIRECRAWL_API_KEY）；'
            '手机可填远程 HTTP MCP URL（若自建 streamable 端点）。',
            style: TextStyle(fontSize: 11.5, height: 1.4, color: ink3),
          ),
          const SizedBox(height: 8),
          if (_mcpServers.isEmpty)
            Text('暂无 MCP · 添加后才有动态工具',
                style: TextStyle(fontSize: 12, color: ink3))
          else
            ..._mcpServers.map((s) {
              final name = s['name']?.toString() ?? '';
              final url = s['url']?.toString() ?? '';
              return Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        '$name\n$url',
                        style: TextStyle(fontSize: 12, height: 1.3, color: ink),
                      ),
                    ),
                    GestureDetector(
                      onTap: _busy ? null : () => _removeMcp(name),
                      child: Text('删除',
                          style: TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.w700,
                              color: PixelColors.red)),
                    ),
                  ],
                ),
              );
            }),
          PxField(label: '名称', controller: _mcpName, hint: 'default'),
          PxField(
            label: 'MCP URL',
            controller: _mcpUrl,
            hint: 'https://example.com/mcp',
          ),
          PxGhostBtn(
            label: _busy ? '…' : '添加 / 更新 MCP',
            onTap: _busy ? () {} : _saveMcp,
          ),
          if (_status.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(_status, style: TextStyle(fontSize: 11.5, color: ink3)),
          ],
        ],
      ),
    );
  }
}
