# Takton 后端 API 路由层 & 模型层 Bug 审查报告

> 审查范围：backend/api/routes/*, backend/api/dependencies.py, backend/api/websocket.py, backend/models/*
> 审查日期：2026-07-06

---

## 🔴 CRITICAL

### 1. `skills.py` - `dry_run` 类型不匹配导致逻辑反转
**位置**: 社区 Skill 导入逻辑，约第 180 行
**问题**: `request.options` 是 `dict[str, Any]`，前端传字符串 `"false"` 会被视为 `True`。
**修复**: 
```python
dry_run = str(request.options.get("dry_run", False)).lower() in ("true", "1", "yes")
```

### 2. `workflows.py` - `execute_workflow` 裸 `except Exception` 吞异常
**位置**: `execute_workflow` 函数
**问题**: 捕获所有异常返回 200 OK，HTTP 状态码不规范，客户端无法感知失败。
**修复**: 区分业务异常和系统异常，返回适当的 HTTP 状态码。

### 3. `upload.py` - 路径穿越漏洞风险
**位置**: 文件上传处理
**问题**: `_sanitize_filename` 未过滤 `..` 和空字符，虽然后续有 `commonpath` 检查，但 `safe_filename` 仍可能包含路径分隔符。
**修复**: 在 `_sanitize_filename` 中额外过滤 `..` 和空字符，移除所有路径分隔符。

### 4. `wiki.py` - `WikiImportResult` 未初始化 `detail` 列表
**位置**: `import_wiki` 函数
**问题**: 如果 `WikiImportResult` 的 `detail` 字段默认值为 `None`，`append` 会引发 `AttributeError`。
**修复**: 确保 schema 中 `detail` 默认值为 `[]`，或显式初始化。

### 5. `tools.py` - `result.startswith("[Error]")` 脆弱判断
**位置**: `execute_tool_endpoint` 函数
**问题**: 依赖字符串前缀判断不可靠，`None` 或非字符串类型会触发 `AttributeError`。
**修复**: 返回结构化对象或抛出异常，而非依赖字符串前缀。

---

## 🟠 HIGH

### 6. `dependencies.py` - `uuid.UUID(payload["sub"])` 可能失败
**位置**: `get_current_user`
**问题**: 格式非有效 UUID 时抛出 `ValueError` 未被捕获，返回 500 而非 401。
**修复**: 添加异常处理，返回 401。

### 7. `websocket.py` - WebSocket 认证依赖在事务外创建
**位置**: `websocket_endpoint`
**问题**: `session_repo` 等 repository 通过 `Depends` 注入，但可能持有旧的 session 连接，导致事务隔离问题。
**修复**: 在 WebSocket 中统一使用 `get_db_context()` 获取的 session 创建 repository 实例。

### 8. `skills.py` - 创建 Skill 未设置 `user_id`
**位置**: `create_skill`
**问题**: 自定义 Skill 无权限隔离，所有用户全局可见。
**修复**: `payload["user_id"] = current_user.id`

### 9. `knowledge.py` - 查询所有文档无用户隔离
**位置**: `list_documents`
**问题**: `list_all()` 返回所有用户的文档，数据泄露风险。
**修复**: 改为 `list_by_user(current_user.id)` 或添加过滤。

### 10. `notification.py` - `NotificationType` 继承 `str` 而非 `Enum`
**问题**: 类型检查工具无法识别为枚举，数据库不自动校验范围。
**修复**: `class NotificationType(str, Enum):`

### 11. `auth.py` - 注册即 `is_superuser=True`
**问题**: 第一个注册用户自动成为管理员，数据库重置后任何新用户都能提权。
**修复**: 添加 setup token 或检查数据库是否为空。

### 12. `context.py` - `ScopeKey` 和 `ItemKind` 枚举混用
**问题**: `PyEnum` 与 `str` 混用，数据库层面不报错。
**修复**: 在 Pydantic schema 层增加额外值校验。

---

## 🟡 MEDIUM

### 13. `devices.py` - 无权限校验
**问题**: 查询/更新/删除设备时未检查 `device.user_id`。
**修复**: 添加所有权检查，403。

### 14. `messages.py` - 同样缺少权限校验
**问题**: 未校验 `session_id` 是否属于当前用户。
**修复**: 添加 session 所有权检查。

### 15. `settings.py` - `update_setting` 无权限校验
**问题**: 设置更新无权限隔离。
**修复**: 添加用户权限检查。

---

## 总结

| 级别 | 数量 | 关键问题 |
|------|------|----------|
| CRITICAL | 5 | 类型逻辑反转、异常吞没、路径穿越、未初始化、脆弱判断 |
| HIGH | 7 | JWT解析、事务隔离、权限隔离、枚举类型、提权漏洞 |
| MEDIUM | 3 | 设备/消息/设置权限校验缺失 |

**最优先修复**:
1. `upload.py` 路径穿越漏洞（安全）
2. `auth.py` 注册即管理员（安全）
3. `skills.py` 无用户隔离（安全）
4. `knowledge.py` 无用户隔离（安全）
5. `workflows.py` 异常处理（稳定性）
