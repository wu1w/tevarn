# Tevarn Mobile

像素控制台手机端 · **业务全 Rust** · **壳为 Flutter**

```
Flutter UI  (pixel console · 四 Tab · 双模式 · 扫码配对)
   │  HTTP (web)  /  FFI C ABI (native)
   ▼
tevarn-mobile-host  (axum · /api/mobile/*)
   ├── core: 本机 LLM · PC 代理 · ModeSnapshot · pair · mesh
   └── 静态：Flutter web build
```

## 双模式

| 模式 | 入口 | 需要 | 能力 |
|------|------|------|------|
| **本机对话** | 顶栏「本机对话」 | API Key 供应商 | 直连模型 SSE |
| **远端 Agent** | 顶栏「远端 Agent」 | 已连 PC | 工具链、审批、OAuth |

## 扫码配对（M1–M3）

```
PC 工作台「匹配手机」→ 生成二维码 (tevarn://pair?…)
手机 App「连接」→ 扫描 / 粘贴 → claim + login → 自动重连
```

| 端 | 职责 |
|----|------|
| **PC** | 出码、mesh 密钥一次配置、允许/取消配对 |
| **手机 App** | 仅扫码/粘贴与登录，**不**生成二维码 |

| 阶段 | 能力 | 接口 |
|------|------|------|
| **M1** | QR 配对协议、出码/扫码、device token | `/api/mobile/pair/*` |
| **M2** | 远程访问模式 off/lan/ts · tsnet 侧车 | `/api/mobile/mesh` · `sidecar/tsnet` |
| **M3** | 配对持久化 · 冷启动自动重连 · mesh facade | SharedPreferences + `MeshRuntime` |

### 用户路径

1. **推荐 mesh=自动**：QR 同时写入 LAN + Tailscale + 主机名；手机优先局域网，失败再 TS  
2. **同一 Wi‑Fi**：即使用户只选局域网，出码仍尽量附带 TS（若已检测到）便于出门用  
3. **无公网 IP / 外网**：PC 跑系统 Tailscale 或 `sidecar/tsnet`；手机侧 mesh 会上报网卡指纹并在 Wi‑Fi↔5G 时自动 `path/reconnect`  
4. **扫码时网络不可达**：软配对保存端点与 claim，回到可达网络后自动完成（TTL 5 分钟）  
5. **LAN IP DHCP 漂移**：成功连上后用当前 mesh 状态刷新候选；优先 hostname / TS 稳定地址  

### 配对 URI

```
tevarn://pair?v=2&pair_id=…&code=…&host=…&port=8090&exp=…&mesh=auto|lan|ts&scheme=http&lan=…&ts=…&hn=…
```

## 运行（预览）

```bash
cd flutter_app && flutter pub get && flutter build web --release
export TEVARN_MOBILE_UI=$PWD/build/web
export TEVARN_BASE_URL=http://127.0.0.1:8090
cargo run -p tevarn-mobile-host --release
```

## 目录

```
flutter_app/          Flutter 壳
crates/core/          平台无关业务（含 pair / mesh）
crates/host/          axum API + 静态 UI
crates/ffi/           C ABI for Flutter
sidecar/tsnet/        Go tsnet 侧车（M2，PC 上构建）
```
