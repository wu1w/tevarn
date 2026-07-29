# Takton Agent 代码审计报告

**审计日期：** 2026-07-29
**审计范围：** `backend/` 全模块（531个Python文件）
**审计团队：** 安澜（安全）、凌远（逻辑）、钟离（架构）

---

## 📊 总览

| 严重度 | 安全（安澜） | 逻辑（凌远） | 架构（钟离） | 合计 |
|--------|:-----------:|:-----------:|:-----------:|:----:|
| 🔴 Critical | 4 | 3 | 4 | **11** |
| 🟠 High | 2 | 1 | 5 | **8** |
| 🟡 Medium | 1 | 2 | 5 | **8** |
| 🔵 Low | 2 | 2 | 3 | **7** |
| **小计** | **9** | **8** | **17** | **34** |

---

## 一、安全审计（安澜）

### 🔴 Critical（4项）

**S-C1. Shell 命令注入（`backend/tools/sandbox.py`）**
- 位置：`_exec()` 函数
- 问题：`shell=True` 时 Windows 下 `cmd.exe` 转义规则不完善，攻击者可绕过 `shlex.quote()` 执行任意命令
- 影响：任意代码执行
- 修复：Windows 改用列表参数 `shell=False`；增加命令白名单；对管道/重载符显式过滤

**S-C2. 沙箱路径绕过（`backend/tools/sandbox.py`）**
- 位置：路径校验逻辑
- 问题：路径规范化（`os.path.normpath`）后未再验证最终路径是否仍在 `workspace_root` 内，`../` 序列可绕过
- 影响：读写沙箱外任意文件
- 修复：校验 `os.path.realpath()` 结果是否以 `workspace_root` 开头

**S-C3. 工具执行无认证（`backend/agent/tool_execution.py`）**
- 位置：`execute_tool()` 入口
- 问题：工具调用不校验调用者身份，任何 session 可调用任意已注册工具
- 影响：权限提升、越权操作
- 修复：增加 tool-level 权限矩阵，按 identity role 过滤

**S-C4. 数据库注入风险（`backend/agent/context_pipeline.py`）**
- 位置：消息查询拼接
- 问题：部分查询使用 f-string 拼接 SQL，未使用参数化查询
- 影响：SQL 注入
- 修复：全部改用 SQLAlchemy ORM 或参数化查询

### 🟠 High（2项）

**S-H1. API Key 明文比较（`backend/services/`）**
- 问题：使用 `==` 比较 API key，存在时序攻击
- 修复：改用 `hmac.compare_digest()`

**S-H2. 临时文件信息泄露（`backend/tools/sandbox.py`）**
- 问题：临时文件创建后未在 finally 中清理，异常时残留敏感输出
- 修复：用 `try/finally` 或 `tempfile.TemporaryDirectory` 自动清理

### 🟡 Medium（1项）

**S-M1. 错误信息泄露堆栈（`backend/agent/`）**
- 问题：异常消息直接返回给用户，含文件路径和内部结构
- 修复：生产环境返回通用错误，详细信息仅写日志

### 🔵 Low（2项）

**S-L1. 日志中可能包含敏感数据** — 日志未脱敏处理
**S-L2. 硬编码超时值** — 多处硬编码超时，建议统一配置

---

## 二、逻辑审计（凌远）

### 🔴 Critical（3项）

**L-C1. 重试无上限（`backend/agent/tool_execution.py`）**
- 位置：工具执行重试逻辑
- 问题：`_retry_on_transient()` 无限重试，无最大次数限制
- 影响：永久循环消耗资源
- 修复：增加 `max_retries` 参数（默认3-5次），超限抛异常

**L-C2. 内存状态写入竞态（`backend/kernel/memory.py`）**
- 位置：并发写入
- 问题：多 session 同时写入 memory store 时无锁保护，可能丢数据
- 影响：记忆数据丢失/损坏
- 修复：加 `asyncio.Lock` 或用数据库事务保证原子性

**L-C3. 上下文截断丢失关键信息（`backend/agent/context_pipeline.py`）**
- 位置：token 超限时截断
- 问题：截断策略不保留 system prompt 和最近消息边界，可能截断工具调用-结果对
- 影响：LLM 上下文断裂，行为异常
- 修复：截断时保护完整的消息对（tool_call + tool_result）

### 🟠 High（1项）

**L-H1. 会话状态泄漏（`backend/agent/session_manager.py`）**
- 问题：session 关闭时未清理关联的临时状态（工具缓存、中间结果）
- 影响：下一 session 可能读到前一 session 的残留数据
- 修复：session close 时显式清理所有临时状态

### 🟡 Medium（2项）

**L-M1. Checkpoint 恢复不一致（`backend/agent/checkpoint.py`）**
- 问题：恢复时只恢复消息列表，不恢复工具缓存和中间状态
- 影响：恢复后重试工具调用可能产生不同结果

**L-M2. Compaction 生成不幂等（`backend/agent/compaction.py`）**
- 问题：同一上下文多次压缩可能产生不同摘要
- 影响：压缩结果不稳定
- 修复：压缩输入标准化后再压缩

### 🔵 Low（2项）

**L-L1. 日志级别硬编码** — 部分模块日志级别写死，无法动态调整
**L-L2. 超时值分散** — 多处超时值各写各的，缺乏统一配置

---

## 三、架构审计（钟离）

### 🔴 Critical（4项）

**A-C1. 全局单例阈值竞态（`backend/agent/context_compress.py` + `context_engine.py`）**
- 位置：`compress_history_if_needed()` 直接修改全局 `engine.threshold_percent`
- 问题：多 async session 并发调用时阈值互相覆盖
- 影响：压缩行为不可预测
- 修复：阈值改为 per-session 或加锁

**A-C2. 数据库连接泄漏（`backend/database.py`）**
- 位置：`get_db()` 依赖
- 问题：异常路径下 session 未正确关闭，连接池耗尽
- 影响：服务不可用
- 修复：改用 `async with AsyncSessionLocal() as session:` 模式

**A-C3. Identity 注册表无唯一约束（`backend/kernel/identity.py`）**
- 问题：相同 name 可重复注册，无幂等保护
- 影响：身份冲突
- 修复：数据库层加 `UNIQUE` 约束

**A-C4. 工具注册表无去重（`backend/tools/registry.py`）**
- 问题：同名工具重复注册不报错，后者静默覆盖前者
- 影响：工具行为不可预期
- 修复：重复注册抛异常或警告

### 🟠 High（5项）

**A-H1. Memory 向量索引一致性（`backend/memory/`）**
- 问题：向量写入和元数据写入非原子操作，部分失败导致索引不一致

**A-H2. 配置项缺默认值（多处）**
- 问题：部分配置项不设置会抛 KeyError 而非优雅降级

**A-H3. 模块循环依赖风险（`backend/kernel/` ↔ `backend/agent/`）**
- 问题：kernel 和 agent 互相 import，可能导致初始化失败

**A-H4. 硬编码 Windows 路径（多处）**
- 问题：路径使用 `\\` 硬编码，Linux/macOS 不兼容

**A-H5. 工具超时无统一管理（`backend/tools/`）**
- 问题：各工具各自设置超时，无全局上限

### 🟡 Medium（5项）

**A-M1. 启动/关闭流程不完整（`backend/main.py`）** — 缺少优雅关闭和资源释放
**A-M2. Session 恢复后 context_engine 状态不同步** — 恢复时钟表对象可能与实际不一致
**A-M3. Memory 查询无分页限制** — 大量记忆时一次性加载全部
**A-M4. 状态模型版本无迁移机制** — 模型变更后旧数据可能不兼容
**A-M5. Agent 循环无健康检查** — 主循环卡死无自动恢复

### 🔵 Low（3项）

**A-L1. 缺少类型注解** — 部分模块无 type hints
**A-L2. 测试覆盖率低** — 核心模块缺少单元测试
**A-L3. 文档缺失** — API 和架构文档不完整

---

## 四、修复优先级建议

### 第一波（本周）—— 安全类 Critical + High
1. 消除所有 `shell=True` 的命令注入漏洞（S-C1）
2. 修复沙箱路径绕过（S-C2）
3. 工具执行增加权限校验（S-C3）
4. SQL 拼接全部改参数化查询（S-C4）
5. 沙箱默认启用（S-H1）
6. API Key 改用安全比较（S-H2）

### 第二波（下周）—— 逻辑类 Critical
1. 重试增加上限（L-C1）
2. 内存写入加锁（L-C2）
3. 上下文截断保护消息对完整性（L-C3）
4. Session 关闭时清理状态（L-H1）

### 第三波（持续）—— 架构类
1. 全局单例竞态修复（A-C1）
2. 数据库连接泄漏修复（A-C2）
3. Identity/工具注册表加唯一约束（A-C3, A-C4）
4. 配置系统统一化
5. 补充测试和文档

---

## 五、风险总结

| 风险类别 | 风险等级 | 说明 |
|---------|---------|------|
| 远程命令执行 | 🔴 严重 | Shell 注入 + 沙箱绕过 |
| 数据泄露 | 🟠 高 | 路径遍历、错误信息泄露 |
| 服务可用性 | 🟠 高 | 数据库连接泄漏、重试死循环 |
| 数据一致性 | 🟡 中 | 竞态条件、索引不一致 |
| 可维护性 | 🔵 低 | 硬编码、缺文档、测试不足 |

---

*报告完毕。如有疑问可联系审计团队：安澜（安全）、凌远（逻辑）、钟离（架构）。*
