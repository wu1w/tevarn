"""Takton AIOS 互操作协议（0.1）——可移植 Agent Card + A2A-lite 工单信封。

设计原则：
- 对齐业界 Agent-OS / A2A「可描述、可投递」最小集，不假装完整多厂联邦。
- 用户心智仍只暴露 员工 / 工单 / 审批；协议层是工程互操作面。
- schema 可演进：凡对外 JSON 带 protocol_version + kind。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = "0.2.0"
PROTOCOL_NAME = "takton-aios-protocol"

# 标准能力词表（与编制 / mediate 对齐；未知能力仍允许但标 extension）
STANDARD_CAPABILITIES: dict[str, str] = {
    "file_read": "Read files in workspace",
    "file_rw": "Read and write files",
    "command": "Run shell commands",
    "web_search": "Search the web",
    "browser": "Browse pages / extract content",
    "git": "Git operations",
    "notify": "Notify the owner",
    "mcp": "Call MCP tools",
    "memory_read": "Read long-term memory",
    "memory_write": "Write long-term memory (policy gated)",
}

# 产品心智三词（机器可读，与 docs/internal/concepts.md 同源）
PRODUCT_CONCEPTS: dict[str, dict[str, str]] = {
    "employee": {
        "user_term_zh": "员工",
        "user_term_en": "Employee",
        "system_entity": "AgentIdentity",
        "summary_zh": "持久编制：姓名、职责、权限、预算、记忆",
        "summary_en": "Durable crew profile: role, caps, budget, memory",
    },
    "job": {
        "user_term_zh": "工单",
        "user_term_en": "Job",
        "system_entity": "AgentInboxItem",
        "summary_zh": "派给某员工的一条活：pending→claimed→done/failed/cancelled",
        "summary_en": "One unit of work assigned to an employee",
    },
    "approval": {
        "user_term_zh": "审批",
        "user_term_en": "Approval",
        "system_entity": "Escalation|EvolutionProposal",
        "summary_zh": "提权与进化等人审；默认不静默改权",
        "summary_en": "Human review for escalation and evolution",
    },
}

# 工程名 → 用户说法（不对用户发明新名词）
LEGACY_TERM_MAP: dict[str, str] = {
    "SubAgent": "员工技能包/模板",
    "KernelProcess": "员工正在执行的一次运行",
    "Cluster": "高级（多代理集群）",
    "Workflow": "高级（工作流）",
    "Goal": "高级（目标）",
    "Hire": "新建员工",
    "WorkforceReport": "日报/工作汇报",
    "CapabilityToken": "权限令牌（内部）",
}


@dataclass
class AgentSkill:
    """Agent Card skill（能力/技能一项）。"""

    id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
        }


@dataclass
class AgentCard:
    """可移植员工描述（A2A Agent Card 风格 + Takton 扩展）。"""

    name: str
    description: str
    identity_id: str
    skills: list[AgentSkill] = field(default_factory=list)
    status: str = "active"
    role: str | None = None
    token_budget: int | None = None
    credit_score: float | None = None
    url: str = ""
    version: str = "0.4.10-alpha"
    memory_kinds: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "agent_card",
            "protocol_version": PROTOCOL_VERSION,
            "protocol": PROTOCOL_NAME,
            "name": self.name,
            "description": self.description,
            "url": self.url or f"takton://identity/{self.identity_id}",
            "version": self.version,
            "protocolVersion": PROTOCOL_VERSION,  # A2A 常见字段
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "stateTransitionHistory": True,
            },
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [s.to_dict() for s in self.skills],
            "takton": {
                "identity_id": self.identity_id,
                "status": self.status,
                "role": self.role,
                "token_budget": self.token_budget,
                "credit_score": self.credit_score,
                "memory_kinds": list(self.memory_kinds),
                "product_concept": "employee",
                "meta": dict(self.meta or {}),
            },
        }


def capability_to_skill(cap: str) -> AgentSkill:
    desc = STANDARD_CAPABILITIES.get(cap, f"Extension capability: {cap}")
    tags = ["capability"]
    if cap not in STANDARD_CAPABILITIES:
        tags.append("extension")
    return AgentSkill(id=cap, name=cap, description=desc, tags=tags)


def identity_to_agent_card(
    ident: Any,
    *,
    memory_entries: list[Any] | None = None,
    version: str | None = None,
) -> AgentCard:
    """从 Identity 行构造可移植 Agent Card。"""
    if version is None:
        try:
            from backend.core.version import product_version

            version = product_version()
        except Exception:
            version = "0.4.10-alpha"
    caps = list(getattr(ident, "capabilities", None) or [])
    skills = [capability_to_skill(str(c)) for c in caps]
    duty = ""
    persona = ""
    kinds: list[str] = []
    for m in memory_entries or []:
        k = str(getattr(m, "kind", "") or "")
        if k and k not in kinds:
            kinds.append(k)
        content = str(getattr(m, "content", "") or "")
        if k == "duty" and not duty:
            duty = content[:400]
        if k == "persona" and not persona:
            persona = content[:200]
    role = getattr(ident, "role", None)
    name = str(getattr(ident, "name", "") or "agent")
    desc_parts = [p for p in (role, duty or persona) if p]
    description = " — ".join(str(p) for p in desc_parts) or f"Takton employee «{name}»"
    return AgentCard(
        name=name,
        description=description,
        identity_id=str(getattr(ident, "id", "")),
        skills=skills,
        status=str(getattr(ident, "status", "active") or "active"),
        role=str(role) if role else None,
        token_budget=getattr(ident, "default_token_budget", None),
        credit_score=getattr(ident, "credit_score", None),
        version=version,
        memory_kinds=kinds,
        meta={
            "sub_agent_id": str(getattr(ident, "sub_agent_id", "") or "") or None,
        },
    )


@dataclass
class TaskEnvelope:
    """A2A-lite 任务信封 → 映射为 Inbox 工单。

    最小兼容：message_id + parts[].text + metadata.identity_*
    """

    message_id: str
    text: str
    identity_id: str | None = None
    identity_name: str | None = None
    priority: int = 0
    source: str = "a2a"
    role: str = "user"
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "task_envelope",
            "protocol_version": PROTOCOL_VERSION,
            "message_id": self.message_id,
            "role": self.role,
            "parts": [{"type": "text", "text": self.text}],
            "metadata": {
                "identity_id": self.identity_id,
                "identity_name": self.identity_name,
                "priority": self.priority,
                "source": self.source,
                **{k: v for k, v in self.metadata.items() if k not in (
                    "identity_id", "identity_name", "priority", "source"
                )},
            },
        }


def parse_task_envelope(body: dict[str, Any]) -> TaskEnvelope:
    """解析 A2A-lite / 简化 JSON 为 TaskEnvelope。校验失败抛 ValueError。"""
    if not isinstance(body, dict):
        raise ValueError("task body must be object")
    message_id = str(body.get("message_id") or body.get("id") or uuid.uuid4())
    # parts: [{type:text, text:...}] or instruction / content top-level
    text = ""
    parts = body.get("parts")
    if isinstance(parts, list):
        chunks: list[str] = []
        for p in parts:
            if not isinstance(p, dict):
                continue
            if p.get("type") in (None, "text") and p.get("text"):
                chunks.append(str(p["text"]))
            elif p.get("type") == "data" and p.get("data") is not None:
                chunks.append(str(p["data"])[:2000])
        text = "\n".join(chunks).strip()
    if not text:
        text = str(
            body.get("instruction")
            or body.get("content")
            or body.get("text")
            or ""
        ).strip()
    if not text:
        raise ValueError("task requires text (parts[].text or instruction)")

    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    # 也接受顶层 identity 字段
    identity_id = (
        body.get("identity_id")
        or meta.get("identity_id")
        or meta.get("agent_id")
        or None
    )
    identity_name = (
        body.get("identity_name")
        or meta.get("identity_name")
        or meta.get("agent_name")
        or None
    )
    if identity_id is not None:
        identity_id = str(identity_id).strip() or None
    if identity_name is not None:
        identity_name = str(identity_name).strip() or None
    if not identity_id and not identity_name:
        raise ValueError("task requires metadata.identity_id or identity_name")

    try:
        priority = int(body.get("priority", meta.get("priority", 0)) or 0)
    except (TypeError, ValueError):
        priority = 0
    source = str(body.get("source") or meta.get("source") or "a2a")
    if source not in ("cron", "webhook", "api", "manual", "a2a"):
        # inbox 只认白名单；a2a 映射为 api
        source = "api"
    if str(body.get("source") or meta.get("source") or "") == "a2a":
        # 保留语义在 payload，投递用 api
        pass

    return TaskEnvelope(
        message_id=message_id,
        text=text,
        identity_id=identity_id,
        identity_name=identity_name,
        priority=priority,
        source="api",  # InboxService 白名单
        role=str(body.get("role") or "user"),
        metadata={
            **meta,
            "a2a_message_id": message_id,
            "a2a_source": str(body.get("source") or meta.get("source") or "a2a"),
            "protocol_version": PROTOCOL_VERSION,
        },
        raw=dict(body),
    )


def protocol_manifest() -> dict[str, Any]:
    """协议清单：版本、能力、端点提示、心智三词。"""
    return {
        "kind": "protocol_manifest",
        "protocol": PROTOCOL_NAME,
        "protocol_version": PROTOCOL_VERSION,
        "product_concepts": PRODUCT_CONCEPTS,
        "legacy_term_map": LEGACY_TERM_MAP,
        "standard_capabilities": STANDARD_CAPABILITIES,
        "interop": {
            "agent_card": {
                "list": "GET /api/kernel/protocol/agent-cards",
                "get": "GET /api/kernel/protocol/agent-cards/{identity_id}",
            },
            "a2a_tasks": {
                "submit": "POST /api/kernel/protocol/a2a/tasks",
                "note": "Maps to Inbox job for employee; source stored in payload",
            },
            "governance": "GET /api/kernel/protocol/governance",
            "surface": "GET /api/kernel/protocol/surface",
            "domain_events": {
                "rest": "GET /api/kernel/events/domain",
                "ws": "WS /api/ws/domain",
                "note": "0.2: multi-client subscribe (CLI/Electron/Web) same Kernel",
            },
            "runtime": "GET /api/runtime/status",
            "mcp": "Existing MCP hub under /api/mcp*",
        },
        "client_guide": {
            "snapshot_then_events": True,
            "commands": [
                "POST /api/kernel/inbox",
                "POST /api/kernel/jobs/stop",
                "POST /api/kernel/escalations/{id}/approve",
            ],
            "cli": "python -m backend.cli status|jobs|job-stop|approve|events",
        },
        "non_goals": [
            "multi-tenant SaaS",
            "hard real-time HRT scheduling",
            "silent auto_apply of evolution caps",
            "full Google A2A multi-hop federation (0.2 is local interop + events)",
        ],
        "ts": time.time(),
    }
