import 'dart:async';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../models/app_models.dart';
import '../services/attach_utils.dart';
import '../services/app_controller.dart';
import '../services/voice_service.dart';
import '../theme/pixel_theme.dart';
import '../widgets/chat_markdown.dart';
import '../widgets/pixel_icons.dart';
import '../widgets/tool_trace_panel.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _ctrl = TextEditingController();
  final _scroll = ScrollController();
  bool _hasText = false;
  bool _listening = false;
  /// When true, stream/new messages pin the list to the bottom.
  /// User scroll-up clears this; returning near bottom re-enables.
  bool _stickBottom = true;
  int _prevMsgLen = 0;

  @override
  void initState() {
    super.initState();
    _ctrl.addListener(() {
      final next = _ctrl.text.trim().isNotEmpty;
      if (next != _hasText) setState(() => _hasText = next);
    });
    _scroll.addListener(_onUserScroll);
  }

  void _onUserScroll() {
    if (!_scroll.hasClients) return;
    final pos = _scroll.position;
    final dist = pos.maxScrollExtent - pos.pixels;
    if (dist > 96) {
      _stickBottom = false;
    } else if (dist <= 48) {
      _stickBottom = true;
    }
    // Near top → load older history (P1 pagination).
    if (pos.pixels <= 48) {
      final ctrl = context.read<AppController>();
      if (ctrl.hasMoreOlder && !ctrl.loadingOlder) {
        unawaited(ctrl.loadOlderMessages());
      }
    }
  }

  @override
  void dispose() {
    _scroll.removeListener(_onUserScroll);
    _ctrl.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _scrollEnd({bool force = false}) {
    if (!force && !_stickBottom) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      if (!force && !_stickBottom) return;
      final max = _scroll.position.maxScrollExtent;
      // jumpTo during stream avoids animation fighting user scroll
      _scroll.jumpTo(max);
    });
  }

  @override
  Widget build(BuildContext context) {
    final c = context.watch<AppController>();
    final dark = c.dark;
    final ink = dark ? PixelColors.dInk : PixelColors.ink;
    final ink2 = dark ? PixelColors.dInk2 : PixelColors.ink2;
    final ink3 = dark ? PixelColors.dInk3 : PixelColors.ink3;
    final card = dark ? const Color(0xFF151A2E) : PixelColors.elev;
    final card2 = dark
        ? Colors.white.withValues(alpha: 0.06)
        : PixelColors.ink.withValues(alpha: 0.05);

    // New tail bubble → re-stick; load-older prepend must NOT jump to bottom.
    final len = c.messages.length;
    if (len > _prevMsgLen) {
      final prepended = c.loadingOlder || c.lastPrependCount > 0;
      if (prepended) {
        final added = len - _prevMsgLen;
        _prevMsgLen = len;
        _stickBottom = false;
        // Preserve viewport: after prepend, keep visual position.
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (!mounted || !_scroll.hasClients) return;
          final pos = _scroll.position;
          // Rough anchor: keep distance from bottom stable when possible.
          final target = (pos.pixels + (added * 72.0)).clamp(0.0, pos.maxScrollExtent);
          _scroll.jumpTo(target);
          // Consume prepend signal
          c.lastPrependCount = 0;
        });
      } else {
        _stickBottom = true;
        _prevMsgLen = len;
        _scrollEnd(force: true);
      }
    } else if (len < _prevMsgLen) {
      _prevMsgLen = len;
    } else if (_stickBottom && len > 0 && c.streaming) {
      // Only chase the tail while the model is generating
      _scrollEnd();
    }

    // Sync external clear of input (after send / controller reset)
    if (c.input.isEmpty && _ctrl.text.isNotEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && c.input.isEmpty && _ctrl.text.isNotEmpty) {
          _ctrl.clear();
          if (_hasText) setState(() => _hasText = false);
        }
      });
    }

    return Column(
      children: [
        Container(
          padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
          decoration: BoxDecoration(
            border: Border(
              bottom: BorderSide(
                color: dark
                    ? Colors.white.withValues(alpha: 0.08)
                    : PixelColors.ink.withValues(alpha: 0.09),
              ),
            ),
          ),
          child: Row(
            children: [
              _IconBtn(
                onTap: c.openDrawer,
                child: PixelIcon.menu(color: ink2),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      c.surface == 'local' ? '本机对话' : '远端 Agent',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w800,
                        color: ink,
                        letterSpacing: -0.2,
                      ),
                    ),
                    Text(
                      c.mode.subtitle.isNotEmpty
                          ? c.mode.subtitle
                          : (c.surface == 'local'
                              ? (c.mode.localLlmReady
                                  ? '本机模型就绪'
                                  : '配置 LLM 后可对话')
                              : (c.pcConnected ? '已连 PC' : '未连 PC')),
                      style: TextStyle(fontSize: 11.5, color: ink3),
                    ),
                  ],
                ),
              ),
              _IconBtn(
                onTap: c.newChat,
                child: PixelIcon.plus(color: ink2),
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: Container(
            padding: const EdgeInsets.all(3),
            decoration: BoxDecoration(
              color: card2,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: dark
                    ? Colors.white.withValues(alpha: 0.1)
                    : PixelColors.ink.withValues(alpha: 0.1),
              ),
            ),
            child: Row(
              children: [
                Expanded(
                  child: _ModeChip(
                    label: '本机对话',
                    active: c.surface == 'local',
                    color: PixelColors.cyan,
                    onTap: () => c.setSurface('local'),
                  ),
                ),
                Expanded(
                  child: _ModeChip(
                    label: '远端 Agent',
                    active: c.surface == 'remote',
                    color: PixelColors.purple,
                    onTap: () {
                      c.setSurface('remote');
                    },
                  ),
                ),
              ],
            ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 6, 12, 4),
          child: Row(
            children: [
              Container(
                width: 7,
                height: 7,
                decoration: BoxDecoration(
                  color: c.surface == 'local'
                      ? (c.mode.localLlmReady
                          ? PixelColors.green
                          : PixelColors.amber)
                      : (c.pcConnected
                          ? PixelColors.green
                          : PixelColors.red),
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  c.healthLine.isNotEmpty
                      ? c.healthLine
                      : (c.mode.reason.isNotEmpty
                          ? c.mode.reason
                          : (c.surface == 'local' ? '本机通道' : '远端通道')),
                  style: TextStyle(fontSize: 11.5, color: ink3),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
        if (c.pendingApprovalCount > 0)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 6),
            child: Material(
              color: PixelColors.amber.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(14),
              child: InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: () => c.setTab(AppTab.approve),
                child: Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  child: Row(
                    children: [
                      Icon(Icons.fact_check_outlined,
                          size: 16, color: PixelColors.amber),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '有 ${c.pendingApprovalCount} 项待审批',
                          style: TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w600,
                            color: dark ? PixelColors.dInk : PixelColors.ink,
                          ),
                        ),
                      ),
                      Icon(Icons.chevron_right,
                          size: 18, color: PixelColors.cyan),
                    ],
                  ),
                ),
              ),
            ),
          ),
        if (c.surface == 'remote' && !c.pcConnected)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 6),
            child: Material(
              color: PixelColors.amber.withValues(alpha: 0.14),
              borderRadius: BorderRadius.circular(14),
              child: InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: () => c.setTab(AppTab.remote),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                  child: Row(
                    children: [
                      Icon(Icons.wifi_off_rounded, size: 16, color: PixelColors.amber),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '未连接 PC · 点此去连接或重连',
                          style: TextStyle(
                            fontSize: 12.5,
                            fontWeight: FontWeight.w600,
                            color: dark ? PixelColors.dInk : PixelColors.ink,
                          ),
                        ),
                      ),
                      Text(
                        '连接',
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w700,
                          color: PixelColors.cyan,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        Expanded(
          child: c.messages.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 28),
                    child: Text(
                      c.surface == 'local'
                          ? (c.mode.localLlmReady
                              ? '开始对话'
                              : '在「我的」配置 API Key 后开始对话')
                          : (c.pcConnected
                              ? '开始远端对话'
                              : '连接 PC 后开始远端对话'),
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        fontSize: 14,
                        color: ink3,
                        height: 1.45,
                      ),
                    ),
                  ),
                )
              : ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
            cacheExtent: 480,
            addAutomaticKeepAlives: false,
            itemCount: c.messages.length + (c.loadingOlder ? 1 : 0),
            itemBuilder: (context, i) {
              if (c.loadingOlder && i == 0) {
                return const Padding(
                  padding: EdgeInsets.symmetric(vertical: 10),
                  child: Center(
                    child: SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  ),
                );
              }
              if (c.loadingOlder) {
                i -= 1;
              }
              final m = c.messages[i];
              if (m.role == 'confirm') {
                final kind = m.modelText ?? m.who;
                final rawId = m.id.startsWith('confirm-')
                    ? m.id.substring('confirm-'.length)
                    : m.id;
                return RepaintBoundary(
                  key: ValueKey(m.id),
                  child: _ConfirmBubble(
                    detail: m.text,
                    dark: dark,
                    ink: ink,
                    card: card,
                    onApprove: () => c.decideFromChat(
                      id: rawId,
                      approved: true,
                      kind: kind.isEmpty ? 'escalation' : kind,
                    ),
                    onDeny: () => c.decideFromChat(
                      id: rawId,
                      approved: false,
                      kind: kind.isEmpty ? 'escalation' : kind,
                    ),
                    onOpenList: () => c.setTab(AppTab.approve),
                  ),
                );
              }
              final isLastAssistant = !m.streaming &&
                  m.role == 'assistant' &&
                  i == c.messages.length - 1;
              return RepaintBoundary(
                key: ValueKey(m.id),
                child: _ChatBubble(
                  msg: m,
                  dark: dark,
                  ink: ink,
                  ink3: ink3,
                  card: card,
                  card2: card2,
                  showActions: !m.streaming && m.text.isNotEmpty,
                  canRegenerate: isLastAssistant && !c.streaming,
                  onCopy: () async {
                    await Clipboard.setData(ClipboardData(text: m.text));
                    c.showToast('已复制');
                  },
                  onRegenerate: isLastAssistant ? () => c.regenerateLast() : null,
                  onImageTap: m.hasImages
                      ? (bytes) => _showImagePreview(context, bytes)
                      : null,
                ),
              );
            },
          ),
        ),
        if (c.attachments.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
            child: SizedBox(
              height: 88,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: c.attachments.length,
                separatorBuilder: (_, __) => const SizedBox(width: 8),
                itemBuilder: (context, i) {
                  final f = c.attachments[i];
                  final isImg =
                      f.isImage && f.bytes != null && f.bytes!.isNotEmpty;
                  return Stack(
                    clipBehavior: Clip.none,
                    children: [
                      Container(
                        width: isImg ? 88 : 140,
                        height: 88,
                        decoration: BoxDecoration(
                          color: card2,
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(
                            color: dark
                                ? Colors.white.withValues(alpha: 0.12)
                                : PixelColors.ink.withValues(alpha: 0.12),
                          ),
                        ),
                        clipBehavior: Clip.antiAlias,
                        child: isImg
                            ? Image.memory(
                                f.bytes!,
                                fit: BoxFit.cover,
                                width: 88,
                                height: 88,
                                gaplessPlayback: true,
                                errorBuilder: (_, __, ___) => Center(
                                  child: Icon(Icons.broken_image_outlined,
                                      size: 28, color: ink3),
                                ),
                              )
                            : Padding(
                                padding: const EdgeInsets.all(10),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Icon(Icons.insert_drive_file_outlined,
                                        size: 22, color: ink2),
                                    const Spacer(),
                                    Text(
                                      f.name,
                                      maxLines: 2,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(
                                        fontSize: 11.5,
                                        color: ink2,
                                        height: 1.25,
                                      ),
                                    ),
                                  ],
                                ),
                              ),
                      ),
                      Positioned(
                        top: -4,
                        right: -4,
                        child: Material(
                          color: dark ? const Color(0xFF1A1F33) : Colors.white,
                          shape: const CircleBorder(),
                          elevation: 1,
                          child: InkWell(
                            customBorder: const CircleBorder(),
                            onTap: () => c.removeAttach(i),
                            child: Padding(
                              padding: const EdgeInsets.all(4),
                              child: Icon(Icons.close, size: 14, color: ink3),
                            ),
                          ),
                        ),
                      ),
                      if (isImg)
                        Positioned(
                          left: 6,
                          bottom: 6,
                          child: Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: Colors.black.withValues(alpha: 0.55),
                              borderRadius: BorderRadius.circular(8),
                            ),
                            child: Text(
                              f.name.length > 12
                                  ? '${f.name.substring(0, 10)}…'
                                  : f.name,
                              style: const TextStyle(
                                fontSize: 9.5,
                                color: Colors.white,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                        ),
                    ],
                  );
                },
              ),
            ),
          ),
        // Desktop-style activity strip above composer (replaces Dynamic Island)
        _ComposerStatusStrip(
          controller: c,
          listening: _listening,
          dark: dark,
          ink3: ink3,
        ),
        Padding(
          padding: EdgeInsets.fromLTRB(
            10,
            0,
            10,
            10 + MediaQuery.viewInsetsOf(context).bottom,
          ),
          child: Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: dark ? const Color(0xFF151A2E) : PixelColors.card,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: dark
                    ? Colors.white.withValues(alpha: 0.12)
                    : PixelColors.ink.withValues(alpha: 0.16),
              ),
              boxShadow: PixelTheme.hardShadow,
            ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                _SquareBtn(
                  onTap: () => _pickAttach(context, c),
                  bg: card2,
                  child: PixelIcon.attach(color: ink2),
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 120),
                    child: TextField(
                      controller: _ctrl,
                      minLines: 1,
                      maxLines: 5,
                      textInputAction: TextInputAction.send,
                      // Prevent IME from keeping focus after send action.
                      onEditingComplete: () {
                        if (c.streaming) return;
                        unawaited(_doSend(c));
                      },
                      onChanged: (v) => c.setInput(v, notify: false),
                      onSubmitted: (_) {
                        // Keyboard "send" never stops generation — only the red stop btn does.
                        if (c.streaming) return;
                        unawaited(_doSend(c));
                      },
                      style: TextStyle(
                        fontSize: 14.5,
                        height: 1.45,
                        color: ink,
                      ),
                      decoration: InputDecoration(
                        hintText: c.mode.placeholder.isNotEmpty
                            ? c.mode.placeholder
                            : (c.surface == 'local'
                                ? '发消息给本机模型…'
                                : '发消息给远端 Agent…'),
                        hintStyle: TextStyle(color: ink3, fontSize: 14.5),
                        border: InputBorder.none,
                        enabledBorder: InputBorder.none,
                        focusedBorder: InputBorder.none,
                        isDense: true,
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 6,
                          vertical: 7,
                        ),
                        filled: false,
                      ),
                    ),
                  ),
                ),
                if (c.voiceOn) ...[
                  const SizedBox(width: 4),
                  GestureDetector(
                    onLongPressStart: (_) => _startVoice(c),
                    onLongPressEnd: (_) => _stopVoice(c),
                    onTap: () {
                      if (_listening) {
                        _stopVoice(c);
                      } else {
                        c.showToast('按住麦克风说话');
                      }
                    },
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 120),
                      width: 36,
                      height: 36,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: _listening
                            ? PixelColors.red.withValues(alpha: 0.18)
                            : card2,
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(
                          color: _listening
                              ? PixelColors.red.withValues(alpha: 0.5)
                              : (dark
                                  ? Colors.white.withValues(alpha: 0.1)
                                  : PixelColors.ink.withValues(alpha: 0.1)),
                        ),
                      ),
                      child: Icon(
                        _listening ? Icons.mic : Icons.mic_none_rounded,
                        size: 20,
                        color: _listening ? PixelColors.red : ink2,
                      ),
                    ),
                  ),
                ],
                const SizedBox(width: 6),
                _SendBtn(
                  streaming: c.streaming,
                  enabled: _hasText ||
                      c.streaming ||
                      c.attachments.isNotEmpty,
                  warn: (_hasText || c.attachments.isNotEmpty) &&
                      !c.mode.canSend,
                  onTap: () => _doSend(c),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Future<void> _startVoice(AppController c) async {
    if (kIsWeb) {
      c.showToast('Web 不支持语音输入');
      return;
    }
    if (!c.voiceOn) {
      c.showToast('请先在「我的」开启语音输入');
      return;
    }
    setState(() => _listening = true);
    c.pulseIsland(text: '聆听中…', kind: 'local');
    final text = await VoiceService.instance.listenOnce(
      onPartial: (p) {
        if (!mounted) return;
        _ctrl.text = p;
        _ctrl.selection = TextSelection.collapsed(offset: _ctrl.text.length);
        c.setInput(p, notify: false);
        setState(() => _hasText = p.trim().isNotEmpty);
      },
    );
    if (!mounted) return;
    setState(() => _listening = false);
    if (text != null && text.isNotEmpty) {
      _ctrl.text = text;
      _ctrl.selection = TextSelection.collapsed(offset: text.length);
      c.setInput(text, notify: true);
      setState(() => _hasText = true);
      c.showToast('已识别 · 可发送');
    } else {
      c.showToast('未识别到语音');
    }
  }

  Future<void> _stopVoice(AppController c) async {
    await VoiceService.instance.stopListen();
    if (mounted) setState(() => _listening = false);
  }

  Future<void> _doSend(AppController c) async {
    if (c.streaming) {
      // Red stop button path only (keyboard submit is blocked above).
      await c.stopGeneration();
      return;
    }
    final text = _ctrl.text;
    if (text.trim().isEmpty && c.attachments.isEmpty) return;

    // Instant UX: dismiss keyboard + clear box before network round-trip.
    FocusManager.instance.primaryFocus?.unfocus();
    SystemChannels.textInput.invokeMethod('TextInput.hide');
    _ctrl.clear();
    c.setInput('', notify: false);
    if (mounted) setState(() => _hasText = false);

    final accepted = await c.send(text);
    if (!mounted) return;
    if (accepted) {
      _stickBottom = true;
      // Ensure controller stays empty even if notify raced.
      if (_ctrl.text.isNotEmpty) _ctrl.clear();
      c.setInput('', notify: false);
      setState(() => _hasText = false);
      _scrollEnd(force: true);
    } else {
      // Restore draft when blocked (cannot send / empty / busy).
      _ctrl.value = TextEditingValue(
        text: text,
        selection: TextSelection.collapsed(offset: text.length),
      );
      c.setInput(text, notify: false);
      setState(() => _hasText = text.trim().isNotEmpty);
    }
  }

  Future<void> _pickAttach(BuildContext context, AppController c) async {
    if (c.surface == 'remote' && !c.pcConnected) {
      c.showToast('远端附件需先连接 PC');
      c.setTab(AppTab.remote);
      return;
    }
    showModalBottomSheet(
      context: context,
      backgroundColor: c.dark ? const Color(0xFF151A2E) : PixelColors.card,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(12)),
      ),
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              _SheetTile(
                title: '文件',
                sub: c.surface == 'remote'
                    ? '文档 / 图片 · 发送时上传到 PC'
                    : '图片会显示预览 · 发送时 OCR 识别文字',
                onTap: () async {
                  Navigator.pop(ctx);
                  final r = await FilePicker.platform.pickFiles(
                    allowMultiple: true,
                    withData: true,
                  );
                  if (r == null) return;
                  var n = 0;
                  for (final f in r.files) {
                    if (f.name.isEmpty) continue;
                    final af = AttachFile(
                      name: f.name,
                      bytes: f.bytes != null
                          ? Uint8List.fromList(f.bytes!)
                          : null,
                      path: f.path,
                      mime: _guessMime(f.name),
                    );
                    final bytes = await resolveAttachBytes(af);
                    if (bytes == null || bytes.isEmpty) {
                      c.showToast('无法读取 ${f.name}');
                      continue;
                    }
                    c.addAttach(af);
                    n++;
                  }
                  if (n > 0) {
                    c.showToast(
                        '已选择 $n 个文件（内容已读入）');
                  }
                },
              ),
              _SheetTile(
                title: '相机拍照',
                sub: c.cameraOn
                    ? (kIsWeb
                        ? 'Web 可能受限 · 真机摄像头优先'
                        : '实时预览拍摄 · 真机摄像头')
                    : '已在「我的」关闭相机',
                onTap: () async {
                  Navigator.pop(ctx);
                  if (!c.cameraOn) {
                    c.showToast('请先在「我的」开启相机');
                    return;
                  }
                  try {
                    final picker = ImagePicker();
                    final x = await picker.pickImage(
                        source: ImageSource.camera, imageQuality: 85);
                    if (x != null) {
                      final bytes = await x.readAsBytes();
                      c.addAttach(AttachFile(
                        name: x.name,
                        bytes: bytes,
                        path: x.path,
                        mime: _guessMime(x.name),
                      ));
                      c.showToast('已附加拍照 ${x.name}');
                    }
                  } catch (_) {
                    c.showToast(kIsWeb
                        ? 'Web 相机不可用，请用相册或文件'
                        : '请在 Android/iOS 真机使用相机');
                  }
                },
              ),
              _SheetTile(
                title: '相册',
                sub: '从相册选择图片 · 预览后发送',
                onTap: () async {
                  Navigator.pop(ctx);
                  try {
                    final picker = ImagePicker();
                    final x = await picker.pickImage(
                        source: ImageSource.gallery, imageQuality: 85);
                    if (x != null) {
                      final bytes = await x.readAsBytes();
                      c.addAttach(AttachFile(
                        name: x.name,
                        bytes: bytes,
                        path: x.path,
                        mime: _guessMime(x.name),
                      ));
                      c.showToast('已附加 ${x.name}');
                      return;
                    }
                  } catch (_) {}
                  final r = await FilePicker.platform.pickFiles(
                    type: FileType.image,
                    withData: true,
                  );
                  if (r != null) {
                    for (final f in r.files) {
                      if (f.name.isEmpty) continue;
                      final af = AttachFile(
                        name: f.name,
                        bytes: f.bytes != null
                            ? Uint8List.fromList(f.bytes!)
                            : null,
                        path: f.path,
                        mime: _guessMime(f.name),
                      );
                      final bytes = await resolveAttachBytes(af);
                      if (bytes == null || bytes.isEmpty) {
                        c.showToast('无法读取 ${f.name}');
                        continue;
                      }
                      c.addAttach(af);
                    }
                  }
                },
              ),
              const SizedBox(height: 6),
              SizedBox(
                width: double.infinity,
                child: TextButton(
                  onPressed: () => Navigator.pop(ctx),
                  child: Text(
                    '取消',
                    style: TextStyle(color: PixelColors.ink3),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _showImagePreview(BuildContext context, Uint8List bytes) {
    showDialog(
      context: context,
      builder: (ctx) => Dialog(
        backgroundColor: Colors.black,
        insetPadding: const EdgeInsets.all(16),
        child: Stack(
          children: [
            InteractiveViewer(
              child: Center(
                child: Image.memory(bytes, fit: BoxFit.contain),
              ),
            ),
            Positioned(
              top: 8,
              right: 8,
              child: IconButton(
                onPressed: () => Navigator.pop(ctx),
                icon: const Icon(Icons.close, color: Colors.white),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _guessMime(String name, {String? platformMime}) =>
      guessMime(name, platformMime: platformMime);
}

class _ModeChip extends StatelessWidget {
  const _ModeChip({
    required this.label,
    required this.active,
    required this.color,
    required this.onTap,
  });
  final String label;
  final bool active;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 140),
      curve: Curves.easeOutCubic,
      height: 32,
      decoration: BoxDecoration(
        color: active ? color.withValues(alpha: 0.16) : Colors.transparent,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(14),
          child: Center(
            child: AnimatedDefaultTextStyle(
              duration: const Duration(milliseconds: 140),
              style: TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w700,
                color: active ? color : PixelColors.ink3,
              ),
              child: Text(label),
            ),
          ),
        ),
      ),
    );
  }
}

/// Bubble isolated so streaming only repaints the active assistant message.
class _ConfirmBubble extends StatelessWidget {
  const _ConfirmBubble({
    required this.detail,
    required this.dark,
    required this.ink,
    required this.card,
    required this.onApprove,
    required this.onDeny,
    required this.onOpenList,
  });
  final String detail;
  final bool dark;
  final Color ink;
  final Color card;
  final VoidCallback onApprove;
  final VoidCallback onDeny;
  final VoidCallback onOpenList;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
        decoration: BoxDecoration(
          color: PixelColors.amber.withValues(alpha: dark ? 0.12 : 0.1),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: PixelColors.amber.withValues(alpha: 0.45)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.shield_outlined, size: 16, color: PixelColors.amber),
                const SizedBox(width: 6),
                Text(
                  '需要你的确认',
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w800,
                    color: ink,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              detail,
              style: TextStyle(
                fontSize: 13,
                height: 1.4,
                color: ink.withValues(alpha: 0.85),
              ),
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                _ConfirmBtn(
                  label: '同意',
                  color: PixelColors.green,
                  onTap: onApprove,
                ),
                const SizedBox(width: 8),
                _ConfirmBtn(
                  label: '拒绝',
                  color: PixelColors.red,
                  onTap: onDeny,
                ),
                const SizedBox(width: 8),
                TextButton(
                  onPressed: onOpenList,
                  child: Text(
                    '审批列表',
                    style: TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      color: PixelColors.purple,
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _ConfirmBtn extends StatelessWidget {
  const _ConfirmBtn({
    required this.label,
    required this.color,
    required this.onTap,
  });
  final String label;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: color.withValues(alpha: 0.16),
      borderRadius: BorderRadius.circular(10),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w800,
              color: color,
            ),
          ),
        ),
      ),
    );
  }
}

class _ChatBubble extends StatelessWidget {
  const _ChatBubble({
    required this.msg,
    required this.dark,
    required this.ink,
    required this.ink3,
    required this.card,
    required this.card2,
    this.showActions = false,
    this.canRegenerate = false,
    this.onCopy,
    this.onRegenerate,
    this.onImageTap,
  });

  final ChatMsg msg;
  final bool dark;
  final Color ink;
  final Color ink3;
  final Color card;
  final Color card2;
  final bool showActions;
  final bool canRegenerate;
  final VoidCallback? onCopy;
  final VoidCallback? onRegenerate;
  final void Function(Uint8List bytes)? onImageTap;

  @override
  Widget build(BuildContext context) {
    final isUser = msg.role == 'user';
    final data = msg.text.isEmpty && msg.streaming && !msg.hasTools
        ? '…'
        : msg.text;
    // Markdown for assistant when marked, or auto-detect tables/code
    final useMd = !isUser &&
        (msg.format == 'markdown' || looksLikeMarkdown(msg.text));

    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment:
            isUser ? MainAxisAlignment.end : MainAxisAlignment.start,
        children: [
          if (!isUser) ...[
            Container(
              width: 28,
              height: 28,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: PixelColors.purple.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(
                  color: PixelColors.purple.withValues(alpha: 0.35),
                ),
              ),
              child: const Text(
                'TK',
                style: TextStyle(
                  fontFamily: 'Silkscreen',
                  fontSize: 8,
                  color: PixelColors.purple,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            const SizedBox(width: 8),
          ],
          Flexible(
            child: Container(
              padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
              decoration: BoxDecoration(
                color: isUser
                    ? PixelColors.purple.withValues(alpha: 0.12)
                    : card,
                borderRadius: BorderRadius.circular(14),
                border: Border.all(
                  color: isUser
                      ? PixelColors.purple.withValues(alpha: 0.28)
                      : (dark
                          ? Colors.white.withValues(alpha: 0.1)
                          : PixelColors.ink.withValues(alpha: 0.12)),
                ),
                boxShadow: PixelTheme.hardShadowSm,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (!isUser && msg.who.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: Text(
                        msg.who,
                        style: TextStyle(
                          fontSize: 10.5,
                          fontWeight: FontWeight.w700,
                          color: ink3,
                        ),
                      ),
                    ),
                  if (msg.hasImages) ...[
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final bytes in msg.images)
                          GestureDetector(
                            onTap: onImageTap == null
                                ? null
                                : () => onImageTap!(bytes),
                            child: ClipRRect(
                              borderRadius: BorderRadius.circular(10),
                              child: Image.memory(
                                bytes,
                                width: msg.images.length == 1 ? 200 : 120,
                                height: msg.images.length == 1 ? 200 : 120,
                                fit: BoxFit.cover,
                                gaplessPlayback: true,
                                errorBuilder: (_, __, ___) => Container(
                                  width: 120,
                                  height: 120,
                                  color: card2,
                                  alignment: Alignment.center,
                                  child: Icon(Icons.broken_image_outlined,
                                      color: ink3),
                                ),
                              ),
                            ),
                          ),
                      ],
                    ),
                    if (data.isNotEmpty && data != '…')
                      const SizedBox(height: 8),
                  ],
                                    if (msg.hasAttachChips) ...[
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        for (final name in msg.attachNames)
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 5),
                            decoration: BoxDecoration(
                              color: card2,
                              borderRadius: BorderRadius.circular(10),
                              border: Border.all(
                                color: dark
                                    ? Colors.white.withValues(alpha: 0.12)
                                    : PixelColors.ink.withValues(alpha: 0.12),
                              ),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(Icons.insert_drive_file_outlined,
                                    size: 14, color: ink3),
                                const SizedBox(width: 4),
                                ConstrainedBox(
                                  constraints:
                                      const BoxConstraints(maxWidth: 160),
                                  child: Text(
                                    name,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                    style: TextStyle(
                                      fontSize: 11.5,
                                      color: ink,
                                      fontWeight: FontWeight.w600,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                      ],
                    ),
                    if (data.isNotEmpty && data != '…')
                      const SizedBox(height: 8),
                  ],
                  // TRACE panel (PC ToolCallPanel parity)
                  if (!isUser && msg.hasTools)
                    ToolTracePanel(
                      tools: msg.toolCalls,
                      dark: dark,
                      ink: ink,
                      ink3: ink3,
                      card2: card2,
                      pending: msg.streaming,
                    ),
                  if (useMd && data.isNotEmpty && data != '…')
                    ChatMarkdown(
                      data: data,
                      dark: dark,
                      ink: ink,
                      card2: card2,
                    )
                  else if (data.isNotEmpty)
                    Text(
                      data,
                      style: TextStyle(
                        fontSize: 14,
                        height: 1.5,
                        color: ink,
                      ),
                    )
                  else if (msg.streaming && !msg.hasTools)
                    Text(
                      '…',
                      style: TextStyle(fontSize: 14, height: 1.5, color: ink3),
                    ),
                  if (showActions) ...[
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        _MsgAction(
                          label: '复制',
                          onTap: onCopy,
                        ),
                        if (canRegenerate && onRegenerate != null) ...[
                          const SizedBox(width: 8),
                          _MsgAction(
                            label: '重新生成',
                            onTap: onRegenerate,
                          ),
                        ],
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _MsgAction extends StatelessWidget {
  const _MsgAction({required this.label, this.onTap});
  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(14),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 11.5,
            fontWeight: FontWeight.w700,
            color: PixelColors.cyan,
          ),
        ),
      ),
    );
  }
}

class _IconBtn extends StatelessWidget {
  const _IconBtn({required this.onTap, required this.child});
  final VoidCallback onTap;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: PixelColors.card,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Container(
          width: 44,
          height: 44,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: PixelColors.ink.withValues(alpha: 0.16),
            ),
            boxShadow: PixelTheme.hardShadowSm,
          ),
          child: child,
        ),
      ),
    );
  }
}

class _SquareBtn extends StatelessWidget {
  const _SquareBtn(
      {required this.onTap, required this.child, required this.bg});
  final VoidCallback onTap;
  final Widget child;
  final Color bg;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: bg,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Container(
          width: 44,
          height: 44,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: PixelColors.ink.withValues(alpha: 0.12),
            ),
          ),
          child: child,
        ),
      ),
    );
  }
}

class _SendBtn extends StatelessWidget {
  const _SendBtn({
    required this.streaming,
    required this.enabled,
    required this.warn,
    required this.onTap,
  });
  final bool streaming;
  final bool enabled;
  final bool warn;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final bg = streaming
        ? PixelColors.red
        : (warn
            ? PixelColors.amber
            : (enabled ? PixelColors.purple : PixelColors.ink3));
    return Material(
      color: bg,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        onTap: enabled || streaming ? onTap : null,
        borderRadius: BorderRadius.circular(14),
        child: Container(
          width: 44,
          height: 44,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: PixelColors.ink, width: 1.1),
            boxShadow: PixelTheme.hardShadowSm,
          ),
          child: streaming
              ? PixelIcon.stop(color: Colors.white)
              : PixelIcon.send(color: Colors.white),
        ),
      ),
    );
  }
}

class _SheetTile extends StatelessWidget {
  const _SheetTile({
    required this.title,
    required this.sub,
    required this.onTap,
  });
  final String title;
  final String sub;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Material(
        color: PixelColors.ink.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(14),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: const TextStyle(
                    fontSize: 14.5,
                    fontWeight: FontWeight.w700,
                    color: PixelColors.ink,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  sub,
                  style: TextStyle(fontSize: 11.5, color: PixelColors.ink3),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Compact status row above the composer — mirrors desktop ActivityPanel.
class _ComposerStatusStrip extends StatelessWidget {
  const _ComposerStatusStrip({
    required this.controller,
    required this.listening,
    required this.dark,
    required this.ink3,
  });

  final AppController controller;
  final bool listening;
  final bool dark;
  final Color ink3;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final show = c.streaming || c.islandLive || listening;
    if (!show) return const SizedBox.shrink();

    final accent = listening
        ? PixelColors.red
        : (c.streaming
            ? PixelColors.cyan
            : (c.islandKind == 'local'
                ? PixelColors.green
                : PixelColors.purple));

    String label;
    if (listening) {
      label = '聆听中…';
    } else if (c.streaming) {
      final t = c.islandText.trim();
      label = t.isEmpty || t == '本机' || t == '已连 PC' ? '生成中…' : t;
    } else {
      label = c.islandText.trim().isEmpty ? '状态更新' : c.islandText.trim();
    }

    int toolRun = 0;
    int toolDone = 0;
    for (var i = c.messages.length - 1; i >= 0; i--) {
      final m = c.messages[i];
      if (m.role != 'assistant') continue;
      if (m.hasTools) {
        for (final tc in m.toolCalls) {
          if (tc.status == ToolCallStatus.running) {
            toolRun++;
          } else {
            toolDone++;
          }
        }
      }
      break;
    }

    return Container(
      width: double.infinity,
      margin: const EdgeInsets.fromLTRB(10, 0, 10, 6),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: dark
            ? Colors.white.withValues(alpha: 0.04)
            : PixelColors.ink.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: accent.withValues(alpha: 0.28)),
      ),
      child: Row(
        children: [
          _PulseDot(color: accent, active: c.streaming || listening),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w600,
                color: dark ? PixelColors.dInk2 : PixelColors.ink2,
                height: 1.2,
              ),
            ),
          ),
          if (c.streaming && (toolRun + toolDone) > 0) ...[
            const SizedBox(width: 6),
            Text(
              toolRun > 0 ? '工具 $toolRun↑' : '工具 $toolDone',
              style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w700,
                color: accent,
              ),
            ),
          ],
          if (c.streaming) ...[
            const SizedBox(width: 8),
            Text(
              '点红键停止',
              style: TextStyle(fontSize: 10, color: ink3),
            ),
          ],
        ],
      ),
    );
  }
}

class _PulseDot extends StatefulWidget {
  const _PulseDot({required this.color, required this.active});
  final Color color;
  final bool active;

  @override
  State<_PulseDot> createState() => _PulseDotState();
}

class _PulseDotState extends State<_PulseDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 900),
    );
    if (widget.active) _ctrl.repeat(reverse: true);
  }

  @override
  void didUpdateWidget(covariant _PulseDot oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.active && !_ctrl.isAnimating) {
      _ctrl.repeat(reverse: true);
    } else if (!widget.active && _ctrl.isAnimating) {
      _ctrl.stop();
      _ctrl.value = 1;
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: widget.active
          ? Tween<double>(begin: 0.35, end: 1.0).animate(_ctrl)
          : const AlwaysStoppedAnimation(1),
      child: Container(
        width: 7,
        height: 7,
        decoration: BoxDecoration(
          color: widget.color,
          borderRadius: BorderRadius.circular(2),
        ),
      ),
    );
  }
}
