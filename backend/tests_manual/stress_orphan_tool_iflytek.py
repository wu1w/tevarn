# -*- coding: utf-8 -*-
"""孤儿 tool 修复的真实网关集成压测（讯飞 Coding Plan / astron-code-latest）。

用 takton 自己的 PipelineContextEngine + OpenAICompatibleService，
真实模拟"复杂搜索+开发"的多轮工具调用：每轮 assistant 发起多个 tool_calls、
tool 返回大段结果，历史不断累积，真实触发 L1/L3/L5 压缩，然后真实调用讯飞端点，
全程监控是否出现 400（tool 配对错乱 / 孤儿 tool 消息）。

运行：
    cd /opt/hermes-workspace/takton
    API_KEY=x .venv/bin/python backend/tests_manual/stress_orphan_tool_iflytek.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from types import SimpleNamespace

import aiohttp

from backend.agent.context_pipeline import PipelineContextEngine
from backend.services.llm.openai_compatible import OpenAICompatibleService

# 讯飞 Coding Plan（OpenAI 协议）
IFLYTEK_BASE_URL = "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2"
# 从环境变量读取，避免硬编码凭证泄露到仓库
IFLYTEK_API_KEY = os.environ.get("IFLYTEK_API_KEY", "")
IFLYTEK_MODEL = "astron-code-latest"

# 模拟可用工具（搜索 + 开发）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索网页",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读文件",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "执行 shell 命令",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        },
    },
]


def make_service() -> OpenAICompatibleService:
    cfg = SimpleNamespace(
        base_url=IFLYTEK_BASE_URL,
        model=IFLYTEK_MODEL,
        api_key=IFLYTEK_API_KEY,
        max_tokens=2048,
        temperature=0.7,
    )
    return OpenAICompatibleService(cfg)


def big_tool_result(seed: str, kb: int = 4) -> str:
    """生成大段工具结果，加速 token 累积以触发压缩。"""
    line = f"[{seed}] " + ("数据块 " * 30) + "\n"
    return line * (kb * 1024 // len(line) + 1)


async def call_llm(service: OpenAICompatibleService, messages, tools):
    """非流式调用，返回 (content, tool_calls, error)。

    chat() 在 400 时会 yield 一个 delta 含 [LLM Error ...] 且 finish_reason="error"
    的 chunk（而非抛异常），所以必须检测 error chunk 才算真实失败。
    """
    content_parts = []
    tool_calls = []
    err = None
    try:
        async for chunk in service.chat(messages, tools=tools, stream=False):
            if getattr(chunk, "tool_call", None):
                tc = chunk.tool_call
                tool_calls.append(
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                )
            if getattr(chunk, "delta", None):
                content_parts.append(chunk.delta)
            if getattr(chunk, "content", None):
                content_parts.append(chunk.content)
            # 检测错误 chunk（400/500 等以 [LLM Error 形式流出）
            if getattr(chunk, "finish_reason", None) == "error":
                err = getattr(chunk, "delta", None) or "LLM error chunk"
    except aiohttp.ClientResponseError as e:
        err = f"HTTP {e.status}: {e.message}"
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    joined = "".join(content_parts)
    # 兜底：内容里出现 [LLM Error 也判失败
    if err is None and "[LLM Error" in joined:
        err = joined[:200]
    return joined, tool_calls, err


async def main() -> int:
    import os

    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 18
    # 允许用较小 context_window 让压缩尽早、多次触发（更贴近"几轮工具调用+长文本"场景）
    ctx_window = int(os.environ.get("STRESS_CTX_WINDOW", "8000"))
    service = make_service()
    engine = PipelineContextEngine()
    engine.context_length = ctx_window
    engine.meter.context_window = ctx_window

    history: list[dict] = [
        {
            "role": "system",
            "content": "你是一个全栈开发助手，擅长搜索资料并完成开发任务。",
        }
    ]

    print(f"=== 压测开始：{rounds} 轮复杂搜索+开发，真实调讯飞端点 ===")
    print(f"context_window={engine.context_length} threshold={engine.meter.threshold_tokens}")
    errors = 0
    compress_events = 0

    for i in range(rounds):
        user_msg = {
            "role": "user",
            "content": (
                f"第{i+1}轮任务：搜索关于分布式任务队列、上下文压缩、LLM provider 容错的资料，"
                f"然后读取相关源码并运行几个验证命令，最后给出开发建议。"
            ),
        }
        history.append(user_msg)

        # 压缩前预检 + 真实压缩
        est_before = engine.meter.estimate_messages(history)
        compressed, meta = await engine.compress(history, session_id=f"stress-{uuid.uuid4().hex[:6]}")
        est_after = engine.meter.estimate_messages(compressed)
        layers = meta.get("layers", [])
        if layers:
            compress_events += 1
        print(
            f"[轮{i+1:02d}] tokens {est_before}→{est_after} "
            f"layers={layers or '-'} msgs={len(compressed)}"
        )

        # 真实调用讯飞端点（带工具）
        content, tool_calls, err = await call_llm(service, compressed, TOOLS)
        if err:
            errors += 1
            print(f"  ✗ LLM 调用失败: {err}")
            # 把错误轮也从历史去掉避免污染后续
            history.pop()
            continue

        # 把 assistant 响应（含 tool_calls）加回历史
        asst_msg: dict = {"role": "assistant", "content": content or None}
        if tool_calls:
            asst_msg["tool_calls"] = tool_calls
        history.append(asst_msg)

        # 对每个 tool_call 追加一个大段 tool 结果（模拟真实工具返回，加速累积）
        if tool_calls:
            for tc in tool_calls:
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": big_tool_result(tc["function"]["name"], kb=5),
                    }
                )
        else:
            # 模型没调工具：手动补一对真实 tool 交互，确保工具链持续累积以触发压缩
            tid = f"call_manual_{i}_{uuid.uuid4().hex[:6]}"
            history.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tid,
                            "type": "function",
                            "function": {"name": "web_search", "arguments": json.dumps({"query": f"轮{i+1} 上下文压缩"})},
                        }
                    ],
                }
            )
            history.append(
                {"role": "tool", "tool_call_id": tid, "content": big_tool_result("web_search", kb=6)}
            )

    print("\n=== 压测结束 ===")
    print(f"总轮数={rounds} 压缩触发={compress_events} LLM调用失败={errors}")
    if errors == 0:
        print("✓ 全程无 400 / 无 tool 配对错乱——修复在真实网关上验证通过")
        return 0
    print(f"✗ 出现 {errors} 次调用失败，需进一步排查")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
