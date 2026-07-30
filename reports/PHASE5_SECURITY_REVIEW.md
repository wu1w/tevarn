# Phase 5.2a 公开暴露面安全终审

> 日期：2026-07-30 · 版本：`0.4.10-alpha` · 分支：`feature/agent-kernel`  
> 范围：**公开暴露面**（非全库哲学审计）

---

## 1. 清单与结论

| 面 | 检查点 | 状态 | 证据 / 动作 |
|----|--------|------|-------------|
| 认证 / JWT | 长度 ≥16；拒绝已知弱密钥；可自动生成 | ✅ | `security_check` · `_reject_default_secrets` · `test_phase5_security_surface` |
| 绑定 × single_user | 非 loopback + single_user = fail | ✅ | `host_single_user_combo` |
| CORS | 默认非 `*`；空=仅 loopback | ✅ | `cors_allowed_origins` 默认 `""` |
| Channel 入站 | 长度上限 / NUL / 非打印 | ✅ | `sanitize_channel_ingress` · D1 测试 |
| Channel webhook 签名 | 平台 adapter 侧；网关去重 | ⚠️ 部分 | 去重+入站 harden 已做；各 IM 签名依 adapter 配置 |
| 工具执行 | loopback 信任边界 · shell 注入回归 | ✅ | `backend/tests/security/` |
| 默认凭据 | 禁止弱 admin；首启随机密码 | ✅ | `default_admin_password` 逻辑 |
| 零依赖启动 | 无 Redis/Qdrant 不阻断 | ✅ | `smoke_zero_deps` · `test_phase5_zero_deps` |
| 管理 API | 依赖 `get_current_user` | ✅ | 既有路由 Depends |

**整体**：无未关闭 **fail** 级项。webhook 各平台签名依赖运营配置，文档见 `docs/CHANNEL_POLICY.md`。

---

## 2. 回归命令

```text
.venv/Scripts/python.exe -m pytest backend/tests/security backend/tests/test_phase5_security_surface.py backend/tests/test_phase5_zero_deps.py -q
.venv/Scripts/python.exe scripts/smoke_zero_deps.py
```

---

## 3. 残留 / 后续

| ID | 项 | 优先级 |
|----|-----|--------|
| R1 | 各 channel adapter webhook 签名开关统一文档化 | 中 |
| R2 | 激进 prompt-injection 过滤（易误伤，0.4.10 不做） | 低 |
| R3 | 非 loopback 生产部署 checklist 单独页 | 中 |

---

## 4. 签字

工程侧 Phase 5.2a：**通过（有文档化残留）**
