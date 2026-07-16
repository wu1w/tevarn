# takton-agent (L1 MVP)

边缘设备上的轻量 agent：WebSocket JSON-RPC，供 Takton 控制面调用。

## 启动

```bash
pip install -r requirements.txt
# 在仓库根或本目录：
set PYTHONPATH=%CD%
python -m takton_agent --host 0.0.0.0 --port 19876 --token <密钥> --root C:\path\to\projects --name my-pc
```

## 协议

见 `backend/services/remote/protocol.py`：

- `hello` / `ping`
- `file.list` / `file.read`
- `exec.run`（黑名单 + 超时）

## 配对到控制面

```http
POST /api/devices/pair
{ "name": "my-pc", "host": "192.168.x.x", "port": 19876, "token": "..." }
```

## 对话

```
@my-pc echo hello
@my-pc list:.
@my-pc read:demo.txt
```
