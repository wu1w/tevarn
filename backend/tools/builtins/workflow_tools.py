"""Agent 工作流工具集 — generate_workflow, update_dag, validate_dag, save_workflow"""

from __future__ import annotations

import re
from typing import Any

from backend.tools.base import BaseTool, ToolSource, ToolRiskLevel


# 端口默认映射：常见节点输出 → 下游输入
_DEFAULT_OUT_PORT = {
    "input": "value",
    "output": "value",
    "llm": "response",
    "agent": "result",
    "sub_agent": "result",
    "rag": "answer",
    "http": "response",
    "condition": "true",
    "loop": "results",
    "merge": "list",
    "python": "output",
    "custom": "output",
    "trigger": "value",
}
_DEFAULT_IN_PORT = {
    "input": "value",
    "output": "value",
    "llm": "prompt",
    "agent": "task",
    "sub_agent": "task",
    "rag": "query",
    "http": "body",
    "condition": "input",
    "loop": "items",
    "merge": "a",
    "python": "input_data",
    "custom": "input",
}


def _layout_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给节点补 position，线性水平布局。"""
    x0, y0, dx = 60, 200, 240
    out = []
    for i, n in enumerate(nodes):
        nn = dict(n)
        if "position" not in nn or not isinstance(nn.get("position"), dict):
            nn["position"] = {"x": x0 + i * dx, "y": y0}
        if "config" not in nn or nn["config"] is None:
            nn["config"] = {}
        if "label" not in nn:
            nn["label"] = nn.get("type", "node")
        out.append(nn)
    return out


def _wire_linear(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for i in range(len(nodes) - 1):
        a, b = nodes[i], nodes[i + 1]
        a_type = a.get("type", "")
        b_type = b.get("type", "")
        edges.append(
            {
                "id": f"edge-{i}",
                "from": a["id"],
                "to": b["id"],
                "fromPort": _DEFAULT_OUT_PORT.get(a_type, "output"),
                "toPort": _DEFAULT_IN_PORT.get(b_type, "input"),
            }
        )
    return edges


async def _load_enabled_subagents() -> list[dict[str, Any]]:
    """加载可用子代理（失败返回空列表）。"""
    try:
        from backend.repositories.sub_agent_repo import AsyncSubAgentRepository

        repo = AsyncSubAgentRepository()
        # 兼容不同 repo 接口
        rows = []
        if hasattr(repo, "list_enabled"):
            rows = await repo.list_enabled()  # type: ignore[attr-defined]
        elif hasattr(repo, "list_all"):
            rows = await repo.list_all()  # type: ignore[attr-defined]
        elif hasattr(repo, "list"):
            rows = await repo.list()  # type: ignore[attr-defined]
        else:
            # 尝试 get_all / list_by_user 无参
            for meth in ("get_all", "list_by_user"):
                if hasattr(repo, meth):
                    try:
                        rows = await getattr(repo, meth)()
                        break
                    except TypeError:
                        try:
                            rows = await getattr(repo, meth)(None)
                            break
                        except Exception:
                            pass
        result = []
        for r in rows or []:
            if hasattr(r, "enabled") and not getattr(r, "enabled", True):
                continue
            result.append(
                {
                    "id": str(getattr(r, "id", "")),
                    "name": getattr(r, "name", "") or "",
                    "description": getattr(r, "description", "") or "",
                    "icon": getattr(r, "icon", "🤖") or "🤖",
                    "model_ref": getattr(r, "model_ref", "") or "",
                    "max_iterations": int(getattr(r, "max_iterations", 5) or 5),
                }
            )
        return result
    except Exception:
        return []


def _match_subagents(description: str, agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按描述关键词匹配子代理；若提到子代理/集群/协作则取前 2 个。"""
    if not agents:
        return []
    desc = description.lower()
    matched: list[dict[str, Any]] = []
    for a in agents:
        keys = [a["name"].lower()]
        if a.get("description"):
            keys.append(a["description"].lower())
        # 拆词
        for token in re.split(r"[\s,，、/|]+", a["name"]):
            if len(token) >= 2:
                keys.append(token.lower())
        if any(k and k in desc for k in keys):
            matched.append(a)
    force = any(
        k in desc
        for k in (
            "子代理",
            "集群",
            "协作",
            "subagent",
            "sub-agent",
            "cluster",
            "审查",
            "审计",
            "研究员",
            "写手",
            "代码",
        )
    )
    if not matched and force:
        matched = agents[:2]
    # 去重保持顺序
    seen = set()
    out = []
    for a in matched:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        out.append(a)
    return out[:4]


class GenerateWorkflow(BaseTool):
    """根据自然语言描述生成工作流 DAG"""

    def __init__(self):
        super().__init__(
            name="generate_workflow",
            description=(
                "根据自然语言描述生成工作流 DAG（含节点 position / 端口连线）。"
                "支持子代理节点：若系统已配置子代理且描述涉及审查/协作/集群，"
                "会自动插入 type=sub_agent 节点。生成后可用 save_workflow 落库。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "用户用自然语言描述想要的工作流，如'输入文档后用代码审查员子代理审查再总结输出'",
                    },
                    "auto_save": {
                        "type": "boolean",
                        "description": "是否自动保存为工作流（默认 false）",
                        "default": False,
                    },
                    "name": {
                        "type": "string",
                        "description": "auto_save 时的工作流名称",
                    },
                },
                "required": ["description"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    async def execute(self, description: str, auto_save: bool = False, name: str = "", **kwargs: Any) -> dict[str, Any]:
        """根据描述生成工作流 DAG 建议"""
        from backend.schemas.workflow_node import get_all_node_type_definitions

        all_types = get_all_node_type_definitions()
        type_names = [t.type for t in all_types]
        agents = await _load_enabled_subagents()
        matched_agents = _match_subagents(description, agents)

        suggested_nodes: list[dict[str, Any]] = []
        desc_lower = description.lower()

        # 起点：输入
        suggested_nodes.append(
            {
                "id": "input-1",
                "type": "input",
                "label": "输入",
                "config": {"input_type": "text", "default_value": ""},
            }
        )

        if any(k in desc_lower for k in ["定时", "每天", "cron", "schedule", "自动"]):
            # 触发语义：保留 input，并在 label 提示 schedule
            suggested_nodes[0]["label"] = "定时/输入"
            suggested_nodes[0]["config"]["default_value"] = "0 9 * * *"

        if any(k in desc_lower for k in ["抓取", "爬取", "fetch", "http", "api", "请求", "webhook"]):
            suggested_nodes.append(
                {
                    "id": "http-1",
                    "type": "http",
                    "label": "HTTP 请求",
                    "config": {"method": "GET", "url": "https://example.com/api", "timeout": 30},
                }
            )

        if any(k in desc_lower for k in ["知识", "检索", "rag", "wiki", "文档库"]):
            suggested_nodes.append(
                {
                    "id": "rag-1",
                    "type": "rag",
                    "label": "知识检索",
                    "config": {"top_k": 5, "threshold": 0.7, "rerank": True},
                }
            )

        # 子代理优先于通用 llm/agent
        if matched_agents:
            for i, a in enumerate(matched_agents):
                suggested_nodes.append(
                    {
                        "id": f"subagent-{i + 1}",
                        "type": "sub_agent",
                        "label": f"{a.get('icon') or '🤖'} {a['name']}",
                        "config": {
                            "sub_agent_id": a["id"],
                            "sub_agent_name": a["name"],
                            "max_steps": a.get("max_iterations", 5),
                            "append_system_prompt": "",
                        },
                    }
                )
        else:
            if any(k in desc_lower for k in ["总结", "summarize", "llm", "ai", "生成", "写", "翻译", "分类", "意图"]):
                suggested_nodes.append(
                    {
                        "id": "llm-1",
                        "type": "llm",
                        "label": "LLM 处理",
                        "config": {
                            "model": "default",
                            "temperature": 0.7,
                            "max_tokens": 2048,
                            "system_prompt": "请根据输入完成任务并给出简洁结果。",
                        },
                    }
                )
            if any(k in desc_lower for k in ["agent", "智能体", "工具调用", "多步"]):
                suggested_nodes.append(
                    {
                        "id": "agent-1",
                        "type": "agent",
                        "label": "Agent 处理",
                        "config": {"agent_profile": "default", "max_steps": 10, "enable_tools": True},
                    }
                )

        if any(k in desc_lower for k in ["条件", "判断", "if", "分支", "是否"]):
            suggested_nodes.append(
                {
                    "id": "condition-1",
                    "type": "condition",
                    "label": "条件判断",
                    "config": {"condition": "len(str(input)) > 0"},
                }
            )

        if any(k in desc_lower for k in ["发送", "推送", "notify", "飞书", "邮件", "slack", "通知"]):
            suggested_nodes.append(
                {
                    "id": "http-notify",
                    "type": "http",
                    "label": "发送通知",
                    "config": {"method": "POST", "url": "https://webhook.example.com", "timeout": 30},
                }
            )

        # 至少 llm 一跳
        if len(suggested_nodes) == 1:
            suggested_nodes.append(
                {
                    "id": "llm-1",
                    "type": "llm",
                    "label": "LLM 处理",
                    "config": {
                        "model": "default",
                        "temperature": 0.7,
                        "max_tokens": 2048,
                        "system_prompt": f"根据用户目标处理输入：{description[:200]}",
                    },
                }
            )

        # 终点输出
        if not any(n.get("type") == "output" for n in suggested_nodes):
            suggested_nodes.append(
                {
                    "id": "output-1",
                    "type": "output",
                    "label": "输出",
                    "config": {"output_name": "result"},
                }
            )

        suggested_nodes = _layout_nodes(suggested_nodes)
        suggested_edges = _wire_linear(suggested_nodes)

        wf_name = (name or "").strip() or f"NL工作流-{description[:24].strip()}"
        saved: dict[str, Any] = {}
        if auto_save:
            try:
                save_tool = SaveWorkflow()
                save_res = await save_tool.execute(
                    name=wf_name,
                    nodes=suggested_nodes,
                    edges=suggested_edges,
                    description=description,
                    trigger="manual",
                )
                if save_res.get("success"):
                    saved = save_res.get("data") or {}
            except Exception as e:
                saved = {"error": str(e)}

        return {
            "success": True,
            "data": {
                "description": description,
                "name": wf_name,
                "suggested_nodes": suggested_nodes,
                "suggested_edges": suggested_edges,
                "dag": {"nodes": suggested_nodes, "edges": suggested_edges},
                "available_node_types": type_names,
                "available_sub_agents": [
                    {"id": a["id"], "name": a["name"], "icon": a.get("icon"), "model_ref": a.get("model_ref")}
                    for a in agents
                ],
                "matched_sub_agents": [a["name"] for a in matched_agents],
                "node_count": len(suggested_nodes),
                "edge_count": len(suggested_edges),
                "saved": saved,
            },
            "message": (
                f"已根据描述生成 {len(suggested_nodes)} 个节点 / {len(suggested_edges)} 条边"
                + (f"，匹配子代理: {', '.join(a['name'] for a in matched_agents)}" if matched_agents else "")
                + ("，并已保存" if saved.get("workflow_id") else "。可用 save_workflow 落库，或打开 /workflows 查看。")
            ),
        }


class UpdateDag(BaseTool):
    """更新工作流 DAG（增量修改）"""

    def __init__(self):
        super().__init__(
            name="update_dag",
            description="更新工作流 DAG。支持 add_node(添加节点)、remove_node(删除节点)、add_edge(添加连接)、remove_edge(删除连接)、update_node(更新节点配置) 等操作",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add_node", "remove_node", "add_edge", "remove_edge", "update_node"],
                        "description": "操作类型",
                    },
                    "workflow_id": {"type": "string", "description": "工作流 ID（可选，新建时省略）"},
                    "node": {
                        "type": "object",
                        "description": "add_node/update_node 时: 节点定义 {id, type, label, config, position?}",
                    },
                    "edge": {
                        "type": "object",
                        "description": "add_edge/remove_edge 时: 边定义 {id, from, to, condition?}",
                    },
                    "node_id": {"type": "string", "description": "remove_node/update_node 时: 节点 ID"},
                    "edge_id": {"type": "string", "description": "remove_edge 时: 边 ID"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, action: str, **kwargs: Any) -> dict[str, Any]:
        from backend.repositories.workflow_repo import AsyncWorkflowRepository

        workflow_id = kwargs.get("workflow_id")

        if workflow_id:
            repo = AsyncWorkflowRepository()
            wf = await repo.get_by_id(workflow_id)
            if not wf:
                return {"success": False, "data": {}, "message": f"❌ 工作流 `{workflow_id}` 不存在"}
            dag = wf.dag if isinstance(wf.dag, dict) else {"nodes": [], "edges": []}
        else:
            dag = {"nodes": [], "edges": []}

        nodes = list(dag.get("nodes", []) or [])
        edges = list(dag.get("edges", []) or [])

        if action == "add_node":
            node = kwargs.get("node", {})
            if not node or "id" not in node or "type" not in node:
                return {"success": False, "data": {}, "message": "❌ add_node 需要提供 node {id, type, ...}"}
            if any(n["id"] == node["id"] for n in nodes):
                return {"success": False, "data": {}, "message": f"❌ 节点 ID `{node['id']}` 已存在"}
            nodes.append(node)
            msg = f"✅ 节点 `{node.get('label', node['id'])}` 已添加"
        elif action == "remove_node":
            node_id = kwargs.get("node_id", "")
            if not node_id:
                return {"success": False, "data": {}, "message": "❌ remove_node 需要提供 node_id"}
            original_len = len(nodes)
            nodes = [n for n in nodes if n["id"] != node_id]
            edges = [e for e in edges if e.get("from") != node_id and e.get("to") != node_id]
            if len(nodes) == original_len:
                return {"success": False, "data": {}, "message": f"❌ 节点 `{node_id}` 不存在"}
            msg = f"✅ 节点 `{node_id}` 已删除"
        elif action == "add_edge":
            edge = kwargs.get("edge", {})
            if not edge or "from" not in edge or "to" not in edge:
                return {"success": False, "data": {}, "message": "❌ add_edge 需要提供 edge {from, to, ...}"}
            if "id" not in edge:
                edge = {**edge, "id": f"edge-{len(edges)+1}"}
            edges.append(edge)
            msg = f"✅ 连接 `{edge['from']} → {edge['to']}` 已添加"
        elif action == "remove_edge":
            edge_id = kwargs.get("edge_id", "")
            if not edge_id:
                return {"success": False, "data": {}, "message": "❌ remove_edge 需要提供 edge_id"}
            original_len = len(edges)
            edges = [e for e in edges if e.get("id") != edge_id]
            if len(edges) == original_len:
                return {"success": False, "data": {}, "message": f"❌ 边 `{edge_id}` 不存在"}
            msg = f"✅ 边 `{edge_id}` 已删除"
        elif action == "update_node":
            node_id = kwargs.get("node_id", "")
            node_update = kwargs.get("node", {})
            if not node_id or not node_update:
                return {"success": False, "data": {}, "message": "❌ update_node 需要提供 node_id 和 node"}
            found = False
            for i, n in enumerate(nodes):
                if n["id"] == node_id:
                    merged = {**n, **node_update, "id": node_id}
                    if "config" in node_update and isinstance(n.get("config"), dict):
                        merged["config"] = {**(n.get("config") or {}), **(node_update.get("config") or {})}
                    nodes[i] = merged
                    found = True
                    break
            if not found:
                return {"success": False, "data": {}, "message": f"❌ 节点 `{node_id}` 不存在"}
            msg = f"✅ 节点 `{node_id}` 已更新"
        else:
            return {"success": False, "data": {}, "message": f"❌ 未知 action: {action}"}

        # 若有 workflow_id 则写回
        if workflow_id:
            repo = AsyncWorkflowRepository()
            await repo.update(workflow_id, {"dag": {"nodes": nodes, "edges": edges}})

        return {
            "success": True,
            "data": {"nodes": nodes, "edges": edges, "workflow_id": workflow_id},
            "message": msg,
        }


class ValidateDag(BaseTool):
    """校验工作流 DAG"""

    def __init__(self):
        super().__init__(
            name="validate_dag",
            description="校验工作流 DAG 结构：节点类型、边引用、环检测、input/output 节点",
            parameters={
                "type": "object",
                "properties": {
                    "nodes": {"type": "array", "items": {"type": "object"}, "description": "节点列表"},
                    "edges": {"type": "array", "items": {"type": "object"}, "description": "边列表"},
                },
                "required": ["nodes", "edges"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    async def execute(self, nodes: list, edges: list, **kwargs: Any) -> dict[str, Any]:
        from backend.schemas.workflow_node import get_all_node_type_definitions

        errors: list[str] = []
        warnings: list[str] = []
        type_set = {t.type for t in get_all_node_type_definitions()}

        ids = set()
        types_hint = ", ".join(sorted(type_set))
        for n in nodes or []:
            nid = n.get("id")
            if not nid:
                errors.append("存在缺少 id 的节点")
                continue
            if nid in ids:
                errors.append(f"重复节点 ID: {nid}")
            ids.add(nid)
            ntype = n.get("type")
            if ntype not in type_set:
                errors.append(
                    f"未知节点类型: {ntype} ({nid})。合法类型: [{types_hint}]"
                )
            if ntype == "sub_agent":
                cfg = n.get("config") or {}
                if not cfg.get("sub_agent_id"):
                    warnings.append(f"子代理节点 {nid} 未配置 sub_agent_id")

        for e in edges or []:
            if e.get("from") not in ids:
                errors.append(f"边引用不存在的 from: {e.get('from')}")
            if e.get("to") not in ids:
                errors.append(f"边引用不存在的 to: {e.get('to')}")

        # 简单环检测
        adj: dict[str, list[str]] = {i: [] for i in ids}
        for e in edges or []:
            if e.get("from") in adj and e.get("to") in ids:
                adj[e["from"]].append(e["to"])
        visiting, visited = set(), set()

        def dfs(u: str) -> bool:
            visiting.add(u)
            for v in adj.get(u, []):
                if v in visiting:
                    return True
                if v not in visited and dfs(v):
                    return True
            visiting.discard(u)
            visited.add(u)
            return False

        for nid in ids:
            if nid not in visited and dfs(nid):
                errors.append("DAG 中存在环，请检查连接")
                break

        has_input = any(n.get("type") == "input" for n in nodes or [])
        has_output = any(n.get("type") == "output" for n in nodes or [])
        if not has_input:
            warnings.append("缺少 input 节点")
        if not has_output:
            warnings.append("缺少 output 节点")

        is_valid = len(errors) == 0
        msg = (
            "✅ DAG 验证通过"
            if is_valid
            else f"❌ DAG 验证失败 ({len(errors)} 个错误, {len(warnings)} 个警告)"
        )
        if not is_valid:
            msg += "\n合法节点类型: [" + types_hint + "]"
            if errors:
                msg += "\n- " + "\n- ".join(errors[:12])
        return {
            "success": is_valid,
            "data": {
                "valid": is_valid,
                "errors": errors,
                "warnings": warnings,
                "node_count": len(nodes or []),
                "edge_count": len(edges or []),
                "available_node_types": sorted(type_set),
            },
            "message": msg,
        }



class SaveWorkflow(BaseTool):
    """保存工作流到数据库"""

    def __init__(self):
        super().__init__(
            name="save_workflow",
            description="保存或更新工作流到数据库。如果提供了 workflow_id 则更新，否则创建新工作流",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "工作流名称"},
                    "description": {"type": "string", "description": "工作流描述"},
                    "nodes": {
                        "type": "array",
                        "description": "节点列表",
                        "items": {"type": "object"},
                    },
                    "edges": {
                        "type": "array",
                        "description": "边列表",
                        "items": {"type": "object"},
                    },
                    "workflow_id": {"type": "string", "description": "可选，存在则更新"},
                    "trigger": {
                        "type": "string",
                        "description": "触发方式: manual/cron/webhook",
                        "default": "manual",
                    },
                },
                "required": ["name", "nodes", "edges"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(
        self,
        name: str,
        nodes: list,
        edges: list,
        description: str = "",
        workflow_id: str = "",
        trigger: str = "manual",
        **kwargs: Any,
    ) -> dict[str, Any]:
        from backend.repositories.workflow_repo import AsyncWorkflowRepository

        repo = AsyncWorkflowRepository()
        # 补齐布局与端口
        nodes = _layout_nodes(list(nodes or []))
        edges = list(edges or [])
        for i, e in enumerate(edges):
            if "id" not in e:
                e["id"] = f"edge-{i}"
            # 补端口
            from_node = next((n for n in nodes if n.get("id") == e.get("from")), None)
            to_node = next((n for n in nodes if n.get("id") == e.get("to")), None)
            if from_node and not e.get("fromPort"):
                e["fromPort"] = _DEFAULT_OUT_PORT.get(from_node.get("type", ""), "output")
            if to_node and not e.get("toPort"):
                e["toPort"] = _DEFAULT_IN_PORT.get(to_node.get("type", ""), "input")

        dag = {"nodes": nodes, "edges": edges}

        if workflow_id:
            data = {"name": name, "description": description, "dag": dag, "trigger": trigger}
            wf = await repo.update(workflow_id, data)
            if wf:
                return {
                    "success": True,
                    "data": {"workflow_id": str(wf.id), "name": name, "dag": dag},
                    "message": f"✅ 工作流 `{name}` 已更新",
                }
            return {"success": False, "data": {}, "message": f"❌ 工作流 `{workflow_id}` 不存在"}

        data = {
            "name": name,
            "description": description,
            "dag": dag,
            "trigger": trigger or "manual",
            "status": "draft",
        }
        # 尽量绑定默认用户
        try:
            import uuid as _uuid

            data["user_id"] = _uuid.UUID("314016d7-a9d5-4719-8371-7ec9301fba0b")
        except Exception:
            pass
        wf = await repo.create(data)
        return {
            "success": True,
            "data": {"workflow_id": str(wf.id), "name": name, "dag": dag},
            "message": f"✅ 工作流 `{name}` 已创建（可在 /workflows 打开）",
        }
