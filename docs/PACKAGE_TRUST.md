# 包市场生产密钥与信任根（H-13）

**版本**：0.5.x · 与 `agent_package_*` 配置对齐  
**目标**：远程装包可验证签名与内容哈希，禁止 `insecure_default` 当生产信任。

---

## 1. 环境变量 / Settings

| 变量 / 配置键 | 说明 | 生产要求 |
|---------------|------|----------|
| `TEVARN_PKG_SIGNING_KEY` / `agent_package_signing_key` | 包签名密钥（≥16 字符） | **必设** |
| `agent_package_trusted_content_hashes` | 允许的内容 sha256（逗号/空白分隔） | 远程安装时**强烈建议非空** |
| `agent_package_require_content_hash` | 强制 catalog/查询带 content_sha256 | 生产建议 `true` |
| `agent_package_market_url` | 远程 catalog JSON URL | 可选；空=仅本地 |
| JWT secret | host 可从 JWT **派生**签名密钥（开发） | **不可**替代专用 PKG key |

未设置 `TEVARN_PKG_SIGNING_KEY` 时，host 可能使用 JWT 派生或 `insecure_default`——**仅限本地开发**。

---

## 2. 生成密钥（示例）

```bash
# 32 字节 hex
python -c "import secrets; print(secrets.token_hex(32))"
```

```env
TEVARN_PKG_SIGNING_KEY=<上一步输出>
TEVARN_PKG_REQUIRE_CONTENT_HASH=1
# 或 settings：agent_package_require_content_hash=true
```

内容哈希白名单（示例）：

```env
# 多个用逗号分隔
TEVARN_PKG_TRUSTED_CONTENT_HASHES=abc123...,def456...
```

（具体 env 名以 `backend/core/config.py` 的 `Settings` 字段映射为准：`agent_package_*` → 通常 `TEVARN_AGENT_PACKAGE_*` 或文档中的别名。）

---

## 3. 校验 API

| 端点 | 作用 |
|------|------|
| `GET /api/packages/market/trust` | 当前信任状态（是否 verified、白名单是否生效） |
| 市场安装路径 | HTTPS + 重定向再校验 SSRF；内容 sha256 命中白名单 |

前端：`/market` 页展示 trust 状态。

---

## 4. 失败模式（预期行为）

| 场景 | 行为 |
|------|------|
| 生产无签名密钥 | 包不得标为 verified；安装应拒绝或告警 |
| 远程包 hash 不在白名单 | 拒绝安装 |
| 仅 JWT 派生密钥 | 日志警告；控制台显示非生产信任 |
| `insecure_default` | 仅测试；**禁止**当生产 verified |

---

## 5. 轮换

1. 生成新 `TEVARN_PKG_SIGNING_KEY`  
2. 用新密钥重签已发布包并更新 content hash 白名单  
3. 滚动重启 host / backend  
4. 确认 `/api/packages/market/trust` 为 verified  

---

## 6. 关联代码

- `backend/core/config.py` — `agent_package_*`  
- `backend/kernel_rust/client.py` — `_configure_pkg_signing`  
- `crates/tevarn-kernel/src/package_mgr.rs`  
- `backend/packages/market.py` / `backend/api/routes/packages.py`  
- [RELEASE_0.5.0-alpha.md](./RELEASE_0.5.0-alpha.md) 已知注意点  

---

## 7. Court fail-closed 速查（H-04）

| host | `agent_court_rust_required` | 行为 |
|------|----------------------------|------|
| 在线，Rust 裁决成功 | true/false | 使用 Rust 结果 |
| 在线，Rust 无结果/失败 | **true** | **deny**（fail-closed） |
| 在线，Rust 无结果/失败 | false | 回退 Python court |
| 离线 | true/false | 回退 Python court（单测/无 host 可跑） |
| `agent_permission_enabled=false` | * | 全放行（仅调试） |
