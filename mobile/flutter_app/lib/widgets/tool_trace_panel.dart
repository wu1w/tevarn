import 'package:flutter/material.dart';

import '../models/tool_call_ui.dart';
import '../theme/pixel_theme.dart';

/// Collapsible TRACE panel — mobile counterpart of PC ToolCallPanel.
class ToolTracePanel extends StatefulWidget {
  const ToolTracePanel({
    super.key,
    required this.tools,
    required this.dark,
    required this.ink,
    required this.ink3,
    required this.card2,
    this.pending = false,
  });

  final List<ToolCallUi> tools;
  final bool dark;
  final Color ink;
  final Color ink3;
  final Color card2;
  final bool pending;

  @override
  State<ToolTracePanel> createState() => _ToolTracePanelState();
}

class _ToolTracePanelState extends State<ToolTracePanel> {
  bool _open = false;
  bool _autoOpened = false;

  @override
  void didUpdateWidget(covariant ToolTracePanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    final running = widget.tools.any((t) => t.status == ToolCallStatus.running);
    if (running && !_autoOpened) {
      _open = true;
      _autoOpened = true;
    }
  }

  @override
  void initState() {
    super.initState();
    final running = widget.tools.any((t) => t.status == ToolCallStatus.running);
    if (running || widget.pending) {
      _open = true;
      _autoOpened = true;
    }
  }

  @override
  Widget build(BuildContext context) {
    if (widget.tools.isEmpty) return const SizedBox.shrink();
    final running = widget.tools.where((t) => t.status == ToolCallStatus.running).length;
    final failed = widget.tools.where((t) => t.status == ToolCallStatus.failed).length;
    final suffix = running > 0
        ? ' · $running 运行中'
        : failed > 0
            ? ' · $failed 失败'
            : ' · 已完成';
    final border = widget.dark
        ? Colors.white.withValues(alpha: 0.14)
        : PixelColors.ink.withValues(alpha: 0.14);

    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: border,
          width: 1,
          strokeAlign: BorderSide.strokeAlignInside,
        ),
        // dashed feel via low-contrast fill
        color: widget.card2.withValues(alpha: widget.dark ? 0.35 : 0.55),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InkWell(
            onTap: () => setState(() => _open = !_open),
            borderRadius: BorderRadius.circular(10),
            child: Padding(
              padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
              child: Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                    decoration: BoxDecoration(
                      color: PixelColors.amber.withValues(alpha: 0.18),
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(
                        color: PixelColors.amber.withValues(alpha: 0.4),
                      ),
                    ),
                    child: const Text(
                      'TRACE',
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 0.4,
                        color: PixelColors.amber,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      '工具轨迹 ${widget.tools.length} 步$suffix',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                        color: widget.ink,
                      ),
                    ),
                  ),
                  if (running > 0)
                    Container(
                      width: 7,
                      height: 7,
                      margin: const EdgeInsets.only(right: 6),
                      decoration: const BoxDecoration(
                        color: PixelColors.amber,
                        shape: BoxShape.circle,
                      ),
                    ),
                  Text(
                    _open ? '收起' : '展开',
                    style: TextStyle(fontSize: 11, color: widget.ink3),
                  ),
                  Icon(
                    _open ? Icons.expand_less : Icons.expand_more,
                    size: 16,
                    color: widget.ink3,
                  ),
                ],
              ),
            ),
          ),
          if (_open)
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 0, 10, 10),
              child: Column(
                children: [
                  for (var i = 0; i < widget.tools.length; i++)
                    _TraceStep(
                      index: i + 1,
                      tool: widget.tools[i],
                      dark: widget.dark,
                      ink: widget.ink,
                      ink3: widget.ink3,
                      card2: widget.card2,
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _TraceStep extends StatefulWidget {
  const _TraceStep({
    required this.index,
    required this.tool,
    required this.dark,
    required this.ink,
    required this.ink3,
    required this.card2,
  });
  final int index;
  final ToolCallUi tool;
  final bool dark;
  final Color ink;
  final Color ink3;
  final Color card2;

  @override
  State<_TraceStep> createState() => _TraceStepState();
}

class _TraceStepState extends State<_TraceStep> {
  bool _detail = false;

  Color get _statusColor {
    switch (widget.tool.status) {
      case ToolCallStatus.running:
        return PixelColors.amber;
      case ToolCallStatus.completed:
        return PixelColors.green;
      case ToolCallStatus.failed:
        return PixelColors.red;
    }
  }

  String get _mark {
    switch (widget.tool.status) {
      case ToolCallStatus.running:
        return '…';
      case ToolCallStatus.completed:
        return '✓';
      case ToolCallStatus.failed:
        return '✗';
    }
  }

  @override
  Widget build(BuildContext context) {
    final hasDetail =
        widget.tool.result.isNotEmpty || widget.tool.summary.isNotEmpty;
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            onTap: hasDetail ? () => setState(() => _detail = !_detail) : null,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${widget.index}.',
                  style: TextStyle(
                    fontFamily: 'JetBrains Mono',
                    fontSize: 11.5,
                    color: widget.ink3,
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  _mark,
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w800,
                    color: _statusColor,
                  ),
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text.rich(
                    TextSpan(
                      children: [
                        TextSpan(
                          text: widget.tool.name,
                          style: TextStyle(
                            fontFamily: 'JetBrains Mono',
                            fontSize: 12.5,
                            fontWeight: FontWeight.w700,
                            color: widget.ink,
                          ),
                        ),
                        if (widget.tool.summary.isNotEmpty && !_detail)
                          TextSpan(
                            text:
                                '  ${widget.tool.summary.length > 60 ? '${widget.tool.summary.substring(0, 60)}…' : widget.tool.summary}',
                            style: TextStyle(fontSize: 11.5, color: widget.ink3),
                          ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
          if (_detail && hasDetail)
            Container(
              width: double.infinity,
              margin: const EdgeInsets.only(left: 28, top: 4),
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: widget.dark ? const Color(0xFF0E1220) : widget.card2,
                borderRadius: BorderRadius.circular(8),
              ),
              child: SelectableText(
                widget.tool.result.isNotEmpty
                    ? widget.tool.result
                    : widget.tool.summary,
                style: TextStyle(
                  fontFamily: 'JetBrains Mono',
                  fontSize: 11,
                  height: 1.4,
                  color: widget.ink,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
