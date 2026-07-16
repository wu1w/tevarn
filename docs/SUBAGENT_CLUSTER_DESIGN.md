# 子代理集群设计方案 v0.2

> 核心原则：所有模型统一在 Settings 中配置，形成一个"模型池"，
> 子代理和主 Agent 都从这个池里选模型。

---

## 一、架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                    Settings 页（模型统一入口）                      │
│                                                                  │
│  ┌────────── 已配模型清单 ─────────────────────────────────┐     │
│  │  当前使用: Claude 3.5 Sonnet  ·  默认: GPT-4o  ·  备用: DeepSeek V4 │
│  │  ┌──────────────────────────────────────────────────────┐ │     │
│  │  │ 🤖 Anthropic  ·  Claude 3.5 Sonnet     ✅ 当前/默认   │ │     │
│  │  │ 🤖 OpenAI    ·  GPT-4o                  ✅ 可用        │ │     │
│  │  │ 🤖 DeepSeek  ·  DeepSeek V4             ✅ 备用        │ │     │
│  │  │ 🤖 Ollama    ·  qwen3:32b               ⚠️ 未连接     │ │     │
│  │  └──────────────────────────────────────────────────────┘ │     │
│  └──────────────────────────────────────────────────────────┘     │
│                                                                  │
│  ┌── 新会话默认模型 ─────────────────────────────────────────┐   │
│  │  [Claude 3.5 Sonnet ▼]   ← 从已配清单中选择                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌── 备用模型（主模型不可用时自动回退） ──────────────────┐     │
│  │  [GPT-4o ▼]   ← 从已配清单中选择                         │     │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  下面保持现有"选择服务商 → 填密钥 → 拉模型 → 保存"流程不变         │
└─────────────────────────────────────────────────────────────▲────┘
                                                              │
                                                              │ 模型池 API
                                                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     /subagents 页（子代理管理）                    │
│                                                                  │
│  ┌── 新建子代理 ────────────────────────────────────────────┐   │
│  │  名称: [代码审查员              ]  图标: [📋 ▼]            │   │
│  │  模型: [Claude 3.5 Sonnet ▼]  ← 下拉只显示已配清单中的模型  │   │
│  │                                                             │   │
│  │  工具: ☑ file  ☑ terminal  ☑ git                           │   │
│  │                                                             │   │
│  │  系统提示词:                                                 │   │
│  │  [你是资深代码审查专家，擅长发现逻辑错误...]                    │   │
│  │                                                             │   │
│  │  最大工具轮次: [5]   创意度: [0.3 ■□□□□]                     │   │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
                                                              │
                                                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    后端数据流                                     │
│                                                                  │
│  ┌──────────────┐     ┌──────────────────┐     ┌────────────┐   │
│  │ ModelCatalog │────▶│  ModelInventory   │◀────│  SubAgent  │   │
│  │  (供应商)     │     │  (展平模型池)      │     │  (选模型)   │   │
│  │              │     │                   │     │            │   │
│  │ providers[]  │     │ inventory = [     │     │ model_ref  │   │
│  │  └ cached_   │     │  {provider_id,    │     │ = "anthro- │   │
│  │    _models[] │     │   provider_name,  │     │   pic/clau- │   │
│  │ default_id   │     │   model_name,     │     │   de-sonnet"│   │
│  │ default_mod  │     │   status}         │     │            │   │
│  │ fallback_id  │     │ ]                 │     │ → 运行时从  │   │
│  │ fallback_mod │     └──────────────────┘     │   catalog   │   │
│  └──────────────┘                               │   查配置    │   │
│                                                  └────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 二、模型池设计（核心变更）

### 2.1 ModelCatalog 结构变更

在现有 `llm_model_catalog` 中新增字段：

```python
{
  "version": 2,
  "active_provider_id": "anthropic",     # 当前运行时使用
  "active_model": "claude-sonnet-4-20250514",

  # === 新增字段 ===
  "default_provider_id": "anthropic",    # 新会话默认
  "default_model": "claude-sonnet-4-20250514",
  "fallback_provider_id": "openai",      # 主模型不可用时回退
  "fallback_model": "gpt-4o",

  "providers": [
    {
      "id": "anthropic",
      "name": "Anthropic",
      "icon": "🤖",
      "llm_provider": "anthropic",
      "llm_base_url": "",
      "enabled": true,
      "credentials": [...],
      "active_credential_id": "cred_abc123",
      "disabled_models": ["claude-3-haiku"],
      # === 新增字段 ===
      "cached_models": [                  # 上次拉取的模型列表
        "claude-sonnet-4-20250514",
        "claude-3-5-sonnet-20241022",
        "claude-opus-4-20250514"
      ]
    }
  ]
}
```

### 2.2 ModelInventory API（新增）

展平的"模型池"接口，前端和子代理配置都从它获取可选模型：

```
GET /api/settings/model-inventory

Response:
{
  "inventory": [
    {
      "ref": "anthropic/claude-sonnet-4-20250514",     # 唯一标识
      "provider_id": "anthropic",
      "provider_name": "Anthropic",
      "provider_icon": "🤖",
      "model_name": "claude-sonnet-4-20250514",
      "status": "active",        # active | default | fallback | available | disabled
      "connected": true          # 是否已验证连接
    },
    {
      "ref": "openai/gpt-4o",
      "provider_id": "openai",
      "provider_name": "OpenAI",
      "provider_icon": "🤖",
      "model_name": "gpt-4o",
      "status": "fallback",
      "connected": true
    }
  ]
}
```

模型池的构成规则：
1. 取所有 `enabled=true` 的 provider
2. 若 `cached_models` 非空，取 cached_models 中的每个模型
3. 若 `cached_models` 为空，取 `active_model`（至少有一条）
4. 标记哪些是 default / fallback / active

### 2.3 Settings 页变更

**顶部新增"已配模型清单"区块**，取代现有的"当前对话使用中"信息条：

```
┌── 已配模型清单 ─────────────────────────────────────────┐
│  ┌──── 当前会话 ────┐  ┌── 新会话默认 ──┐  ┌── 备用 ──┐  │
│  │ 🔵 Claude 3.5   │  │ ⭐ GPT-4o     │  │ 🔴 DeepSeek│  │
│  │   Sonnet        │  │               │  │   V4      │  │
│  └────────────────┘  └───────────────┘  └───────────┘  │
│                                                          │
│  🤖 Anthropic  Claude 3.5 Sonnet          ✅ 可用        │
│  🤖 OpenAI     GPT-4o                     ⭐ 默认        │
│  🤖 OpenAI     GPT-4o-mini                ✅ 可用        │
│  🤖 DeepSeek   DeepSeek V4                🔄 备用        │
│  🤖 Ollama     qwen3:32b                  ⚠️ 未测试     │
│  [+ 新增服务商 → 跳转下方配置]                           │
└──────────────────────────────────────────────────────────┘
```

**"新会话默认模型"和"备用模型"作为两个独立下拉选择器**，放在清单下方，选项来源就是上面的 inventory。

---

## 三、子代理配置

### 3.1 数据模型

```python
class SubAgent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "sub_agents"

    user_id: uuid.UUID | None

    # 基本信息
    name: str(64)
    description: str(256)
    icon: str(8)          # emoji

    # 模型引用 —— 从模型池中选，存 ref
    model_ref: str(128)   # "anthropic/claude-sonnet-4-20250514"

    # 角色定义
    system_prompt: Text

    # 工具配置
    enabled_toolsets: JSON   # ["file", "terminal", ...]

    # 执行参数
    max_iterations: int = 5
    temperature: float = 0.3

    # 状态
    enabled: bool = True
    sort_order: int = 0
```

**关键设计**: `model_ref` 格式为 `{provider_id}/{model_name}`，运行时通过 Catalog 查找对应的 provider 配置（base_url / api_key / provider 类型）。

### 3.2 子代理运行时解析

```python
class SubAgentRunner:
    async def _resolve_model(self, model_ref: str) -> LLMConfig:
        """从模型池解析模型配置"""
        provider_id, model_name = model_ref.split("/", 1)
        catalog = await load_catalog(repo)
        provider = next(p for p in catalog["providers"] if p["id"] == provider_id)
        return LLMConfig(
            provider=provider["llm_provider"],
            model=model_name,
            base_url=provider["llm_base_url"],
            api_key=provider["active_api_key"],
        )
```

**降级策略**：子代理填的 `model_ref` 对应的 provider 被删除或不可用时：
- 自动降级到主 Agent 的 active model
- 在子代理状态栏显示黄色警告

### 3.3 前端配置 UI

新建/编辑子代理弹窗，模型字段改为**下拉选择器**，选项来自 `GET /api/settings/model-inventory`：

```
  模型:
  ┌──────────────────────────────────────────────┐
  │ 🤖 Anthropic · Claude 3.5 Sonnet       ▼    │
  ├──────────────────────────────────────────────┤
  │ 🤖 Anthropic · Claude 3.5 Sonnet  ✅ 当前使用 │
  │ 🤖 OpenAI    · GPT-4o              ⭐ 默认   │
  │ 🤖 OpenAI    · GPT-4o-mini                    │
  │ 🤖 DeepSeek  · DeepSeek V4         🔄 备用   │
  │ 🤖 Ollama    · qwen3:32b           ⚠️ 未连接  │
  └──────────────────────────────────────────────┘
```

---

## 四、聊天界面集群模式

### 4.1 集群模式开关

在 `MessageInput` 工具栏左侧添加，效果参考 Hermes Desktop 的模型选择器位置：

```
┌──────────────────────────────────────────────────┐
│  [集群模式: OFF]  [📎 附件] [发送 🠅]             │
└──────────────────────────────────────────────────┘
  点击 → 打开子代理选择面板：
┌── 集群模式 ───────────────────────────────────────┐
│  🔵 启用集群模式                                   │
│                                                    │
│  选择本次要加入的子代理：                            │
│  ☑ 📋 代码审查员     Claude 3.5 Sonnet             │
│  ☑ 🔍 安全审计员     DeepSeek V4                   │
│  ☐ 📝 文档生成器     GPT-4o                        │
│  ☐ 🐛 Bug 调试员     Claude 3.5 Sonnet             │
│                                                    │
│  [+ 管理子代理 → /subagents]                        │
└────────────────────────────────────────────────────┘
```

### 4.2 子代理状态栏

每次用户发送消息后，在聊天区底部显示实时状态：

```
📋 代码审查员 ✅ 已完成  发现 3 个问题
🔍 安全审计员 🔄 执行中... 扫描 auth.py
📝 文档生成器 ⏸ 待命
```

点击完成状态的子代理可展开查看详细输出。

---

## 五、实施计划（更新版）

### Sprint 1：模型池改造（3-4天）

| 任务 | 说明 |
|------|------|
| Catalog 新增字段 | `cached_models`、`default_*`、`fallback_*` |
| `ModelInventory API` | 展平模型池接口 |
| Settings 页改造 | 顶部已配模型清单 + 默认+备用下拉选择器 |
| Provider 拉取模型后缓存 | 拉取模型列表后写入 `cached_models` |

### Sprint 2：子代理后端（3-4天）

| 任务 | 说明 |
|------|------|
| `sub_agents` 表 | migration + ORM |
| CRUD API | `backend/api/routes/sub_agents.py` |
| SubAgentRunner | 从模型池解析配置 + 独立 LLM + 独立工具 |
| SubAgentOrchestrator | 并行分发 + 顾问模式 |
| NexusAgentLoop 改造 | 集群模式钩子 |

### Sprint 3：子代理前端（2-3天）

| 任务 | 说明 |
|------|------|
| `/subagents` 页面 | 配置管理 + 模型下拉选已配清单 |
| 新建/编辑弹窗 | 模型选择器（数据源：inventory API） |
| 聊天界面集群开关 | 工具栏开关 + 子代理选择面板 + 状态栏 |

### Sprint 4：打磨（1-2天）

| 任务 | 说明 |
|------|------|
| 预设模板 | 内置 5-6 个常用子代理模板 |
| 错误处理 | 模型降级、超时、重试 |
| 空状态引导 | 首次使用指引 |

---

## 六、关键决策

1. **Settings 是模型配置的唯一入口**。子代理没有独立模型配置页，所有 provider/api key 都在 Settings 配
2. **模型池 = 所有 enabled provider 的已知模型集合**。每个 provider 的 `cached_models` 在拉取模型列表时更新
3. **子代理的 model_ref 格式 `provider_id/model_name`**。运行时从 catalog 查配置，不存冗余
4. **模型不可用时自动降级到主 Agent 的 active model**，并在状态栏提示
5. **第一版只做并行分发 + 顾问模式**，串行流水线留给工作流引擎
6. **子代理不和用户直接交互**，结果注入主 Agent 上下文
