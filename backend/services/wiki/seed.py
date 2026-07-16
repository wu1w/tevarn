"""
Wiki 图谱预置种子数据：Takton / Agent / RAG 通识基础知识

原则：
- 仅空库时写入
- 内容稳定，不随时效过期
- 覆盖核心概念、常用技术、典型关系
"""

from __future__ import annotations

from typing import Any

from backend.services.wiki.schema import WikiSchema


WIKI_SEED_ENTITIES: list[dict[str, Any]] = [
    # 组织 / 项目
    {
        "name": "Takton",
        "entity_type": "project",
        "description": "个人专属 AI Agent 终端，整合对话、知识库、工作流、定时任务与工具生态。",
        "aliases": ["Takton Agent", "Agent Terminal"],
    },
    {
        "name": "Nous Research",
        "entity_type": "organization",
        "description": "开源 AI 研究机构，Hermes Agent 系列与相关工具生态的维护者。",
        "aliases": ["Nous"],
    },
    # 核心概念
    {
        "name": "Agent",
        "entity_type": "concept",
        "description": "能够感知环境、调用工具并自主决策完成任务的智能体。",
        "aliases": ["AI Agent", "智能体"],
    },
    {
        "name": "RAG",
        "entity_type": "concept",
        "description": "Retrieval-Augmented Generation，检索增强生成，通过外部知识库减少模型幻觉。",
        "aliases": ["检索增强生成", "RAG 系统"],
    },
    {
        "name": "知识库",
        "entity_type": "concept",
        "description": "存储结构化或半结构化文档的仓库，为 RAG 提供可检索的外部上下文。",
        "aliases": ["Knowledge Base"],
    },
    {
        "name": "Wiki 图谱",
        "entity_type": "concept",
        "description": "以实体和关系构建的知识网络，用于展示概念关联并辅助推理。",
        "aliases": ["知识图谱", "Wiki Graph"],
    },
    {
        "name": "Embedding",
        "entity_type": "concept",
        "description": "将文本映射为稠密向量，使语义相似的内容在向量空间中距离相近。",
        "aliases": ["嵌入", "文本向量"],
    },
    {
        "name": "向量检索",
        "entity_type": "concept",
        "description": "通过向量相似度（如余弦相似度）从向量库中召回相关文本。",
        "aliases": ["Vector Search"],
    },
    {
        "name": "混合检索",
        "entity_type": "concept",
        "description": "结合向量检索与关键词检索（如 BM25），并通过 RRF 等算法融合结果。",
        "aliases": ["Hybrid Search"],
    },
    {
        "name": "精排",
        "entity_type": "concept",
        "description": "在初排结果基础上使用更精确的模型对候选重新排序。",
        "aliases": ["Rerank", "Re-ranking"],
    },
    {
        "name": "工作流",
        "entity_type": "concept",
        "description": "由多个节点按依赖关系组成的可编排自动化任务流程。",
        "aliases": ["Workflow", "DAG"],
    },
    {
        "name": "定时任务",
        "entity_type": "concept",
        "description": "按 Cron 表达式触发的周期性自动化任务。",
        "aliases": ["Cron Job", "Scheduled Task"],
    },
    {
        "name": "上下文窗口",
        "entity_type": "concept",
        "description": "LLM 单次推理可处理的最大 token 数量，决定能参考多少历史与检索结果。",
        "aliases": ["Context Window"],
    },
    {
        "name": "提示词工程",
        "entity_type": "concept",
        "description": "通过设计输入提示来引导 LLM 输出质量更高、更可控的结果。",
        "aliases": ["Prompt Engineering"],
    },
    # 技术 / 工具
    {
        "name": "Qdrant",
        "entity_type": "tech",
        "description": "开源向量数据库，支持向量存储、混合检索与多租户。",
        "aliases": ["向量数据库"],
    },
    {
        "name": "llama.cpp",
        "entity_type": "tech",
        "description": "基于 C/C++ 的 LLM 推理引擎，支持 GGUF 量化模型高效本地运行。",
        "aliases": ["llama cpp"],
    },
    {
        "name": "FastAPI",
        "entity_type": "tech",
        "description": "Python 高性能异步 Web 框架，用于构建 Takton 后端 API。",
        "aliases": [],
    },
    {
        "name": "Next.js",
        "entity_type": "tech",
        "description": "React 全栈框架，用于构建 Takton 前端界面。",
        "aliases": ["NextJS"],
    },
    {
        "name": "Docker",
        "entity_type": "tech",
        "description": "容器化平台，常用于部署 Qdrant、Embedding 服务等依赖组件。",
        "aliases": [],
    },
    {
        "name": "Python",
        "entity_type": "tech",
        "description": "Takton 后端主要编程语言，拥有丰富的 AI/ML 生态。",
        "aliases": [],
    },
    {
        "name": "TypeScript",
        "entity_type": "tech",
        "description": "Takton 前端主要编程语言，提供静态类型支持。",
        "aliases": ["TS"],
    },
    {
        "name": "React",
        "entity_type": "tech",
        "description": "用于构建用户界面的 JavaScript 库。",
        "aliases": [],
    },
    {
        "name": "Electron",
        "entity_type": "tech",
        "description": "跨平台桌面应用框架，用于将 Takton 打包为桌面客户端。",
        "aliases": [],
    },
    {
        "name": "SQLAlchemy",
        "entity_type": "tech",
        "description": "Python ORM 工具，用于 Takton 数据库模型与异步查询。",
        "aliases": [],
    },
    {
        "name": "Pydantic",
        "entity_type": "tech",
        "description": "Python 数据验证与序列化库，用于 FastAPI 请求/响应模型。",
        "aliases": [],
    },
    # 文档 / 事件
    {
        "name": "Takton 文档",
        "entity_type": "docs",
        "description": "Takton 项目的官方使用文档与开发指南。",
        "aliases": ["Takton Docs"],
    },
    {
        "name": "LLM 微调",
        "entity_type": "concept",
        "description": "在基础模型之上使用自有数据进一步训练，以获得领域专属能力。",
        "aliases": ["Fine-tuning"],
    },
    {
        "name": "API 兼容性",
        "entity_type": "concept",
        "description": "通过统一的 OpenAI 兼容接口对接不同厂商或本地部署的模型服务。",
        "aliases": ["OpenAI-compatible API"],
    },
    # 问题 / 方案
    {
        "name": "模型幻觉",
        "entity_type": "problem",
        "description": "LLM 生成与事实不符或无法验证的内容，是 RAG 要解决的核心问题之一。",
        "aliases": ["Hallucination"],
    },
    {
        "name": "上下文过载",
        "entity_type": "problem",
        "description": "注入 LLM 的上下文过长导致成本上升、注意力分散与性能下降。",
        "aliases": ["Context Overflow"],
    },
    {
        "name": "知识检索",
        "entity_type": "solution",
        "description": "通过 Embedding + 向量库 + 精排从外部知识库召回相关文本作为上下文。",
        "aliases": ["Knowledge Retrieval"],
    },
    {
        "name": "上下文压缩",
        "entity_type": "solution",
        "description": "对历史对话与检索结果进行摘要、去重，以适配有限的上下文窗口。",
        "aliases": ["Context Compression"],
    },
    {
        "name": "MCP",
        "entity_type": "concept",
        "description": "Model Context Protocol，标准化 LLM 与外部工具/数据源之间的上下文交换协议。",
        "aliases": ["Model Context Protocol"],
    },
    {
        "name": "工具调用",
        "entity_type": "concept",
        "description": "LLM 根据上下文生成调用外部工具的参数，由执行环境运行并返回结果。",
        "aliases": ["Function Calling", "Tool Use"],
    },
]

WIKI_SEED_RELATIONS: list[dict[str, Any]] = [
    {"source_name": "Takton", "target_name": "Agent", "relation_type": "presents", "evidence": "Takton 是 Agent 终端"},
    {"source_name": "Takton", "target_name": "知识库", "relation_type": "uses", "evidence": "Takton 使用知识库存储文档"},
    {"source_name": "Takton", "target_name": "Wiki 图谱", "relation_type": "presents", "evidence": "Takton 提供 Wiki 图谱功能"},
    {"source_name": "Takton", "target_name": "工作流", "relation_type": "uses", "evidence": "Takton 支持工作流编排"},
    {"source_name": "Takton", "target_name": "定时任务", "relation_type": "uses", "evidence": "Takton 支持定时任务"},
    {"source_name": "Takton", "target_name": "FastAPI", "relation_type": "uses", "evidence": "Takton 后端使用 FastAPI"},
    {"source_name": "Takton", "target_name": "Next.js", "relation_type": "uses", "evidence": "Takton 前端使用 Next.js"},
    {"source_name": "Takton", "target_name": "Electron", "relation_type": "uses", "evidence": "Takton 桌面端使用 Electron"},
    {"source_name": "Takton", "target_name": "SQLAlchemy", "relation_type": "uses", "evidence": "Takton 使用 SQLAlchemy 访问数据库"},
    {"source_name": "Takton", "target_name": "Pydantic", "relation_type": "uses", "evidence": "Takton 使用 Pydantic 做数据校验"},
    {"source_name": "Takton", "target_name": "Takton 文档", "relation_type": "presents", "evidence": "Takton 项目提供官方文档"},
    {"source_name": "RAG", "target_name": "知识库", "relation_type": "depends_on", "evidence": "RAG 依赖知识库提供检索源"},
    {"source_name": "RAG", "target_name": "Embedding", "relation_type": "uses", "evidence": "RAG 使用 Embedding 做语义检索"},
    {"source_name": "RAG", "target_name": "向量检索", "relation_type": "uses", "evidence": "RAG 使用向量检索召回文本"},
    {"source_name": "RAG", "target_name": "精排", "relation_type": "uses", "evidence": "RAG 使用精排模型重排序结果"},
    {"source_name": "RAG", "target_name": "混合检索", "relation_type": "uses", "evidence": "RAG 可结合向量与关键词检索"},
    {"source_name": "混合检索", "target_name": "向量检索", "relation_type": "part_of", "evidence": "混合检索包含向量检索"},
    {"source_name": "混合检索", "target_name": "Qdrant", "relation_type": "uses", "evidence": "Qdrant 支持混合检索"},
    {"source_name": "模型幻觉", "target_name": "RAG", "relation_type": "solves", "evidence": "RAG 通过外部知识缓解幻觉"},
    {"source_name": "上下文过载", "target_name": "上下文压缩", "relation_type": "solves", "evidence": "上下文压缩解决上下文过长问题"},
    {"source_name": "上下文窗口", "target_name": "上下文过载", "relation_type": "related_to", "evidence": "窗口长度有限导致过载"},
    {"source_name": "知识检索", "target_name": "RAG", "relation_type": "part_of", "evidence": "知识检索是 RAG 的核心环节"},
    {"source_name": "Qdrant", "target_name": "向量检索", "relation_type": "uses", "evidence": "Qdrant 提供向量检索能力"},
    {"source_name": "Qdrant", "target_name": "Docker", "relation_type": "uses", "evidence": "Qdrant 常以 Docker 方式部署"},
    {"source_name": "llama.cpp", "target_name": "Embedding", "relation_type": "uses", "evidence": "llama.cpp 可部署 Embedding 服务"},
    {"source_name": "llama.cpp", "target_name": "Python", "relation_type": "alternative_to", "evidence": "llama.cpp 提供 C++ 推理方案"},
    {"source_name": "FastAPI", "target_name": "Python", "relation_type": "depends_on", "evidence": "FastAPI 基于 Python"},
    {"source_name": "Next.js", "target_name": "React", "relation_type": "depends_on", "evidence": "Next.js 基于 React"},
    {"source_name": "Next.js", "target_name": "TypeScript", "relation_type": "uses", "evidence": "Next.js 项目通常使用 TypeScript"},
    {"source_name": "React", "target_name": "TypeScript", "relation_type": "uses", "evidence": "React 可配合 TypeScript 使用"},
    {"source_name": "Electron", "target_name": "Next.js", "relation_type": "uses", "evidence": "Electron 内嵌 Next.js 前端"},
    {"source_name": "Nous Research", "target_name": "Takton", "relation_type": "related_to", "evidence": "Takton 参考 Nous Research 的 Hermes Agent 生态"},
    {"source_name": "MCP", "target_name": "工具调用", "relation_type": "related_to", "evidence": "MCP 标准化工具调用协议"},
    {"source_name": "工具调用", "target_name": "Agent", "relation_type": "part_of", "evidence": "工具调用是 Agent 核心能力之一"},
    {"source_name": "提示词工程", "target_name": "Agent", "relation_type": "related_to", "evidence": "提示词工程影响 Agent 行为"},
    {"source_name": "LLM 微调", "target_name": "Agent", "relation_type": "related_to", "evidence": "微调可提升 Agent 领域能力"},
    {"source_name": "API 兼容性", "target_name": "Agent", "relation_type": "related_to", "evidence": "Agent 通过兼容 API 接入不同模型"},
    {"source_name": "API 兼容性", "target_name": "llama.cpp", "relation_type": "uses", "evidence": "llama.cpp 提供 OpenAI 兼容接口"},
    {"source_name": "上下文压缩", "target_name": "上下文窗口", "relation_type": "solves", "evidence": "压缩结果适配上下文窗口"},
]
