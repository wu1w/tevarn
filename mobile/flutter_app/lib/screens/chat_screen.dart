import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';

import '../models/app_models.dart';
import '../services/app_controller.dart';
import '../theme/pixel_theme.dart';
import '../widgets/pixel_icons.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _ctrl = TextEditingController();
  final _scroll = ScrollController();
  bool _hasText = false;

  @override
  void initState() {
    super.initState();
    _ctrl.addListener(() {
      final next = _ctrl.text.trim().isNotEmpty;
      if (next != _hasText) setState(() => _hasText = next);
    });
  }

  @override
  void dispose() {
    _ctrl.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _scrollEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 140),
          curve: Curves.easeOut,
        );
      }
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

    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (c.messages.isNotEmpty) _scrollEnd();
    });

    // Sync external clear of input (after send)
    if (c.input.isEmpty && _ctrl.text.isNotEmpty && !c.streaming) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && c.input.isEmpty) _ctrl.clear();
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
              borderRadius: BorderRadius.circular(6),
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
                  c.mode.reason.isNotEmpty
                      ? c.mode.reason
                      : (c.surface == 'local' ? '本机通道' : '远端通道'),
                  style: TextStyle(fontSize: 11.5, color: ink3),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
        if (c.surface == 'remote' && !c.pcConnected)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 6),
            child: Material(
              color: PixelColors.amber.withValues(alpha: 0.14),
              borderRadius: BorderRadius.circular(6),
              child: InkWell(
                borderRadius: BorderRadius.circular(6),
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
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
            itemCount: c.messages.length,
            itemBuilder: (context, i) {
              final m = c.messages[i];
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
                ),
              );
            },
          ),
        ),
        if (c.attachments.isNotEmpty)
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 0, 12, 6),
            child: SizedBox(
              height: 32,
              child: ListView(
                scrollDirection: Axis.horizontal,
                children: [
                  for (var i = 0; i < c.attachments.length; i++)
                    Padding(
                      padding: const EdgeInsets.only(right: 6),
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          color: card2,
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(
                            color: dark
                                ? Colors.white.withValues(alpha: 0.12)
                                : PixelColors.ink.withValues(alpha: 0.12),
                          ),
                        ),
                        child: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              c.attachments[i].name,
                              style: TextStyle(fontSize: 11.5, color: ink2),
                            ),
                            if (c.attachments[i].hasData)
                              Padding(
                                padding: const EdgeInsets.only(left: 4),
                                child: Text(
                                  '·实传',
                                  style: TextStyle(
                                    fontSize: 10,
                                    color: PixelColors.green,
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                              ),
                            const SizedBox(width: 4),
                            GestureDetector(
                              onTap: () => c.removeAttach(i),
                              child: Icon(Icons.close, size: 14, color: ink3),
                            ),
                          ],
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(10, 0, 10, 10),
          child: Container(
            padding: const EdgeInsets.all(6),
            decoration: BoxDecoration(
              color: dark ? const Color(0xFF151A2E) : PixelColors.card,
              borderRadius: BorderRadius.circular(6),
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
                      onChanged: (v) => c.setInput(v, notify: false),
                      onSubmitted: (_) => _doSend(c),
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

  Future<void> _doSend(AppController c) async {
    final text = _ctrl.text;
    // Do not clear draft until send accepts it (canSend / empty checks).
    final accepted = await c.send(text);
    if (accepted && mounted) {
      _ctrl.clear();
      setState(() => _hasText = false);
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
                    : '本机模式仅附文件名提示',
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
                    c.addAttach(AttachFile(
                      name: f.name,
                      bytes: f.bytes != null
                          ? Uint8List.fromList(f.bytes!)
                          : null,
                      path: f.path,
                      mime: _guessMime(f.name),
                    ));
                    n++;
                  }
                  if (n > 0) {
                    c.showToast(
                        '已选择 $n 个文件${c.surface == 'remote' ? '（发送时实传）' : ''}');
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
                sub: '从相册选择图片 · 发送时上传',
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
                      c.addAttach(AttachFile(
                        name: f.name,
                        bytes: f.bytes != null
                            ? Uint8List.fromList(f.bytes!)
                            : null,
                        path: f.path,
                        mime: _guessMime(f.name),
                      ));
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

  String _guessMime(String name) {
    final n = name.toLowerCase();
    if (n.endsWith('.png')) return 'image/png';
    if (n.endsWith('.jpg') || n.endsWith('.jpeg')) return 'image/jpeg';
    if (n.endsWith('.gif')) return 'image/gif';
    if (n.endsWith('.webp')) return 'image/webp';
    if (n.endsWith('.pdf')) return 'application/pdf';
    if (n.endsWith('.txt') || n.endsWith('.md')) return 'text/plain';
    return 'application/octet-stream';
  }
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
        borderRadius: BorderRadius.circular(4),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(4),
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

  @override
  Widget build(BuildContext context) {
    final isUser = msg.role == 'user';
    final data = msg.text.isEmpty && msg.streaming ? '…' : msg.text;
    // format comes from Rust normalize; mid-stream may flip to markdown
    final useMd = !isUser && msg.format == 'markdown';

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
                borderRadius: BorderRadius.circular(4),
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
                borderRadius: BorderRadius.circular(6),
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
                  if (useMd)
                    MarkdownBody(
                      data: data,
                      selectable: false,
                      styleSheet: MarkdownStyleSheet(
                        p: TextStyle(
                          fontSize: 14,
                          height: 1.5,
                          color: ink,
                        ),
                        code: TextStyle(
                          fontFamily: 'JetBrains Mono',
                          fontSize: 12.5,
                          backgroundColor: card2,
                          color: ink,
                        ),
                      ),
                    )
                  else
                    Text(
                      data,
                      style: TextStyle(
                        fontSize: 14,
                        height: 1.5,
                        color: ink,
                      ),
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
      borderRadius: BorderRadius.circular(4),
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
      borderRadius: BorderRadius.circular(4),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(4),
        child: Container(
          width: 36,
          height: 36,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(4),
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
      borderRadius: BorderRadius.circular(4),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(4),
        child: Container(
          width: 34,
          height: 34,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(4),
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
      borderRadius: BorderRadius.circular(4),
      child: InkWell(
        onTap: enabled || streaming ? onTap : null,
        borderRadius: BorderRadius.circular(4),
        child: Container(
          width: 34,
          height: 34,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(4),
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
        borderRadius: BorderRadius.circular(6),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(6),
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
