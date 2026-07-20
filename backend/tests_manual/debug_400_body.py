# -*- coding: utf-8 -*-
"""精准复现并抓取 400 真实响应体：构造触发 L3 的长历史，压缩后真实发讯飞，打印完整 error body。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

import aiohttp

from backend.agent.context_pipeline import PipelineContextEngine
from backend.services.llm.openai_compatible import OpenAICompatibleService

IFLYTEK_BASE_URL = "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2"
IFLYTEK_API_KEY = os.environ.get("IFLYTEK_API_KEY", "")
IFLYTEK_MODEL = "astron-code-latest"

TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "搜索", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "读文件", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]


def big(seed: str, kb: int) -> str:
    line = f"[{seed}] " + ("数据块 " * 30) + "\n"
    return line * (kb * 1024 // len(line) + 1)


def build_history(n_tool_pairs: int) -> list[dict]:
    msgs = [{"role": "system", "content": "你是全栈开发助手。"}]
    msgs.append({"role": "user", "content": "开始复杂搜索+开发任务"})
    msgs.append({"role": "assistant", "content": "好的，我先搜索再开发。"})
    for i in range(n_tool_pairs):
        tid = f"call_{i}_{i*7}"
        msgs.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": tid, "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": f"主题{i}"})}}],
        })
        msgs.append({"role": "tool", "tool_call_id": tid, "content": big(f"search{i}", 5)})
    msgs.append({"role": "user", "content": "基于以上所有搜索结果，给出最终的开发方案和代码框架。"})
    return msgs


async def raw_send(messages, tools):
    """绕过 service 的错误包装，直接拿原始响应。"""
    cfg = SimpleNamespace(base_url=IFLYTEK_BASE_URL, model=IFLYTEK_MODEL, api_key=IFLYTEK_API_KEY, max_tokens=1024, temperature=0.7)
    svc = OpenAICompatibleService(cfg)
    url = svc._chat_completions_url()
    safe = svc._sanitize_messages_for_api(messages)
    payload = {"model": svc.model, "messages": safe, "stream": False, "max_tokens": svc.max_tokens, "temperature": svc.temperature, "tools": svc._normalize_tools(tools), "tool_choice": "auto"}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {IFLYTEK_API_KEY}"}

    # 统计发出的消息里 tool 配对是否完整
    declared = set()
    for m in safe:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if tc.get("id"):
                    declared.add(tc["id"])
    orphan = [m.get("tool_call_id") for m in safe if m.get("role") == "tool" and m.get("tool_call_id") and m["tool_call_id"] not in declared]
    print(f"发出消息数={len(safe)} tool_calls声明数={len(declared)} tool消息数={sum(1 for m in safe if m.get('role')=='tool')} 孤儿tool={orphan}")

    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, headers=headers) as resp:
            body = await resp.text()
            print(f"HTTP {resp.status}")
            if resp.status != 200:
                print(f"ERROR BODY: {body[:1500]}")
            else:
                data = json.loads(body)
                c = data.get("choices", [{}])[0].get("message", {})
                print(f"OK content_len={len(c.get('content') or '')} tool_calls={len(c.get('tool_calls') or [])}")
            return resp.status


async def main():
    engine = PipelineContextEngine()
    # 先不压缩，直接发原始长历史（含完整配对）—— 验证基础可用
    print("=== A. 未压缩长历史（完整配对）===")
    h = build_history(6)
    await raw_send(h, TOOLS)

    print("\n=== B. L3 压缩后（修复后应无孤儿）===")
    engine.context_length = 6000
    engine.meter.context_window = 6000
    engine.protect_first_n = 2
    engine.protect_last_n = 4
    compressed, meta = await engine.compress(h)
    print(f"压缩 layers={meta.get('layers')} msgs={len(compressed)}")
    await raw_send(compressed, TOOLS)


if __name__ == "__main__":
    asyncio.run(main())
