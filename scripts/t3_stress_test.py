"""T-3 压力测试：长对话 + 上下文压缩 + 工具链 + RAG 召回
本脚本会构造一个多轮会话，逐轮注入大量文本，观察上下文压缩是否触发、
RAG 是否持续召回、工具链是否稳定返回。"""
import json
import socket
import struct
import time
import uuid

import requests

BASE = "http://127.0.0.1:8000/api"
WS_BASE = "ws://127.0.0.1:8000/api"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def get_token() -> str:
    r = requests.post(f"{BASE}/auth/auto-login", json={})
    r.raise_for_status()
    return r.json()["access_token"]


def create_session(token: str) -> str:
    r = requests.post(
        f"{BASE}/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "T-3 stress test"},
    )
    r.raise_for_status()
    return r.json()["id"]


def ws_send(sock, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    length = len(data)
    if length < 126:
        header = struct.pack("!BB", 0x81, 0x80 | length)
    elif length < 65536:
        header = struct.pack("!BBH", 0x81, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", 0x81, 0x80 | 127, length)
    import random
    mask = bytes([random.randint(0, 255) for _ in range(4)])
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(header + mask + masked)


def ws_recv(sock, timeout: float = 20.0) -> dict | None:
    sock.settimeout(timeout)
    try:
        data = sock.recv(2)
        if not data:
            return None
        fin_opcode = data[0]
        if (fin_opcode & 0x0F) == 0x08:
            return {"type": "close"}
        payload_len = data[1] & 0x7F
        if payload_len == 126:
            payload_len = struct.unpack("!H", sock.recv(2))[0]
        elif payload_len == 127:
            payload_len = struct.unpack("!Q", sock.recv(8))[0]
        mask_bit = (data[1] & 0x80) != 0
        if mask_bit:
            mask_key = sock.recv(4)
        else:
            mask_key = None

        payload = b""
        while len(payload) < payload_len:
            chunk = sock.recv(min(4096, payload_len - len(payload)))
            if not chunk:
                break
            payload += chunk

        if mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        text = payload.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
    except socket.timeout:
        return {"type": "timeout"}
    except Exception as e:
        return {"type": "error", "error": str(e)}


def one_round(token: str, session_id: str, question: str, round_no: int) -> dict:
    """通过 WebSocket 发送一个问题并等待 idle 状态。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(("127.0.0.1", 8000))
    # WebSocket handshake
    path = f"/api/ws/{session_id}?token={token}"
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:8000\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode("utf-8")
    sock.sendall(req)
    response = sock.recv(4096)
    if b"101" not in response:
        raise RuntimeError(f"WebSocket handshake failed: {response[:200]}")

    ws_send(sock, {"type": "user_input", "content": question})

    last_status = None
    deltas = []
    start = time.time()
    while True:
        msg = ws_recv(sock, timeout=90.0)
        if msg is None or msg.get("type") in ("close",):
            break
        if msg.get("type") == "timeout":
            log(f"R{round_no} recv timeout after {time.time() - start:.1f}s")
            break
        if msg.get("type") == "error":
            log(f"R{round_no} recv error: {msg}")
            break
        if msg.get("type") == "status":
            last_status = msg.get("state")
            if last_status == "idle":
                break
        if msg.get("type") == "stream_delta":
            deltas.append(msg.get("content", ""))

    reply = "".join(deltas)
    sock.close()
    return {"status": last_status, "reply": reply, "elapsed": time.time() - start}


def fetch_messages(token: str, session_id: str) -> list:
    r = requests.get(
        f"{BASE}/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    return r.json()


def main():
    token = get_token()
    session_id = create_session(token)
    log(f"T-3 session_id={session_id}")

    # 先创建知识库并上传一份长文档，供 RAG 召回
    r = requests.post(
        f"{BASE}/knowledge/documents",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "T3 Knowledge Base", "content": "T-3 stress test knowledge base."},
    )
    r.raise_for_status()
    kb_id = r.json()["id"]
    log(f"knowledge base id={kb_id}")

    long_text = (
        "Takton 项目设计原则：\n"
        "1. 上下文优先：Agent 必须能访问长上下文、多层记忆和本地知识库。\n"
        "2. 工具链可扩展：通过统一工具注册表，动态加载内置与自定义 skill/tool。\n"
        "3. 自主运行：支持 goal 模式、任务规划和多轮循环。\n"
        "4. 安全降级：向量 RAG 不可用时自动回退到本地记忆文件。\n"
        "5. 用户隔离：多用户场景下数据与会话严格隔离。\n"
    ) * 5

    r = requests.post(
        f"{BASE}/knowledge/documents",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "knowledge_base_id": kb_id,
            "title": "T3 Long Doc",
            "content": long_text,
            "source_type": "text",
        },
    )
    doc_id = r.json()["id"]
    log(f"doc id={doc_id}, waiting indexing")
    for i in range(30):
        r = requests.get(f"{BASE}/knowledge/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"})
        doc = r.json()
        if doc.get("status") == "indexed":
            log(f"doc indexed, chunks={doc.get('chunks')}")
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("document not indexed")

    questions = [
        "你好，请简单自我介绍。",
        "Takton 项目有哪些设计原则？请根据知识库回答。",
        "请用 bash 查看当前目录下 README 文件的前 5 行。",
        "请搜索本地知识库：Takton 的上下文优先原则是什么意思？",
        "请创建一个任务：'整理 Takton 设计文档'。",
        "请总结一下前面对话的核心内容。",
        # 大文本注入，推动上下文压缩
        "请阅读以下长文本并总结关键要点：\n" + long_text,
        "在上面的长文本里，Takton 强调了几条设计原则？",
        "再次检查当前任务列表。",
        "请根据知识库说明：Takton 如何处理 RAG 不可用的降级场景？",
    ]

    results = []
    for i, q in enumerate(questions, 1):
        log(f"--- Round {i}/{len(questions)} ---")
        log(f"Q: {q[:80]}...")
        res = one_round(token, session_id, q, i)
        log(f"R{i} status={res['status']} elapsed={res['elapsed']:.1f}s reply_len={len(res['reply'])}")
        results.append(res)
        time.sleep(0.5)

    # 统计
    messages = fetch_messages(token, session_id)
    user_count = sum(1 for m in messages if m.get("role") == "user")
    assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
    empty_count = sum(1 for m in messages if m.get("role") == "assistant" and not m.get("content"))
    log(f"messages: user={user_count} assistant={assistant_count} empty_assistant={empty_count}")

    if empty_count == 0 and all(r["status"] == "idle" for r in results):
        log("T-3 PASSED")
    else:
        log("T-3 FAILED")


if __name__ == "__main__":
    main()
