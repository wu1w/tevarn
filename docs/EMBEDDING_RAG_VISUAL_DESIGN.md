# Embedding & RAG 可视化增强 + 健壮性设计 v0.1

---

## 现状问题分析

### 1. Embedding 模块问题

| 问题 | 细节 |
|------|------|
| **维度不感知** | 创建 Qdrant collection 时才从第一次 embed 结果推导 `vector_size`，但从不验证后续 embed 是否同维度。换模型后维度变化会导致 Qdrant 写入失败，无法提前预警 |
| **单例不可热切换** | `EmbeddingServiceFactory` 是单例，Settings 改了 provider/model 后需要重启生效，用户无感知 |
| **无健康检查** | 只在启动时测一次，运行中断连不会自动降级 |
| **批量大小硬编码** | `batch=16` 写死在 indexer 里，大维度模型（4096维）IO 瓶颈，小维度（384维）浪费 |
| **无错误分类** | 所有异常一把抓，不区分网络错误 / 维度不匹配 / 认证错误 |

### 2. RAG 检索链路问题

| 问题 | 细节 |
|------|------|
| **无重试机制** | Qdrant 瞬时不可用（重启/迁移）直接返回空 |
| **collection 耦合** | 一个 collection 存所有类型文档，无租户/namespace 隔离 |
| **无检索诊断** | 用户只知道"搜到 0 条"，不知是 embedding 失败 / Qdrant 挂了 / 确实没数据 |
| **Reranker 回退粗糙** | Reranker 失败时退回粗排分数，但无日志告知用户降级了 |
| **无检索测试** | 前端没有"输入查询→实时看结果"的控制台 |

### 3. 知识库前端问题

| 问题 | 细节 |
|------|------|
| **索引进度不可见** | 只有文字状态标签，没有进度条/分块可视化 |
| **无检索测试** | 无法在前端验证"我的文档到底能不能被搜到" |
| **无 Qdrant 状态** | 不知道 collection 是否存在、有多少向量、维度多少 |
| **无检索链路可视化** | 不知道一次查询走了什么路径（embed→search→rerank→format） |

---

## 设计方案

### 一、Embedding 模块增强

#### 1.1 EmbeddingMetadata — 维度感知

```python
@dataclass
class EmbeddingMetadata:
    """Embedding 服务的元信息，用于维度校验和前端展示"""
    provider: str          # ollama / openai / openai-compatible
    model: str             # text-embedding-3-small
    dimension: int         # 1536
    max_batch_size: int    # 建议批量大小
    healthy: bool          # 是否健康
    last_check: datetime   # 最后一次健康检查时间
    supported_dimensions: list[int]  # 该模型支持的维度（如 OpenAI text-embedding-3 支持 512/1536）
```

每次 `embed()` 调用后自动更新 `EmbeddingMetadata`。Settings 页展示当前维度。

#### 1.2 智能批量大小

```python
BATCH_SIZE_MAP = {
    384:  64,   # nomic-embed-text / all-MiniLM-L6-v2
    768:  32,   # bge-base / e5-base
    1024: 24,   # bge-large
    1536: 16,   # OpenAI text-embedding-3-small
    3072: 8,    # OpenAI text-embedding-3-large
    4096: 4,    # 大维度模型
}
# fallback: max(4, 16384 // dimension)
```

根据实际 `dimension` 动态选择批量大小，减少硬编码。

#### 1.3 维度不匹配检测 + 自动迁移

```python
async def embed_with_validation(service, texts):
    vectors = await service.embed(texts)
    current_dim = len(vectors[0])
    
    # 对比缓存维度
    if service.metadata.dimension and current_dim != service.metadata.dimension:
        raise EmbeddingDimensionMismatchError(
            f"维度变化：{service.metadata.dimension} → {current_dim}。"
            f"可能是模型已更换。Qdrant collection 需要重建。"
        )
    return vectors
```

当检测到维度变化时：
1. Settings 页顶部显示红色警告："⚠️ Embedding 模型维度不匹配，知识库检索可能失败"
2. 提供"一键重建索引"按钮 — 删除旧 collection，重建新维度 collection，重新索引所有文档
3. 索引过程中显示进度条

#### 1.4 健康检查 + 自动降级

```
定时检查 (每 60s):
  embedding.healthy = await service.embed(["ping"]) succeeds?
  → 不健康时 Settings 页显示状态
  → RAG 检索时自动降级为"仅粗排"或"返回空+友好提示"
```

---

### 二、RAG 检索增强

#### 2.1 检索诊断信息

`search_knowledge_base` 返回结构化诊断信息，而非简单字符串：

```python
@dataclass
class RAGDiagnostics:
    """RAG 检索诊断信息"""
    # 每步状态
    embed_status: str       # "ok" | "empty" | "timeout" | "error"
    embed_time_ms: float
    embed_dimension: int
    
    search_status: str      # "ok" | "empty" | "timeout" | "error"
    search_time_ms: float
    search_hits: int        # Qdrant 返回的原始命中数
    
    rerank_status: str      # "ok" | "skipped" | "fallback" | "error"
    rerank_time_ms: float
    
    total_time_ms: float
    formatted_context: str  # 最终返回给 LLM 的上下文
```

前端检索测试面板可视化这条链路。

#### 2.2 重试 + 指数退避

```python
@retry(
    max_attempts=3,
    delay=1.0,
    backoff=2.0,
    exceptions=(aiohttp.ClientError, asyncio.TimeoutError)
)
async def _qdrant_search(...)
```

#### 2.3 链路可视化（前端新增检索测试面板）

```
┌── 检索测试 ──────────────────────────────────────────────┐
│  [输入查询文本...]  [检索]  [top_k: 5 ▼]                  │
│                                                           │
│  ┌── 检索链路 ──── 总耗时 1.2s ──────────────────────┐   │
│  │                                                    │   │
│  │  ① Embedding  ✅ 0.3s                              │   │
│  │     └ 维度: 1536 · 模型: text-embedding-3-small     │   │
│  │                                                    │   │
│  │  ② Qdrant 粗排  ✅ 0.5s                            │   │
│  │     └ 命中 23 条 · Collection: knowledge_base      │   │
│  │                                                    │   │
│  │  ③ Reranker 精排  ✅ 0.4s                           │   │
│  │     └ BGE-Reranker · Top-5 筛选                     │   │
│  │                                                    │   │
│  │  ④ 上下文组装  ✅  <0.1s                             │   │
│  └────────────────────────────────────────────────────┘   │
│                                                           │
│  ┌── 检索结果 ──────────────────────────────────────┐    │
│  │  ┌────────────────────────────────────────────┐  │    │
│  │  │ 1.  Python 异步编程指南         相关度 0.92  │  │    │
│  │  │     asyncio 是 Python 3.4 引入的...         │  │    │
│  │  ├────────────────────────────────────────────┤  │    │
│  │  │ 2.  FastAPI 最佳实践             相关度 0.87  │  │    │
│  │  │     FastAPI 是一个现代、高性能的...          │  │    │
│  │  └────────────────────────────────────────────┘  │    │
│  └───────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

#### 2.4 Qdrant 状态监控

```
┌── Qdrant 连接状态 ──────────────────────────────────────┐
│  🔵 已连接  ·  http://localhost:6333                      │
│                                                           │
│  Collection: knowledge_base                              │
│  ├ 向量维度: 1536                                        │
│  ├ 向量数量: 2,341                                       │
│  ├ 索引状态: 100% (2,341 / 2,341)                       │
│  └ 占用空间: 14.2 MB                                     │
│                                                           │
│  [重建索引]  [清空 Collection]  [备份]                     │
└──────────────────────────────────────────────────────────┘
```

---

### 三、知识库前端可视化增强

#### 3.1 文档索引进度条

替换现有的文字状态标签：

```
┌── 文档列表 ─────────────────────────────────────────────┐
│                                                          │
│  📄 项目架构文档                           ✅ 已索引     │
│     ████████████████████████  12 分块                   │
│                                                          │
│  📄 API 接口说明                          🔄 索引中     │
│     ████████░░░░░░░░░░░░░░░░  8/23 分块  剩余 ~15s     │
│     (实时更新进度)                                       │
│                                                          │
│  📄 用户手册                              ❌ 失败        │
│     ░░░░░░░░░░░░░░░░░░░░░░░░                    [重试] │
│     错误: Embedding 维度不匹配 (当前: 1536, 期望: 768)    │
│                                                          │
│  📄 设计规范                              ⏸ 待索引     │
│     ░░░░░░░░░░░░░░░░░░░░░░░░                    [索引] │
└──────────────────────────────────────────────────────────┘
```

#### 3.2 索引页面整体布局

```
┌── 知识库 ───────────────────────────────────────────────┐
│                                                           │
│  ┌──────────── 仪表盘 ────────────────┐                  │
│  │  文档总数: 12   已索引: 9           │                  │
│  │  总向量数: 2,341   存储: 14.2 MB    │                  │
│  │  当前 Embedding: 1536维 ✅          │                  │
│  └────────────────────────────────────┘                  │
│                                                           │
│  ┌── 检索测试 ─────┐  ┌── Qdrant 状态 ─────────────┐   │
│  │  [输入查询...]   │  │  🔵 已连接 · 1536维        │   │
│  │  [检索]         │  │  向量: 2,341/2,341 已索引  │   │
│  │                  │  │  存储: 14.2 MB             │   │
│  │  ① Embed ✅     │  │                             │   │
│  │  ② Search ✅    │  │  [重建索引] [清空] [备份]   │   │
│  │  ③ Rerank ✅    │  │                             │   │
│  │  ④ Format ✅    │  │                             │   │
│  └─────────────────┘  └─────────────────────────────┘   │
│                                                           │
│  ┌── 文档列表 ───────────────────────────────────┐      │
│  │  [🔍 搜索...] [📤 批量上传] [+ 新建]          │      │
│  │  ...（带进度的文档卡片）                       │      │
│  └───────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

---

### 四、多维度 Embedding 兼容性设计

#### 4.1 问题场景

| 场景 | 示例 | 影响 |
|------|------|------|
| 用户切换 Embedding 模型 | Ollama nomic-embed-text(768维) → OpenAI text-embedding-3-small(1536维) | 旧向量与新查询向量维度不同，Qdrant 搜索报错 |
| 同一模型支持多维度 | OpenAI text-embedding-3-small 支持 512/1536 | 用户可能选择不同维度 |
| Qdrant 迁移 | 从本地 Qdrant 迁到云端 Qdrant | collection 重建 |

#### 4.2 解决方案

```python
class DimensionManager:
    """维度管理器 — 处理 Embedding 模型切换时的兼容性"""
    
    def __init__(self):
        self._current_dim: int | None = None
        self._collection_dims: dict[str, int] = {}  # collection → dimension
    
    async def detect_collection_dim(self, collection: str) -> int | None:
        """查询 Qdrant collection 的实际维度"""
        # GET /collections/{name} → config.params.vectors.size
    
    async def check_compatibility(
        self, 
        embedding_dim: int, 
        collection: str
    ) -> DimensionStatus:
        """
        检查 Embedding 维度与 Qdrant collection 是否兼容。
        
        返回:
        - COMPATIBLE: 维度匹配
        - MISMATCH: 维度不匹配，需要重建
        - MISSING: Collection 不存在
        - EMPTY: Collection 存在但为空（可安全用新维度）
        """
```

当检测到 `MISMATCH` 时，前端显示：

```
⚠️ Embedding 维度与知识库不匹配

当前 Embedding 模型: OpenAI text-embedding-3-small (1536维)
知识库 Collection:   knowledge_base (768维)

旧模型产生的向量无法用新模型搜索。
建议操作:

[方案 A] 一键重建索引 — 清空旧向量，用新模型重新索引（推荐）
         需要重新处理 9 个文档（约 2,341 个分块）⏱ 预计 3 分钟

[方案 B] 切回旧模型 — 将 Embedding 模型切回 nomic-embed-text (768维)
         知识库立即可用，无需重建

[方案 C] 新建 Collection — 保留旧数据，新建 knowledge_base_v2 (1536维)
         新旧数据并存，切换需修改配置
```

#### 4.3 Qdrant Collection 多维度支持

```python
# 备用方案：允许多个 collection 并存
# knowledge_base_768  — 旧模型数据
# knowledge_base_1536 — 新模型数据

# 检索时：
# 1. 检测当前 embedding_dim
# 2. 查找匹配维度的 collection
# 3. 如果无匹配 → 返回空 + 提示重建索引
```

---

### 五、Qdrant 健壮性增强

#### 5.1 连接池 + 重试

```python
class QdrantClientPool:
    """Qdrant 连接池 — 复用 HTTP 连接，带重试"""
    
    def __init__(self, url: str, pool_size: int = 5):
        self._session: aiohttp.ClientSession | None = None
        self._url = url
    
    async def get_session(self) -> aiohttp.ClientSession:
        """获取或创建 session（复用 TCP 连接）"""
    
    async def request(
        self,
        method: str,
        path: str,
        json: dict = None,
        retries: int = 3,
    ) -> dict:
        """带重试的 Qdrant API 请求"""
```

#### 5.2 健康检查 API（后端新增）

```
GET /api/knowledge/qdrant-status

Response:
{
  "connected": true,
  "url": "http://localhost:6333",
  "collection": "knowledge_base",
  "collection_exists": true,
  "dimension": 1536,
  "vectors_count": 2341,
  "vectors_indexed": 2341,
  "disk_size_mb": 14.2,
  "embedding": {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "dimension": 1536,
    "healthy": true
  },
  "compatibility": "OK"   // OK | MISMATCH | MISSING
}
```

#### 5.3 检索健康检查 API

```
POST /api/knowledge/rag-test
Body: { "query": "Python 异步编程", "top_k": 5 }

Response:
{
  "results": [...],
  "diagnostics": {
    "embed_status": "ok",
    "embed_time_ms": 320,
    "embed_dimension": 1536,
    "search_status": "ok", 
    "search_time_ms": 480,
    "search_hits": 23,
    "rerank_status": "ok",
    "rerank_time_ms": 400,
    "total_time_ms": 1200
  }
}
```

---

### 六、实施计划

#### Sprint 1：Embedding 健壮性 + 维度管理（3-4天）

| 任务 | 说明 |
|------|------|
| `EmbeddingMetadata` | 维度感知 + 健康检查 |
| `DimensionManager` | 维度兼容性检测 |
| 智能批量大小 | 根据维度动态选择 batch size |
| 维度不匹配检测 | embed 时校验 + Settings 页警告 |
| 工厂可重载 | `EmbeddingServiceFactory.reload()` |

#### Sprint 2：RAG 检索增强（2-3天）

| 任务 | 说明 |
|------|------|
| `RAGDiagnostics` | 检索诊断信息 |
| Qdrant 连接池+重试 | `QdrantClientPool` |
| 检索测试 API | `POST /api/knowledge/rag-test` |
| Qdrant 状态 API | `GET /api/knowledge/qdrant-status` |
| 检索降级策略 | Embedding 失败 → 提示 / Reranker 失败 → 退回粗排 |

#### Sprint 3：前端可视化（3-4天）

| 任务 | 说明 |
|------|------|
| 检索测试面板 | 查询→可视化链路→结果展示 |
| Qdrant 状态面板 | 连接状态 + 维度 + 向量数 + 存储 |
| 文档索引进度条 | 实时进度 + 错误详情 |
| 知识库仪表盘 | 统计卡片（文档数/向量数/存储） |
| 维度不匹配处理 UI | 三种方案（重建/回退/新建） |
| 一键重建索引 | 后台任务 + 进度轮询 |

#### Sprint 4：打磨（1-2天）

| 任务 | 说明 |
|------|------|
| 错误分类+友好提示 | 网络错误 / 认证错误 / 维度错误 分别处理 |
| 空状态引导 | 第一次使用知识库的引导流程 |
| 批量上传 UX | 拖拽多文件 + 总体进度 |

---

### 七、关键决策

1. **维度管理前置到配置层** — Settings 页保存 Embedding 维度，创建 Qdrant collection 时就用它，不等第一次 embed
2. **Qdrant 重试策略** — 瞬时不可用重试 3 次（总时间不超过 10s），避免假失败
3. **一键重建索引** — 删除旧 collection → 用新维度创建 → 遍历文档重新索引，后台执行，轮询进度
4. **检索诊断信息不增加 LLM 上下文** — 只把 `formatted_context` 给 LLM，诊断信息仅供前端展示
5. **多 collection 并存作为远期方案** — 第一版只做单 collection + 维度不匹配提示，不引入多 collection 复杂度

---

### 八、混合检索（Hybrid Search: BM25 + Vector + RRF）

#### 8.1 问题分析

当前 `QdrantRAGService.search()` 只做纯向量检索（Cosine 相似度）。实际场景中：

| 场景 | 纯向量检索表现 | 原因 |
|------|---------------|------|
| 精确关键词匹配（"MI210"、"6333端口"） | ❌ 差 | 语义距离可能较远，但关键词精确命中 |
| 缩写/术语（"OOM"、"CVE-2024-1234"） | ❌ 差 | Embedding 模型对缩写/编号的语义理解弱 |
| 语义相似查询（"风扇调速"→"GPU温度控制"） | ✅ 好 | 向量检索的强项 |
| 长尾/罕见词 | ❌ 差 | 训练数据少，向量空间中距离远 |

**竞品对标：**
- **Hermes Agent**（Issue #844）：规划 Hybrid Search — 向量 + 全文 + 元数据过滤
- **Claude Code**：AST 解析 + 文件路径匹配 + 语义搜索混合
- **LlamaIndex**：2025 主推 Agentic Retrieval，核心是 BM25 + Vector + Reranker 三层
- **Qdrant 1.8+**：原生支持 BM25 全文索引（`text_index` type）+ `prefetch` + `fusion: rrf`

#### 8.2 设计方案

**8.2.1 Collection 创建时同时建向量索引 + 全文索引**

```python
async def ensure_qdrant_collection(vector_size: int, collection: str | None = None) -> dict[str, Any]:
    """创建 collection 时同时建向量索引 + BM25 全文索引"""
    import aiohttp

    url = settings.qdrant_url.rstrip("/")
    col = collection or settings.qdrant_collection
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        # 检查是否已存在
        async with session.get(f"{url}/collections/{col}") as resp:
            if resp.status == 200:
                return {"ok": True, "existed": True, "collection": col}

        # 创建：向量索引 + 全文索引
        payload = {
            "vectors": {"size": vector_size, "distance": "Cosine"},
            "payload_schema": {
                "text": {
                    "type": "text",
                    "tokenizer": "word",
                    "min_token_len": 2,
                    "max_token_len": 20,
                    "lowercase": True,
                },
            },
        }
        async with session.put(f"{url}/collections/{col}", json=payload) as resp:
            text = await resp.text()
            if resp.status not in (200, 201):
                return {"ok": False, "message": f"创建 collection 失败 HTTP {resp.status}: {text[:300]}"}
            return {"ok": True, "existed": False, "collection": col}
```

**8.2.2 检索时用 prefetch + RRF 融合**

```python
async def hybrid_search(
    self,
    collection: str,
    query: str,
    vector: list[float],
    limit: int = 20,
    user_id: str | None = None,
) -> list[Document]:
    """混合检索：向量检索 + BM25 全文检索 + RRF 融合"""
    import aiohttp

    url = f"{self.qdrant_url}/collections/{collection}/points/search"
    
    # Qdrant prefetch + rrf 融合
    payload: dict[str, Any] = {
        "prefetch": [
            {"query": vector, "limit": limit, "options": {"exact": False}},       # 向量检索
            {"query": {"text": query}, "limit": limit, "using": "text"},           # BM25
        ],
        "limit": limit,
        "with_payload": True,
        "fusion": "rrf",  # Reciprocal Rank Fusion
    }
    if user_id:
        payload["filter"] = {"must": [{"key": "user_id", "match": {"value": user_id}}]}

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()
            results = []
            for item in data.get("result", []):
                payload_data = item.get("payload", {})
                results.append(Document(
                    id=str(item.get("id", "")),
                    text=payload_data.get("text", ""),
                    score=item.get("score", 0.0),
                    payload=payload_data,
                ))
            return results
```

**8.2.3 降级策略**

```python
async def search(self, collection, vector, limit=20, user_id=None, query_text=None):
    """检索入口：优先混合检索，Qdrant 版本不支持时降级为纯向量检索"""
    try:
        if query_text:
            return await self.hybrid_search(collection, query_text, vector, limit, user_id)
    except Exception as e:
        logger.warning(f"Hybrid search failed, falling back to vector-only: {e}")
    # 降级：纯向量检索
    return await self._vector_only_search(collection, vector, limit, user_id)
```

#### 8.3 前端展示

检索测试面板中增加检索模式选择：

```
检索模式: [混合 (BM25+Vector) ▼]  ← 默认
          ├ 混合 (BM25+Vector) — 推荐，召回率最高
          ├ 纯向量检索 — 语义相似场景
          └ 纯关键词检索 — 精确匹配场景
```

链路可视化中增加 BM25 分支：

```
① Embedding  ✅ 0.3s
   └ 维度: 1536 · 模型: text-embedding-3-small

② 混合检索  ✅ 0.5s
   ├ 向量检索: 命中 18 条
   └ BM25 检索: 命中 12 条
   └ RRF 融合: 合并 23 条（去重后）

③ Reranker 精排  ✅ 0.4s
   └ Top-5 筛选
```

---

### 九、多 Collection 路由 + 跨源 RRF 融合

#### 9.1 问题分析

当前 `qdrant_collection` 只配了一个 `knowledge_base`，但用户实际有 7 个 Qdrant collection：
- `knowledge_base` — 知识库文档
- `wiki_pages` — Wiki 图谱
- `session_history` — 会话记录
- `feishu_messages` — 飞书对话
- `pubchem` — PubChem 化合物
- `openfda_ndc` — OpenFDA NDC
- 其他自定义

所有类型文档混在一个 collection 里，检索噪声大，无法按类型过滤。

**竞品对标：**
- **Hermes Agent**：`qdrant_session_search` / `qdrant_wiki_search` / `qdrant_feishu_search` — 三个独立 collection + 独立工具
- **Claude Code**：代码库 / 文档 / 会话记忆 分开检索
- **OpenAI File Search**：每个 Vector Store 独立

#### 9.2 设计方案

**9.2.1 配置层：多 Collection 注册**

```python
# backend/core/config.py 新增:

qdrant_collections: dict[str, str] = {
    "knowledge": "knowledge_base",
    "wiki": "wiki_pages",
    "session": "session_history",
    "feishu": "feishu_messages",
}

# 默认检索范围（不指定 collection 时检索哪些）
rag_default_collections: list[str] = ["knowledge", "wiki"]
```

**9.2.2 多 Collection 并行检索 + RRF 融合**

```python
async def search_multi_collection(
    self,
    query: str,
    vector: list[float],
    collections: list[str] | None = None,
    top_k: int = 5,
    user_id: str | None = None,
) -> list[Document]:
    """多 Collection 并行检索 + RRF 融合"""
    import asyncio

    target_cols = collections or list(settings.qdrant_collections.values())
    
    # 并行检索
    tasks = [
        self.search(col, vector, limit=top_k * 3, user_id=user_id, query_text=query)
        for col in target_cols
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 过滤异常
    valid_results: list[list[Document]] = []
    for col, result in zip(target_cols, results_list):
        if isinstance(result, Exception):
            logger.warning(f"Collection {col} search failed: {result}")
            continue
        # 给每个结果打上来源标签
        for doc in result:
            doc.payload["_source_collection"] = col
        valid_results.append(result)
    
    if not valid_results:
        return []
    
    # RRF 融合
    return self._reciprocal_rank_fusion(valid_results, top_k=top_k)

def _reciprocal_rank_fusion(
    self,
    result_lists: list[list[Document]],
    top_k: int = 5,
    k: int = 60,
) -> list[Document]:
    """Reciprocal Rank Fusion — 多源结果融合排序"""
    scores: dict[str, float] = {}
    doc_map: dict[str, Document] = {}
    
    for results in result_lists:
        for rank, doc in enumerate(results):
            if doc.id not in scores:
                scores[doc.id] = 0.0
                doc_map[doc.id] = doc
            scores[doc.id] += 1.0 / (k + rank + 1)
    
    # 按融合分数排序
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [doc_map[doc_id] for doc_id, _ in ranked[:top_k]]
```

**9.2.3 search_knowledge_base 接口扩展**

```python
async def search_knowledge_base(
    self,
    query: str,
    top_k: int = 5,
    collection: str | None = None,       # 单 collection
    collections: list[str] | None = None, # 多 collection
    user_id: str | None = None,
    search_mode: str = "hybrid",          # hybrid | vector | keyword
    **kwargs: Any,
) -> str:
    """完整的 RAG 检索链路（支持多 Collection + 混合检索）"""
```

#### 9.3 前端展示

检索测试面板增加 Collection 选择：

```
检索范围: [☑ 知识库  ☑ Wiki  ☐ 会话记录  ☐ 飞书对话]  ← 多选
```

---

### 十、查询变换（Query Transformation）

#### 10.1 问题分析

当前 `search_knowledge_base(query)` 直接把用户原始查询丢给 embedding。但用户查询往往是：

| 问题类型 | 示例 | 当前表现 | 期望 |
|---------|------|---------|------|
| 口语化 | "那个风扇调速怎么搞" | ❌ 搜不到 | 扩展为 "MI210 GPU 风扇 powercap 调速配置" |
| 多意图 | "PubChem 有多少化合物？和 OpenFDA NDC 的关系？" | ❌ 只搜到一半 | 拆成两个子查询分别检索 |
| 精确匹配 | "Qdrant 6333 端口" | ❌ 语义距离远 | 走 BM25 精确匹配 |
| 模糊/宽泛 | "怎么用" | ❌ 噪声太多 | 缩窄到具体领域 |

**竞品对标：**
- **Hermes Agent**：RAG skill 里做了 query expansion + self-query（自动提取元数据过滤条件）
- **Self-Healing RAG**：HyDE（假设性文档嵌入）+ Query Decomposition
- **LlamaIndex Agentic Retrieval**：SubQuestionQueryEngine 自动拆解

#### 10.2 设计方案

**10.2.1 QueryTransformer 类**

```python
class QueryTransformer:
    """查询变换器 — 优化检索质量"""
    
    def __init__(self, llm_client=None):
        self.llm = llm_client  # 可选，用于 LLM 驱动的变换
    
    async def expand_query(self, query: str) -> list[str]:
        """查询扩展：生成同义/相关查询（无需 LLM，基于规则）"""
        expanded = [query]
        # 规则 1：中英互译扩展
        zh_en_map = {"风扇": "fan", "调速": "speed control", ...}
        # 规则 2：缩写展开
        abbr_map = {"OOM": "Out of Memory", "GPU": "Graphics Processing Unit", ...}
        # 规则 3：领域术语扩展
        # 如果有 LLM，则用 LLM 生成 2-3 个同义查询
        return expanded
    
    async def decompose_query(self, query: str) -> list[str]:
        """复杂查询拆解（需要 LLM）"""
        if not self.llm:
            return [query]
        # 用 LLM 判断是否需要拆解，并生成子查询
        prompt = f"""分析以下查询是否包含多个独立问题。如果是，拆解为子查询。
查询: {query}
输出 JSON: {{"needs_decompose": bool, "sub_queries": [...]}}"""
        # ...
    
    async def hyde_embed(self, query: str) -> list[float]:
        """HyDE: 先让 LLM 生成假设性答案，再对答案做 embedding。
        检索质量提升 15-30%（论文数据）。
        如果 LLM 不可用，退回原始查询 embedding。
        """
        if not self.llm:
            return []  # 退回原始 embedding
        # 1. LLM 生成假设性答案
        hypothetical = await self.llm.generate(f"简要回答: {query}")
        # 2. 对假设性答案做 embedding（而非原始查询）
        return await self.embedding_service.embed_query(hypothetical)
```

**10.2.2 集成到 RAG 检索链路**

```python
async def search_knowledge_base(self, query, top_k=5, ...):
    # 0. 查询变换
    transformer = QueryTransformer(llm_client=self._get_llm())
    sub_queries = await transformer.decompose_query(query)
    
    all_docs = []
    for sq in sub_queries:
        # 可选：HyDE embedding
        vector = await transformer.hyde_embed(sq) or await self.embed(sq)
        docs = await self.search_multi_collection(sq, vector, ...)
        all_docs.extend(docs)
    
    # 去重 + RRF 融合
    merged = self._reciprocal_rank_fusion([all_docs], top_k=top_k * 2)
    # Rerank
    ...
```

#### 10.3 配置项

```python
/core/config.py 新增:

rag_query_transform: bool = True           # 是否启用查询变换
rag_hyde_enabled: bool = False             # HyDE 默认关闭（需要额外 LLM 调用）
rag_query_expansion: bool = True           # 查询扩展（规则驱动，无额外开销）
rag_decompose_enabled: bool = False        # 查询拆解（需要 LLM，默认关闭）
```

---

### 十一、上下文注入策略（Retrieval Contract）

#### 11.1 问题分析

当前 `_format_context()` 只是简单拼 `## 文档 N (相关度: 0.92)` + 文本。问题：

| 问题 | 影响 |
|------|------|
| 无相关度阈值过滤缺失 | 0.3 以下的低相关度噪声也注入了，浪费 token |
| Token 预算无控制 | 检索结果可能占满上下文窗口，挤掉对话历史 |
| 来源不标注 | 用户无法区分知识库 vs Wiki vs 飞书 |
| 时效性不区分 | 3 年前的文档和今天的文档权重相同 |
| 去重缺失 | 同一文档的不同 chunk 可能重复注入 |

**竞品对标：**
- **Claude Code**：检索结果按 "Retrieval Contract" 结构化注入，带来源和置信度
- **Hermes Agent**：`.hermes.md` + RAG skill 控制注入时机和格式
- **Codex**：检索结果作为 tool output 注入，而非 system prompt

#### 11.2 设计方案

**11.2.1 RetrievalContract 数据类**

```python
@dataclass
class RetrievalContract:
    """检索契约 — 控制上下文注入策略"""
    min_score: float = 0.5                  # 最低相关度阈值
    max_tokens: int = 4000                  # 上下文 token 预算
    source_weights: dict[str, float] = {    # 来源权重
        "knowledge": 1.0,
        "wiki": 0.8,
        "session": 0.6,
        "feishu": 0.5,
    }
    recency_decay_days: int = 365           # 时效衰减周期（天）
    recency_decay_factor: float = 0.95      # 每周期衰减因子
    deduplicate: bool = True                # 跨源去重
    include_source_label: bool = True       # 标注来源
    include_timestamp: bool = True          # 标注时间
```

**11.2.2 上下文组装器**

```python
class ContextAssembler:
    """上下文组装器 — 按 RetrievalContract 组装最终上下文"""
    
    def __init__(self, contract: RetrievalContract | None = None):
        self.contract = contract or RetrievalContract()
    
    def assemble(self, results: list[RerankedResult], metadata: list[dict] | None = None) -> str:
        """组装上下文"""
        # 1. 阈值过滤
        filtered = [r for r in results if r.score >= self.contract.min_score]
        
        # 2. 来源加权
        if metadata:
            for r, m in zip(filtered, metadata):
                source = m.get("_source_collection", "knowledge")
                weight = self.contract.source_weights.get(source, 1.0)
                r.score *= weight
        
        # 3. 时效衰减
        if metadata and self.contract.recency_decay_days > 0:
            now = datetime.now(timezone.utc)
            for r, m in zip(filtered, metadata):
                created = m.get("created_at")
                if created:
                    age_days = (now - created).days
                    periods = age_days / self.contract.recency_decay_days
                    r.score *= self.contract.recency_decay_factor ** periods
        
        # 4. 去重（基于 document_id）
        if self.contract.deduplicate:
            seen_docs = set()
            deduped = []
            for r, m in zip(filtered, metadata or [{}] * len(filtered)):
                doc_id = m.get("document_id", r.text[:50])
                if doc_id not in seen_docs:
                    seen_docs.add(doc_id)
                    deduped.append((r, m))
            filtered_meta = deduped
        else:
            filtered_meta = list(zip(filtered, metadata or [{}] * len(filtered)))
        
        # 5. Token 预算控制
        budget = self.contract.max_tokens
        output_parts = []
        used_tokens = 0
        for r, m in filtered_meta:
            # 粗略估算：1 token ≈ 4 字符（中文）或 0.75 word（英文）
            est_tokens = len(r.text) // 3
            if used_tokens + est_tokens > budget:
                break
            
            source_label = ""
            if self.contract.include_source_label:
                src = m.get("_source_collection", "knowledge")
                source_label = f" [{src}]"
            
            time_label = ""
            if self.contract.include_timestamp and m.get("created_at"):
                time_label = f" ({m['created_at'][:10]})"
            
            output_parts.append(
                f"## 文档 {len(output_parts) + 1}{source_label} (相关度: {r.score:.3f}){time_label}\n{r.text}"
            )
            used_tokens += est_tokens
        
        if not output_parts:
            return ""
        return "# 检索到的相关知识\n\n" + "\n\n".join(output_parts)
```

**11.2.3 配置项**

```python
/core/config.py 新增:

rag_min_score: float = 0.5                 # 最低相关度阈值
rag_max_context_tokens: int = 4000         # 上下文 token 预算
rag_source_weights: dict = {               # 来源权重
    "knowledge": 1.0, "wiki": 0.8, "session": 0.6, "feishu": 0.5
}
rag_deduplicate: bool = True               # 跨源去重
```

---

### 十二、修订后的实施计划

#### Sprint 1：Embedding 健壮性 + 维度管理（3-4天）

| 任务 | 说明 |
|------|------|
| `EmbeddingMetadata` | 维度感知 + 健康检查 |
| `DimensionManager` | 维度兼容性检测 |
| 智能批量大小 | 根据维度动态选择 batch size |
| 维度不匹配检测 | embed 时校验 + Settings 页警告 |
| 工厂可重载 | `EmbeddingServiceFactory.reload()` |

#### Sprint 1.5：混合检索 + 多 Collection 路由（3-4天）🆕

| 任务 | 说明 |
|------|------|
| Collection 创建增强 | 同时建向量索引 + BM25 全文索引 |
| `hybrid_search()` | prefetch + RRF 融合检索 |
| 多 Collection 配置 | `qdrant_collections` + `rag_default_collections` |
| `search_multi_collection()` | 并行检索 + RRF 融合 |
| 降级策略 | Qdrant 版本不支持时自动降级为纯向量检索 |

#### Sprint 2：RAG 检索增强（2-3天）

| 任务 | 说明 |
|------|------|
| `RAGDiagnostics` | 检索诊断信息 |
| Qdrant 连接池+重试 | `QdrantClientPool` |
| 检索测试 API | `POST /api/knowledge/rag-test` |
| Qdrant 状态 API | `GET /api/knowledge/qdrant-status` |
| 检索降级策略 | Embedding 失败 → 提示 / Reranker 失败 → 退回粗排 |

#### Sprint 2.5：查询变换 + 上下文注入策略（2-3天）🆕

| 任务 | 说明 |
|------|------|
| `QueryTransformer` | 查询扩展（规则驱动）+ 拆解（LLM 驱动）+ HyDE |
| `RetrievalContract` | 上下文注入策略（阈值/预算/来源权重/时效/去重） |
| `ContextAssembler` | 按 Contract 组装最终上下文 |
| 配置项 | `rag_query_transform` / `rag_hyde_enabled` / `rag_min_score` 等 |

#### Sprint 3：前端可视化（3-4天）

| 任务 | 说明 |
|------|------|
| 检索测试面板 | 查询→可视化链路→结果展示（含混合检索模式选择 + Collection 多选） |
| Qdrant 状态面板 | 连接状态 + 维度 + 向量数 + 存储 |
| 文档索引进度条 | 实时进度 + 错误详情 |
| 知识库仪表盘 | 统计卡片（文档数/向量数/存储） |
| 维度不匹配处理 UI | 三种方案（重建/回退/新建） |
| 一键重建索引 | 后台任务 + 进度轮询 |

#### Sprint 4：打磨（1-2天）

| 任务 | 说明 |
|------|------|
| 错误分类+友好提示 | 网络错误 / 认证错误 / 维度错误 分别处理 |
| 空状态引导 | 第一次使用知识库的引导流程 |
| 批量上传 UX | 拖拽多文件 + 总体进度 |
| 检索质量评估 | 内置测试查询 + NDCG@10 / Recall@5 评估 |
